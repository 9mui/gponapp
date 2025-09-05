import sqlite3
import pathlib
import contextlib
import threading
from queue import Queue
from typing import Optional

BASE = pathlib.Path(__file__).resolve().parent
DB = BASE / "instance" / "onulist.db"
SCHEMA = BASE / "schema.sql"

# Connection pool для оптимизации производительности
class ConnectionPool:
    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self.pool = Queue(maxsize=max_connections)
        self.current_connections = 0
        self.lock = threading.Lock()
    
    def get_connection(self) -> sqlite3.Connection:
        try:
            # Попытка получить соединение из пула
            conn = self.pool.get_nowait()
            return conn
        except:
            # Создание нового соединения, если пул пуст
            with self.lock:
                if self.current_connections < self.max_connections:
                    conn = self._create_connection()
                    self.current_connections += 1
                    return conn
                else:
                    # Ожидание освобождения соединения
                    return self.pool.get()
    
    def return_connection(self, conn: sqlite3.Connection):
        try:
            self.pool.put_nowait(conn)
        except:
            # Пул переполнен, закрываем соединение
            conn.close()
            with self.lock:
                self.current_connections -= 1
    
    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB), timeout=30.0, check_same_thread=False)
        # Оптимизация SQLite для производительности
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        return conn

# Глобальный пул соединений
connection_pool = ConnectionPool()

def ensure_db():
    """Создает базу данных и таблицы, если они не существуют"""
    DB.parent.mkdir(parents=True, exist_ok=True)
    
    # Используем отдельное соединение для инициализации
    conn = sqlite3.connect(str(DB))
    try:
        with open(SCHEMA, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()

@contextlib.contextmanager
def db():
    """Контекстный менеджер для работы с базой данных с пулом соединений"""
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

def get_db_stats() -> dict:
    """Возвращает статистику использования базы данных"""
    with db() as conn:
        cursor = conn.execute("PRAGMA page_count")
        page_count = cursor.fetchone()[0]
        
        cursor = conn.execute("PRAGMA page_size")
        page_size = cursor.fetchone()[0]
        
        total_size = page_count * page_size
        
        return {
            "database_size_bytes": total_size,
            "database_size_mb": round(total_size / 1024 / 1024, 2),
            "page_count": page_count,
            "page_size": page_size
        }

