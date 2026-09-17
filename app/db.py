import os

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]


def check_connection() -> None:
    with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
        conn.execute("SELECT 1")
