"""Structured logging with a human-readable console renderer."""
from __future__ import annotations

import logging
import os
import sys

_FMT = "%(asctime)s %(levelname)-7s %(name)-26s %(message)s"
_configured = False


def setup(level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    lvl = (level or os.getenv("DEGEN_LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FMT, datefmt="%H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(lvl)
    # third-party noise
    for noisy in ("urllib3", "httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(name)
