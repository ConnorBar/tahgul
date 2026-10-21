"""Browser smoke tests against a running stack (SPEC: tests, "Playwright smoke tests").

Skipped unless TAHGUL_SMOKE_URL is set. CI starts the Docker stack, seeds demo data and
runs these; locally:

    docker compose exec api tahgul seed-demo
    TAHGUL_SMOKE_URL=http://localhost:8000 TAHGUL_SMOKE_EMAIL=you@example.com \\
      TAHGUL_SMOKE_PASSWORD=… pytest tests/smoke
"""

import io
import os
import re
import struct
import wave

import pytest

URL = os.environ.get("TAHGUL_SMOKE_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not URL, reason="set TAHGUL_SMOKE_URL to run smoke tests")

sync_api = pytest.importorskip("playwright.sync_api") if URL else None


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        # TAHGUL_SMOKE_CHROMIUM: use an already-installed Chromium instead of Playwright's.
        exe = os.environ.get("TAHGUL_SMOKE_CHROMIUM") or None
        b = pw.chromium.launch(executable_path=exe)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900}, timezone_id="America/Chicago"
    )
    p = ctx.new_page()
    p.on("dialog", lambda d: d.accept())
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(URL + "/")
    p.get_by_label("Email").fill(os.environ["TAHGUL_SMOKE_EMAIL"])
    p.get_by_label("Password").fill(os.environ["TAHGUL_SMOKE_PASSWORD"])
    p.get_by_role("button", name="Sign in").click()
    p.get_by_role("heading", name="Today").wait_for()
    yield p
    ctx.close()
    assert errors == [], f"JavaScript errors: {errors}"


def expect(locator):
    return sync_api.expect(locator)


def test_api_docs(page):
    page.goto(URL + "/api/docs")
    expect(page.get_by_text("Tahgul API").first).to_be_visible()


def test_timer_start_and_stop(page):
    page.get_by_label("Timer notes").fill("Smoke test timer")
    page.get_by_label("Start timer").click()
    expect(page.get_by_label("Stop timer")).to_be_visible()
    page.get_by_label("Stop timer").click()
    expect(page.get_by_label("Start timer")).to_be_visible()


def test_board_quick_add(page):
    page.goto(URL + "/projects")
    page.get_by_role("button", name=re.compile("Network audit")).first.click()
    page.get_by_label("Quick add task").fill("Smoke: label the patch panel !low #network")
    page.get_by_label("Quick add task").press("Enter")
    expect(page.get_by_text("Smoke: label the patch panel").first).to_be_visible()


def test_time_and_reports(page):
    page.goto(URL + "/time")
    expect(page.locator(".entry").first).to_be_visible()
    page.goto(URL + "/reports")
    expect(page.get_by_role("heading", name="By client")).to_be_visible()
    expect(page.locator("main table").get_by_text("Acme Farms").first).to_be_visible()


def test_calendar_renders(page):
    page.goto(URL + "/calendar")
    expect(page.locator(".fc").first).to_be_visible()


def test_invoice_draft_issue_pdf(page):
    page.goto(URL + "/invoices")
    page.get_by_role("button", name="New invoice").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Client").select_option(label="Acme Farms")
    dialog.get_by_label("Work from").fill("2020-01-01")
    dialog.get_by_role("button", name="Create draft").click()
    expect(page.get_by_role("heading", name=re.compile("Draft invoice"))).to_be_visible()
    page.get_by_role("button", name="Issue invoice").click()
    expect(page.get_by_role("heading", name=re.compile(r"^Invoice "))).to_be_visible()
    with page.expect_download() as dl:
        page.get_by_role("link", name="Download PDF").click()
    path = dl.value.path()
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF"


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * 16000)
    return buf.getvalue()


def test_idea_with_voice_note(page):
    page.goto(URL + "/ideas")
    page.locator(".capture input[type=file]").set_input_files(
        {"name": "smoke-idea.wav", "mimeType": "audio/wav", "buffer": _wav()}
    )
    audio = page.locator(".ideas .note audio").first
    expect(audio).to_be_attached()
    duration = audio.evaluate(
        "a => new Promise(r => a.readyState >= 1 ? r(a.duration)"
        " : a.addEventListener('loadedmetadata', () => r(a.duration)))"
    )
    assert 1.9 < duration < 2.1
