import psycopg
import pymupdf
import pytest

from app import db
from app.db import DATABASE_URL


@pytest.fixture(autouse=True)
def clean_ingest_tables():
    db.reset()
    yield


def make_pdf(path: str, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 545, 792), text, fontsize=10)
    doc.save(path)


def node_ids_for(document_id: int) -> set[str]:
    with psycopg.connect(DATABASE_URL) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT id FROM nodes WHERE document_id = %s", (document_id,)
            ).fetchall()
        }
