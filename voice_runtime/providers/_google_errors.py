"""Stream outcome and error classification for GooglePersistentStt (VR-013 R-2).

Moved unchanged from google_stt.py (VR-015) to keep that module within the
file-size gate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google.api_core import exceptions as gexc

logger = logging.getLogger("voice_runtime.providers.google_stt")

UNEXPECTED_EOF = "UnexpectedEOF"

RETRYABLE: tuple[type[Exception], ...] = (
    gexc.ServiceUnavailable,
    gexc.DeadlineExceeded,
    gexc.InternalServerError,
    gexc.Aborted,
)
TERMINAL: tuple[type[Exception], ...] = (
    gexc.PermissionDenied,
    gexc.Unauthenticated,
    gexc.InvalidArgument,
    gexc.NotFound,
    gexc.ResourceExhausted,
)


@dataclass
class Outcome:
    kind: str  # "end" | "rotate" | "retry" | "terminal"
    name: str = ""
    succeeded: bool = False


def classify(exc: BaseException, succeeded: bool) -> Outcome:
    name = type(exc).__name__
    if isinstance(exc, RETRYABLE):
        return Outcome("retry", name, succeeded)
    if not isinstance(exc, TERMINAL):
        logger.error("Google STT unclassified error treated as terminal: %r", exc)
    return Outcome("terminal", name)
