"""VR-002 RED: Twilio REST clients must carry a bounded HTTP timeout.

GitHub issue sheikkinen/voice_runtime#2 — the Twilio SDK's default HTTP
client applies no request timeout, so a SYN-blackhole egress blocks a
synchronous call-path request for the OS TCP timeout (~2 min), freezing
worker teardown in consumers.

Deterministic and offline: `twilio.rest.Client` is always mocked.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ReadTimeout

_CREDS = {
    "TWILIO_ACCOUNT_SID": "AC123",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_PHONE_NUMBER": "+358401234567",
    "VOICE_STREAM_URL": "https://example.ngrok.io",
}
_NO_CREDS = {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": ""}

_TRANSPORTS_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "voice_runtime" / "transports"
)
_HELPER_FILENAME = "_twilio_client.py"


def _http_timeout_of(client_mock: MagicMock) -> float:
    """Timeout carried by the http_client the call site handed to Client()."""
    assert client_mock.call_args is not None, "twilio.rest.Client was never constructed"
    http_client = client_mock.call_args.kwargs.get("http_client")
    assert http_client is not None, "Client constructed without an http_client"
    return http_client.timeout


def _call_sites():
    from voice_runtime.transports.twilio_call import (
        hangup_call,
        initiate_outbound_call,
        list_recent_calls,
    )
    from voice_runtime.transports.twilio_sms import send_sms

    return [
        ("send_sms", lambda: send_sms("+358401234567", "Hei")),
        ("initiate_outbound_call", lambda: initiate_outbound_call("+358401234567")),
        ("hangup_call", lambda: hangup_call("CA123")),
        ("list_recent_calls", lambda: list_recent_calls()),
    ]


@pytest.mark.req("VR-002")
class TestDefaultTimeout:
    """AC-1: every Twilio REST call site is bounded by default (15s)."""

    @pytest.mark.parametrize("name", [s[0] for s in _call_sites()])
    def test_call_site_client_has_default_timeout(self, name, monkeypatch):
        for key, value in _CREDS.items():
            monkeypatch.setenv(key, value)
        invoke = dict(_call_sites())[name]

        with patch("twilio.rest.Client") as client_mock:
            invoke()

        assert _http_timeout_of(client_mock) == 15.0


@pytest.mark.req("VR-002")
class TestTimeoutConfiguration:
    """AC-2: TWILIO_HTTP_TIMEOUT overrides; invalid values are not silent."""

    def test_env_override(self, monkeypatch):
        for key, value in _CREDS.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("TWILIO_HTTP_TIMEOUT", "3")
        from voice_runtime.transports.twilio_sms import send_sms

        with patch("twilio.rest.Client") as client_mock:
            send_sms("+358401234567", "Hei")

        assert _http_timeout_of(client_mock) == 3.0

    def test_invalid_timeout_raises(self, monkeypatch):
        for key, value in _CREDS.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("TWILIO_HTTP_TIMEOUT", "abc")
        from voice_runtime.transports.twilio_sms import send_sms

        with patch("twilio.rest.Client"):
            with pytest.raises(ValueError):
                send_sms("+358401234567", "Hei")


@pytest.mark.req("VR-002")
class TestSingleConstructionBoundary:
    """AC-3 (R-1): only the shared helper may construct a Twilio client."""

    def test_no_direct_client_construction_outside_helper(self):
        offenders = [
            path.name
            for path in sorted(_TRANSPORTS_DIR.glob("*.py"))
            if path.name != _HELPER_FILENAME
            and "from twilio.rest import Client" in path.read_text()
        ]
        assert offenders == [], (
            f"direct twilio.rest.Client import outside {_HELPER_FILENAME}: {offenders}"
        )

    def test_helper_exists_and_is_the_construction_point(self):
        from voice_runtime.transports._twilio_client import build_twilio_client

        with patch("twilio.rest.Client") as client_mock:
            build_twilio_client("AC123", "token")

        assert _http_timeout_of(client_mock) == 15.0


@pytest.mark.req("VR-002")
class TestRequestTimeoutPropagates:
    """AC-4 (R-2): a timeout raised by the REST *request* reaches the caller."""

    def test_send_sms_propagates_request_timeout(self, monkeypatch):
        for key, value in _CREDS.items():
            monkeypatch.setenv(key, value)
        from voice_runtime.transports.twilio_sms import send_sms

        with patch("twilio.rest.Client") as client_mock:
            client_mock.return_value.messages.create.side_effect = ReadTimeout("boom")
            with pytest.raises(ReadTimeout):
                send_sms("+358401234567", "Hei")

    def test_hangup_call_propagates_request_timeout(self, monkeypatch):
        for key, value in _CREDS.items():
            monkeypatch.setenv(key, value)
        from voice_runtime.transports.twilio_call import hangup_call

        with patch("twilio.rest.Client") as client_mock:
            client_mock.return_value.calls.return_value.update.side_effect = ReadTimeout(
                "boom"
            )
            with pytest.raises(ReadTimeout):
                hangup_call("CA123")


@pytest.mark.req("VR-002")
class TestCredentialBehaviourPreserved:
    """AC-5 (R-3): the two distinct pre-client behaviours both survive."""

    @pytest.mark.parametrize(
        "name", ["send_sms", "initiate_outbound_call", "hangup_call"]
    )
    def test_missing_credentials_raise_before_client(self, name, monkeypatch):
        monkeypatch.setenv("VOICE_STREAM_URL", _CREDS["VOICE_STREAM_URL"])
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", _CREDS["TWILIO_PHONE_NUMBER"])
        for key, value in _NO_CREDS.items():
            monkeypatch.setenv(key, value)
        invoke = dict(_call_sites())[name]

        with patch(
            "voice_runtime.transports._twilio_client.build_twilio_client"
        ) as helper:
            with pytest.raises(RuntimeError):
                invoke()

        helper.assert_not_called()

    def test_list_recent_calls_without_credentials_is_a_no_op(self, monkeypatch):
        for key, value in _NO_CREDS.items():
            monkeypatch.setenv(key, value)
        from voice_runtime.transports import twilio_call

        with patch.object(twilio_call, "build_twilio_client") as helper:
            assert twilio_call.list_recent_calls() == []

        helper.assert_not_called()


@pytest.mark.req("VR-002")
class TestOptionalImportPreserved:
    """AC-6: twilio stays an in-function import across the transports package."""

    def test_no_module_level_twilio_imports(self):
        offenders: list[str] = []
        for path in sorted(_TRANSPORTS_DIR.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if isinstance(node, ast.Import) and any(
                    alias.name.split(".")[0] == "twilio" for alias in node.names
                ):
                    offenders.append(path.name)
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "twilio"
                ):
                    offenders.append(path.name)
        assert offenders == [], f"module-level twilio import in: {offenders}"
