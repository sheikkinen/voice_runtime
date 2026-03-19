"""Conftest for voice_runtime tests.

Adds projects/ to sys.path so `from voice_runtime.xxx` resolves correctly.
"""

import sys
from pathlib import Path

PROJECTS_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECTS_DIR))
