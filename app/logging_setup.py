"""Root-logger wiring for the secret-redacting filter.

The application entrypoint calls :func:`configure_logging` so every captured
record is passed through :class:`~app.logredact.RedactionFilter`. The filter is
registered on the root logger and on its current handlers; registering on the
handlers is what covers records propagated from child loggers, since ancestor
logger filters are not applied to propagated records.
"""

from __future__ import annotations

import logging

from app.logredact import RedactionFilter


def _ensure_filter(target: logging.Logger | logging.Handler) -> RedactionFilter:
    """Attach one :class:`RedactionFilter` to ``target`` if it has none."""
    for existing in target.filters:
        if isinstance(existing, RedactionFilter):
            return existing
    installed = RedactionFilter()
    target.addFilter(installed)
    return installed


def configure_logging() -> None:
    """Register the redaction filter on the root logger and its handlers.

    Idempotent: repeated calls (for example when the app module is re-imported
    in tests) do not stack duplicate filters.
    """
    root = logging.getLogger()
    _ensure_filter(root)
    for handler in root.handlers:
        _ensure_filter(handler)
