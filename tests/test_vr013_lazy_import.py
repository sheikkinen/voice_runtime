"""VR-013 AC-02: the Google SDK is never imported unless the google provider is chosen."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_DIR = Path(__file__).resolve().parent.parent

_CHILD = """
import sys
import voice_runtime
import voice_runtime.stt
import voice_runtime.tts
from voice_runtime.stt import create_stt
from voice_runtime.tts import create_tts

create_stt("azure", subscription_key="test", region="westeurope")
create_tts("azure", subscription_key="test", region="westeurope")
loaded = sorted(m for m in sys.modules if m == "google.cloud" or m.startswith("google.cloud."))
print("LOADED=" + ",".join(loaded))
sys.exit(1 if loaded else 0)
"""


@pytest.mark.req("VR-013")
def test_azure_factories_do_not_import_google_sdk():
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(_REPO_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "*",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
    }
    result = subprocess.run(
        [sys.executable, "-c", _CHILD],
        env=env,
        cwd=str(_REPO_DIR),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LOADED=\n" in result.stdout
