"""Logging filter that masks secret-shaped values.

The filter only recognises declared key/value shapes (``password=``, ``pwd:``,
and friends) and the userinfo password segment of ``scheme://user:pass@host``
connections strings, so it does not touch legitimately sensitive-but-unrelated
prose. Matched values are replaced wholly with ``****`` so no part of a long
value is revealed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

MASK = "****"
SECRET_KEYS = ("password", "pwd", "secret", "api_key", "token")

_KEY_VALUE_RE = re.compile(
    r"(?i)\b("
    + "|".join(SECRET_KEYS)
    + r")\b(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&)\]}]+)"
)
_CONNECTION_STRING_RE = re.compile(
    r"(?i)\b([a-z][a-z0-9+.\-]*://[^:@/\s]+):([^@/\s]+)@"
)


def redact_text(text: str) -> str:
    """Return ``text`` with recognised secret values replaced by :data:`MASK`."""
    text = _CONNECTION_STRING_RE.sub(r"\1:" + MASK + "@", text)
    return _KEY_VALUE_RE.sub(r"\1\2" + MASK, text)


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and key.lower() in SECRET_KEYS


class RedactionFilter(logging.Filter):
    """Mask secret-shaped values in a record's rendered message and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, Mapping):
            record.args = {
                key: MASK if _is_secret_key(key) else value
                for key, value in record.args.items()
            }
        try:
            rendered = record.getMessage()
        except (KeyError, TypeError, ValueError, IndexError):
            rendered = str(record.msg)
        record.msg = redact_text(rendered)
        record.args = ()
        return True
