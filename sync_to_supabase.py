"""
sync_to_supabase.py
--------------------
Syncs the local SQLite database (data/leidsa_supermas.db) to Postgres on
Supabase. Creates the tables if they do not exist and upserts by
draw_number, so it can be run after every incremental scrape without
creating duplicates.

Requires a SUPABASE_DB_URL environment variable with the Postgres
connection string (Project Settings -> Database -> Connection string -> URI,
in the Supabase dashboard). Put it in a local .env file (never commit it).

Usage:
    python sync_to_supabase.py --db data/leidsa_supermas.db
"""

import argparse
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DDL = """
CREATE TABLE IF NOT EXISTS draws (
    draw_number       TEXT PRIMARY KEY,
    draw_date         DATE NOT NULL,
    day_of_week       TEXT,
    more_number       INTEGER,
    super_more_number INTEGER,
    draw_time         TEXT,
    api_id            BIGINT,
    scraped_at        TIMESTAMP,
    raw_json          JSONB
);

CREATE TABLE IF NOT EXISTS draw_numbers (
    draw_number TEXT NOT NULL REFERENCES draws(draw_number),
    position    INTEGER NOT NULL,
    number      INTEGER NOT NULL,
    PRIMARY KEY (draw_number, position)
);

CREATE INDEX IF NOT EXISTS idx_draws_date ON draws(draw_date);
CREATE INDEX IF NOT EXISTS idx_draw_numbers_number ON draw_numbers(number);
"""

UPSERT_DRAW = """
INSERT INTO draws (draw_number, draw_date, day_of_week, more_number, super_more_number,
                    draw_time, api_id, scraped_at, raw_json)
VALUES (:draw_number, :draw_date, :day_of_week, :more_number, :super_more_number,
        :draw_time, :api_id, :scraped_at, :raw_json)
ON CONFLICT (draw_number) DO UPDATE SET
    draw_date         = EXCLUDED.draw_date,
    day_of_week       = EXCLUDED.day_of_week,
    more_number       = EXCLUDED.more_number,
    super_more_number = EXCLUDED.super_more_number,
    draw_time         = EXCLUDED.draw_time,
    api_id            = EXCLUDED.api_id,
    scraped_at        = EXCLUDED.scraped_at,
    raw_json          = EXCLUDED.raw_json;
"""

UPSERT_NUMBER = """
INSERT INTO draw_numbers (draw_number, position, number)
VALUES (:draw_number, :position, :number)
ON CONFLICT (draw_number, position) DO UPDATE SET number = EXCLUDED.number;
"""


def sync(sqlite_path: Path) -> None:
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        raise SystemExit(
            "Missing SUPABASE_DB_URL. Define it in a .env file "
            "(see .env.example) or as an environment variable."
        )

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    engine = create_engine(db_url)
    with engine.begin() as pg_conn:
        for statement in DDL.strip().split(";\n\n"):
            if statement.strip():
                pg_conn.execute(text(statement))

        draws = sqlite_conn.execute("SELECT * FROM draws").fetchall()
        for row in draws:
            pg_conn.execute(text(UPSERT_DRAW), dict(row))
        print(f"Synced {len(draws)} draws to Supabase.")

        numbers = sqlite_conn.execute("SELECT * FROM draw_numbers").fetchall()
        for row in numbers:
            pg_conn.execute(text(UPSERT_NUMBER), dict(row))
        print(f"Synced {len(numbers)} individual numbers to Supabase.")

    sqlite_conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync SQLite -> Supabase")
    parser.add_argument("--db", type=Path, default=Path("data/leidsa_supermas.db"))
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sync(args.db)
