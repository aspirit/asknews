"""Forward-only SQL migrations.

Migrations are plain SQL files named ``NNNN_description.sql`` in the
``migrations`` directory. Each file is applied once, inside its own
transaction, and recorded with a checksum so that editing an already-applied
file is detected instead of silently ignored.

Run with ``python -m asknews.db.migrate``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from asknews.db.connection import Connection, connect

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    integer PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(Exception):
    """Raised when the migration files or their recorded state are inconsistent."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Load every migration file in ``directory``, ordered by version."""
    migrations: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(f"migration file name is not NNNN_name.sql: {path.name}")
        version = int(match["version"])
        if version in migrations:
            raise MigrationError(
                f"duplicate migration version {version:04d}: "
                f"{migrations[version].name} and {match['name']}"
            )
        migrations[version] = Migration(version, match["name"], path.read_text("utf-8"))
    return [migrations[version] for version in sorted(migrations)]


def applied_checksums(conn: Connection) -> dict[int, str]:
    """Return ``{version: checksum}`` for migrations already applied."""
    conn.execute(_TRACKING_TABLE)
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {row["version"]: row["checksum"] for row in rows}


def pending(migrations: Iterable[Migration], applied: dict[int, str]) -> list[Migration]:
    """Return the migrations not yet applied, verifying applied ones are unchanged."""
    todo: list[Migration] = []
    for migration in migrations:
        recorded = applied.get(migration.version)
        if recorded is None:
            todo.append(migration)
        elif recorded != migration.checksum:
            raise MigrationError(
                f"{migration.label} was modified after being applied "
                f"(recorded {recorded[:12]}, file {migration.checksum[:12]})"
            )
    return todo


def apply(conn: Connection, migration: Migration) -> None:
    """Apply one migration and record it, atomically."""
    with conn.transaction():
        conn.execute(migration.sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
            (migration.version, migration.name, migration.checksum),
        )


def migrate(conn: Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Apply all pending migrations and return them in the order applied."""
    todo = pending(discover(directory), applied_checksums(conn))
    for migration in todo:
        apply(conn, migration)
    return todo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply pending database migrations.")
    parser.add_argument("--dsn", help="connection URL (default: DATABASE_URL)")
    args = parser.parse_args(argv)
    with connect(args.dsn, autocommit=True) as conn:
        done = migrate(conn)
    for migration in done:
        sys.stdout.write(f"applied {migration.label}\n")
    if not done:
        sys.stdout.write("database is up to date\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
