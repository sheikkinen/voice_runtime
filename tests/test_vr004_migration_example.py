"""Execute only the VR-004 migration example against a fake HTTP send."""

from pathlib import Path

import pytest

from tests.twilio_http_fakes import FakeTwilioHTTP


@pytest.mark.req("VR-004")
def test_hangup_migration_example_is_executable_offline(monkeypatch):
    guide = Path(__file__).parents[1] / "docs/regional-twilio-migration.md"
    code = guide.read_text().split("```python\n")[1].split("```", 1)[0]
    namespace = {}
    exec(compile(code, str(guide), "exec"), namespace)
    wire = FakeTwilioHTTP(monkeypatch)
    wire.reply(404, {"code": 20404})
    wire.reply()
    assert (
        namespace["finish_call"]("CA123", "AC123", ("SKus", "dummy"), ("SKie", "dummy"))
        is None
    )
    assert len(wire.sent) == 2
