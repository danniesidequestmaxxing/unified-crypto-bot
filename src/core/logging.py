"""Structured logging configuration using structlog."""
from __future__ import annotations

import structlog


def setup_logging() -> None:
    """Configure structlog with ISO timestamps and console rendering."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
