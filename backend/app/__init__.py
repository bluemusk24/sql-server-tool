"""SQL Server Diagnostic Assistant — FastAPI backend (in-memory MVP).

This backend implements the contract in ``openapi.yaml``. Persistence is
an in-memory store seeded with demo data so the frontend has something to
show without a real database. Swapping the store for a real one (SQLAlchemy,
etc.) is the intended next step and should not change the HTTP surface.
"""

__version__ = "0.1.0"
