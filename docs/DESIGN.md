# Store Intelligence System — Architecture Design

This document describes the complete technical architecture, component decisions, AI integration details, and known boundaries of the Store Intelligence system implemented for the Purplle retail analytics challenge.

---

## 1. System Architecture Overview

The system operates as a 6-stage event-driven pipeline, converting raw CCTV footage and POS logs into interactive, real-time retail intelligence:

```
┌─────────────────────────────────────────────────────────────────┐
│                     INPUT DATA SOURCES                          │
│   5x CCTV Camera Feeds (MP4)      POS Transaction Logs (CSV)   │
└───────────────────┬───────────────────────────┬────────────────┘
                    ▼                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              COMPUTER VISION PIPELINE (Python)                  │
│   YOLOv8n Detector → ByteTrack → Custom Retail Logic Modules   │
│   - cam_entry.py   : Foot-crossing entry/exit at y=620         │
│   - cam_zones.py   : Polygon dwell detection (Skincare/Makeup) │
│   - cam_billing.py : Queue join/abandon state machine          │
│   - cam_stockroom.py: HSV Re-ID staff profile builder          │
└───────────────────┬─────────────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                   EVENT STREAM INGESTION                        │
│   events.jsonl → FastAPI POST /events/ingest                   │
│   (Idempotent batch loader, WebSocket broadcast trigger)        │
└───────────────────┬─────────────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│               FASTAPI CORE ANALYTICS SERVICES                   │
│   SQLite + SQLAlchemy Core (persistent store)                   │
│   REST endpoints: /metrics, /funnel, /heatmap, /anomalies       │
│   WebSocket endpoint: /stores/{id}/ws (real-time broadcast)     │
│   Journey Flow: /stores/{id}/flow (Sankey sequence compiler)    │
│   AI Query: /stores/{id}/ask (Gemini 2.5 Flash + fallback)     │
└───────────────────┬─────────────────────────────────────────────┘
                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                 CLIENT PRESENTATION LAYER                       │
│   React + Vite + Tailwind CSS Dashboard (Vercel)                │
│   Terminal Monitor: rich.live (CLI replay at 10x speed)         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Computer Vision Pipeline Design

### 2.1 Detection & Tracking
- **YOLOv8n** is used for human detection on each video frame. It runs at ~10fps on CPU, producing bounding box centroids per frame.
- **ByteTrack** assigns persistent track IDs across frames using Kalman filter motion prediction and IoU matching, ensuring a single visitor is not double-counted.

### 2.2 Camera Module Separation
Each camera has a dedicated processing module to isolate different behavioral signals:

| Module | Camera | Responsibility |
|---|---|---|
| `cam_entry.py` | CAM_3 (Entrance) | Line crossing at `y=620`. Logs `ENTRY`, `EXIT`, `RE_ENTRY` based on direction of centroid crossing. |
| `cam_zones.py` | CAM_1, CAM_2 | `cv2.pointPolygonTest` checks if centroid is inside a named zone polygon. Emits `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`. |
| `cam_billing.py` | CAM_5 | Queue state machine. Monitors billing counter ROI. Emits `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`. `queue_depth` is tracked per frame. |
| `cam_stockroom.py` | CAM_4 | Builds HSV upper-body color histogram profiles for staff identification. |

### 2.3 Staff Re-ID Design
To filter staff events from customer analytics, a multi-signal composite approach is used:
1. **HSV Histogram Similarity**: 48-dimensional normalized histogram of the top 45% of bounding box (upper body) is compared to stored staff templates using cosine similarity (threshold: 0.72).
2. **Entry Position Heuristic**: Staff consistently enter from `x > 400` (stockroom side), while customers enter from `x < 120` (entrance side).
3. **Lateral Movement Pattern**: Staff walk parallel to shelf edges. Movement vector angle is evaluated to distinguish restocking behavior from browsing.

A track is flagged `is_staff=true` if at least 2 of 3 signals are triggered.

### 2.4 Visitor ID Cross-Camera Propagation
Within a 10-second FIFO time window, a visitor leaving one camera's field of view is matched to the next arriving track on a downstream camera. This allows journey continuity across zones even though cameras do not overlap.

---

## 3. Event Schema Design

All events follow this validated JSON schema (stored in `events.jsonl`):

```json
{
  "event_id": "uuid-v4",
  "store_id": "ST1008",
  "camera_id": "CAM_SKINCARE_01",
  "visitor_id": "TRACK_2",
  "event_type": "ZONE_ENTER",
  "timestamp": "2026-04-10T20:10:02+05:30",
  "zone_id": "SKINCARE_SHELF_RIGHT",
  "dwell_ms": 0,
  "is_staff": false,
  "confidence": 0.81,
  "metadata": {
    "queue_depth": null,
    "sku_zone": null,
    "session_seq": 1
  }
}
```

**Event types emitted**: `ENTRY`, `EXIT`, `RE_ENTRY`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_ABANDON`

**Schema validation**: All 310 events in `events.jsonl` pass schema validation (verified by automated test in `tests/test_ingestion.py`).

---

## 4. FastAPI Backend Architecture

### 4.1 REST Analytics Endpoints
| Route | Logic |
|---|---|
| `GET /stores/{id}/metrics` | Unique visitors (excluding staff), conversion rate (POS 5-min correlation), avg dwell per zone |
| `GET /stores/{id}/funnel` | Counts per funnel stage: ENTRY → ZONE_VISIT → BILLING_QUEUE_JOIN → PURCHASE |
| `GET /stores/{id}/heatmap` | Normalized heat score 0–100 per zone based on visit count and dwell time |
| `GET /stores/{id}/anomalies` | Queue depth spikes (>3 threshold), dead zones (0 visits), conversion drops vs 7-day baseline |
| `GET /stores/{id}/flow` | Visitor journey sequence compiler: nodes + links for Sankey diagram |
| `POST /stores/{id}/ask` | Gemini 2.5 Flash / rule-based NL analytics query |
| `WS /stores/{id}/ws` | Live event broadcast subscription (JSON over WebSocket) |
| `POST /events/ingest` | Idempotent bulk event loader; triggers WebSocket broadcast on completion |

### 4.2 WebSocket Broadcaster
A `ConnectionManager` class (`app/websocket.py`) maintains a dictionary of active WebSocket connections keyed by `store_id`. On each successful `/events/ingest` call, a background task broadcasts a JSON notification to all subscribers of the relevant store.

### 4.3 Visitor Journey Flow Compiler (`app/flow.py`)
The Sankey flow diagram data is computed server-side by:
1. Grouping all events by `visitor_id`, sorted chronologically.
2. Mapping raw zone IDs to 5 logical categories: `Entry`, `Skincare`, `Makeup`, `Billing`, `Exit`.
3. Collapsing consecutive identical categories (de-duplication).
4. Checking POS transactions within a 5-minute window of billing entry. If matched, the journey ends at `Billing` (conversion). If no match, `Exit` is appended (dropout).
5. Aggregating transition counts between sequential node pairs to produce `links`.

### 4.4 Idempotent Storage
The `POST /events/ingest` endpoint checks each `event_id` against the database before insertion. Duplicate events are silently skipped. This enables safe replay of `events.jsonl` without creating duplicate records.

---

## 5. Real-Time Web Dashboard Architecture

The React dashboard (`dashboard-web/`) is a production-grade single-page application:

| Component | Technology | Purpose |
|---|---|---|
| Build Tool | Vite 8 + React 19 | Fast HMR dev server and optimized production bundle |
| Styling | Tailwind CSS v3 + Glassmorphism | Premium dark-mode design system |
| Charts | Recharts | Shopper funnel area charts, dwell time bar charts |
| Icons | Lucide React | High-fidelity icon set |
| Connections | Native WebSocket API | Sub-second event ticker without polling |
| Visualization | Custom SVG (no libraries) | Animated Sankey flow diagram with cubic Bezier ribbons |
| AI Chat | REST POST to /ask | Conversational analytics panel with Gemini badges |

### Dashboard Sections:
1. **KPI Cards**: Unique Shoppers, Conversion Rate, Queue Depth, Abandonment Rate
2. **Live 2D Floorplan Heatmap**: SVG zone polygons dynamically colored by heat score
3. **CCTV Surveillance Matrix**: Simulated 2×2 camera grid with live alert overlays
4. **Recharts Funnel & Dwell Charts**: Conversion funnel + zone dwell analysis
5. **Sankey Visitor Journey Flow**: Animated node-link transition diagram
6. **WebSocket Ticker**: Live scrolling event stream
7. **Ask AI Panel**: NL chat with Gemini grounding badges

---

## 6. AI-Assisted Decisions

### Decision 1: Staff Detection — Composite Signal Overrides Single Heuristic
**What AI suggested**: Use HSV colour analysis alone (high black-pixel fraction = staff uniform).

**What I found**: After running pixel analysis on CAM_2 frames from the Brigade Road footage, staff black% ranged 5–20% while dark-clothed customers reached 31% — far too much overlap for a reliable single threshold.

**What I decided**: Override with a 3-signal composite requiring at least 2 of 3 signals: colour fraction, first-appearance position (staff enter from `x>400`, customers from `x<120`), and lateral movement pattern (staff walk parallel to shelf wall). This significantly reduced false positives in the `is_staff` flag.

---

### Decision 2: IST Timezone Correction
**What AI suggested**: Treat all POS timestamps as UTC to align with the CCTV clip-start time.

**What I found**: Reading the timestamp overlay on the actual camera footage showed `20:10:27 IST` on screen. The POS data timestamps (12:15–21:39) also reflect IST store opening hours.

**What I decided**: Corrected both the clip start parsing and the POS loader to use `UTC+5:30`. Without this correction, all POS correlation would have been offset by exactly 5 hours 30 minutes, causing zero purchases to match any billing queue event.

---

### Decision 3: SQLite Over PostgreSQL
**What AI suggested**: Use PostgreSQL for production-readiness.

**What I decided**: Use SQLite with SQLAlchemy Core. This challenge demonstrates a single-store pipeline on a local machine — adding an external PostgreSQL container increases docker-compose complexity with zero benefit at demo scale. SQLAlchemy Core keeps SQL explicit and lightweight. Migrating to PostgreSQL requires only changing the `DATABASE_URL` environment variable. I documented SQLite as the first scaling bottleneck for 40-store production in the Known Limitations section.

---

### Decision 4: Custom SVG Sankey Over D3 or npm Libraries
**What AI suggested**: Use `d3-sankey` or `react-flow` npm packages.

**What I decided**: Implement a fully custom SVG Sankey layout in React with no external diagram libraries. Reasons:
1. The Vercel frontend environment had npm version conflicts with certain D3 packages.
2. A custom implementation gave full control over animation (flowing dashed CSS keyframes), interactive hover states, and the glassmorphic visual style required for the premium dashboard aesthetic.
3. The mathematical layout (column-based node positioning, cubic Bezier ribbon curves, proportional link widths) is straightforward to implement and test without black-box library dependencies.

---

### Decision 5: Gemini 2.5 Flash + Rule-Based Fallback
**What AI suggested**: Use only the Gemini API for natural language analytics.

**What I decided**: Implement a dual-mode NL engine. In production (when `GEMINI_API_KEY` is set), the system uses Gemini 2.5 Flash with a structured context payload containing current metrics, heatmap scores, funnel counts, and active anomalies. In all other environments (tests, local dev without key, evaluator environments), a robust keyword-matching rule engine handles the same set of analytical questions deterministically. This ensures:
- Zero test flakiness (no network calls in tests)
- Graceful degradation when the API key is not set
- Evaluators can test the NL query feature offline

---

## 7. Known Limitations

- **Short Clip Window**: The provided CCTV clips cover ~2 minutes (`20:10–20:12 IST`). The nearest POS transaction is 13 minutes outside this window, so `conversion_rate` correctly returns `0.0`. A full production day would show real conversion data.
- **Cross-Camera Re-ID**: Visitor ID propagation uses a FIFO 10-second time-window match. When multiple customers transit simultaneously, misassignment is possible. Production requires embedding-based cross-camera re-identification.
- **YOLOv8n Occlusion**: The nano model may miss partially occluded people behind the Summer display in CAM_2. Confidence is always reported accurately — never suppressed — so low-confidence detections remain visible to evaluators.
- **7-day Conversion Baseline**: Anomaly detection uses a simulated 0.28 baseline conversion rate since only one day of POS data is available.
- **SQLite Concurrency**: SQLite does not support concurrent writes. Multi-threaded high-throughput ingestion would require PostgreSQL or a message queue (e.g., Kafka).
