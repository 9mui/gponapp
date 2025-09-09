import sqlite3, pathlib, contextlib
import threading
from queue import Queue
from typing import Optional

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "instance" / "onulist.db"
SCHEMA = BASE / "schema.sql"

# Connection pool for performance optimization
class ConnectionPool:
    def __init__(self, max_connections: int = 20):
        self.max_connections = max_connections
        self.pool = Queue(maxsize=max_connections)
        self.current_connections = 0
        self.lock = threading.Lock()
    
    def get_connection(self) -> sqlite3.Connection:
        try:
            # Try to get connection from pool
            conn = self.pool.get_nowait()
            return conn
        except:
            # Create new connection if pool is empty
            with self.lock:
                if self.current_connections < self.max_connections:
                    conn = self._create_connection()
                    self.current_connections += 1
                    return conn
                else:
                    # Wait for connection to be released
                    return self.pool.get()
    
    def return_connection(self, conn: sqlite3.Connection):
        try:
            self.pool.put_nowait(conn)
        except:
            # Pool is full, close connection
            conn.close()
            with self.lock:
                self.current_connections -= 1
    
    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB), timeout=30.0, check_same_thread=False)
        # SQLite performance optimizations
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        conn.execute("PRAGMA busy_timeout=30000")  # 30 second timeout
        return conn

# Global connection pool
connection_pool = ConnectionPool()

def ensure_db():
    """Creates database and tables if they don't exist"""
    DB.parent.mkdir(parents=True, exist_ok=True)
    
    # Use separate connection for initialization
    conn = sqlite3.connect(str(DB))
    try:
        with open(SCHEMA, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()

@contextlib.contextmanager
def db():
    """Context manager for database operations with connection pooling"""
    ensure_db()
    conn = connection_pool.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        connection_pool.return_connection(conn)

