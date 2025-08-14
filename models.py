import sqlite3, pathlib, contextlib

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "instance" / "onulist.db"
SCHEMA = BASE / "schema.sql"
SCHEMA_VERSION = 1


def ensure_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()
        version = cur.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            with open(SCHEMA, "r") as f:
                cur.executescript(f.read())
            cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

