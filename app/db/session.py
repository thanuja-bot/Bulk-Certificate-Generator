"""Per-application SQLAlchemy engine and session factory."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class Database:
    """Owns one engine and its session factory."""

    engine: Engine
    session_factory: sessionmaker[Session]

    def dispose(self) -> None:
        """Release the connection pool."""
        self.engine.dispose()


def create_database(database_url: str) -> Database:
    """Create a database engine with settings appropriate for SQLite or PostgreSQL."""
    database_url = normalize_database_url(database_url)
    ensure_sqlite_directory(database_url)
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
    return Database(
        engine=engine,
        session_factory=sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )


def normalize_database_url(database_url: str) -> str:
    """Use the maintained psycopg 3 SQLAlchemy dialect for PostgreSQL URLs."""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    elif database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url


def ensure_sqlite_directory(database_url: str) -> None:
    """Create the parent folder required by a file-backed SQLite URL."""
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "sqlite":
        return
    sqlite_database = parsed_url.database
    if sqlite_database and sqlite_database != ":memory:":
        Path(sqlite_database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def get_session(database: Database) -> Iterator[Session]:
    """Yield one session and always close it after the request."""
    session = database.session_factory()
    try:
        yield session
    finally:
        session.close()
