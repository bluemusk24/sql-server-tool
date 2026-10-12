"""Tests for the secret-redacting logging filter and its root-logger wiring."""

from __future__ import annotations

import logging

from app.logging_setup import configure_logging
from app.logredact import RedactionFilter, redact_text


def _record(message: str, args: object = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_masks_password_key_value():
    record = _record("login failed password=supersecret")
    assert RedactionFilter().filter(record) is True
    rendered = record.getMessage()
    assert "supersecret" not in rendered
    assert "password=****" in rendered


def test_masks_connection_string_password():
    record = _record("mssql://user:supersecret@host")
    RedactionFilter().filter(record)
    assert record.getMessage() == "mssql://user:****@host"


def test_masks_msg_with_args():
    record = _record("connecting password=%s", ("supersecret",))
    RedactionFilter().filter(record)
    assert "supersecret" not in record.getMessage()


def test_masks_dict_args_by_key():
    record = _record(
        "credentials %(password)s for %(user)s",
        {"password": "supersecret", "user": "sa"},
    )
    RedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "supersecret" not in rendered
    assert "sa" in rendered


def test_masks_every_declared_key():
    for key in ("password", "pwd", "secret", "api_key", "token"):
        assert "topsecretvalue" not in redact_text(f"{key}=topsecretvalue")
        assert "topsecretvalue" not in redact_text(f"{key}: topsecretvalue")


def test_masks_long_value_wholly():
    masked = redact_text("token=abcdefghij")
    assert "abcdefghij" not in masked
    assert masked == "token=****"


def test_leaves_unrelated_text_alone():
    text = "the password policy was updated for all users"
    assert redact_text(text) == text


def test_configure_logging_registers_filter_on_root():
    configure_logging()
    root = logging.getLogger()
    installed = [f for f in root.filters if isinstance(f, RedactionFilter)]
    assert len(installed) == 1

    configure_logging()
    installed = [f for f in root.filters if isinstance(f, RedactionFilter)]
    assert len(installed) == 1


def test_configure_logging_formats_without_secret():
    configure_logging()
    record = _record("password=%s", ("supersecret",))
    for filter_ in logging.getLogger().filters:
        filter_.filter(record)
    assert "supersecret" not in record.getMessage()
