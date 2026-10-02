"""Structured-ish console logging shared by the API and the Celery workers."""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-34s | %(message)s"


class RedactCredentialsFilter(logging.Filter):
    """Mask URL credentials in every record the process writes.

    Call sites already log redacted URLs; this is the backstop for text they do
    not control - a library's exception message or traceback that happens to
    embed a connection string with a password in it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from app.connection_urls import scrub_credentials

        try:
            message = record.getMessage()
            scrubbed = scrub_credentials(message)
            if scrubbed != message:
                record.msg, record.args = scrubbed, None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = scrub_credentials(record.exc_text)
        except Exception:  # pragma: no cover - a filter must never break logging
            pass
        return True


def configure_logging(level: str | None = None) -> None:
    """Install a single stdout handler. Safe to call repeatedly."""
    global _CONFIGURED
    from app.config import settings

    resolved = (level or settings.log_level).upper()
    root = logging.getLogger()
    root.setLevel(resolved)

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))
        # On the handler, not a logger: logger filters skip records that
        # propagate up from Celery, kombu or redis-py.
        handler.addFilter(RedactCredentialsFilter())
        root.handlers = [handler]
        # Prophet/cmdstanpy are extremely chatty on every single fit.
        for noisy in ("cmdstanpy", "prophet", "matplotlib", "botocore", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def quiet_ml_loggers() -> None:
    """Re-assert quiet levels on the ML stack's own loggers.

    cmdstanpy's own ``get_logger()`` reconfigures the logger to DEBUG whenever it
    finds no handlers attached, and it runs lazily during the fit - after
    `configure_logging` has already set its level. Attaching a NullHandler
    occupies that slot so cmdstanpy leaves the level alone; records below
    WARNING are then dropped at the logger and never reach the root handler.
    Without this, every Prophet fit dumps the full Stan command line into the
    worker log. Call this immediately before fitting.
    """
    for noisy in ("cmdstanpy", "prophet"):
        logger = logging.getLogger(noisy)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.WARNING)
        for handler in logger.handlers:
            handler.setLevel(logging.WARNING)
