from __future__ import annotations

from pathlib import Path

import pytest

from asknews.db.connection import Connection
from asknews.db.migrate import (
    MIGRATIONS_DIR,
    Migration,
    MigrationError,
    applied_checksums,
    discover,
    migrate,
    pending,
)


def write(directory: Path, name: str, sql: str = "SELECT 1") -> Path:
    path = directory / name
    path.write_text(sql, "utf-8")
    return path


def test_discover_orders_by_version(tmp_path: Path) -> None:
    write(tmp_path, "0002_second.sql")
    write(tmp_path, "0001_first.sql")
    write(tmp_path, "0010_tenth.sql")
    assert [m.label for m in discover(tmp_path)] == ["0001_first", "0002_second", "0010_tenth"]


def test_discover_rejects_bad_file_names(tmp_path: Path) -> None:
    write(tmp_path, "1_first.sql")
    with pytest.raises(MigrationError, match=r"NNNN_name\.sql"):
        discover(tmp_path)


def test_discover_rejects_duplicate_versions(tmp_path: Path) -> None:
    write(tmp_path, "0001_first.sql")
    write(tmp_path, "0001_again.sql")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_checksum_depends_only_on_sql() -> None:
    a = Migration(1, "a", "CREATE TABLE t (x int)")
    b = Migration(2, "b", "CREATE TABLE t (x int)")
    assert a.checksum == b.checksum
    assert a.checksum != Migration(1, "a", "CREATE TABLE t (y int)").checksum


def test_pending_skips_applied_and_detects_edits() -> None:
    first = Migration(1, "first", "SELECT 1")
    second = Migration(2, "second", "SELECT 2")
    assert pending([first, second], {first.version: first.checksum}) == [second]
    with pytest.raises(MigrationError, match="modified after being applied"):
        pending([first], {first.version: "0" * 64})


def test_shipped_migrations_are_well_formed() -> None:
    migrations = discover(MIGRATIONS_DIR)
    assert migrations, "the migrations directory must not be empty"
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))


@pytest.mark.integration
def test_migrate_applies_everything_once(db: Connection) -> None:
    first_run = migrate(db)
    assert [m.version for m in first_run] == [m.version for m in discover()]
    assert migrate(db) == []
    assert applied_checksums(db) == {m.version: m.checksum for m in discover()}


@pytest.mark.integration
def test_schema_has_core_tables_and_seed_rows(db: Connection) -> None:
    migrate(db)
    tables = {
        row["table_name"]
        for row in db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    assert {"institutions", "documents", "media", "segments", "segment_embeddings"} <= tables
    codes = [row["code"] for row in db.execute("SELECT code FROM institutions ORDER BY code")]
    assert codes == ["boj", "fed"]


@pytest.mark.integration
def test_edited_applied_migration_is_refused(db: Connection, tmp_path: Path) -> None:
    write(tmp_path, "0001_first.sql", "CREATE TABLE t (x int)")
    migrate(db, tmp_path)
    write(tmp_path, "0001_first.sql", "CREATE TABLE t (y int)")
    with pytest.raises(MigrationError):
        migrate(db, tmp_path)


@pytest.mark.integration
def test_main_reports_progress(
    db: Connection, test_database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from asknews.db.migrate import main

    assert main(["--dsn", test_database_url]) == 0
    assert "applied 0001_initial_schema" in capsys.readouterr().out
    assert main(["--dsn", test_database_url]) == 0
    assert "up to date" in capsys.readouterr().out
