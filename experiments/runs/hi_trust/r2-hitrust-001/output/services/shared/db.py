"""
Shared database utilities.

Uses SQLite for testing / standalone mode, PostgreSQL for production.
Services import get_db() to get a connection.
"""

import sqlite3
import os
import threading
from contextlib import contextmanager
from typing import Optional


# Global database mode
DB_MODE = os.environ.get("DB_MODE", "sqlite")  # "sqlite" or "postgres"
_local = threading.local()


class SQLiteConnection:
    """Wrapper around SQLite to provide a PostgreSQL-like interface."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def execute(self, query: str, params=None):
        # Convert PostgreSQL-style $1, $2 to ? placeholders
        converted = query
        if params is None:
            params = []
        # Convert %s style params
        converted = converted.replace("%s", "?")
        # Convert PostgreSQL-specific syntax
        converted = converted.replace("ON CONFLICT", "ON CONFLICT")
        converted = converted.replace("RETURNING *", "")
        converted = converted.replace("RETURNING id", "")
        cursor = self.conn.execute(converted, params)
        return cursor

    def fetchone(self, query: str, params=None):
        cursor = self.execute(query, params)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def fetchall(self, query: str, params=None):
        cursor = self.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            yield self
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


class Database:
    """Database abstraction for services."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: Optional[SQLiteConnection] = None
        self._lock = threading.Lock()

    def get_connection(self) -> SQLiteConnection:
        if self._conn is None:
            self._conn = SQLiteConnection(self.db_path)
        return self._conn

    def execute(self, query: str, params=None):
        return self.get_connection().execute(query, params)

    def fetchone(self, query: str, params=None):
        return self.get_connection().fetchone(query, params)

    def fetchall(self, query: str, params=None):
        return self.get_connection().fetchall(query, params)

    def commit(self):
        self.get_connection().commit()

    def rollback(self):
        self.get_connection().rollback()

    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        with conn.transaction():
            yield conn
