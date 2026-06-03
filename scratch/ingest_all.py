import json
import httpx
import time

API_BASE = "http://localhost:8000"

def ingest_file(file_path):
    print(f"Ingesting events from {file_path}...")
    events = []
    
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line.strip()))
                
    # Ingest in chunks of 100 to avoid overloading and comply with max 500 limit
    chunk_size = 100
    for i in range(0, len(events), chunk_size):
        chunk = events[i:i + chunk_size]
        try:
            res = httpx.post(f"{API_BASE}/events/ingest", json={"events": chunk}, timeout=10.0)
            if res.status_code == 200:
                print(f"Ingested {len(chunk)} events ({i + len(chunk)}/{len(events)})")
            else:
                print(f"Failed chunk {i}: {res.status_code} {res.text}")
        except Exception as e:
            print(f"Error during ingestion chunk {i}: {e}")
        time.sleep(0.05)

if __name__ == "__main__":
    ingest_file("events.jsonl")
    ingest_file("events_store_2.jsonl")
    print("Ingestion complete.")
