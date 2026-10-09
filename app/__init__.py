"""SQL Server Diagnostic Assistant application package."""

__version__ = "0.1.0"


def health() -> dict[str, str]:
    """Return a trivial service status used by the baseline test."""
    return {"status": "ok"}