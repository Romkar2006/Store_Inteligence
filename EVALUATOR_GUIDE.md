# Store Intelligence System — Evaluator Guide

Welcome to the Evaluator Guide for the Purplle Store Intelligence Challenge. This document outlines the step-by-step instructions to set up, execute, and verify the retail analytics pipeline and FastAPI service.

---

## 🛠️ System Requirements & Prerequisites
Before starting, ensure you have the following installed on your machine:
- **Docker Desktop** (version 20.10+ / Compose V2)
- **Python 3.11+** (if running locally without Docker)
- **Git** (to clone and manage the codebase)

### Placement of Raw Challenge Data
Ensure the required video feeds and POS logs are placed under the `data/` folder:
- `data/CAM_1.mp4` to `data/CAM_5.mp4` (Raw CCTV video feeds)
- `data/pos_transactions.csv` (POS transaction logs)

---

## 🚀 Option A: Run & Test with Docker Compose (Recommended)
This option launches the complete FastAPI server and database environment inside a verified Linux container.

### 1. Build and Start Services
From the root of the project repository, run:
```bash
# Build the FastAPI container and launch it in the background
docker compose up --build -d

# Verify that the service is running and healthy
docker compose ps
```

### 2. Run the Computer Vision Pipeline
With the server running, execute the detection script on the host to process the video feeds, generate events, and auto-ingest them into the API database:
```bash
# Process raw CCTV feeds and populate the API database
bash pipeline/run.sh
```

### 3. Verify API Endpoints
Open a browser or use curl to test the analytics endpoints:
- **Health Check**: `curl http://localhost:8000/health` (Uptime, database connection, stale feed indicators)
- **Store KPIs**: `curl http://localhost:8000/stores/ST1008/metrics` (Unique visitors, conversion rate, zone dwells)
- **Conversion Funnel**: `curl http://localhost:8000/stores/ST1008/funnel` (Progression drop-offs)
- **Operational Anomalies**: `curl http://localhost:8000/stores/ST1008/anomalies` (Alert checks)

### 4. Run Automated Tests Inside Docker
To execute the 26-test suite inside the running container environment:
```bash
docker compose exec api python -m pytest tests/ -v
```

---

## 💻 Option B: Run & Test Locally (Native Python CLI)
Use this option if you want to inspect outputs, run the interactive terminal dashboard, or do not have Docker running.

### 1. Environment Setup
Create a virtual environment and install packages:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Execute the Pipeline
Run the CV tracking pipeline to process the 5 video feeds and generate `events.jsonl`:
```bash
python pipeline/detect.py \
  --cam1 data/CAM_1.mp4 \
  --cam2 data/CAM_2.mp4 \
  --cam3 data/CAM_3.mp4 \
  --cam4 data/CAM_4.mp4 \
  --cam5 data/CAM_5.mp4 \
  --pos  data/pos_transactions.csv \
  --store ST1008 \
  --clip-start 2026-04-10T20:10:02+05:30 \
  --out  events.jsonl
```

### 3. Start the FastAPI Server
Launch the server in your active terminal:
```bash
python -m uvicorn app.main:app --port 8000 --host 127.0.0.1
```

### 4. Launch the Live Terminal Dashboard
In a **new terminal tab**, run the interactive console dashboard to replay events and watch live KPI updates:
```bash
python dashboard/live_dashboard.py --events events.jsonl --store ST1008
```

### 5. Run Tests & Coverage
Run the unit test suite and generate a terminal coverage report:
```bash
python -m pytest tests/ --cov=app --cov-report=term -v
```

---

## 🔍 Troubleshooting Tips
- **Port 8000 Conflict**: If the server fails to bind, check if port 8000 is occupied:
  - *Windows*: `netstat -ano | findstr :8000` (Kill the matching process PID)
  - *Mac/Linux*: `lsof -i :8000`
- **Volume Mount Permission (Windows)**: If Docker returns a volume mount failure, verify that Docker Desktop has File Sharing permission enabled for the project directory.
- **Line Endings (Windows/Git)**: If script execution fails on `run.sh`, fix the script file endings:
  `dos2unix pipeline/run.sh`
