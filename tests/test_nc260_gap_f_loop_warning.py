"""NC-260 Gap F: session.put_inbound warns when _loop is None.

Silent audio drops when _loop is not yet set must emit a warning log
instead of silently returning.
"""

from __future__ import annotations

import logging

import pytest
from voice_runtime.session import VoiceSession


@pytest.mark.req("REQ-YG-170")
class TestPutInboundLoopWarning:
    """put_inbound must warn when _loop is None."""

    def test_put_inbound_warns_when_loop_none(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Calling put_inbound with _loop=None should log a warning."""
        session = VoiceSession(call_sid="test")
        assert session._loop is None

        with caplog.at_level(logging.WARNING):
            session.put_inbound(b"\x00" * 160)

        assert any(
            "_loop is None" in msg for msg in caplog.messages
        ), f"Expected warning about _loop being None, got: {caplog.messages}"

    def test_put_inbound_warns_once_not_spam(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Warning should not spam — emit at most once."""
        session = VoiceSession(call_sid="test")

        with caplog.at_level(logging.WARNING):
            for _ in range(10):
                session.put_inbound(b"\x00" * 160)

        warn_count = sum(1 for msg in caplog.messages if "_loop is None" in msg)
        assert warn_count == 1, f"Expected 1 warning, got {warn_count}"
