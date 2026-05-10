"""Conftest for voice_runtime tests.

Adds projects/ to sys.path so `from voice_runtime.xxx` resolves correctly.
Provides the `azure_sdk` fixture: tests that require the azure optional dep
are automatically skipped when it is not installed.
"""

import sys
from pathlib import Path

import pytest

PROJECTS_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECTS_DIR))


def _azure_available() -> bool:
    try:
        import azure.cognitiveservices.speech  # noqa: F401

        return True
    except ImportError:
        return False


AZURE_AVAILABLE = _azure_available()

requires_azure = pytest.mark.skipif(
    not AZURE_AVAILABLE,
    reason="azure extra not installed (pip install voice-runtime[azure])",
)
