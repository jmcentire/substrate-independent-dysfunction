"""
Shared database utilities for all services.
PostgreSQL is source of truth. Redis is cache only.
"""
import os
import psycopg2
import psycopg2.extras
import redis
from contextlib import contextmanager

# Register UUID adapter
psycopg2.extras.register_uuid()

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://wander:wander@localhost:5432/wander")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def get_db_connection():
    """Get a new database connection."""
    return psycopg2.connect(DATABASE_URL)


@contextmanager
def get_db():
    """Context manager for database connections with auto-commit/rollback."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_db_cursor(dict_cursor=True):
    """Context manager for database cursor."""
    with get_db() as conn:
        cursor_factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur, conn
        finally:
            cur.close()


def get_redis():
    """Get Redis connection. Returns None if Redis unavailable."""
    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        return r
    except (redis.ConnectionError, redis.TimeoutError):
        return None


def init_schema(sql: str):
    """Initialize database schema."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        cur.close()
