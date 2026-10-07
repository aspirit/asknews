"""Shared fixtures.

Database-backed tests run only when ``TEST_DATABASE_URL`` is set, and refuse
to run against a database whose name does not end in ``_test``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest

from asknews.db.connection import Connection, connect


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if not urlparse(url).path.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point at a database whose name ends with _test")
    return url


@pytest.fixture
def db(test_database_url: str) -> Iterator[Connection]:
    """A connection to an empty test database; the schema is dropped afterwards."""
    with connect(test_database_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        yield conn
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
