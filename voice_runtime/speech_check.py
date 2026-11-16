"""Cross-provider speech check (VR-014).

Run as ``python -m voice_runtime.speech_check``. Scaffold only.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog="python -m voice_runtime.speech_check")


def _child_main(spec: dict[str, Any], conn: Any, cancel: Any) -> None:
    """Run one provider step inside the spawned child."""


def _supervise(
    spec: dict[str, Any],
    timeout: float,
    *,
    _child_target: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Run one step in a spawned child under a deadline."""
    return {}


def main(
    argv: list[str] | None = None,
    *,
    _child_target: Callable[..., None] | None = None,
) -> int:
    """CLI entry point; returns the process exit code."""
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
