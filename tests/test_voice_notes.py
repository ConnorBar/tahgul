"""Ideas inbox and voice notes (milestone 8)."""

import io
import json
import struct
import wave
from pathlib import Path

import pytest

from tahgul.config import get_settings

API = "/api/v1"


def wav(seconds: float = 2.0, rate: int = 8000) -> bytes:
    """A real (silent) WAV file, so durations and range requests can be checked."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x02\x00M4A isommp42" + b"\x00" * 200


def upload(c, data=None, name="note.wav", meta=None, headers=None, expect=201):
    files = {"file": (name, data if data is not None else wav(), "audio/wav")}
    form = {"metadata": json.dumps(meta)} if meta is not None else {}
    r = c.post(f"{API}/voice-notes", files=files, data=form, headers=headers or {})
    assert r.status_code == expect, r.text
    return r.json()


def token(c, *scopes):
    r = c.post(f"{API}/tokens", json={"name": "yog", "scopes": list(scopes)})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def acme(logged_in):
    c = logged_in
    client = c.post(f"{API}/clients", json={"name": "Acme"}).json()
    return c.post(
        f"{API}/projects", json={"name": "Network audit", "client_id": client["id"]}
    ).json()


def test_upload_makes_an_idea_and_plays_back(logged_in):
    c = logged_in
    n = upload(c, meta={"title": "Drone surveys for farms", "recorded_at": "2026-10-20T08:15:00"})
    assert n["project_status"] == "idea" and n["project_name"] == "Drone surveys for farms"
    assert n["duration_seconds"] == 2.0 and n["mime_type"] == "audio/wav"
    assert n["recorded_at"].startswith("2026-10-20T13:15")  # 8:15 Chicago
    assert n["transcript_status"] == "none"

    # Stored under audio/yyyy/mm/ in the data dir.
    files = list((Path(get_settings().data_dir) / "audio" / "2026" / "10").glob("*.wav"))
    assert len(files) == 1

    r = c.get(n["playback_url"])
    assert r.status_code == 200 and r.content == wav()
    assert r.headers["content-type"] == "audio/wav" and r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-disposition"].startswith("inline")

    # Range requests, so the player can seek.
    r = c.get(n["playback_url"], headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == wav()[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{len(wav())}"

    r = c.get(n["download_url"])
    disp = r.headers["content-disposition"]
    assert disp.startswith("attachment") and "Drone%20surveys%20for%20farms.wav" in disp

    ideas = c.get(f"{API}/ideas").json()
    assert ideas[0]["name"] == "Drone surveys for farms" and ideas[0]["voice_note_count"] == 1


def test_curl_minimal_upload(logged_in):
    """Just a file: it lands in the inbox as a new idea named after the time."""
    n = upload(logged_in, data=M4A, name="Memo.m4a")
    assert n["mime_type"] == "audio/mp4" and n["project_name"].startswith("Voice note ")
    assert n["title"] == "Memo.m4a" and n["duration_seconds"] is None


def test_targets(logged_in, acme):
    c = logged_in
    n = upload(c, meta={"project_id": acme["id"]})
    assert n["project_name"] == "Network audit" and n["task_id"] is None
    r = upload(c, meta={"project": "network"}, expect=409)  # fuzzy: ask, don't guess
    assert r["detail"]["code"] == "needs_confirmation"
    assert len(list((Path(get_settings().data_dir) / "audio").rglob("*.wav"))) == 1
    n = upload(c, meta={"project": "Network Audit"})
    assert n["project_id"] == acme["id"]
    task = c.post(f"{API}/tasks", json={"project_id": acme["id"], "title": "VLAN plan"}).json()
    n = upload(c, meta={"task_id": task["id"], "transcript": "Split guest wifi onto VLAN 30."})
    assert n["task_ref"] == task["ref"] and n["project_id"] == acme["id"]
    assert n["transcript_source"] == "yog" and n["transcript_status"] == "done"
    idea = upload(c, meta={"new_idea": {"title": "Grain bin sensors", "description": "LoRa?"}})
    assert idea["project_status"] == "idea" and idea["project_name"] == "Grain bin sensors"

    on_project = c.get(f"{API}/voice-notes", params={"project_id": acme["id"]}).json()
    assert len(on_project) == 3
    on_task = c.get(f"{API}/voice-notes", params={"task_id": task["id"]}).json()
    assert [x["id"] for x in on_task] == [n["id"]]
    hits = c.get(f"{API}/voice-notes", params={"q": "vlan"}).json()
    assert [x["id"] for x in hits] == [n["id"]]


def test_yog_dedupes_and_token_scopes(logged_in, client):
    c = logged_in
    yog = client.__class__(client.app)
    h = token(c, "read", "write")
    meta = {
        "yog_id": "yog-123",
        "title": "Pricing thoughts",
        "duration_seconds": 61.5,
        "recorded_at": "2026-10-19T21:00:00Z",
    }
    first = upload(yog, meta=meta, headers=h)
    assert first["source"] == "yog" and first["duplicate"] is False
    assert first["duration_seconds"] == 61.5  # Yog's own value wins
    again = upload(yog, meta=meta, headers=h, expect=200)
    assert again["id"] == first["id"] and again["duplicate"] is True
    assert len(c.get(f"{API}/voice-notes").json()) == 1

    ro = token(c, "read")
    upload(yog, meta={}, headers=ro, expect=403)
    # A token can't delete audio (admin only).
    assert yog.delete(f"{API}/voice-notes/{first['id']}", headers=h).status_code == 403


def test_rejects_non_audio_and_bad_metadata(logged_in):
    c = logged_in
    r = upload(c, data=b"#!/bin/sh\necho hi\n" * 10, name="evil.mp3", expect=415)
    assert r["detail"] == "unsupported_audio"
    r = c.post(
        f"{API}/voice-notes",
        files={"file": ("a.wav", wav(), "audio/wav")},
        data={"metadata": "{not json"},
    )
    assert r.status_code == 422
    assert not list((Path(get_settings().data_dir) / "audio").rglob("*.part"))


def test_too_large(logged_in, monkeypatch):
    from tahgul import audio

    monkeypatch.setattr(audio, "MAX_BYTES", 10_000)
    r = upload(logged_in, data=wav(seconds=2), expect=413)
    assert r["detail"] == "audio_too_large"


def test_edit_move_and_delete(logged_in, acme):
    c = logged_in
    n = upload(c, meta={"title": "first take"})
    idea_id = n["project_id"]
    r = c.patch(
        f"{API}/voice-notes/{n['id']}",
        json={"title": "Kickoff notes", "transcript": "Call Laura about the switch."},
    )
    assert r.json()["transcript_source"] == "owner" and r.json()["title"] == "Kickoff notes"
    r = c.patch(f"{API}/voice-notes/{n['id']}", json={"project_id": acme["id"]})
    assert r.json()["project_name"] == "Network audit"
    assert c.get(f"{API}/ideas").json()[0]["voice_note_count"] == 0
    assert c.get(f"{API}/ideas").json()[0]["id"] == idea_id

    path = next((Path(get_settings().data_dir) / "audio").rglob("*.wav"))
    assert c.delete(f"{API}/voice-notes/{n['id']}").status_code == 204
    assert not path.exists()
    assert c.get(f"{API}/voice-notes/{n['id']}").status_code == 404
    assert c.get(n["playback_url"]).status_code == 404


def test_deleting_work_keeps_audio(logged_in, acme):
    c = logged_in
    task = c.post(f"{API}/tasks", json={"project_id": acme["id"], "title": "Survey"}).json()
    on_task = upload(c, meta={"task_id": task["id"]})
    assert c.delete(f"{API}/tasks/{task['id']}").status_code == 204
    n = c.get(f"{API}/voice-notes/{on_task['id']}").json()
    assert n["task_id"] is None and n["project_id"] == acme["id"]

    assert c.delete(f"{API}/projects/{acme['id']}").status_code == 204
    n = c.get(f"{API}/voice-notes/{on_task['id']}").json()
    assert n["project_id"] is None
    assert c.get(f"{API}/voice-notes", params={"unfiled": True}).json()[0]["id"] == n["id"]
    assert c.get(n["playback_url"]).status_code == 200


def test_promote_idea_keeps_notes(logged_in, acme):
    c = logged_in
    n = upload(c, meta={"new_idea": {"title": "Farm drone service"}})
    pid = n["project_id"]
    c.post(f"{API}/tasks", json={"project_id": pid, "title": "Price a drone"})
    r = c.post(f"{API}/ideas/{pid}/promote", json={"client_id": acme["client_id"]})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert c.get(f"{API}/voice-notes", params={"project_id": pid}).json()[0]["id"] == n["id"]
    assert c.get(f"{API}/ideas").json() == []


def test_idea_from_existing_note(logged_in, acme):
    c = logged_in
    n = upload(c, meta={"project_id": acme["id"]})
    idea = c.post(f"{API}/ideas", json={"title": "Spin-off", "voice_note_id": n["id"]}).json()
    assert c.get(f"{API}/voice-notes/{n['id']}").json()["project_id"] == idea["id"]


def test_idempotency_key_does_not_buffer_uploads(logged_in):
    c = logged_in
    h = {"Idempotency-Key": "abc"}
    a = upload(c, meta={"title": "one"}, headers=h)
    b = upload(c, meta={"title": "two"}, headers=h)
    assert a["id"] != b["id"]  # uploads dedupe by yog_id, not the header


def test_features(logged_in):
    assert logged_in.get(f"{API}/features").json()["transcription"] is False
