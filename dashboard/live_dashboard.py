import argparse
import time
import requests
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from threading import Thread
from typing import Dict, Any, List

from rich.live import Live
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn
from rich.layout import Layout
from rich.panel import Panel
from rich.console import Console
from rich.text import Text

console = Console()

# Global state for dashboard update
metrics_data = {}
funnel_data = {}
anomalies_data = []
replay_percent = 0.0
replay_status = "Initializing..."

def fetch_api_data(store_id: str):
    """Periodically fetch metrics, funnel, and anomalies from the FastAPI endpoints."""
    global metrics_data, funnel_data, anomalies_data
    api_url = "http://localhost:8000"
    
    while True:
        try:
            # 1. Fetch metrics
            r_metrics = requests.get(f"{api_url}/stores/{store_id}/metrics")
            if r_metrics.status_code == 200:
                metrics_data = r_metrics.json()
            
            # 2. Fetch funnel
            r_funnel = requests.get(f"{api_url}/stores/{store_id}/funnel")
            if r_funnel.status_code == 200:
                funnel_data = r_funnel.json()
                
            # 3. Fetch anomalies
            r_anom = requests.get(f"{api_url}/stores/{store_id}/anomalies")
            if r_anom.status_code == 200:
                anomalies_data = r_anom.json().get("anomalies", [])
        except Exception as e:
            # Silence connection errors during API startup
            pass
        time.sleep(2.0)

def replay_events(events_path: str, store_id: str):
    """Replay events from events.jsonl into POST /events/ingest at 10x speed (Step 23)."""
    global replay_percent, replay_status
    api_url = "http://localhost:8000/events/ingest"
    
    if not os.path.exists(events_path):
        replay_status = f"Error: {events_path} not found"
        return
        
    try:
        with open(events_path, "r", encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
    except Exception as e:
        replay_status = f"Error reading events: {e}"
        return

    if not events:
        replay_status = "No events found to replay"
        return
        
    # Sort events to ensure correct timeline execution
    events.sort(key=lambda x: x["timestamp"])
    total_events = len(events)
    replay_status = f"Loaded {total_events} events. Replaying at 10x..."
    
    t0_real = time.time()
    # Handle both Z and offset timestamps
    def parse_ts(ts_str):
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)

    t0_event = parse_ts(events[0]["timestamp"])
    
    # Process events and simulate 10x speed playback
    for idx, event in enumerate(events):
        evt_time = parse_ts(event["timestamp"])
        elapsed_event_seconds = (evt_time - t0_event).total_seconds()
        
        # 10x speed calculation
        target_elapsed_real = elapsed_event_seconds / 10.0
        time_to_sleep = target_elapsed_real - (time.time() - t0_real)
        
        if time_to_sleep > 0:
            time.sleep(time_to_sleep)
            
        # Ingest single event
        try:
            requests.post(api_url, json={"events": [event]})
        except Exception:
            pass
            
        replay_percent = ((idx + 1) / total_events) * 100
        replay_status = f"Replaying event {idx + 1}/{total_events} (10x)..."
        
    replay_status = "Replay Complete!"
    replay_percent = 100.0

def make_dashboard_layout(store_id: str) -> Layout:
    """Create dashboard layout panel (Step 23)."""
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3)
    )
    
    layout["main"].split_row(
        Layout(name="metrics", ratio=1),
        Layout(name="funnel_anomalies", ratio=1)
    )
    
    layout["funnel_anomalies"].split(
        Layout(name="funnel", ratio=1),
        Layout(name="anomalies", ratio=1)
    )
    
    # Render Header Panel
    layout["header"].update(
        Panel(
            Text(f"STORE INTELLIGENCE SYSTEM - LIVE MONITOR (STORE: {store_id})", justify="center", style="bold green"),
            border_style="blue"
        )
    )
    
    # Render Metrics Panel
    metrics_table = Table(title="Live Store Metrics", expand=True)
    metrics_table.add_column("Metric", style="cyan")
    metrics_table.add_column("Value", style="magenta")
    
    if metrics_data:
        metrics_table.add_row("Unique Visitors (Customers)", str(metrics_data.get("unique_visitors", 0)))
        conv_rate = metrics_data.get("conversion_rate", 0.0) * 100
        metrics_table.add_row("Conversion Rate", f"{conv_rate:.2f}%")
        metrics_table.add_row("Billing Queue Depth", str(metrics_data.get("current_queue_depth", 0)))
        abandon_rate = metrics_data.get("abandonment_rate", 0.0) * 100
        metrics_table.add_row("Queue Abandonment Rate", f"{abandon_rate:.2f}%")
        
        # Find top zone
        dwells = metrics_data.get("avg_dwell_per_zone", {})
        if dwells:
            top_zone = max(dwells, key=dwells.get)
            top_zone_dwell = dwells[top_zone] / 1000.0
            metrics_table.add_row("Top Active Zone", f"{top_zone} ({top_zone_dwell:.1f}s avg)")
        else:
            metrics_table.add_row("Top Active Zone", "None")
    else:
        metrics_table.add_row("Unique Visitors", "Connecting...")
        metrics_table.add_row("Conversion Rate", "Connecting...")
        metrics_table.add_row("Billing Queue Depth", "Connecting...")
        metrics_table.add_row("Queue Abandonment Rate", "Connecting...")
        metrics_table.add_row("Top Active Zone", "Connecting...")
        
    layout["main"]["metrics"].update(Panel(metrics_table, title="KPI Metrics", border_style="cyan"))
    
    # Render Funnel Panel
    funnel_table = Table(title="Visitor Drop-off Funnel", expand=True)
    funnel_table.add_column("Stage", style="yellow")
    funnel_table.add_column("Visitors", style="green")
    funnel_table.add_column("Drop Off %", style="red")
    
    stages = funnel_data.get("funnel", [])
    for stage in stages:
        funnel_table.add_row(
            stage["stage"],
            str(stage["visitors"]),
            f"{stage['drop_off_pct']}%"
        )
        
    layout["funnel_anomalies"]["funnel"].update(Panel(funnel_table, title="Session Funnel", border_style="yellow"))
    
    # Render Anomalies Panel
    anoms_table = Table(expand=True)
    anoms_table.add_column("Type", style="bold red")
    anoms_table.add_column("Severity", style="bold white")
    anoms_table.add_column("Suggested Action", style="green")
    
    for anom in anomalies_data:
        sev_color = "red" if anom["severity"] == "CRITICAL" else "yellow"
        anoms_table.add_row(
            anom["type"],
            Text(anom["severity"], style=f"bold {sev_color}"),
            anom["suggested_action"]
        )
        
    layout["funnel_anomalies"]["anomalies"].update(Panel(anoms_table, title="Active Operations Alerts", border_style="red"))
    
    # Render Footer Panel (Progress Bar)
    progress_bar = Table.grid(expand=True)
    progress_bar.add_row(Text(replay_status, style="cyan"))
    
    # Render ASCII Bar
    bar_width = 40
    filled = int(replay_percent / 100 * bar_width)
    bar_str = "[" + "#" * filled + "-" * (bar_width - filled) + "]"
    progress_bar.add_row(Text(f"{bar_str} {replay_percent:.1f}% Complete", style="bold green"))
    
    layout["footer"].update(Panel(progress_bar, title="Replay Status", border_style="green"))
    
    return layout

def main():
    parser = argparse.ArgumentParser(description="Live Store Monitor Terminal Dashboard (Step 23)")
    parser.add_argument("--events", default="events.jsonl", help="Path to events.jsonl file")
    parser.add_argument("--store", default="ST1008", help="Store ID")
    args = parser.parse_args()

    # Start API metrics fetcher thread
    fetcher_thread = Thread(target=fetch_api_data, args=(args.store,), daemon=True)
    fetcher_thread.start()

    # Start Event Replay thread
    replay_thread = Thread(target=replay_events, args=(args.events, args.store), daemon=True)
    replay_thread.start()

    # Launch live updating terminal layout
    with Live(make_dashboard_layout(args.store), refresh_per_second=4, screen=True) as live:
        while True:
            live.update(make_dashboard_layout(args.store))
            time.sleep(0.25)

if __name__ == "__main__":
    main()
