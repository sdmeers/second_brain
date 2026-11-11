"""Voice capture from the GTD web app (and the storage it shares with Telegram)."""

import io
import math
import struct
import wave
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import actions as actions_api
from backend.processing import voice_capture
from backend.vault import actions


def tone(seconds=0.3, rate=8000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate))))
    return out.getvalue()


@pytest.fixture(autouse=True)
def clean_inbox(vault):
    inbox = vault / "00_System/Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for f in inbox.glob("*_voice.*"):
        f.unlink()
    yield


@pytest.fixture
def heard(monkeypatch):
    """Stub Gemini; records what audio it was handed."""
    calls = []

    async def fake(file_bytes, mime_type):
        calls.append((file_bytes, mime_type))
        return fake.says

    fake.says = "Chase the electrician about the lights"
    monkeypatch.setattr("backend.processing.transcriber.transcribe_audio_via_gemini", fake)
    fake.calls = calls
    return fake


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(actions_api.router)
    actions_api._recent.clear()
    return TestClient(app)


def upload(client, upload_id="u1", mime="audio/webm;codecs=opus", data=None, **form):
    return client.post("/api/actions/capture-audio",
                       files={"file": ("note", data if data is not None else tone(), mime)},
                       data={"upload_id": upload_id, **form})


class TestFormats:
    def test_browser_mime_types_map_to_extensions(self):
        assert voice_capture.extension_for("audio/webm;codecs=opus") == "webm"
        assert voice_capture.extension_for("audio/mp4") == "m4a"
        assert voice_capture.extension_for("audio/ogg") == "ogg"
        assert voice_capture.extension_for("image/png") is None

    async def test_ogg_goes_to_gemini_untouched(self, heard):
        await voice_capture.transcribe(b"ogg-bytes", "audio/ogg")
        assert heard.calls == [(b"ogg-bytes", "audio/ogg")]

    async def test_browser_recordings_are_converted_to_wav_first(self, heard):
        """Gemini does not list WebM or MP4 audio as supported."""
        await voice_capture.transcribe(tone(), "audio/webm;codecs=opus")
        sent, mime = heard.calls[0]
        assert mime == "audio/wav"
        with wave.open(io.BytesIO(sent)) as w:
            assert (w.getframerate(), w.getnchannels()) == (16000, 1)
            assert w.getnframes() > 0


class TestSaveVoiceCapture:
    def test_keeps_the_audio_and_links_it_from_captured(self, vault):
        when = datetime(2026, 11, 11, 9, 30, 5)
        line = voice_capture.save_voice_capture(b"audio", "ogg", "Ring the bank", via="Telegram Voice", when=when)
        assert line == ("- Ring the bank (Captured on 2026-11-11 09:30 via Telegram Voice) "
                        "(Source: [[2026-11-11_09-30-05_voice.ogg]])")
        assert (vault / "00_System/Inbox/2026-11-11_09-30-05_voice.ogg").read_bytes() == b"audio"
        assert actions.read_action_lists()["lists"]["captured"][-1]["text"] == "Ring the bank"

    def test_two_notes_in_the_same_second_do_not_overwrite_each_other(self, vault):
        when = datetime(2026, 11, 11, 9, 30, 5)
        voice_capture.save_voice_capture(b"one", "webm", "First", via="GTD app Voice", when=when)
        voice_capture.save_voice_capture(b"two", "webm", "Second", via="GTD app Voice", when=when)
        assert sorted(f.read_bytes() for f in (vault / "00_System/Inbox").glob("*_voice.webm")) == [b"one", b"two"]


class TestInboxScannerLeavesRecordingsAlone:
    async def test_voice_files_of_any_format_are_not_ingested_as_drops(self, vault, monkeypatch):
        """Only .ogg and .mp3 were excluded; a .webm or .m4a recording would have been
        picked up as a dropped file and captured a second time."""
        from backend.ingestion import watcher
        inbox = vault / "00_System/Inbox"
        for ext in ("ogg", "webm", "m4a"):
            (inbox / f"2026-11-11_09-30-05_voice.{ext}").write_bytes(b"audio")
        queued = []
        monkeypatch.setattr(watcher, "add_to_queue", lambda *a, **k: queued.append((a, k)))
        await watcher.scan_and_process_inbox()
        assert queued == []
        assert len(list(inbox.glob("*_voice.*"))) == 3


class TestCaptureAudioEndpoint:
    def test_transcript_lands_in_captured(self, client, heard, vault):
        body = upload(client, captured_at="2026-11-11 09:30").json()
        assert body["status"] == "applied"
        assert body["text"] == "Chase the electrician about the lights"
        assert body["new_raw"].startswith(
            "- Chase the electrician about the lights (Captured on 2026-11-11 09:30 via GTD app Voice) (Source: [[")
        assert body["lists"]["captured"][-1]["text"] == "Chase the electrician about the lights"
        assert len(list((vault / "00_System/Inbox").glob("*_voice.webm"))) == 1

    def test_resending_the_same_recording_captures_it_once(self, client, heard):
        first = upload(client).json()
        second = upload(client).json()
        assert second["new_raw"] == first["new_raw"]
        assert len(heard.calls) == 1
        assert [i["text"] for i in second["lists"]["captured"]].count("Chase the electrician about the lights") == 1

    def test_silence_is_dropped(self, client, heard, vault):
        heard.says = "  "
        body = upload(client).json()
        assert (body["status"], body["reason"]) == ("skipped", "silent")
        assert body["lists"]["captured"] == []
        assert list((vault / "00_System/Inbox").glob("*_voice.*")) == []

    def test_transcription_failure_is_retryable(self, client, monkeypatch, vault):
        async def boom(*_):
            raise RuntimeError("quota")
        monkeypatch.setattr("backend.processing.transcriber.transcribe_audio_via_gemini", boom)
        assert upload(client).status_code == 502
        assert actions.read_action_lists()["lists"]["captured"] == []
        # and it was not remembered as done, so the retry is attempted for real
        assert "u1" not in actions_api._recent

    def test_unreadable_recording_is_refused_not_retried(self, client, heard):
        """A 502 here made the app re-send a broken recording every 15 seconds, forever."""
        assert upload(client, data=b"\x1a\x45\xdf\xa3 not really webm").status_code == 422
        assert heard.calls == []

    def test_non_audio_upload_is_refused(self, client, heard):
        assert upload(client, mime="image/png").status_code == 415
        assert heard.calls == []
