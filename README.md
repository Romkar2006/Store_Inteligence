# Store Intelligence System — Purplle Brigade Road (ST1008)

End-to-end retail analytics pipeline: raw CCTV footage → behavioral events → real-time FastAPI analytics.

## Quick Start (5 commands)

```bash
git clone https://github.com/YOUR_USERNAME/store-intelligence.git
cd store-intelligence
cp data/sample.env .env
docker compose up --build -d
bash pipeline/run.sh
```

Visit: http://localhost:8000/stores/ST1008/metrics

If you are working inside this workspace, use the project root directly:

```powershell
cd "C:\Users\Victus\OneDrive\Desktop\purple_tech\store-intelligence-pipeline-setup\store-intelligence"
docker compose up --build -d
```

Do not run `uvicorn app.main:app` from the parent `purple_tech` folder. The `app` package lives inside the `store-intelligence` project root.

## Prerequisites

- Docker Desktop installed and running
- Python 3.11+ (for running pipeline outside Docker)
- Camera footage files placed in data/ (see data/README.md)

## Docker Setup

The recommended process is Docker Compose from the `store-intelligence` project root. That keeps the API, SQLite volume, and input data paths aligned with the container paths used in `Dockerfile` and `docker-compose.yml`.

```powershell
cd "C:\Users\Victus\OneDrive\Desktop\purple_tech\store-intelligence-pipeline-setup\store-intelligence"
docker compose up --build -d
docker compose ps
curl -sS http://127.0.0.1:8000/health
```

## Setup Data Files

```bash
# Rename and place POS data
cp /path/to/Brigade_Bangalore_10_April_26.csv data/pos_transactions.csv

# Place camera files
cp /path/to/CAM_*.mp4 data/
```

## Run the Detection Pipeline

```bash
# Processes all 5 cameras, generates events.jsonl
bash pipeline/run.sh

# Expected output: ~330 events in events.jsonl
# Event types: ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT,
#              ZONE_DWELL, BILLING_QUEUE_JOIN,
#              BILLING_QUEUE_ABANDON
```

## Run Tests

```bash
pip install -r requirements.txt
pytest tests/ -v --tb=short
# Expected: 26 passed, 84% coverage
```

## Live Dashboard (Bonus)

```bash
python dashboard/live_dashboard.py \
	--events events.jsonl \
	--store ST1008
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| GET /stores/ST1008/metrics | Unique visitors, conversion rate, zone dwell |
| GET /stores/ST1008/funnel | Entry → Zone → Billing → Purchase |
| GET /stores/ST1008/heatmap | Zone heat scores normalised 0–100 |
| GET /stores/ST1008/anomalies | Queue spikes, conversion drops, dead zones |
| GET /health | Service status, stale feed detection |
| POST /events/ingest | Batch event ingestion (idempotent) |

## Expected Output Note

The provided clips are a ~2-minute window (20:10–20:12 IST).
The nearest POS transaction falls 13 minutes outside this window.
The system correctly returns conversion_rate: 0.0 for this window.
A full production deployment with day-long feeds would show real conversion data.

## Architecture

See docs/DESIGN.md for full architecture and AI-assisted decisions.
See docs/CHOICES.md for engineering trade-off reasoning.
