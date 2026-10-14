"""Shared fixtures: an in-process TestClient over a fresh in-memory database."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Seeded demo credentials (see app/store.py).
DBA = ("dba", "dba-password")

# Each test gets its own isolated, empty SQLite database.
TEST_DATABASE_URL = "sqlite:///:memory:"

# CI-only bug hunt: pin WHEN sqldiag.investigations gets .disabled=True.
# conftest imports after pytest plugin loading but before test-module
# imports, so this snapshot splits "during pytest startup" from
# "during collection of a test module".
print(
    "CONFTEST-IMPORT-SNAPSHOT disabled="
    f"{logging.getLogger('sqldiag.investigations').disabled}",
    flush=True,
)

# pytest's --log-disable (dest=logger_disable) disables named loggers at
# plugin-init time — before this file imports. Print where the list came
# from if it is non-empty in this process.
import sys as _sys

import pytest as _pytest


def pytest_configure(config: _pytest.Config) -> None:
    import os as _os

    disable_list = getattr(config.option, "logger_disable", None)
    log_env = {k: v for k, v in _os.environ.items() if "PYTEST" in k or k.startswith("LOG")}
    print(
        f"LOG-DISABLE-LIST disable={disable_list!r}\n"
        f"argv={_sys.argv!r}\n"
        f"inifile={config.inifile!r} addopts={config.getoption('addopts', default=None)!r}\n"
        f"log_env={log_env!r} pytest={_pytest.__version__}",
        flush=True,
    )

# If the snapshot above is False, this tracer catches the exact stack
# of whoever flips .disabled to True afterwards (data descriptor on the
# class beats instance attributes).
if not logging.getLogger("sqldiag.investigations").disabled:

    def _get_disabled(self: logging.Logger) -> bool:
        return self.__dict__.get("disabled", False)

    def _set_disabled(self: logging.Logger, value: bool) -> None:
        if value and not self.__dict__.get("disabled", False):
            import traceback

            print(
                f"LOGGER-DISABLED-BY {self.name}:\n"
                + "".join(traceback.format_stack()[-8:]),
                flush=True,
            )
        self.__dict__["disabled"] = value

    logging.Logger.disabled = property(_get_disabled, _set_disabled)  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _track_logger_disabled(request: pytest.FixtureRequest) -> None:
    """Report any test that flips `.disabled` on the app loggers (CI-only bug hunt).

    Something in the suite was disabling the sqldiag loggers on GitHub
    Actions, silencing test_backend_log_names_target_server. Print the
    transition so the CI log names the culprit test.
    """
    watch = ["sqldiag", "sqldiag.investigations"]
    before = {name: logging.getLogger(name).disabled for name in watch}
    yield
    after = {name: logging.getLogger(name).disabled for name in watch}
    if before != after:
        print(f"LOGGER-DISABLED-CHANGE in {request.node.name}: {before} -> {after}")


@pytest.fixture()
def client() -> TestClient:
    app = create_app(database_url=TEST_DATABASE_URL)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client: TestClient) -> dict[str, str]:
    """Log in as the seeded DBA and return Authorization headers."""
    response = client.post("/auth/login", json={"username": DBA[0], "password": DBA[1]})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
