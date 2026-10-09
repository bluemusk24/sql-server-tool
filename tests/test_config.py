import pytest

from app import config
from app.config import REQUIRED_ENV_NAMES, load_settings

VALID_ENV = {
    "DATABASE_URL": "mssql+pyodbc://user:pass@db:1433/repo?driver=ODBC+Driver+18+for+SQL+Server",
    "REDIS_URL": "redis://localhost:6379/0",
    "OIDC_ISSUER": "https://issuer.example.com/",
    "OIDC_CLIENT_ID": "client",
    "OIDC_CLIENT_SECRET": "client-secret",
    "LLM_ENDPOINT": "https://llm.example.com/v1",
    "LLM_API_KEY": "llm-key",
}


@pytest.fixture
def clear_settings_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    for name in REQUIRED_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)


def _set_all(monkeypatch, values: dict[str, str]):
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_load_settings_reads_all_required(monkeypatch, clear_settings_env):
    _set_all(monkeypatch, VALID_ENV)
    settings = load_settings()
    assert settings.database_url == VALID_ENV["DATABASE_URL"]
    assert str(settings.oidc_issuer) == VALID_ENV["OIDC_ISSUER"]
    assert settings.log_level == "info"


def test_missing_database_url_raises_with_name(monkeypatch, clear_settings_env):
    values = dict(VALID_ENV)
    del values["DATABASE_URL"]
    _set_all(monkeypatch, values)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        load_settings()


def test_missing_oidc_client_secret_raises_with_name(monkeypatch, clear_settings_env):
    values = dict(VALID_ENV)
    del values["OIDC_CLIENT_SECRET"]
    _set_all(monkeypatch, values)
    with pytest.raises(RuntimeError, match="OIDC_CLIENT_SECRET"):
        load_settings()


def test_missing_settings_list_all_names(monkeypatch, clear_settings_env):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        load_settings()
    with pytest.raises(RuntimeError, match="OIDC_ISSUER"):
        load_settings()


def test_invalid_oidc_issuer_rejected(monkeypatch, clear_settings_env):
    values = dict(VALID_ENV)
    values["OIDC_ISSUER"] = "not a url"
    _set_all(monkeypatch, values)
    with pytest.raises(RuntimeError, match="oidc_issuer"):
        load_settings()


def test_non_http_scheme_rejected(monkeypatch, clear_settings_env):
    values = dict(VALID_ENV)
    values["LLM_ENDPOINT"] = "ftp://example.com/v1"
    _set_all(monkeypatch, values)
    with pytest.raises(RuntimeError, match="must be an absolute http"):
        load_settings()


def test_env_values_override_dotenv(monkeypatch, clear_settings_env, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=mssql://dotenv\n"
        "REDIS_URL=redis://dotenv/0\n"
        "OIDC_ISSUER=https://dotenv.example.com\n"
        "OIDC_CLIENT_ID=dotenv-client\n"
        "OIDC_CLIENT_SECRET=dotenv-secret\n"
        "LLM_ENDPOINT=https://dotenv.example.com/v1\n"
        "LLM_API_KEY=dotenv-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    _set_all(monkeypatch, VALID_ENV)
    settings = load_settings()
    assert settings.database_url == VALID_ENV["DATABASE_URL"]
    assert settings.oidc_client_secret == VALID_ENV["OIDC_CLIENT_SECRET"]


def test_repr_masks_secrets(monkeypatch, clear_settings_env):
    _set_all(monkeypatch, VALID_ENV)
    settings = load_settings()
    rendered = repr(settings)
    assert "client-secret" not in rendered
    assert "llm-key" not in rendered
    assert settings.oidc_client_secret == "client-secret"


def test_settings_public_dict_excludes_secrets(monkeypatch, clear_settings_env):
    _set_all(monkeypatch, VALID_ENV)
    settings = load_settings()
    public = settings.public_dict()
    assert "oidc_client_secret" not in public
    assert "llm_api_key" not in public
    assert public["oidc_issuer"] == VALID_ENV["OIDC_ISSUER"]