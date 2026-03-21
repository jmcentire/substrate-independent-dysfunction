"""
Test configuration and fixtures.
Sets up test database and provides common test utilities.
"""
import os
import pytest
import psycopg2
import psycopg2.extras

# Test database URL
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", 
    os.environ.get("DATABASE_URL", "postgresql://wander:wander@localhost:5432/wander"))

# Override DB URL for all tests
os.environ["DATABASE_URL"] = TEST_DB_URL

# Set service URLs for integration tests (all on localhost different ports)
os.environ["AVAILABILITY_URL"] = "http://localhost:8001"
os.environ["PRICING_URL"] = "http://localhost:8002"
os.environ["PROPERTY_URL"] = "http://localhost:8003"
os.environ["PAYMENTS_URL"] = "http://localhost:8005"
os.environ["SYNC_URL"] = "http://localhost:8006"


def _clean_all_tables(conn):
    """Truncate all tables in correct order (respecting FK constraints)."""
    conn.autocommit = True
    cur = conn.cursor()
    tables = [
        "booking_price", "inventory_block", "booking",
        "invoice_item", "payment", "refund", "payout_snapshot", 
        "reconciliation_exception", "invoice",
        "sync_job_subsystem", "sync_job", "webhook_event", "pms_reservation_mapping",
        "rate_limit_state", "fee_dictionary",
        "property_override_conflict", "property_override", "property_fee",
        "cancellation_policy", "property_external_id", "property",
        "audit_event",
        "availability", "unit_region",
        "pricing",
    ]
    for table in tables:
        try:
            cur.execute(f"TRUNCATE TABLE {table} CASCADE")
        except Exception as e:
            conn.rollback()
            conn.autocommit = True
    cur.close()


@pytest.fixture(scope="session")
def db_connection():
    """Session-scoped database connection."""
    conn = psycopg2.connect(TEST_DB_URL)
    conn.autocommit = True
    
    # Initialize schemas once
    cur = conn.cursor()
    schema_files = [
        "services/availability/schema.sql",
        "services/pricing/schema.sql",
        "services/property/schema.sql",
        "services/event/schema.sql",
        "services/payments/schema.sql",
        "services/sync/schema.sql",
        "services/booking/schema.sql",
    ]
    
    for schema_file in schema_files:
        try:
            with open(schema_file) as f:
                sql = f.read()
                cur.execute(sql)
        except Exception as e:
            conn.rollback()
            conn.autocommit = True
    
    cur.close()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def setup_schemas(db_connection):
    """No-op — schemas are session-scoped now."""
    yield


@pytest.fixture
def clean_db(db_connection):
    """Clean all tables before each test."""
    _clean_all_tables(db_connection)
    yield
    # Also clean after test
    _clean_all_tables(db_connection)
