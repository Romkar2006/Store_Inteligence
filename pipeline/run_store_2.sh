#!/bin/bash
set -e

# Change directory to script parent's parent (store-intelligence workspace root)
cd "$(dirname "$0")/.."

echo "[1/2] Running detection pipeline for Store 2..."
python pipeline/detect_store_2.py \
  --entry1 data/store_2_entry_1.mp4 \
  --entry2 data/store_2_entry_2.mp4 \
  --zone data/store_2_zone.mp4 \
  --billing data/store_2_billing_area.mp4 \
  --pos data/store_2_pos_transactions.csv \
  --store ST1009 \
  --clip-start 2026-04-10T20:10:02+05:30 \
  --out events_store_2.jsonl

echo "[2/2] Ingesting events into API for Store 2..."
python -c "
import json, requests, sys
try:
    with open('events_store_2.jsonl') as f:
        batch = [json.loads(l) for l in f]
except FileNotFoundError:
    print('Error: events_store_2.jsonl file not found. Ensure pipeline executed successfully.')
    sys.exit(1)

# Split into chunks of 500 to adhere to API restrictions
chunk_size = 500
print(f'Total events to ingest: {len(batch)}')
for i in range(0, len(batch), chunk_size):
    chunk = batch[i:i+chunk_size]
    try:
        r = requests.post('http://localhost:8000/events/ingest', json={'events': chunk})
        r.raise_for_status()
        print(f'Ingested chunk {i//chunk_size + 1}: {r.json()}')
    except Exception as e:
        print(f'Ingest failed for chunk starting at {i}: {e}')
"
echo "Done. Visit http://localhost:8000/stores/ST1009/metrics"
