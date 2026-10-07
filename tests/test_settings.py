import pytest
from pydantic import ValidationError

from asknews.settings import Settings


def test_database_url_is_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example:5432/asknews")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.database_dsn == "postgresql://user:secret@db.example:5432/asknews"


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_database_url_must_be_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://user:secret@db.example/asknews")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from asknews.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example:5432/asknews")
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
