"""Connection helpers.

Every caller goes through :func:`connect` so that connection options (row
factory, autocommit, timeouts) are decided in one place.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row

from asknews.settings import get_settings

Connection = psycopg.Connection[DictRow]


def connect(dsn: str | None = None, **options: Any) -> Connection:
    """Open a connection returning rows as dictionaries.

    ``dsn`` defaults to the configured ``DATABASE_URL``. Additional keyword
    arguments are passed through to :func:`psycopg.connect`.
    """
    return psycopg.connect(dsn or get_settings().database_dsn, row_factory=dict_row, **options)
