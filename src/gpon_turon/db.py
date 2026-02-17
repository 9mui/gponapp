import sqlite3
import threading
from pathlib import Path

from flask import g


_init_lock = threading.Lock()
_initialized_dbs: set[str] = set()


def init_db(db_path: Path, schema_path: Path) -> None:
    db_key = str(db_path.resolve())
    if db_key in _initialized_dbs:
        return

    with _init_lock:
        if db_key in _initialized_dbs:
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_sql = schema_path.read_text(encoding="utf-8")

        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_sql)
            _apply_runtime_migrations(conn)
            conn.commit()
            _initialized_dbs.add(db_key)
        finally:
            conn.close()


def _apply_runtime_migrations(conn: sqlite3.Connection) -> None:
    """Lightweight backward-compatible migrations for existing local DB."""
    cols = conn.execute("PRAGMA table_info(olts)").fetchall()
    col_names = {c[1] for c in cols}
    if "last_refresh_at" not in col_names:
        conn.execute("ALTER TABLE olts ADD COLUMN last_refresh_at DATETIME NULL")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recent_new_onu (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sn_norm TEXT NOT NULL UNIQUE,
          first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          olt_ip TEXT NOT NULL,
          portonu TEXT NOT NULL,
          idonu TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recent_new_onu_first_seen ON recent_new_onu(first_seen DESC)"
    )

    seen_cols = conn.execute("PRAGMA table_info(onu_seen)").fetchall()
    seen_col_names = {c[1] for c in seen_cols}
    if "last_online" not in seen_col_names:
        conn.execute("ALTER TABLE onu_seen ADD COLUMN last_online TIMESTAMP NULL")
    if "status" not in seen_col_names:
        conn.execute("ALTER TABLE onu_seen ADD COLUMN status INTEGER NULL")


def get_db(db_path: Path) -> sqlite3.Connection:
    conn = g.get("_db_conn")
    if conn is None:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        g._db_conn = conn
    return conn


def close_db() -> None:
    conn = g.pop("_db_conn", None)
    if conn is not None:
        conn.close()
