"""Typed application configuration loaded from the environment and .env."""

import os
from pathlib import Path

from pydantic import AnyUrl, BaseModel, ValidationError, field_validator

REQUIRED_ENV_NAMES = (
    "DATABASE_URL",
    "REDIS_URL",
    "OIDC_ISSUER",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "LLM_ENDPOINT",
    "LLM_API_KEY",
)

SECRET_FIELDS = ("oidc_client_secret", "llm_api_key")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> dict[str, str]:
    """Load KEY=VALUE pairs from a .env file (no python-dotenv dependency)."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolved_values() -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in _load_dotenv().items():
        name = key.upper()
        if name in REQUIRED_ENV_NAMES or name == "LOG_LEVEL":
            resolved[name.lower()] = value
    resolved.update({name.lower(): os.environ[name] for name in REQUIRED_ENV_NAMES if name in os.environ})
    if "LOG_LEVEL" in os.environ:
        resolved["log_level"] = os.environ["LOG_LEVEL"]
    return resolved


class Settings(BaseModel):
    database_url: str
    redis_url: str
    oidc_issuer: AnyUrl
    oidc_client_id: str
    oidc_client_secret: str
    llm_endpoint: AnyUrl
    llm_api_key: str
    log_level: str = "info"

    @field_validator("oidc_issuer", "llm_endpoint")
    @classmethod
    def _must_be_http(cls, value: AnyUrl) -> AnyUrl:
        if value.scheme not in ("http", "https"):
            raise ValueError("must be an absolute http(s) URL")
        return value

    def public_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in self.model_dump(exclude=set(SECRET_FIELDS)).items()}

    def __repr__(self) -> str:
        return f"Settings({self.public_dict()})"


def load_settings() -> Settings:
    """Build Settings from the environment (with .env as base), failing fast on gaps."""
    values = _resolved_values()
    missing = [name for name in REQUIRED_ENV_NAMES if name.lower() not in values]
    if missing:
        raise RuntimeError(f"Missing required settings: {', '.join(sorted(missing))}")
    try:
        return Settings(**values)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid settings: {exc}") from exc