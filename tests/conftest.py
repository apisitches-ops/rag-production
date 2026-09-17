import pytest

from app import db


@pytest.fixture(autouse=True)
def clean_ingest_tables():
    db.reset()
    yield
