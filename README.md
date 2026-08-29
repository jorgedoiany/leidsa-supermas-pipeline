# LEIDSA Loto Mas Super Mas — Data Pipeline

Scraping and data structuring pipeline for LEIDSA's **Loto - Loto Mas -
Super Mas** draw (Dominican Republic), covering the period since the Super
Mas modality was introduced (April 10, 2019) up to today.

## Data source

Internal API of [elboletoganador.com](https://www.elboletoganador.com)
(`api.elboletoganador.com/api/sorteos/buscar/historial`), a non-official
lottery results information portal. This is not LEIDSA's official source;
the results here are for data analysis purposes, not for prize verification.

## What the pipeline does

1. `scraper.py` walks the draw history backwards (the API returns windows of
   ~15 draws anchored to a date) and stores them in a local SQLite database
   (`data/leidsa_supermas.db`), with a raw JSON backup of every API call in
   `data/raw/`.
2. `sync_to_supabase.py` syncs (upserts) that SQLite database to Postgres on
   Supabase, so the dashboard and the EDA/ML notebooks can read from the
   cloud.

## Schema

```
draws            — 1 row per draw (date, More/Super More numbers, time, raw JSON)
draw_numbers     — 1 row per main drawn number (long format, 6 per draw)
```

`draws.draw_id` (the API's internal `id` field) is the primary key, not
`draw_number` (the API's `numero_sorteo`). The API only started populating
`numero_sorteo` for recent draws — most historical draws have it as `null` —
so it is kept as a plain reference column instead of a key.

## Usage

```bash
pip install -r requirements.txt

# 1. Initial scrape (or incremental — it is idempotent, no duplicates)
python scraper.py

# 2. Upload to Supabase
cp .env.example .env   # fill in SUPABASE_DB_URL
python sync_to_supabase.py
```

## Notes

- The scraper is polite: 1 second delay between requests.
- `data/*.db` and `data/raw/*.json` are in `.gitignore` — they are generated
  data, not code. If you want to version the `.db` for portfolio
  reproducibility, remove it from `.gitignore`.
- A lottery draw is a random process by design; this pipeline is meant for
  descriptive, historical analysis, not for predicting future draws.
- **Supabase connection:** the direct connection host (`db.<ref>.supabase.co`)
  is IPv6-only unless you have purchased the dedicated IPv4 add-on. If your
  network's DNS resolver only requests A (IPv4) records, the direct host
  will fail to resolve. Use the **Session pooler** connection string instead
  (Project Settings → Database → Connect → Session pooler), which is
  IPv4-compatible: `postgresql://postgres.<ref>:[password]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
