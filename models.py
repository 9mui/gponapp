import sqlite3, pathlib, contextlib

BASE = pathlib.Path(__file__).resolve().parent
DB   = BASE / "instance" / "onulist.db"
SCHEMA = BASE / "schema.sql"

def ensure_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn, open(SCHEMA,"r") as f:
        conn.executescript(f.read())

@contextlib.contextmanager
def db():
    ensure_db()
    conn = sqlite3.connect(DB)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

