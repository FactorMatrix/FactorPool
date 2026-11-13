"""Remote data access; computation processes never open PostgreSQL connections."""
from .client import DataClient

__all__ = ["DataClient"]
