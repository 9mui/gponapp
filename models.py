import sqlite3, pathlib, contextlib

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "instance" / "onulist.db"
SCHEMA = BASE / "schema.sql"
SCHEMA_VERSION = 2


def ensure_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn:
        cur = conn.cursor()
        version = cur.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            with open(SCHEMA, "r") as f:
                cur.executescript(f.read())
            cur.execute("PRAGMA user_version = 1")
            version = 1
        if version < 2:
            cur.execute("ALTER TABLE gpon ADD COLUMN comment1 TEXT")
            cur.execute("ALTER TABLE gpon ADD COLUMN comment2 TEXT")
            cur.execute("PRAGMA user_version = 2")
        conn.commit()


@contextlib.contextmanager
def db():
    conn = sqlite3.connect(DB)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

