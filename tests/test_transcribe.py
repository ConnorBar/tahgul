"""Whisper transcription (milestone 8b), against a fake OpenAI-compatible server."""

import httpx
import pytest
from test_voice_notes import API, upload

from tahgul import transcribe
from tahgul.config import get_settings


@pytest.fixture
def whisper(monkeypatch):
    """A fake speaches server. ``state`` controls it and records what it was sent."""
    state = {
        "calls": [],
        "downloaded": False,
        "need_download": False,
        "fail": False,
        "text": " Call Laura about the VLAN plan.\n",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append((request.method, request.url.path))
        if request.url.path.startswith("/v1/models/"):
            state["downloaded"] = True
            return httpx.Response(201, json={})
        if state["fail"]:
            return httpx.Response(500, text="CUDA out of memory")
        if state["need_download"] and not state["downloaded"]:
            return httpx.Response(
                404,
                json={"detail": "Model 'Systran/faster-whisper-small' is not installed locally"},
            )
        body = request.read()
        assert b'name="model"' in body and b"Systran/faster-whisper-small" in body
        assert b'filename="note.wav"' in body
        return httpx.Response(200, json={"text": state["text"]})

    monkeypatch.setattr(get_settings(), "whisper_url", "http://whisper:8000")
    monkeypatch.setattr(transcribe, "_transport", httpx.MockTransport(handler))
    return state


def test_upload_is_transcribed(logged_in, whisper):
    c = logged_in
    assert c.get(f"{API}/features").json()["transcription"] is True
    n = upload(c, meta={"title": "VLAN"})
    assert n["transcript_status"] == "pending"  # returned before the background job ran
    n = c.get(f"{API}/voice-notes/{n['id']}").json()
    assert n["transcript_status"] == "done" and n["transcript_source"] == "whisper"
    assert n["transcript"] == "Call Laura about the VLAN plan."
    assert c.get(f"{API}/voice-notes", params={"q": "laura"}).json()[0]["id"] == n["id"]


def test_yog_transcript_is_kept(logged_in, whisper):
    n = upload(logged_in, meta={"transcript": "Yog's words"})
    assert n["transcript_status"] == "done" and n["transcript_source"] == "yog"
    assert whisper["calls"] == []


def test_model_downloaded_on_first_use(logged_in, whisper):
    whisper["need_download"] = True
    n = upload(logged_in, meta={})
    n = logged_in.get(f"{API}/voice-notes/{n['id']}").json()
    assert n["transcript_status"] == "done"
    assert ("POST", "/v1/models/Systran/faster-whisper-small") in whisper["calls"]


def test_failure_then_retry(logged_in, whisper):
    c = logged_in
    whisper["fail"] = True
    n = upload(c, meta={})
    n = c.get(f"{API}/voice-notes/{n['id']}").json()
    assert n["transcript_status"] == "failed" and "500" in n["transcript_error"]
    whisper["fail"] = False
    whisper["text"] = "Second try."
    r = c.post(f"{API}/voice-notes/{n['id']}/transcribe")
    assert r.status_code == 200
    n = c.get(f"{API}/voice-notes/{n['id']}").json()
    assert n["transcript"] == "Second try." and n["transcript_error"] is None


def test_typed_transcript_wins_over_late_whisper(logged_in, whisper, monkeypatch):
    c = logged_in
    monkeypatch.setattr(transcribe, "run_pending", lambda *a, **k: 0)  # hold the job
    n = upload(c, meta={})
    c.patch(f"{API}/voice-notes/{n['id']}", json={"transcript": "Mine."})
    monkeypatch.undo()
    monkeypatch.setattr(get_settings(), "whisper_url", "http://whisper:8000")
    assert transcribe.run_pending() == 0
    assert c.get(f"{API}/voice-notes/{n['id']}").json()["transcript"] == "Mine."


def test_unreachable_server(logged_in, monkeypatch):
    def down(request):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(get_settings(), "whisper_url", "http://whisper:8000")
    monkeypatch.setattr(transcribe, "_transport", httpx.MockTransport(down))
    n = upload(logged_in, meta={})
    n = logged_in.get(f"{API}/voice-notes/{n['id']}").json()
    assert n["transcript_status"] == "failed" and "reach" in n["transcript_error"]


def test_off_by_default(logged_in):
    n = upload(logged_in, meta={})
    assert n["transcript_status"] == "none"
    r = logged_in.post(f"{API}/voice-notes/{n['id']}/transcribe")
    assert r.status_code == 409 and r.json()["detail"] == "whisper_not_configured"
    assert transcribe.run_pending() == 0
