"""Seed credential overrides (#41).

A production copy of the stack must not ship with the published demo
passwords, so seeding honors ``SEED_DBA_PASSWORD`` / ``SEED_ADMIN_PASSWORD``.
When the variables are unset the demo defaults stay in place, keeping
local flows and the dev environment unchanged.
"""

from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import db_models as orm
from app.db_models import Base
from app.security import verify_password
from app.store import seed_database


def _seeded_passwords(session: Session) -> dict[str, tuple[str, str]]:
    """Map each seeded username to (salt, hash) from the database."""
    rows = session.execute(select(orm.UserRow)).scalars().all()
    return {row.username: (row.password_salt, row.password_hash) for row in rows}


def _fresh_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_seeds_demo_passwords_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SEED_DBA_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_ADMIN_PASSWORD", raising=False)

    session = _fresh_session()
    seed_database(session)

    stored = _seeded_passwords(session)
    assert verify_password("dba-password", *stored["dba"])
    assert verify_password("admin-password", *stored["admin"])


def test_seed_passwords_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("SEED_DBA_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "tr0ub4dor&3")

    session = _fresh_session()
    seed_database(session)

    stored = _seeded_passwords(session)
    assert verify_password("correct-horse-battery", *stored["dba"])
    assert verify_password("tr0ub4dor&3", *stored["admin"])
    # The published demo passwords no longer work.
    assert not verify_password("dba-password", *stored["dba"])
    assert not verify_password("admin-password", *stored["admin"])


def test_seeding_still_noops_on_a_populated_database(monkeypatch) -> None:
    session = _fresh_session()
    seed_database(session)
    before = _seeded_passwords(session)

    # A second boot (with different overrides) must not re-seed.
    monkeypatch.setenv("SEED_DBA_PASSWORD", "too-late")
    seed_database(session)

    assert _seeded_passwords(session) == before
