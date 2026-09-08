"""Execute only the VR-006 migration example against a fake HTTP send."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.twilio_http_fakes import FakeTwilioHTTP


@pytest.mark.req("VR-006")
def test_cdr_migration_example_is_executable_offline(monkeypatch):
    guide = Path(__file__).parents[1] / "docs/regional-twilio-migration.md"
    code = guide.read_text().split("```python\n")[2].split("```", 1)[0]
    namespace = {}
    exec(compile(code, str(guide), "exec"), namespace)
    wire = FakeTwilioHTTP(monkeypatch)
    wire.reply(payload={"calls": [], "next_page_uri": None})
    assert (
        namespace["read_region"](
            "api.twilio.com",
            "AC123",
            ("SKkey", "dummy"),
            "+15550001",
            datetime(2026, 9, 8, tzinfo=UTC),
        )
        == []
    )
    assert len(wire.sent) == 1
