# x-bot (Layoffs.fyi → X/Twitter alerts)

MVP service that polls `layoffs.fyi`, dedupes new layoff rows in Postgres, generates a tweet from templates, posts to X, and stores the posted result to prevent duplicates.

## What it does

- Polls `layoffs.fyi` on an interval (default: 15 minutes)
- Extracts layoff rows into a strict normalized shape
- Stores raw + normalized events in Postgres with uniqueness constraints
- Creates post candidates for new events
- Publishes to X via API credentials
- Logs all activity to stdout (Railway-friendly)

## Environment variables

### Required

- `DATABASE_URL`: Postgres connection string (e.g. Railway provided)

### X/Twitter (required to publish)

OAuth 1.0a user-context credentials:

- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_TOKEN_SECRET`

### Optional

- `POLL_SECONDS`: default `900`
- `DRY_RUN`: set to `1` to skip posting (still records candidates, marks posts as `dry_run`)
- `LOG_LEVEL`: default `INFO`

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate  # (Windows PowerShell: .\.venv\Scripts\Activate.ps1)
pip install -r requirements.txt

set DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/xbot
set X_API_KEY=...
set X_API_SECRET=...
set X_ACCESS_TOKEN=...
set X_ACCESS_TOKEN_SECRET=...

python -m src.main
```

## Deploy (Railway)

- Create a Railway project with a Postgres plugin
- Set env vars above
- Use Docker deployment (Dockerfile included) or Railway's default Python build with `python -m src.main`

