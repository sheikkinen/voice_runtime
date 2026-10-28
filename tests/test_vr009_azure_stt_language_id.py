"""VR-009: Azure STT continuous language identification (opt-in).

Witnesses the frozen contract of the FR:
  AC-02 ``_parse_languages`` normalisation and refusals
  AC-03 unset → today's single-locale recognizer, also after reconnect
  AC-04/05 two+ locales → universal-v2 endpoint + continuous LID, also after
        reconnect
  AC-06 ``Azure STT committed lang=… commit_lag_ms=… chars=…`` observability
  AC-07 module-level ``AZURE_STT_LANGUAGES`` parsed at import, constructor wins

Accessors assert the new surface exists, so the RED commit fails on
assertions rather than on import or TypeError.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import requires_azure

speechsdk = pytest.importorskip("azure.cognitiveservices.speech")

pytestmark = [requires_azure, pytest.mark.req("VR-009")]

MODULE = "voice_runtime.providers.azure_stt"
LID = ("fi-FI", "sv-SE", "en-GB")
COMMITTED_RE = re.compile(
    r"^Azure STT committed lang=(?P<lang>\S+) "
    r"commit_lag_ms=(?P<lag>-?\d+) chars=(?P<chars>\d+)$"
)


def _module() -> Any:
    return importlib.import_module(MODULE)


def _parser() -> Any:
    mod = _module()
    assert hasattr(mod, "_parse_languages"), "VR-009: _parse_languages missing"
    return mod._parse_languages


def _make(mod: Any | None = None, **kwargs: Any) -> Any:
    cls = (mod or _module()).AzurePersistentStt
    if "languages" in kwargs:
        assert (
            "languages" in inspect.signature(cls).parameters
        ), "VR-009: AzurePersistentStt has no languages= parameter"
    kwargs.setdefault("subscription_key", "test-key")
    kwargs.setdefault("region", "swedencentral")
    return cls(**kwargs)


@pytest.fixture
def no_env_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_STT_LANGUAGES", raising=False)
    monkeypatch.setattr(_module(), "_ENV_LANGUAGES", (), raising=False)


@pytest.fixture
def reload_env(monkeypatch: pytest.MonkeyPatch):
    """Reload the module under a given env; restore an unset-env module after."""

    def _reload(value: str) -> Any:
        monkeypatch.setenv("AZURE_STT_LANGUAGES", value)
        return importlib.reload(_module())

    yield _reload
    monkeypatch.delenv("AZURE_STT_LANGUAGES", raising=False)
    importlib.reload(_module())


class FakeSpeechConfig:
    """Records constructor kwargs, properties and attribute assignments."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.properties: dict[Any, str] = {}

    def set_property(self, property_id: Any, value: str) -> None:
        self.properties[property_id] = value


class Built:
    def __init__(self) -> None:
        self.configs: list[FakeSpeechConfig] = []
        self.recognizer_kwargs: list[dict[str, Any]] = []
        self.recognizers: list[MagicMock] = []
        self.auto_detect = MagicMock(name="AutoDetectSourceLanguageConfig")


async def _start_and_reconnect(stt: Any) -> Built:
    """Run real start() and _reconnect() against recording SDK fakes."""
    built = Built()

    def _config(**kwargs: Any) -> FakeSpeechConfig:
        cfg = FakeSpeechConfig(**kwargs)
        built.configs.append(cfg)
        return cfg

    def _recognizer(**kwargs: Any) -> MagicMock:
        built.recognizer_kwargs.append(kwargs)
        rec = MagicMock(name=f"recognizer{len(built.recognizers)}")
        built.recognizers.append(rec)
        return rec

    with (
        patch.object(speechsdk, "SpeechConfig", side_effect=_config),
        patch.object(speechsdk, "SpeechRecognizer", side_effect=_recognizer),
        patch.object(speechsdk.audio, "AudioStreamFormat"),
        patch.object(speechsdk.audio, "PushAudioInputStream"),
        patch.object(speechsdk.audio, "AudioConfig"),
        patch.object(
            speechsdk.languageconfig,
            "AutoDetectSourceLanguageConfig",
            built.auto_detect,
        ),
    ):
        await stt.start(asyncio.Queue())
        await stt._reconnect()
        await stt.stop()
    return built


def _assert_single_locale(built: Built, locale: str) -> None:
    assert len(built.configs) == 2
    for cfg, kwargs, rec in zip(
        built.configs, built.recognizer_kwargs, built.recognizers, strict=True
    ):
        assert cfg.kwargs == {"subscription": "test-key", "region": "swedencentral"}
        assert cfg.speech_recognition_language == locale
        assert (
            cfg.properties[
                speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs
            ]
            == "1500"
        )
        assert (
            speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode
            not in cfg.properties
        )
        assert "auto_detect_source_language_config" not in kwargs
        rec.recognized.connect.assert_called_once()
        rec.recognizing.connect.assert_called_once()
        rec.canceled.connect.assert_called_once()
        rec.start_continuous_recognition_async.assert_called_once()
    built.auto_detect.assert_not_called()


def _assert_lid(built: Built, locales: tuple[str, ...]) -> None:
    assert len(built.configs) == 2
    assert built.auto_detect.call_count == 2
    for call in built.auto_detect.call_args_list:
        assert call.args == ()
        assert call.kwargs == {"languages": list(locales)}
    for cfg, kwargs, rec in zip(
        built.configs, built.recognizer_kwargs, built.recognizers, strict=True
    ):
        assert cfg.kwargs == {
            "subscription": "test-key",
            "endpoint": "wss://swedencentral.stt.speech.microsoft.com"
            "/speech/universal/v2",
        }
        assert not hasattr(cfg, "speech_recognition_language")
        assert (
            cfg.properties[speechsdk.PropertyId.SpeechServiceConnection_LanguageIdMode]
            == "Continuous"
        )
        assert (
            cfg.properties[
                speechsdk.PropertyId.SpeechServiceConnection_EndSilenceTimeoutMs
            ]
            == "1500"
        )
        assert (
            kwargs["auto_detect_source_language_config"]
            is built.auto_detect.return_value
        )
        assert set(kwargs) == {
            "speech_config",
            "audio_config",
            "auto_detect_source_language_config",
        }
        rec.recognized.connect.assert_called_once()
        rec.recognizing.connect.assert_called_once()
        rec.canceled.connect.assert_called_once()
        rec.start_continuous_recognition_async.assert_called_once()


# ---------------------------------------------------------------------------
# AC-02: parser
# ---------------------------------------------------------------------------


class TestParseLanguages:
    @pytest.mark.parametrize("raw", [None, "", " , ", (), ["", " "]])
    def test_not_configured_returns_empty(self, raw: Any) -> None:
        assert _parser()(raw) == ()

    @pytest.mark.parametrize(
        "raw",
        [
            " fi-FI, sv-SE ,en-GB ",
            ("fi-FI", "sv-SE", "en-GB"),
            [" fi-FI", "sv-SE ", "en-GB"],
        ],
    )
    def test_normalises_and_keeps_order(self, raw: Any) -> None:
        assert _parser()(raw) == LID

    @pytest.mark.parametrize(
        ("raw", "needle"),
        [
            ("fi", "fi"),
            ("fi_FI", "fi_FI"),
            ("en-US,en-GB", "en"),
            (
                "fi-FI,sv-SE,en-GB,de-DE,fr-FR,es-ES,it-IT,nl-NL,pl-PL,pt-PT,da-DK",
                "10",
            ),
            (["fi-FI", 1], "1"),
        ],
    )
    def test_refusals_raise_value_error(self, raw: Any, needle: str) -> None:
        parse = _parser()
        with pytest.raises(ValueError, match=re.escape(needle)):
            parse(raw)

    def test_ten_distinct_locales_accepted(self) -> None:
        ten = "fi-FI,sv-SE,en-GB,de-DE,fr-FR,es-ES,it-IT,nl-NL,pl-PL,pt-PT"
        assert len(_parser()(ten)) == 10


# ---------------------------------------------------------------------------
# AC-03 / AC-04 / AC-05: recognizer construction, initial and reconnect
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("no_env_languages")
class TestRecognizerConstruction:
    @pytest.mark.asyncio
    async def test_unset_keeps_single_locale_after_reconnect(self) -> None:
        built = await _start_and_reconnect(_make())
        _assert_single_locale(built, "fi-FI")

    @pytest.mark.asyncio
    async def test_three_locales_build_continuous_lid_after_reconnect(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger=MODULE)
        built = await _start_and_reconnect(_make(languages=LID))
        _assert_lid(built, LID)
        assert (
            "AzurePersistentStt started (lid=continuous langs=fi-FI,sv-SE,en-GB)"
            in caplog.messages
        )

    @pytest.mark.asyncio
    async def test_one_locale_is_single_locale_mode(self) -> None:
        built = await _start_and_reconnect(_make(languages=["sv-SE"]))
        _assert_single_locale(built, "sv-SE")

    @pytest.mark.asyncio
    async def test_explicit_empty_is_single_locale_mode(self) -> None:
        built = await _start_and_reconnect(_make(languages=()))
        _assert_single_locale(built, "fi-FI")

    def test_invalid_explicit_languages_raise(self) -> None:
        with pytest.raises(ValueError, match="en"):
            _make(languages=["en-US", "en-GB"])


# ---------------------------------------------------------------------------
# AC-06: committed-result observability
# ---------------------------------------------------------------------------


def _evt(text: str) -> MagicMock:
    evt = MagicMock()
    evt.result.text = text
    return evt


def _committed_lines(caplog: pytest.LogCaptureFixture) -> list[re.Match[str]]:
    lines = [m for m in caplog.messages if m.startswith("Azure STT committed")]
    matches = [COMMITTED_RE.match(m) for m in lines]
    assert all(matches), f"malformed committed line(s): {lines}"
    return matches  # type: ignore[return-value]


def _one_line(caplog: pytest.LogCaptureFixture) -> re.Match[str]:
    lines = _committed_lines(caplog)
    assert len(lines) == 1, f"expected one committed line, got {caplog.messages}"
    return lines[0]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [100.0]
    monkeypatch.setattr(_module().time, "monotonic", lambda: now[0])
    return now


@pytest.mark.usefixtures("no_env_languages")
class TestCommittedObservability:
    def _stt(
        self, caplog: pytest.LogCaptureFixture, **kwargs: Any
    ) -> tuple[Any, list[str]]:
        caplog.set_level(logging.INFO, logger=MODULE)
        stt = _make(**kwargs)
        stt._discard_until = 0.0
        received: list[str] = []
        stt.on_committed = received.append
        return stt, received

    def test_lid_logs_detected_language_without_text(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, received = self._stt(caplog, languages=LID)
        detected = MagicMock()
        detected.language = "sv-SE"
        with patch.object(
            speechsdk, "AutoDetectSourceLanguageResult", return_value=detected
        ) as wrapper:
            stt._on_committed(_evt("  Hej, jag vill boka  "))
        wrapper.assert_called_once()
        assert received == ["Hej, jag vill boka"]
        line = _one_line(caplog)
        assert line["lang"] == "sv-SE"
        assert line["chars"] == str(len("Hej, jag vill boka"))
        assert all("Hej" not in m for m in caplog.messages)

    def test_single_locale_logs_configured_language(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, received = self._stt(caplog)
        stt._on_committed(_evt("Hei"))
        assert received == ["Hei"]
        line = _one_line(caplog)
        assert line["lang"] == "fi-FI"
        assert line["chars"] == "3"

    def test_no_interim_logs_minus_one(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, _ = self._stt(caplog)
        stt._on_committed(_evt("Hei"))
        line = _one_line(caplog)
        assert line["lag"] == "-1"

    def test_lag_measured_from_most_recent_interim(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, _ = self._stt(caplog)
        stt._on_recognizing(_evt("He"))
        clock[0] = 100.1
        stt._on_recognizing(_evt("Hei maa"))
        clock[0] = 100.35
        stt._on_committed(_evt("Hei maailma"))
        line = _one_line(caplog)
        assert line["lag"] == "250"

    def test_empty_interim_does_not_start_timing(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, _ = self._stt(caplog)
        stt._on_recognizing(_evt("   "))
        clock[0] = 101.0
        stt._on_committed(_evt("Hei"))
        line = _one_line(caplog)
        assert line["lag"] == "-1"

    def test_second_commit_does_not_inherit_timing(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, _ = self._stt(caplog)
        stt._on_recognizing(_evt("Hei"))
        clock[0] = 100.5
        stt._on_committed(_evt("Hei"))
        clock[0] = 101.0
        stt._on_committed(_evt("Toinen"))
        lines = _committed_lines(caplog)
        assert len(lines) == 2, caplog.messages
        first, second = lines
        assert first["lag"] == "500"
        assert second["lag"] == "-1"

    def test_speaking_discard_clears_timing(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, received = self._stt(caplog)
        stt._on_recognizing(_evt("kaiku"))
        stt._speaking = True
        stt._on_committed(_evt("kaiku"))
        assert received == []
        assert _committed_lines(caplog) == []
        stt._speaking = False
        clock[0] = 102.0
        stt._on_committed(_evt("Hei"))
        line = _one_line(caplog)
        assert line["lag"] == "-1"

    def test_echo_window_discard_clears_timing(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, received = self._stt(caplog)
        stt._on_recognizing(_evt("kaiku"))
        stt._discard_until = 200.0
        stt._on_committed(_evt("kaiku"))
        assert received == []
        assert _committed_lines(caplog) == []
        stt._discard_until = 0.0
        clock[0] = 102.0
        stt._on_committed(_evt("Hei"))
        line = _one_line(caplog)
        assert line["lag"] == "-1"

    def test_empty_commit_clears_timing(
        self, caplog: pytest.LogCaptureFixture, clock: list[float]
    ) -> None:
        stt, received = self._stt(caplog)
        stt._on_recognizing(_evt("Hei"))
        stt._on_committed(_evt("  "))
        clock[0] = 102.0
        stt._on_committed(_evt("Hei"))
        assert received == ["Hei"]
        line = _one_line(caplog)
        assert line["lag"] == "-1"


# ---------------------------------------------------------------------------
# AC-07: module-level env, parsed at import; constructor sequence wins
# ---------------------------------------------------------------------------


class TestEnvironment:
    @pytest.mark.asyncio
    async def test_valid_env_makes_default_provider_lid(self, reload_env) -> None:
        mod = reload_env("fi-FI,sv-SE,en-GB")
        built = await _start_and_reconnect(_make(mod))
        _assert_lid(built, LID)

    @pytest.mark.asyncio
    async def test_explicit_empty_overrides_env(self, reload_env) -> None:
        mod = reload_env("fi-FI,sv-SE,en-GB")
        built = await _start_and_reconnect(_make(mod, languages=()))
        _assert_single_locale(built, "fi-FI")

    @pytest.mark.asyncio
    async def test_explicit_one_locale_overrides_env(self, reload_env) -> None:
        mod = reload_env("fi-FI,sv-SE,en-GB")
        built = await _start_and_reconnect(_make(mod, languages=["sv-SE"]))
        _assert_single_locale(built, "sv-SE")

    def test_invalid_env_fails_at_import(self, reload_env) -> None:
        with pytest.raises(ValueError, match="fi"):
            reload_env("fi")
