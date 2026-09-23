"""Operational logging: one JSON stream, correlated to the work that produced it.

Distinct from `AuditLogRecord`, which is a product feature recording what happened to
whose data. This is for whoever is asked why a registration has not confirmed.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

import structlog

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")


def configure_logging(level: int = logging.INFO, stream: TextIO | None = None) -> None:
    """Render application and uvicorn records as one JSON stream on stdout.

    Configuring structlog alone leaves uvicorn's records in their own plain-text format,
    so a deployment would have two shapes to parse. Routing the standard library through
    the same processor chain keeps one.

    `stream` exists so a test can read back exactly what a deployment would emit, rather
    than asserting against a mock of the processor chain.
    """
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True


@contextmanager
def correlation_context(correlation_id: str) -> Iterator[None]:
    """Bind a correlation ID for one unit of work, in this process or the worker.

    Restores the previous binding rather than clearing it: a nested unit of work must not
    erase its caller's identifier, and consecutive units still start clean because the
    restore returns the context to whatever preceded them. Context variables are
    task-local, so concurrent requests never observe one another's binding.
    """
    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        yield


def logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
