"""
scraper.py
----------
Scrapes the historical results of LEIDSA's Loto - Loto Mas - Super Mas
draw using the internal API of elboletoganador.com (loteria_id=9).

Pagination strategy:
    The API returns a window of ~15 draws ending on the date passed in the
    `fecha` query parameter. To walk the full history backwards, the oldest
    date in each batch is used as the anchor for the next request, until
    START_DATE is reached (the date Super Mas was introduced).

Primary key note:
    The API's `numero_sorteo` field (our `draw_number`) is only populated
    for recent draws — most historical draws have it as null. The API's
    internal `id` field is always present and unique, so it is used as the
    primary key (`draw_id`) instead. `draw_number` is kept as a plain,
    nullable reference column.

Output:
    - A local SQLite file: data/leidsa_supermas.db
    - A raw backup of every API response in data/raw/ (JSON), in case a
      field we are not using today (e.g. prize/winner breakdown) becomes
      useful later.

Usage:
    python scraper.py
    python scraper.py --start-date 2019-04-10 --db data/leidsa_supermas.db

Note on field names:
    The external API returns Spanish field names (fecha_sorteo, premios,
    numero_sorteo, etc.). Those are the API's contract and are not
    something we control. This script maps them to English column names
    when storing data in our own database.
"""

import argparse
import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

API_URL = "https://api.elboletoganador.com/api/sorteos/buscar/historial"
LOTTERY_ID = 9  # Loto - Loto Mas - Super Mas
DEFAULT_START_DATE = date(2019, 4, 10)  # Super Mas introduction date
REQUEST_DELAY_SECONDS = 1.0  # be a polite citizen of the site's API
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; leidsa-supermas-pipeline/1.0; "
                  "personal data analysis project)"
}


def fetch_batch(anchor_date: date, raw_dir: Path) -> dict:
    """Request a batch of draws anchored on `anchor_date` and save the raw JSON."""
    params = {"id": LOTTERY_ID, "fecha": anchor_date.isoformat()}
    resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    payload = resp.json()

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"history_{anchor_date.isoformat()}.json"
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS draws (
            draw_id           INTEGER PRIMARY KEY,  -- from API's internal "id" field (always present)
            draw_number       TEXT,                 -- from API's numero_sorteo (NULL for most historical draws)
            draw_date         TEXT NOT NULL,         -- from API's fecha_sorteo
            day_of_week       TEXT,
            more_number       INTEGER,   -- "Mas" number (1-12), from API's loto1
            super_more_number INTEGER,   -- "Super Mas" number, from API's loto2
            draw_time         TEXT,      -- from API's hora (also NULL for many historical draws)
            scraped_at        TEXT NOT NULL,
            raw_json          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS draw_numbers (
            draw_id  INTEGER NOT NULL,
            position INTEGER NOT NULL,
            number   INTEGER NOT NULL,
            PRIMARY KEY (draw_id, position),
            FOREIGN KEY (draw_id) REFERENCES draws(draw_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_draws_date_unique ON draws(draw_date);
        CREATE INDEX IF NOT EXISTS idx_draw_numbers_number ON draw_numbers(number);
        """
    )
    conn.commit()


def upsert_draw(conn: sqlite3.Connection, draw: dict) -> bool:
    """Insert a draw and its 6 main numbers. Returns True if it was new."""
    draw_id = draw["id"]

    exists = conn.execute(
        "SELECT 1 FROM draws WHERE draw_id = ?", (draw_id,)
    ).fetchone()
    if exists:
        return False

    draw_date = draw["fecha_sorteo"]
    day_of_week = date.fromisoformat(draw_date).strftime("%A")

    conn.execute(
        """
        INSERT INTO draws
            (draw_id, draw_number, draw_date, day_of_week, more_number,
             super_more_number, draw_time, scraped_at, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draw_id,
            draw.get("numero_sorteo"),
            draw_date,
            day_of_week,
            draw.get("loto1"),
            draw.get("loto2"),
            draw.get("hora"),
            datetime.utcnow().isoformat(timespec="seconds"),
            json.dumps(draw, ensure_ascii=False),
        ),
    )

    numbers = [int(n) for n in draw["premios"].split("-")]
    for position, number in enumerate(numbers, start=1):
        conn.execute(
            """
            INSERT OR IGNORE INTO draw_numbers (draw_id, position, number)
            VALUES (?, ?, ?)
            """,
            (draw_id, position, number),
        )

    return True


def run(start_date: date, db_path: Path, raw_dir: Path) -> None:
    conn = sqlite3.connect(db_path)
    init_db(conn)

    cursor_date = date.today()
    total_new = 0
    seen_cursor_dates = set()

    print(f"Starting backward scraping from {cursor_date} to {start_date}...")

    while True:
        if cursor_date in seen_cursor_dates:
            print(f"[WARNING] Anchor date {cursor_date} was already used. Stopping to avoid an infinite loop.")
            break
        seen_cursor_dates.add(cursor_date)

        payload = fetch_batch(cursor_date, raw_dir)
        history = payload.get("historial", [])

        if not history:
            print(f"[DONE] The API returned no draws for fecha={cursor_date}.")
            break

        new_in_batch = 0
        dates_in_batch = []
        for draw in history:
            dates_in_batch.append(date.fromisoformat(draw["fecha_sorteo"]))
            if upsert_draw(conn, draw):
                new_in_batch += 1
        conn.commit()

        total_new += new_in_batch
        oldest_in_batch = min(dates_in_batch)
        newest_in_batch = max(dates_in_batch)
        print(
            f"  Batch anchored on {cursor_date}: {len(history)} draws "
            f"({oldest_in_batch} -> {newest_in_batch}), {new_in_batch} new. "
            f"Running total: {total_new}"
        )

        if oldest_in_batch <= start_date:
            print(f"[DONE] Reached the start date ({start_date}).")
            break

        if new_in_batch == 0:
            print("[WARNING] The batch contained no new draws. Stopping to avoid an infinite loop.")
            break

        cursor_date = oldest_in_batch - timedelta(days=1)
        time.sleep(REQUEST_DELAY_SECONDS)

    # Final cleanup: in case the cutoff batch included draws older than start_date
    deleted = conn.execute(
        "DELETE FROM draws WHERE draw_date < ?", (start_date.isoformat(),)
    ).rowcount
    conn.execute(
        """
        DELETE FROM draw_numbers
        WHERE draw_id NOT IN (SELECT draw_id FROM draws)
        """
    )
    conn.commit()
    if deleted:
        print(f"Discarded {deleted} draws older than {start_date} (outside the requested range).")

    total_draws = conn.execute("SELECT COUNT(*) FROM draws").fetchone()[0]
    total_numbers = conn.execute("SELECT COUNT(*) FROM draw_numbers").fetchone()[0]
    date_range = conn.execute("SELECT MIN(draw_date), MAX(draw_date) FROM draws").fetchone()
    print(
        f"\nDone. {total_draws} draws / {total_numbers} number rows saved to {db_path}. "
        f"Range: {date_range[0]} -> {date_range[1]}"
    )
    expected_numbers = total_draws * 6
    if total_numbers != expected_numbers:
        print(
            f"[WARNING] Expected {expected_numbers} rows in draw_numbers "
            f"(6 per draw) but found {total_numbers}. Investigate before proceeding."
        )

    conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LEIDSA Loto Mas Super Mas history scraper")
    parser.add_argument(
        "--start-date",
        type=lambda s: date.fromisoformat(s),
        default=DEFAULT_START_DATE,
        help="Oldest date to capture (YYYY-MM-DD). Default: 2019-04-10.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/leidsa_supermas.db"),
        help="Path to the output SQLite file.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw"),
        help="Folder where raw JSON responses from each API call are stored.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    run(start_date=args.start_date, db_path=args.db, raw_dir=args.raw_dir)
