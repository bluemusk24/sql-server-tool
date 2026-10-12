"""Unit tests for the repository models (no database required)."""

from sqlalchemy import BigInteger, Unicode

from app.db.models import Base, Organization


def test_organization_is_registered_in_metadata():
    assert Organization.__tablename__ == "organization"
    assert Base.metadata.tables["organization"] is Organization.__table__


def test_organization_columns_and_constraints():
    table = Organization.__table__
    assert set(table.columns.keys()) == {"id", "name", "sso_issuer"}

    id_column = table.c.id
    assert isinstance(id_column.type, BigInteger)
    assert id_column.primary_key is True
    assert id_column.identity is not None

    name = table.c.name
    assert isinstance(name.type, Unicode)
    assert name.type.length == 255
    assert name.nullable is False
    assert name.unique is True

    issuer = table.c.sso_issuer
    assert isinstance(issuer.type, Unicode)
    assert issuer.type.length == 512
    assert issuer.nullable is False
    assert issuer.unique is True
