"""VR-013 GooglePersistentStt: config (R-4), pacing (R-1), callbacks, rotation
and the retry policy (R-2).

The SDK client class is faked; requests, responses and enums are the real
google.cloud.speech_v2 message types. The provider is reached only through
``get_stt_class("google")`` / ``create_stt("google", ...)``. Every scenario
runs on a virtual-time event loop, so timing assertions are exact and fast.
"""

from __future__ import annotations

import asyncio
import logging
import selectors
import sys

import pytest

speech_v2 = pytest.importorskip(
    "google.cloud.speech_v2", reason="optional google extra not installed"
)

from google.api_core import exceptions as gexc  # noqa: E402 — after skip (AC-02)
from google.cloud.speech_v2.types import cloud_speech  # noqa: E402 — after skip
from voice_runtime.stt import create_stt, get_stt_class  # noqa: E402 — after skip

pytestmark = pytest.mark.req("VR-013")

FRAME = 160
REQUEST = 800
EPS = 1e-9
GOOGLE_ENV = (
    "GOOGLE_SPEECH_PROJECT",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_SPEECH_LOCATION",
    "GOOGLE_STT_MODEL",
    "GOOGLE_STT_LANGUAGE",
    "GOOGLE_STT_PHRASES",
    "GOOGLE_STT_ENDPOINTING",
)
Sens = cloud_speech.StreamingRecognitionFeatures.EndpointingSensitivity
Event = cloud_speech.StreamingRecognizeResponse.SpeechEventType
EOF = object()
CANCELLED = object()
RETRYABLE = [
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.InternalServerError,
    gexc.Aborted,
]
TERMINAL = [
    (gexc.PermissionDenied, "google_stt_permission_denied"),
    (gexc.Unauthenticated, "google_stt_unauthenticated"),
    (gexc.InvalidArgument, "google_stt_invalid_argument"),
    (gexc.NotFound, "google_stt_not_found"),
    (gexc.ResourceExhausted, "google_stt_resource_exhausted"),
]


# ── Virtual-time loop ───────────────────────────────────────────────────


class _VirtualSelector(selectors.DefaultSelector):
    """Never blocks: an idle wait advances the loop's virtual clock instead."""

    loop: VirtualLoop

    def select(self, timeout=None):
        ready = super().select(0)
        if ready:
            return ready
        if timeout is None:
            raise RuntimeError("virtual-time deadlock: nothing ready or scheduled")
        self.loop.advance(timeout)
        return []


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        selector = _VirtualSelector()
        super().__init__(selector)
        selector.loop = self
        self._now = 1000.0

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock; called synchronously it models a held-back loop."""
        self._now += seconds


# ── Fake SDK client ─────────────────────────────────────────────────────


class FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeCall:
    """One bidi stream: consumes the provider's requests, replays responses."""

    def __init__(self, sdk: FakeSdk, index: int, requests, first) -> None:
        self.sdk = sdk
        self.index = index
        self.requests: list[tuple[float, object]] = [(sdk.now(), first)]
        self.responses: asyncio.Queue = asyncio.Queue()
        self.cancelled = False
        self.cancelled_at: float | None = None
        self.half_closed = False
        self.opened_at = sdk.now()
        self._consumer = asyncio.ensure_future(self._consume(requests))

    async def _consume(self, requests) -> None:
        async for request in requests:
            self.requests.append((self.sdk.now(), request))
        self.half_closed = True
        if self.sdk.eof_on_half_close:
            self.responses.put_nowait(EOF)

    def push(self, *items) -> None:
        for item in items:
            self.responses.put_nowait(item)

    def cancel(self) -> None:
        if not self.cancelled:
            self.cancelled = True
            self.cancelled_at = self.sdk.now()
            self.sdk.open_now -= 1
        self._consumer.cancel()
        self.responses.put_nowait(CANCELLED)

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        while True:
            item = await self.responses.get()
            if item is EOF:
                return
            if item is CANCELLED:
                raise gexc.Cancelled("Locally cancelled")
            if isinstance(item, BaseException):
                raise item
            yield item

    def audio(self) -> list[tuple[float, bytes]]:
        return [(t, bytes(r.audio)) for t, r in self.requests if r.audio]


class FakeSdk:
    def __init__(self, loop: VirtualLoop, monkeypatch) -> None:
        self.loop = loop
        self._monkeypatch = monkeypatch
        self._installed = False
        self.clients: list[FakeClient] = []
        self.attempts: list[tuple[float, object]] = []  # (time, first request)
        self.calls: list[FakeCall] = []
        self.open_errors: dict[int, BaseException] = {}
        self.open_block: dict[int, asyncio.Future] = {}
        self.eof_on_half_close = True
        self.on_open = None
        self.open_now = 0
        self.max_open = 0

    def now(self) -> float:
        return self.loop.time()

    def install(self) -> None:
        """Patch the provider module from the test body (AC-01 RED validity)."""
        if self._installed:
            return
        module = provider_module()
        client_cls = type("FakeSpeechAsyncClient", (FakeClient,), {"sdk": self})
        self._monkeypatch.setattr(module, "_monotonic", self.loop.time)
        self._monkeypatch.setattr(module, "SpeechAsyncClient", client_cls)
        self._installed = True

    def audio(self) -> list[tuple[float, int, bytes]]:
        return [(t, c.index, data) for c in self.calls for t, data in c.audio()]


class FakeClient:
    sdk: FakeSdk

    def __init__(self, client_options=None, **kwargs) -> None:
        self.client_options = client_options
        self.kwargs = kwargs
        self.transport = FakeTransport()
        self.sdk.clients.append(self)

    async def streaming_recognize(self, requests=None, **kwargs):
        sdk = self.sdk
        index = len(sdk.attempts)
        first = await requests.__anext__()
        sdk.attempts.append((sdk.now(), first))
        if index in sdk.open_block:
            await sdk.open_block[index]
        if index in sdk.open_errors:
            raise sdk.open_errors[index]
        sdk.open_now += 1
        sdk.max_open = max(sdk.max_open, sdk.open_now)
        call = FakeCall(sdk, len(sdk.calls), requests, first)
        sdk.calls.append(call)
        if sdk.on_open is not None:
            sdk.on_open(call)
        return call


# ── Fixtures and helpers ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in GOOGLE_ENV:
        monkeypatch.delenv(name, raising=False)


def provider_module():
    return sys.modules[get_stt_class("google").__module__]


@pytest.fixture
def vt():
    loop = VirtualLoop()
    yield loop
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()


@pytest.fixture
def sdk(monkeypatch, vt) -> FakeSdk:
    fake = FakeSdk(vt, monkeypatch)
    yield fake
    assert fake.max_open <= 1


class Recorder:
    def __init__(self, stt) -> None:
        self.committed: list[str] = []
        self.recognizing: list[str] = []
        self.errors: list[str] = []
        stt.on_committed = self.committed.append
        stt.on_recognizing = self.recognizing.append
        stt.on_error = self.errors.append


def frame(i: int) -> bytes:
    return bytes([i % 255]) * FRAME


async def start(sdk: FakeSdk, **kwargs):
    sdk.install()
    kwargs.setdefault("project", "p")
    stt = create_stt("google", **kwargs)
    rec = Recorder(stt)
    queue: asyncio.Queue = asyncio.Queue()
    await stt.start(queue)
    await asyncio.sleep(0.001)
    return stt, rec, queue


async def feed(queue, frames, interval: float = 0.02) -> None:
    for data in frames:
        queue.put_nowait(data)
        await asyncio.sleep(interval)


async def stop(stt) -> None:
    owned = list(stt._owned_futures)
    await stt.stop()
    assert all(task.done() for task in owned)
    assert not stt._owned_futures


def final(text: str):
    return cloud_speech.StreamingRecognizeResponse(
        results=[
            cloud_speech.StreamingRecognitionResult(
                alternatives=[
                    cloud_speech.SpeechRecognitionAlternative(transcript=text)
                ],
                is_final=True,
            )
        ]
    )


def interim(text: str):
    return cloud_speech.StreamingRecognizeResponse(
        results=[
            cloud_speech.StreamingRecognitionResult(
                alternatives=[
                    cloud_speech.SpeechRecognitionAlternative(transcript=text)
                ],
                is_final=False,
            )
        ]
    )


def event(kind):
    return cloud_speech.StreamingRecognizeResponse(speech_event_type=kind)


def expected_config(
    project: str = "p",
    location: str = "eu",
    model: str = "chirp_3",
    language: str = "fi-FI",
    hints: tuple[str, ...] = (),
    sensitivity=Sens.ENDPOINTING_SENSITIVITY_SUPERSHORT,
):
    config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.MULAW,
            sample_rate_hertz=8000,
            audio_channel_count=1,
        ),
        model=model,
        language_codes=[language],
    )
    if hints:
        config.adaptation = cloud_speech.SpeechAdaptation(
            phrase_sets=[
                cloud_speech.SpeechAdaptation.AdaptationPhraseSet(
                    inline_phrase_set=cloud_speech.PhraseSet(
                        phrases=[cloud_speech.PhraseSet.Phrase(value=h) for h in hints]
                    )
                )
            ]
        )
    return cloud_speech.StreamingRecognizeRequest(
        recognizer=f"projects/{project}/locations/{location}/recognizers/_",
        streaming_config=cloud_speech.StreamingRecognitionConfig(
            config=config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
                enable_voice_activity_events=True,
                endpointing_sensitivity=sensitivity,
            ),
        ),
    )


def first_request(vt, sdk, **kwargs):
    """Start a provider, return its first request and client options."""

    async def scenario():
        stt, _, _ = await start(sdk, **kwargs)
        await stop(stt)

    vt.run_until_complete(scenario())
    return sdk.attempts[0][1], sdk.clients[0].client_options


def assert_paced(sends: list[tuple[float, bytes]]) -> None:
    for (t_prev, data), (t_next, _) in zip(sends, sends[1:], strict=False):
        assert t_next - t_prev >= len(data) / 8000 - EPS


# ── AC-09: factories ────────────────────────────────────────────────────


def test_get_stt_class_google_returns_provider_class():
    cls = get_stt_class("google")
    assert cls.__name__ == "GooglePersistentStt"
    assert cls.__module__ == "voice_runtime.providers.google_stt"


def test_create_stt_google_returns_instance_with_default_callbacks():
    stt = create_stt("google", project="p")
    assert type(stt).__name__ == "GooglePersistentStt"
    assert stt.on_committed is None
    assert stt.on_recognizing is None
    assert stt.on_error is None


# ── AC-03: configuration ────────────────────────────────────────────────


def test_first_request_holds_full_default_config(vt, sdk):
    request, _ = first_request(vt, sdk)
    assert request == expected_config()


def test_first_request_of_replacement_streams_is_the_same_config(vt, sdk):
    async def scenario():
        stt, _, _ = await start(sdk)
        sdk.calls[0].push(gexc.ServiceUnavailable("down"))
        await asyncio.sleep(1.5)
        sdk.calls[1].push(event(Event.SPEECH_ACTIVITY_BEGIN))
        await asyncio.sleep(290)  # forced rotation of stream 2
        await stop(stt)

    vt.run_until_complete(scenario())
    assert len(sdk.attempts) == 3
    assert all(req == expected_config() for _, req in sdk.attempts)


def test_no_adaptation_without_hints(vt, sdk):
    request, _ = first_request(vt, sdk)
    assert "adaptation" not in request.streaming_config.config


def test_single_hint_produces_exactly_that_phrase(vt, sdk):
    request, _ = first_request(vt, sdk, phrase_hints=["Terveystalo"])
    assert request == expected_config(hints=("Terveystalo",))
    phrase_sets = request.streaming_config.config.adaptation.phrase_sets
    assert [p.value for p in phrase_sets[0].inline_phrase_set.phrases] == [
        "Terveystalo"
    ]


@pytest.mark.parametrize(
    ("value", "enum"),
    [
        ("supershort", Sens.ENDPOINTING_SENSITIVITY_SUPERSHORT),
        ("short", Sens.ENDPOINTING_SENSITIVITY_SHORT),
        ("standard", Sens.ENDPOINTING_SENSITIVITY_STANDARD),
    ],
)
def test_endpointing_maps_to_sdk_enum(vt, sdk, value, enum):
    request, _ = first_request(vt, sdk, endpointing=value)
    assert request.streaming_config.streaming_features.endpointing_sensitivity == enum


@pytest.mark.parametrize("value", ["SUPERSHORT", "long", ""])
def test_unknown_endpointing_raises_in_constructor(value):
    with pytest.raises(ValueError, match="endpointing"):
        create_stt("google", project="p", endpointing=value)


def test_unknown_endpointing_from_env_raises_in_constructor(monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_ENDPOINTING", "fast")
    with pytest.raises(ValueError, match="endpointing"):
        create_stt("google", project="p")


def test_client_endpoint_and_quota_project(vt, sdk):
    _, options = first_request(vt, sdk, project="proj-x")
    assert options.api_endpoint == "eu-speech.googleapis.com"
    assert options.quota_project_id == "proj-x"


def test_explicit_arguments_beat_environment(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "env-p")
    monkeypatch.setenv("GOOGLE_SPEECH_LOCATION", "us")
    monkeypatch.setenv("GOOGLE_STT_MODEL", "env-model")
    monkeypatch.setenv("GOOGLE_STT_LANGUAGE", "sv-SE")
    monkeypatch.setenv("GOOGLE_STT_ENDPOINTING", "standard")
    request, options = first_request(
        vt,
        sdk,
        project="arg-p",
        location="eu",
        model="chirp_3",
        language_code="fi-FI",
        endpointing="short",
    )
    assert request == expected_config(
        project="arg-p", sensitivity=Sens.ENDPOINTING_SENSITIVITY_SHORT
    )
    assert options.quota_project_id == "arg-p"
    assert options.api_endpoint == "eu-speech.googleapis.com"


def test_environment_beats_defaults(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "env-p")
    monkeypatch.setenv("GOOGLE_SPEECH_LOCATION", "us")
    monkeypatch.setenv("GOOGLE_STT_MODEL", "env-model")
    monkeypatch.setenv("GOOGLE_STT_LANGUAGE", "sv-SE")
    monkeypatch.setenv("GOOGLE_STT_ENDPOINTING", "standard")
    request, options = first_request(vt, sdk, project=None)
    assert request == expected_config(
        project="env-p",
        location="us",
        model="env-model",
        language="sv-SE",
        sensitivity=Sens.ENDPOINTING_SENSITIVITY_STANDARD,
    )
    assert options.api_endpoint == "us-speech.googleapis.com"


def test_speech_project_beats_cloud_project(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "speech-p")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cloud-p")
    request, options = first_request(vt, sdk, project=None)
    assert request.recognizer == "projects/speech-p/locations/eu/recognizers/_"
    assert options.quota_project_id == "speech-p"


def test_empty_speech_project_falls_through_to_cloud_project(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "cloud-p")
    request, options = first_request(vt, sdk, project=None)
    assert request.recognizer == "projects/cloud-p/locations/eu/recognizers/_"
    assert options.quota_project_id == "cloud-p"


def test_explicit_empty_project_raises_in_constructor(monkeypatch):
    monkeypatch.setenv("GOOGLE_SPEECH_PROJECT", "env-p")
    with pytest.raises(ValueError, match="project"):
        create_stt("google", project="")


def test_missing_project_raises_at_start_without_sdk_request(vt, sdk):
    sdk.install()
    stt = create_stt("google")

    async def scenario():
        with pytest.raises(ValueError, match="project"):
            await stt.start(asyncio.Queue())

    vt.run_until_complete(scenario())
    assert sdk.clients == []
    assert sdk.attempts == []
    assert not stt._owned_futures


def test_env_phrases_are_stripped_and_empties_dropped(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_PHRASES", " a, ,b ")
    request, _ = first_request(vt, sdk)
    assert request == expected_config(hints=("a", "b"))


def test_explicit_empty_hints_disable_adaptation(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_PHRASES", "a,b")
    request, _ = first_request(vt, sdk, phrase_hints=[])
    assert "adaptation" not in request.streaming_config.config


def test_explicit_hints_are_normalised_in_order(vt, sdk, monkeypatch):
    monkeypatch.setenv("GOOGLE_STT_PHRASES", "env")
    request, _ = first_request(vt, sdk, phrase_hints=[" z ", "", "a"])
    assert request == expected_config(hints=("z", "a"))


@pytest.mark.parametrize("hints", [["ok", 3], [None], [b"bytes"]])
def test_non_string_hint_raises(hints):
    with pytest.raises(ValueError, match="phrase hint"):
        create_stt("google", project="p", phrase_hints=hints)


def test_bare_string_hint_raises():
    with pytest.raises(ValueError, match="phrase_hints"):
        create_stt("google", project="p", phrase_hints="Terveystalo")


# ── AC-04: audio and pacing ─────────────────────────────────────────────


def test_25_frames_become_five_ordered_requests_paced(vt, sdk):
    frames = [frame(i) for i in range(25)]

    async def scenario():
        stt, _, queue = await start(sdk)
        await feed(queue, frames)
        await asyncio.sleep(1.0)
        await stop(stt)

    vt.run_until_complete(scenario())
    sends = [(t, data) for t, _, data in sdk.audio()]
    assert [len(data) for _, data in sends] == [REQUEST] * 5
    assert b"".join(data for _, data in sends) == b"".join(frames)
    assert_paced(sends)


def test_frames_while_speaking_are_sent_as_silence_of_same_length(vt, sdk):
    voice = [frame(i + 1) for i in range(10)]

    async def scenario():
        stt, _, queue = await start(sdk)
        stt.set_speaking(True)
        await feed(queue, voice[:5])
        stt.set_speaking(False)
        await feed(queue, voice[5:])
        await asyncio.sleep(1.0)
        await stop(stt)

    vt.run_until_complete(scenario())
    payload = b"".join(data for _, _, data in sdk.audio())
    assert payload == b"\xff" * (5 * FRAME) + b"".join(voice[5:])


def test_three_frames_then_sentinel_send_one_480_byte_request(vt, sdk):
    frames = [frame(i) for i in range(3)]

    async def scenario():
        stt, rec, queue = await start(sdk)
        await feed(queue, [*frames, None])
        await asyncio.sleep(10.0)
        assert rec.errors == []
        await stop(stt)

    vt.run_until_complete(scenario())
    assert [data for _, _, data in sdk.audio()] == [b"".join(frames)]
    assert len(sdk.attempts) == 1
    assert sdk.calls[0].half_closed


def test_preloaded_backlog_is_sent_at_one_x_never_faster(vt, sdk):
    frames = [frame(i) for i in range(40)]

    async def scenario():
        sdk.install()
        stt = create_stt("google", project="p")
        queue: asyncio.Queue = asyncio.Queue()
        for data in frames:
            queue.put_nowait(data)
        await stt.start(queue)
        await asyncio.sleep(2.0)
        await stop(stt)

    vt.run_until_complete(scenario())
    sends = [(t, data) for t, _, data in sdk.audio()]
    assert [len(data) for _, data in sends] == [REQUEST] * 8
    assert b"".join(data for _, data in sends) == b"".join(frames)
    gaps = [b[0] - a[0] for a, b in zip(sends, sends[1:], strict=False)]
    assert all(gap == pytest.approx(0.1) for gap in gaps)


def test_held_back_scheduler_does_not_burst(vt, sdk):
    frames = [frame(i) for i in range(40)]

    async def scenario():
        sdk.install()
        stt = create_stt("google", project="p")
        queue: asyncio.Queue = asyncio.Queue()
        for data in frames:
            queue.put_nowait(data)
        await stt.start(queue)
        vt.call_later(0.25, vt.advance, 0.5)  # the loop is blocked for 500 ms
        await asyncio.sleep(3.0)
        await stop(stt)

    vt.run_until_complete(scenario())
    sends = [(t, data) for t, _, data in sdk.audio()]
    assert len(sends) == 8
    gaps = [b[0] - a[0] for a, b in zip(sends, sends[1:], strict=False)]
    assert max(gaps) >= 0.5 - EPS  # the hold happened between two sends
    assert_paced(sends)


def test_retained_audio_after_reconnect_is_sent_at_one_x(vt, sdk):
    frames = [frame(i) for i in range(40)]

    async def scenario():
        stt, _, queue = await start(sdk)
        for data in frames:
            queue.put_nowait(data)
        await asyncio.sleep(0.25)
        sdk.calls[0].push(gexc.ServiceUnavailable("down"))
        await asyncio.sleep(3.0)
        await stop(stt)

    vt.run_until_complete(scenario())
    sends = [(t, i, data) for t, i, data in sdk.audio()]
    assert {i for _, i, _ in sends} == {0, 1}
    assert b"".join(data for _, _, data in sends) == b"".join(frames)
    assert_paced([(t, data) for t, _, data in sends])
    replacement = [(t, d) for t, i, d in sends if i == 1]
    assert len(replacement) >= 4
    assert_paced(replacement)


def test_retained_audio_after_rotation_is_sent_at_one_x(vt, sdk):
    frames = [frame(i) for i in range(40)]

    async def scenario():
        stt, _, queue = await start(sdk)
        await asyncio.sleep(269.9)
        for data in frames:
            queue.put_nowait(data)
        await asyncio.sleep(0.25)
        sdk.calls[0].push(final(" Hei."))
        await asyncio.sleep(3.0)
        await stop(stt)

    vt.run_until_complete(scenario())
    assert len(sdk.calls) == 2
    sends = [(t, i, data) for t, i, data in sdk.audio()]
    assert {i for _, i, _ in sends} == {0, 1}
    assert b"".join(data for _, _, data in sends) == b"".join(frames)
    assert_paced([(t, data) for t, _, data in sends])


# ── AC-05: callbacks and commit lag ─────────────────────────────────────


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG, logger="voice_runtime.providers.google_stt")
    return caplog


def commit_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Google STT committed")
    ]


def run_responses(vt, sdk, steps, **kwargs) -> Recorder:
    """Start a provider and push (delay, response) steps on stream 1."""
    holder: dict[str, Recorder] = {}

    async def scenario():
        stt, rec, _ = await start(sdk, **kwargs)
        holder["rec"] = rec
        for delay, item in steps:
            await asyncio.sleep(delay)
            if callable(item):
                item(stt)
            else:
                sdk.calls[-1].push(item)
        await asyncio.sleep(0.01)
        await stop(stt)

    vt.run_until_complete(scenario())
    return holder["rec"]


def test_final_commits_stripped_text_exactly_once(vt, sdk):
    rec = run_responses(vt, sdk, [(0.1, final(" Hei."))])
    assert rec.committed == ["Hei."]


def test_whitespace_only_final_commits_nothing(vt, sdk):
    rec = run_responses(vt, sdk, [(0.1, final("   "))])
    assert rec.committed == []


def test_interim_result_fires_on_recognizing_with_text(vt, sdk):
    rec = run_responses(vt, sdk, [(0.1, interim("Hei"))])
    assert rec.recognizing == ["Hei"]
    assert rec.committed == []


def test_speech_begin_fires_on_recognizing_empty(vt, sdk):
    rec = run_responses(vt, sdk, [(0.1, event(Event.SPEECH_ACTIVITY_BEGIN))])
    assert rec.recognizing == [""]


def test_final_while_speaking_is_discarded(vt, sdk):
    rec = run_responses(
        vt,
        sdk,
        [(0.1, lambda stt: stt.set_speaking(True)), (0.1, final("Kaiku."))],
    )
    assert rec.committed == []


def test_final_inside_echo_window_is_discarded_then_later_commits(vt, sdk):
    window = provider_module().ECHO_DISCARD_WINDOW_S
    rec = run_responses(
        vt,
        sdk,
        [
            (0.1, lambda stt: stt.set_speaking(True)),
            (0.1, lambda stt: stt.set_speaking(False)),
            (window - 0.05, final("Kaiku.")),
            (0.1, final("Hei.")),
        ],
    )
    assert rec.committed == ["Hei."]


def test_commit_log_has_exact_lag_after_speech_end(vt, sdk, logs):
    run_responses(
        vt,
        sdk,
        [(0.1, event(Event.SPEECH_ACTIVITY_END)), (0.55, final(" Hei."))],
    )
    assert commit_lines(logs) == [
        "Google STT committed lang=fi-FI commit_lag_ms=550 chars=4"
    ]


def test_commit_log_lag_is_minus_one_without_speech_end(vt, sdk, logs):
    run_responses(vt, sdk, [(0.1, final("Hei."))])
    assert commit_lines(logs) == [
        "Google STT committed lang=fi-FI commit_lag_ms=-1 chars=4"
    ]


def test_empty_final_consumes_speech_end(vt, sdk, logs):
    rec = run_responses(
        vt,
        sdk,
        [
            (0.1, event(Event.SPEECH_ACTIVITY_END)),
            (0.5, final(" ")),
            (0.5, final("Hei.")),
        ],
    )
    assert rec.committed == ["Hei."]
    assert commit_lines(logs) == [
        "Google STT committed lang=fi-FI commit_lag_ms=-1 chars=4"
    ]


def test_speech_end_before_rotation_is_cleared(vt, sdk, logs):
    rec = run_responses(
        vt,
        sdk,
        [
            (289.0, event(Event.SPEECH_ACTIVITY_END)),
            (1.5, final("Hei.")),  # forced rotation at 290 s happened before
        ],
    )
    assert len(sdk.calls) == 2
    assert rec.committed == ["Hei."]
    assert commit_lines(logs) == [
        "Google STT committed lang=fi-FI commit_lag_ms=-1 chars=4"
    ]


def test_transcript_text_never_appears_in_logs(vt, sdk, logs):
    spoken = "Salainen henkilötunnus"
    rec = run_responses(
        vt,
        sdk,
        [
            (0.1, interim(spoken)),
            (0.1, event(Event.SPEECH_ACTIVITY_END)),
            (0.1, final(spoken)),
            (0.1, lambda stt: stt.set_speaking(True)),
            (0.1, final(spoken)),
            (0.1, lambda stt: stt.set_speaking(False)),
            (0.1, final(spoken)),
        ],
    )
    assert rec.committed == [spoken]
    assert logs.records
    for record in logs.records:
        assert spoken not in record.getMessage()
        assert "Salainen" not in record.getMessage()


# ── AC-06: rotation and lifecycle ───────────────────────────────────────


def rotate_lines(caplog) -> list[tuple[int, str]]:
    return [
        (r.levelno, r.getMessage())
        for r in caplog.records
        if r.getMessage().startswith("Google STT stream rotate")
    ]


def opened(sdk) -> list[float]:
    """Open-attempt times relative to the first attempt."""
    t0 = sdk.attempts[0][0]
    return [round(t - t0, 6) for t, _ in sdk.attempts]


def test_ordinary_rotation_at_first_final_after_270s(vt, sdk, logs):
    rec = run_responses(
        vt,
        sdk,
        [(269.0, final("Ennen.")), (2.0, final("Jälkeen.")), (1.0, lambda stt: None)],
    )
    assert rec.committed == ["Ennen.", "Jälkeen."]
    assert opened(sdk) == pytest.approx([0.0, 271.0], abs=0.01)
    assert sdk.calls[0].cancelled
    assert rotate_lines(logs) == [
        (logging.INFO, "Google STT stream rotate age_s=271 forced=false drained=true")
    ]
    assert rec.errors == []


def test_forced_rotation_at_290s_with_silent_server(vt, sdk, logs):
    rec = run_responses(vt, sdk, [(295.0, lambda stt: None)])
    assert opened(sdk) == pytest.approx([0.0, 290.0], abs=0.01)
    assert sdk.calls[0].cancelled
    assert sdk.calls[0].audio() == []
    assert rotate_lines(logs) == [
        (logging.WARNING, "Google STT stream rotate age_s=290 forced=true drained=true")
    ]
    assert rec.errors == []


def test_blocked_old_stream_drain_is_cancelled_after_5s(vt, sdk, logs):
    sdk.eof_on_half_close = False
    rec = run_responses(vt, sdk, [(300.0, lambda stt: None)])
    assert opened(sdk) == pytest.approx([0.0, 295.0], abs=0.01)
    assert sdk.calls[0].cancelled_at - sdk.calls[0].opened_at == pytest.approx(295.0)
    assert rotate_lines(logs) == [
        (
            logging.WARNING,
            "Google STT stream rotate age_s=290 forced=true drained=false",
        )
    ]
    assert rec.errors == []


def test_partial_batch_and_queued_frames_reach_next_stream_once_in_order(vt, sdk):
    sdk.eof_on_half_close = False
    frames = [frame(i) for i in range(20)]

    async def scenario():
        stt, rec, queue = await start(sdk)
        await asyncio.sleep(269.0)
        await feed(queue, frames[:13])  # two full batches sent, 3 frames held
        await asyncio.sleep(1.0)
        sdk.calls[0].push(final("Hei."))  # rotation due; drain blocks 5 s
        await asyncio.sleep(1.0)
        await feed(queue, frames[13:])  # queued during the drain
        await asyncio.sleep(5.0)
        queue.put_nowait(None)
        await asyncio.sleep(1.0)
        assert rec.errors == []
        await stop(stt)

    vt.run_until_complete(scenario())
    assert len(sdk.calls) == 2
    assert [d for _, d in sdk.calls[0].audio()] == [
        b"".join(frames[0:5]),
        b"".join(frames[5:10]),
    ]
    assert [d for _, d in sdk.calls[1].audio()] == [
        b"".join(frames[10:15]),
        b"".join(frames[15:20]),
    ]


def test_retryable_error_during_drain_is_one_replacement_attempt(vt, sdk, logs):
    sdk.eof_on_half_close = False
    for index in (1, 2, 3):
        sdk.open_errors[index] = gexc.ServiceUnavailable("down")
    rec = run_responses(
        vt,
        sdk,
        [(291.0, gexc.ServiceUnavailable("drain")), (30.0, lambda stt: None)],
    )
    assert opened(sdk) == pytest.approx([0.0, 292.0, 294.0, 298.0], abs=0.01)
    assert rec.errors == ["reconnect_exhausted_ServiceUnavailable"]
    assert rotate_lines(logs) == []


def test_retryable_error_at_replacement_open_is_one_replacement_attempt(vt, sdk):
    sdk.open_errors[1] = gexc.DeadlineExceeded("open")
    rec = run_responses(vt, sdk, [(295.0, lambda stt: None)])
    assert opened(sdk) == pytest.approx([0.0, 290.0, 291.0], abs=0.01)
    assert len(sdk.calls) == 2
    assert rec.errors == []


def test_terminal_error_during_drain_ends_session_with_one_callback(vt, sdk):
    sdk.eof_on_half_close = False
    rec = run_responses(
        vt,
        sdk,
        [(291.0, gexc.PermissionDenied("drain")), (30.0, lambda stt: None)],
    )
    assert opened(sdk) == pytest.approx([0.0], abs=0.01)
    assert rec.errors == ["google_stt_permission_denied"]


def test_terminal_error_at_replacement_open_ends_session_with_one_callback(vt, sdk):
    sdk.open_errors[1] = gexc.NotFound("recognizer")
    rec = run_responses(vt, sdk, [(330.0, lambda stt: None)])
    assert opened(sdk) == pytest.approx([0.0, 290.0], abs=0.01)
    assert rec.errors == ["google_stt_not_found"]


def test_rotation_due_during_backoff_opens_no_second_stream(vt, sdk, logs):
    rec = run_responses(
        vt,
        sdk,
        [(289.5, gexc.Aborted("blip")), (10.0, lambda stt: None)],
    )
    # One replacement after the 1 s backoff, which spans the 290 s mark.
    assert opened(sdk) == pytest.approx([0.0, 290.5], abs=0.01)
    assert sdk.max_open == 1
    assert rotate_lines(logs) == []
    assert rec.errors == []


def test_normal_end_drains_stalled_call_then_exits_without_error(vt, sdk):
    sdk.eof_on_half_close = False
    frames = [frame(i) for i in range(3)]

    async def scenario():
        stt, rec, queue = await start(sdk)
        await feed(queue, [*frames, None])
        sent_at = sdk.calls[0].audio()[0][0]
        assert sdk.calls[0].half_closed
        await asyncio.sleep(4.9)
        assert not sdk.calls[0].cancelled
        await asyncio.sleep(0.2)
        assert sdk.calls[0].cancelled_at - sent_at == pytest.approx(5.0, abs=0.001)
        assert not stt._owned_futures  # the provider exited by itself
        await asyncio.sleep(60.0)
        assert rec.errors == []
        await stop(stt)

    vt.run_until_complete(scenario())
    assert len(sdk.attempts) == 1
    assert [d for _, d in sdk.calls[0].audio()] == [b"".join(frames)]


def test_normal_end_with_server_eof_exits_without_drain_wait(vt, sdk):
    async def scenario():
        stt, rec, queue = await start(sdk)
        await feed(queue, [frame(1), None])
        await asyncio.sleep(0.01)
        assert not stt._owned_futures
        assert rec.errors == []
        await stop(stt)

    vt.run_until_complete(scenario())
    assert len(sdk.attempts) == 1


def test_stop_during_drain_leaves_nothing_pending(vt, sdk):
    sdk.eof_on_half_close = False

    async def scenario():
        stt, rec, _ = await start(sdk)
        await asyncio.sleep(292.0)  # forced rotation at 290, drain blocked
        await stop(stt)
        await asyncio.sleep(30.0)
        assert rec.errors == []

    vt.run_until_complete(scenario())
    assert len(sdk.attempts) == 1
    assert sdk.calls[0].cancelled
    assert sdk.clients[0].transport.closed


def test_stop_during_opening_leaves_nothing_pending(vt, sdk):
    async def scenario():
        sdk.open_block[1] = asyncio.get_running_loop().create_future()
        stt, rec, _ = await start(sdk)
        await asyncio.sleep(291.0)  # replacement open is in progress
        assert len(sdk.attempts) == 2
        await stop(stt)
        await asyncio.sleep(30.0)
        assert rec.errors == []

    vt.run_until_complete(scenario())
    assert len(sdk.attempts) == 2
    assert len(sdk.calls) == 1
    assert sdk.clients[0].transport.closed


def test_stop_during_backoff_leaves_nothing_pending(vt, sdk):
    async def scenario():
        stt, rec, _ = await start(sdk)
        sdk.calls[0].push(gexc.ServiceUnavailable("down"))
        await asyncio.sleep(0.5)  # inside the 1 s backoff
        await stop(stt)
        await asyncio.sleep(30.0)
        assert rec.errors == []

    vt.run_until_complete(scenario())
    assert len(sdk.attempts) == 1
    assert sdk.clients[0].transport.closed


def test_rotations_never_call_on_error(vt, sdk, logs):
    rec = run_responses(
        vt,
        sdk,
        [(271.0, final("Yksi.")), (300.0, lambda stt: None)],
    )
    assert opened(sdk) == pytest.approx([0.0, 271.0, 561.0], abs=0.01)
    assert len(rotate_lines(logs)) == 2
    assert rec.errors == []


# ── AC-07: retry policy ─────────────────────────────────────────────────


def idle(seconds: float):
    return (seconds, lambda stt: None)


@pytest.mark.parametrize("error_cls", RETRYABLE)
def test_retryable_error_at_open_reconnects_with_same_config(vt, sdk, error_cls):
    sdk.open_errors[0] = error_cls("open")
    rec = run_responses(vt, sdk, [idle(3.0)])
    assert opened(sdk) == pytest.approx([0.0, 1.0], abs=0.01)
    assert [req for _, req in sdk.attempts] == [expected_config()] * 2
    assert rec.errors == []


@pytest.mark.parametrize("error", [*RETRYABLE, "eof"])
def test_retryable_error_while_reading_reconnects_with_same_config(vt, sdk, error):
    item = EOF if error == "eof" else error("read")
    rec = run_responses(vt, sdk, [(0.5, item), idle(3.0)])
    assert opened(sdk) == pytest.approx([0.0, 1.5], abs=0.01)
    assert [req for _, req in sdk.attempts] == [expected_config()] * 2
    assert sdk.calls[0].cancelled
    assert rec.errors == []


def test_exhaustion_after_initial_failure_and_three_replacements(vt, sdk):
    for index in range(10):
        sdk.open_errors[index] = gexc.ServiceUnavailable("down")
    rec = run_responses(vt, sdk, [idle(6.5), idle(100.0)])
    assert opened(sdk) == pytest.approx([0.0, 1.0, 3.0, 7.0], abs=0.01)
    assert rec.errors == ["reconnect_exhausted_ServiceUnavailable"]


def test_exhaustion_by_unexpected_eof_uses_fixed_reason(vt, sdk):
    sdk.on_open = lambda call: call.push(EOF)
    holder: dict[str, list[str]] = {}

    async def scenario():
        stt, rec, _ = await start(sdk)
        await asyncio.sleep(6.9)
        holder["before_last"] = list(rec.errors)
        await asyncio.sleep(100.0)
        holder["after"] = list(rec.errors)
        await stop(stt)

    vt.run_until_complete(scenario())
    assert opened(sdk) == pytest.approx([0.0, 1.0, 3.0, 7.0], abs=0.01)
    assert holder["before_last"] == []
    assert holder["after"] == ["reconnect_exhausted_UnexpectedEOF"]


def test_response_resets_reconnect_allowance_and_backoff(vt, sdk):
    sdk.open_errors.update({0: gexc.Aborted("a"), 2: gexc.Aborted("b")})
    sdk.open_errors.update({3: gexc.Aborted("c"), 4: gexc.Aborted("d")})
    holder: dict[str, list[str]] = {}

    async def scenario():
        stt, rec, _ = await start(sdk)
        await asyncio.sleep(1.2)
        sdk.calls[0].push(event(Event.SPEECH_ACTIVITY_BEGIN))  # replacement 1
        await asyncio.sleep(0.3)
        sdk.calls[0].push(gexc.Aborted("read"))
        await asyncio.sleep(6.9)
        holder["before_last"] = list(rec.errors)
        await asyncio.sleep(100.0)
        holder["after"] = list(rec.errors)
        await stop(stt)

    vt.run_until_complete(scenario())
    # Second series: backoff restarts at 1 s, then 2 s and 4 s.
    assert opened(sdk) == pytest.approx([0.0, 1.0, 2.5, 4.5, 8.5], abs=0.01)
    assert holder["before_last"] == []
    assert holder["after"] == ["reconnect_exhausted_Aborted"]


def test_five_quiet_seconds_reset_reconnect_allowance(vt, sdk):
    sdk.open_errors.update({0: gexc.Aborted("a"), 2: gexc.Aborted("b")})
    sdk.open_errors.update({3: gexc.Aborted("c"), 4: gexc.Aborted("d")})
    holder: dict[str, list[str]] = {}

    async def scenario():
        stt, rec, _ = await start(sdk)
        await asyncio.sleep(6.0)  # replacement 1 has run 5 s, no responses
        sdk.calls[0].push(gexc.Aborted("read"))
        await asyncio.sleep(6.9)
        holder["before_last"] = list(rec.errors)
        await asyncio.sleep(100.0)
        holder["after"] = list(rec.errors)
        await stop(stt)

    vt.run_until_complete(scenario())
    assert opened(sdk) == pytest.approx([0.0, 1.0, 7.0, 9.0, 13.0], abs=0.01)
    assert holder["before_last"] == []
    assert holder["after"] == ["reconnect_exhausted_Aborted"]


def test_failure_before_five_quiet_seconds_does_not_reset_allowance(vt, sdk):
    sdk.open_errors.update({0: gexc.Aborted("a"), 2: gexc.Aborted("b")})
    sdk.open_errors.update({3: gexc.Aborted("c")})

    async def scenario():
        stt, rec, _ = await start(sdk)
        await asyncio.sleep(5.9)  # replacement 1 fails after 4.9 quiet seconds
        sdk.calls[0].push(gexc.Aborted("read"))
        await asyncio.sleep(100.0)
        assert rec.errors == ["reconnect_exhausted_Aborted"]
        await stop(stt)

    vt.run_until_complete(scenario())
    assert opened(sdk) == pytest.approx([0.0, 1.0, 7.9, 11.9], abs=0.01)


@pytest.mark.parametrize(("error_cls", "reason"), TERMINAL)
def test_terminal_error_at_open_ends_with_one_callback(vt, sdk, error_cls, reason):
    sdk.open_errors[0] = error_cls("open")
    rec = run_responses(vt, sdk, [idle(100.0)])
    assert len(sdk.attempts) == 1
    assert rec.errors == [reason]


@pytest.mark.parametrize(("error_cls", "reason"), TERMINAL)
def test_terminal_error_while_reading_ends_with_one_callback(
    vt, sdk, error_cls, reason
):
    rec = run_responses(vt, sdk, [(0.5, error_cls("read")), idle(100.0)])
    assert len(sdk.attempts) == 1
    assert sdk.calls[0].cancelled
    assert rec.errors == [reason]


def test_normal_eof_after_rotation_and_sentinel_is_not_an_error(vt, sdk):
    frames = [frame(i) for i in range(3)]

    async def scenario():
        stt, rec, queue = await start(sdk)
        await asyncio.sleep(271.0)
        sdk.calls[0].push(final("Hei."))  # ordinary rotation, server EOF
        await asyncio.sleep(1.0)
        await feed(queue, [*frames, None])  # sentinel, server EOF
        await asyncio.sleep(100.0)
        assert rec.errors == []
        assert not stt._owned_futures
        await stop(stt)

    vt.run_until_complete(scenario())
    assert opened(sdk) == pytest.approx([0.0, 271.0], abs=0.01)
