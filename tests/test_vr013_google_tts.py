"""VR-013 GoogleTTS: config (R-4), speak() table (§2), worker and cancellation (R-5).

The SDK client class is faked; requests, responses and enums are the real
google.cloud.texttospeech message types. The provider is reached only through
``create_tts("google", ...)``.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time

import pytest

texttospeech = pytest.importorskip(
    "google.cloud.texttospeech", reason="optional google extra not installed"
)

from google.api_core import exceptions as gexc  # noqa: E402 — after skip (AC-02)
from voice_runtime.tts import create_tts  # noqa: E402 — after skip (AC-02)

FRAME = 160
BLOCK = object()
STUCK = object()
CANCEL_BUDGET_S = 1.0
GOOGLE_ENV = (
    "GOOGLE_SPEECH_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_SPEECH_LOCATION",
    "GOOGLE_TTS_VOICE",
)


class FakeSession:
    """Records frames, taps and marks with a shared, ordered event log."""

    def __init__(self, disconnected: bool = False) -> None:
        self._disconnected = disconnected
        self._lock = threading.Lock()
        self.events: list[tuple[str, object]] = []
        self.mark_behaviour: str | None = None

    @property
    def is_disconnected(self) -> bool:
        return self._disconnected

    def disconnect(self) -> None:
        self._disconnected = True

    def _record(self, kind: str, data: object) -> None:
        with self._lock:
            self.events.append((kind, data))

    def put_outbound_sync(self, data: bytes) -> None:
        self._record("frame", bytes(data))

    def tap_agent(self, data: bytes) -> None:
        self._record("tap", bytes(data))

    def send_mark_and_wait(self, name: str, timeout: float = 30.0) -> None:
        self._record("mark", (name, timeout))
        if self.mark_behaviour == "timeout":
            raise TimeoutError("mark timeout")
        if self.mark_behaviour == "disconnect":
            self.disconnect()

    def of(self, kind: str) -> list:
        with self._lock:
            return [d for k, d in self.events if k == kind]

    def count(self) -> int:
        with self._lock:
            return len(self.events)


class FakeCall:
    """Response stream; a BLOCK item blocks the read until cancel().

    ``cancel_delay`` makes cancel() itself slow; ``exit_delay`` delays the
    blocked read's exit after cancel. A STUCK item ignores cancel and blocks
    until ``release`` is set (a worker that cannot be terminated).
    """

    def __init__(
        self,
        script: list,
        blocked: threading.Event,
        cancel_delay: float = 0.0,
        exit_delay: float = 0.0,
        release: threading.Event | None = None,
    ) -> None:
        self._items = list(script)
        self._cancelled = threading.Event()
        self._blocked = blocked
        self._cancel_delay = cancel_delay
        self._exit_delay = exit_delay
        self._release = release or threading.Event()
        self.cancel_count = 0

    def cancel(self) -> None:
        self.cancel_count += 1
        time.sleep(self._cancel_delay)
        self._cancelled.set()

    def __iter__(self):
        return self

    def __next__(self):
        if self._cancelled.is_set():
            raise gexc.Cancelled("Locally cancelled")
        if not self._items:
            raise StopIteration
        item = self._items.pop(0)
        if item is BLOCK:
            self._blocked.set()
            self._cancelled.wait(timeout=10.0)
            time.sleep(self._exit_delay)
            raise gexc.Cancelled("Locally cancelled")
        if item is STUCK:
            self._blocked.set()
            self._release.wait(timeout=10.0)
            raise gexc.Cancelled("Locally cancelled")
        if isinstance(item, BaseException):
            raise item
        return texttospeech.StreamingSynthesizeResponse(audio_content=item)


class FakeStreamingIterator:
    """Models api_core's _StreamingResponseIterator: prefetches the first response."""

    def __init__(self, call: FakeCall) -> None:
        self._call = call
        self._first = None
        with contextlib.suppress(StopIteration):
            self._first = next(call)

    def cancel(self) -> None:
        self._call.cancel()

    def __iter__(self):
        return self

    def __next__(self):
        if self._first is not None:
            first, self._first = self._first, None
            return first
        return next(self._call)


class FakeTransport:
    def __init__(self, client: FakeClient) -> None:
        self._client = client
        self.closed = False

    def close(self) -> None:
        self.closed = True
        for call in self._client.calls:
            call.cancel()


class FakeSdk:
    def __init__(self) -> None:
        self.script: list = []
        self.clients: list[FakeClient] = []
        self.blocked = threading.Event()
        self.before_return = None
        self.cancel_delay = 0.0
        self.exit_delay = 0.0
        self.release = threading.Event()

    @property
    def requests(self) -> list:
        return [r for c in self.clients for r in c.requests]

    @property
    def worker_threads(self) -> list[threading.Thread]:
        return [t for c in self.clients for t in c.threads]


class FakeClient:
    sdk: FakeSdk

    def __init__(self, client_options=None, **kwargs) -> None:
        self.client_options = client_options
        self.kwargs = kwargs
        self.requests: list = []
        self.calls: list[FakeCall] = []
        self.threads: list[threading.Thread] = []
        self.transport = FakeTransport(self)
        self.sdk.clients.append(self)

    def streaming_synthesize(self, requests=None, **kwargs):
        self.threads.append(threading.current_thread())
        self.requests.extend(list(requests))
        call = FakeCall(
            self.sdk.script,
            self.sdk.blocked,
            self.sdk.cancel_delay,
            self.sdk.exit_delay,
            self.sdk.release,
        )
        self.calls.append(call)
        if self.transport.closed:
            call.cancel()
        stream = FakeStreamingIterator(call)
        if self.sdk.before_return is not None:
            self.sdk.before_return(self)
        return stream


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in GOOGLE_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sdk(monkeypatch) -> FakeSdk:
    fake = FakeSdk()
    client_cls = type("FakeTextToSpeechClient", (FakeClient,), {"sdk": fake})
    monkeypatch.setattr(texttospeech, "TextToSpeechClient", client_cls)
    yield fake
    fake.release.set()
    for thread in fake.worker_threads:
        thread.join(timeout=5.0)


def _expected_config(voice: str = "fi-FI-Chirp3-HD-Charon", lang: str = "fi-FI"):
    return texttospeech.StreamingSynthesizeRequest(
        streaming_config=texttospeech.StreamingSynthesizeConfig(
            voice=texttospeech.VoiceSelectionParams(name=voice, language_code=lang),
            streaming_audio_config=texttospeech.StreamingAudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MULAW,
                sample_rate_hertz=8000,
            ),
        )
    )


def _assert_quiet_after_return(session: FakeSession, sdk: FakeSdk) -> None:
    for thread in sdk.worker_threads:
        assert thread is not threading.main_thread()
        assert not thread.daemon
        assert thread.is_alive() is False
    before = session.count()
    time.sleep(0.2)
    assert session.count() == before


def _payload(n: int, seed: int) -> bytes:
    return bytes((seed + i * 37) % 256 for i in range(n))


# ── Factory (AC-09) ─────────────────────────────────────────────────────


@pytest.mark.req("VR-013")
def test_create_tts_google_returns_google_tts(sdk):
    tts = create_tts("google", project="p")
    assert type(tts).__name__ == "GoogleTTS"
    assert type(tts).__module__ == "voice_runtime.providers.google_tts"
    assert tts.on_error is None


# ── Configuration (R-4, AC-08) ──────────────────────────────────────────


@pytest.mark.req("VR-013")
def test_default_endpoint_quota_project_and_voice(sdk):
    tts = create_tts("google", project="p")
    session = FakeSession()
    tts.speak("Hei", session)

    assert len(sdk.clients) == 1
    opts = sdk.clients[0].client_options
    assert opts.api_endpoint == "eu-texttospeech.googleapis.com"
    assert opts.quota_project_id == "p"
    assert sdk.requests[0] == _expected_config()


@pytest.mark.req("VR-013")
def test_explicit_args_beat_environment(sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "env-project")
    monkeypatch.setenv("GOOGLE_SPEECH_LOCATION", "us")
    monkeypatch.setenv("GOOGLE_TTS_VOICE", "fi-FI-Chirp3-HD-Aoede")
    tts = create_tts(
        "google", project="p", location="eu", voice_name="fi-FI-Chirp3-HD-Charon"
    )
    tts.speak("Hei", FakeSession())

    opts = sdk.clients[0].client_options
    assert opts.quota_project_id == "p"
    assert opts.api_endpoint == "eu-texttospeech.googleapis.com"
    assert sdk.requests[0] == _expected_config()


@pytest.mark.req("VR-013")
def test_environment_beats_default(sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "env-project")
    monkeypatch.setenv("GOOGLE_SPEECH_LOCATION", "us")
    monkeypatch.setenv("GOOGLE_TTS_VOICE", "en-US-Chirp3-HD-Aoede")
    tts = create_tts("google")
    tts.speak("Hei", FakeSession())

    opts = sdk.clients[0].client_options
    assert opts.quota_project_id == "env-project"
    assert opts.api_endpoint == "us-texttospeech.googleapis.com"
    assert sdk.requests[0] == _expected_config("en-US-Chirp3-HD-Aoede", "en-US")


@pytest.mark.req("VR-013")
def test_speech_project_beats_cloud_project(sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "speech")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cloud")
    create_tts("google").speak("Hei", FakeSession())
    assert sdk.clients[0].client_options.quota_project_id == "speech"


@pytest.mark.req("VR-013")
def test_empty_speech_project_falls_through_to_cloud_project(sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cloud")
    create_tts("google").speak("Hei", FakeSession())
    assert sdk.clients[0].client_options.quota_project_id == "cloud"


@pytest.mark.req("VR-013")
def test_explicit_empty_project_raises_in_constructor(sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "env-project")
    with pytest.raises(ValueError, match="project"):
        create_tts("google", project="")
    assert sdk.clients == []


@pytest.mark.req("VR-013")
def test_missing_project_raises_from_speak_after_early_returns(sdk):
    tts = create_tts("google")
    stop = threading.Event()
    stop.set()

    assert tts.speak("   ", FakeSession()) == {"last_spoken": ""}
    assert tts.speak("Hei", FakeSession(disconnected=True)) == {
        "last_spoken": "",
        "call_disconnected": True,
    }
    assert tts.speak("Hei", FakeSession(), stop_event=stop) == {
        "last_spoken": "Hei",
        "interrupted": True,
    }
    assert sdk.clients == []

    session = FakeSession()
    with pytest.raises(ValueError, match="project"):
        tts.speak("Hei", session)
    assert sdk.clients == []
    assert session.events == []


# ── speak() table (§2) ──────────────────────────────────────────────────


@pytest.mark.req("VR-013")
@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_empty_or_whitespace_text_makes_no_request(sdk, text):
    session = FakeSession()
    assert create_tts("google", project="p").speak(text, session) == {"last_spoken": ""}
    assert sdk.clients == []
    assert session.events == []


@pytest.mark.req("VR-013")
def test_disconnected_before_start_makes_no_request(sdk):
    session = FakeSession(disconnected=True)
    result = create_tts("google", project="p").speak("Hei", session)
    assert result == {"last_spoken": "", "call_disconnected": True}
    assert sdk.clients == []
    assert session.events == []


@pytest.mark.req("VR-013")
def test_stop_already_set_makes_no_request(sdk):
    stop = threading.Event()
    stop.set()
    session = FakeSession()
    result = create_tts("google", project="p").speak("Hei", session, stop_event=stop)
    assert result == {"last_spoken": "Hei", "interrupted": True}
    assert sdk.clients == []
    assert session.events == []


@pytest.mark.req("VR-013")
def test_normal_chunks_frame_exactly_with_end_padding_only(sdk):
    chunk_a = b"RIFF" + b"\xff\xff" + _payload(327, 1)
    chunk_b = b"\xff" + b"RIFF" + _payload(155, 2)
    chunk_c = b"RIF\xffF\xff\x00"
    assert (len(chunk_a), len(chunk_b), len(chunk_c)) == (333, 160, 7)
    raw = chunk_a + chunk_b + chunk_c
    sdk.script = [chunk_a, chunk_b, chunk_c]
    session = FakeSession()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append

    result = tts.speak("Hei maailma", session)

    assert result == {"last_spoken": "Hei maailma"}
    frames = session.of("frame")
    assert all(len(f) == FRAME for f in frames)
    assert len(frames) == 4
    joined = b"".join(frames)
    assert joined[: len(raw)] == raw
    assert joined[len(raw) :] == b"\xff" * (4 * FRAME - len(raw))
    assert session.of("tap") == frames
    assert session.of("mark") == [("tts_complete", 30.0)]
    assert [k for k, _ in session.events][-1] == "mark"
    assert sdk.requests == [
        _expected_config(),
        texttospeech.StreamingSynthesizeRequest(
            input=texttospeech.StreamingSynthesisInput(text="Hei maailma")
        ),
    ]
    assert errors == []
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
def test_exact_frame_multiple_gets_no_padding(sdk):
    raw = _payload(320, 5)
    sdk.script = [raw[:100], raw[100:]]
    session = FakeSession()
    create_tts("google", project="p").speak("Hei", session)
    assert session.of("frame") == [raw[:160], raw[160:]]


@pytest.mark.req("VR-013")
def test_stop_event_none_completes_normally(sdk):
    sdk.script = [_payload(160, 3)]
    session = FakeSession()
    result = create_tts("google", project="p").speak("Hei", session, stop_event=None)
    assert result == {"last_spoken": "Hei"}
    assert session.of("frame") == [_payload(160, 3)]
    assert len(session.of("mark")) == 1
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
def test_mark_timeout_logs_warning_and_returns_text(sdk, caplog):
    sdk.script = [_payload(160, 4)]
    session = FakeSession()
    session.mark_behaviour = "timeout"
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append

    with caplog.at_level(logging.WARNING):
        result = tts.speak("Hei", session)

    assert result == {"last_spoken": "Hei"}
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert errors == []
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
def test_disconnected_after_mark_wait(sdk):
    sdk.script = [_payload(160, 6)]
    session = FakeSession()
    session.mark_behaviour = "disconnect"
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append

    result = tts.speak("Hei", session)

    assert result == {"last_spoken": "Hei", "call_disconnected": True}
    assert len(session.of("mark")) == 1
    assert errors == []
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (gexc.PermissionDenied("denied"), "google_tts_permission_denied"),
        (gexc.ServiceUnavailable("down"), "google_tts_service_unavailable"),
        (gexc.InvalidArgument("bad voice"), "google_tts_invalid_argument"),
    ],
)
def test_api_error_at_call_fires_on_error_once(sdk, exc, reason):
    sdk.script = [exc]
    session = FakeSession()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append

    result = tts.speak("Hei", session)

    assert result == {"last_spoken": "Hei", "error": str(exc)}
    assert errors == [reason]
    assert session.events == []
    _assert_quiet_after_return(session, sdk)


# ── Cancellation (R-5, AC-08) ───────────────────────────────────────────


def _fire_when_blocked(sdk: FakeSdk, action) -> dict:
    """Run ``action`` once the worker's read is blocked; record the event time."""
    fired: dict = {}

    def run() -> None:
        if sdk.blocked.wait(timeout=5.0):
            fired["t"] = time.monotonic()
            action()

    thread = threading.Thread(target=run)
    thread.start()
    fired["thread"] = thread
    return fired


@pytest.mark.req("VR-013")
@pytest.mark.parametrize("first_read_blocks", [False, True])
def test_stop_mid_stream_cancels_within_budget(sdk, first_read_blocks):
    sdk.script = [BLOCK] if first_read_blocks else [_payload(333, 7), BLOCK]
    session = FakeSession()
    stop = threading.Event()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append
    fired = _fire_when_blocked(sdk, stop.set)

    result = tts.speak("Hei", session, stop_event=stop)
    returned = time.monotonic()
    fired["thread"].join()

    assert result == {"last_spoken": "Hei", "interrupted": True}
    assert returned - fired["t"] < CANCEL_BUDGET_S
    frames = session.of("frame")
    assert all(len(f) == FRAME for f in frames)
    assert b"".join(frames) == _payload(333, 7)[: len(frames) * FRAME]
    assert session.of("mark") == []
    assert errors == []
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
@pytest.mark.parametrize("first_read_blocks", [False, True])
def test_disconnect_mid_stream_cancels_within_budget(sdk, first_read_blocks):
    sdk.script = [BLOCK] if first_read_blocks else [_payload(333, 8), BLOCK]
    session = FakeSession()
    stop = threading.Event()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append
    fired = _fire_when_blocked(sdk, session.disconnect)

    result = tts.speak("Hei", session, stop_event=stop)
    returned = time.monotonic()
    fired["thread"].join()

    assert result == {"last_spoken": "Hei", "call_disconnected": True}
    assert returned - fired["t"] < CANCEL_BUDGET_S
    frames = session.of("frame")
    assert all(len(f) == FRAME for f in frames)
    assert session.of("mark") == []
    assert errors == []
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
def test_worker_api_error_mid_stream_returns_error(sdk):
    exc = gexc.InternalServerError("boom")
    sdk.script = [_payload(333, 9), exc, BLOCK]
    session = FakeSession()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append

    result = tts.speak("Hei", session, stop_event=threading.Event())

    assert result == {"last_spoken": "Hei", "error": str(exc)}
    assert errors == ["google_tts_internal_server_error"]
    assert session.of("mark") == []
    assert all(len(f) == FRAME for f in session.of("frame"))
    _assert_quiet_after_return(session, sdk)


# ── Worst-case cancellation and non-termination (round 3, R-4, AC-08) ───

_TRIGGERS = {
    "stop": ({"last_spoken": "Hei", "interrupted": True}, lambda s, e: e.set()),
    "disconnect": (
        {"last_spoken": "Hei", "call_disconnected": True},
        lambda s, e: s.disconnect(),
    ),
}


@pytest.mark.req("VR-013")
@pytest.mark.parametrize("trigger", sorted(_TRIGGERS))
def test_slow_cancel_and_slow_exit_still_return_within_one_second(sdk, trigger):
    expected, fire = _TRIGGERS[trigger]
    sdk.script = [_payload(320, 3), BLOCK]
    sdk.cancel_delay = 0.35
    sdk.exit_delay = 0.4
    session = FakeSession()
    stop = threading.Event()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append
    fired = _fire_when_blocked(sdk, lambda: fire(session, stop))

    result = tts.speak("Hei", session, stop_event=stop)
    returned = time.monotonic()
    fired["thread"].join()

    assert result == expected
    assert returned - fired["t"] < CANCEL_BUDGET_S
    assert errors == []
    assert session.of("mark") == []
    _assert_quiet_after_return(session, sdk)


@pytest.mark.req("VR-013")
@pytest.mark.parametrize("trigger", sorted(_TRIGGERS))
def test_worker_not_terminated_is_explicit_failure(sdk, trigger):
    _, fire = _TRIGGERS[trigger]
    sdk.script = [_payload(320, 4), STUCK]
    session = FakeSession()
    stop = threading.Event()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append
    fired = _fire_when_blocked(sdk, lambda: fire(session, stop))

    result = tts.speak("Hei", session, stop_event=stop)
    returned = time.monotonic()
    fired["thread"].join()

    assert result == {"last_spoken": "Hei", "error": "google_tts_worker_not_terminated"}
    assert errors == ["google_tts_worker_not_terminated"]
    assert returned - fired["t"] < CANCEL_BUDGET_S
    assert session.of("mark") == []
    before = session.count()
    time.sleep(0.2)
    assert session.count() == before
    assert tts._client is None


@pytest.mark.req("VR-013")
def test_client_reused_after_stop_on_open_call_and_fresh_after_transport_close(sdk):
    tts = create_tts("google", project="p")

    sdk.script = [_payload(160, 1)]
    tts.speak("Yksi", FakeSession())
    sdk.script = [_payload(160, 2)]
    tts.speak("Kaksi", FakeSession())
    assert len(sdk.clients) == 1

    sdk.script = [BLOCK]
    stop = threading.Event()
    fired = _fire_when_blocked(sdk, stop.set)
    tts.speak("Kolme", FakeSession(), stop_event=stop)
    fired["thread"].join()
    assert sdk.clients[0].transport.closed is True

    sdk.blocked.clear()
    sdk.script = [_payload(160, 3)]
    session = FakeSession()
    assert tts.speak("Neljä", session) == {"last_spoken": "Neljä"}
    assert len(sdk.clients) == 2
    assert session.of("frame") == [_payload(160, 3)]


@pytest.mark.req("VR-013")
def test_stop_while_call_is_being_returned_cancels_the_call(sdk):
    """Stop lands after the prefetch but before the worker hands the call over."""
    stop = threading.Event()

    def stop_and_wait_for_cancel(client) -> None:
        stop.set()
        deadline = time.monotonic() + 0.5
        while not client.transport.closed and time.monotonic() < deadline:
            time.sleep(0.005)

    sdk.script = [_payload(160, 1), _payload(160, 2)]
    sdk.before_return = stop_and_wait_for_cancel
    session = FakeSession()
    tts = create_tts("google", project="p")
    errors: list[str] = []
    tts.on_error = errors.append

    result = tts.speak("Hei", session, stop_event=stop)

    assert result == {"last_spoken": "Hei", "interrupted": True}
    assert sdk.clients[0].calls[0].cancel_count >= 1
    assert session.events == []
    assert errors == []
    _assert_quiet_after_return(session, sdk)
