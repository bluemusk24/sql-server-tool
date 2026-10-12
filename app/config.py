"""Typed application configuration loaded from the environment and .env."""

import os
from pathlib import Path

from pydantic import AnyUrl, BaseModel, Field, ValidationError, field_validator

REQUIRED_ENV_NAMES = (
    "DATABASE_URL",
    "REDIS_URL",
    "OIDC_ISSUER",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "LLM_ENDPOINT",
    "LLM_API_KEY",
)

#: Optional overrides for the arq background worker settings (#11). Each name
#: is resolved to the ``Settings`` field of the same lowercase name and falls
#: back to the field's default when absent.
OPTIONAL_ENV_NAMES = (
    "JOB_MAX_TRIES",
    "JOB_TIMEOUT_SECONDS",
    "WORKER_MAX_JOBS",
    "WORKER_QUEUE_NAME",
    "JOB_RETRY_JOBS",
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
    recognized = set(REQUIRED_ENV_NAMES) | set(OPTIONAL_ENV_NAMES) | {"LOG_LEVEL"}
    resolved: dict[str, str] = {}
    for key, value in _load_dotenv().items():
        name = key.upper()
        if name in recognized:
            resolved[name.lower()] = value
    for name in REQUIRED_ENV_NAMES:
        if name in os.environ:
            resolved[name.lower()] = os.environ[name]
    for name in OPTIONAL_ENV_NAMES:
        if name in os.environ:
            resolved[name.lower()] = os.environ[name]
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
    #: arq worker retry/timeout policy (#11), overridable via env.
    job_max_tries: int = Field(default=3, ge=1)
    job_timeout_seconds: int = Field(default=300, ge=1)
    worker_max_jobs: int = Field(default=10, ge=1)
    worker_queue_name: str = "arq:queue"
    job_retry_jobs: bool = True

    @field_validator("oidc_issuer", "llm_endpoint")
    @classmethod
    def _must_be_http(cls, value: AnyUrl) -> AnyUrl:
        if value.scheme not in ("http", "https"):
            raise ValueError("must be an absolute http(s) URL")
        return value

    @field_validator("job_retry_jobs", mode="before")
    @classmethod
    def _parse_job_retry_bool(cls, value: object) -> object:
        """Accept the usual env spellings for a boolean worker flag."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("1", "true", "yes", "on"):
                return True
            if normalized in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"job_retry_jobs must be a boolean, got {value!r}")
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