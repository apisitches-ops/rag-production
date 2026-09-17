import psycopg
import pytest

from app.db import DATABASE_URL


@pytest.fixture(autouse=True)
def clean_ingest_tables():
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("TRUNCATE nodes, documents RESTART IDENTITY CASCADE")
    yield
