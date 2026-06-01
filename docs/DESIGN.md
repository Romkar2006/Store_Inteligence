# Store Intelligence System Architecture Design (Step 20)

This document describes the technical architecture, component choices, AI integration details, and known boundaries of the Store Intelligence system implemented for the retail analytics hiring challenge.

---

## 1. Architecture Overview

The system operates as a 5-stage pipeline mapping raw CCTV feeds to queryable analytics tables and live terminal dashboard updates.

```
+------------------+     +-------------------+     +------------------+
|   Video Feeds    | --> |  Detection Layer  | --> |   Event Stream   |
| (5 CAMs, 1080p)  |     | (YOLOv8 + Re-ID)  |     |  (events.jsonl)  |
+------------------+     +-------------------+     +------------------+
                                                            |
                                                            v
+------------------+     +-------------------+     +------------------+
|  Live Dashboard  | <-- |  Intelligence API | <-- | SQLite Database  |
|  (Terminal Rich) |     |  (FastAPI Router) |     |  (SQLAlchemy)    |
+------------------+     +-------------------+     +------------------+
```

### Pipeline Description:
1. **Video Input**: Raw 1920x1080 retail camera clips (CAM_1 to CAM_5) representing the entrance, makeup aisles, skincare floor, stockroom, and billing counter.
2. **Detection Layer**: Extracts tracks using YOLOv8n (or YOLOv8s depending on `--quality`) and ByteTrack. Tracks are processed by camera-specific modules:
   * `cam_entry.py` (CAM_3): Evaluates line crossings at `y=620` to log store entry/exit/reentry.
   * `cam_zones.py` (CAM_1 & CAM_2): Computes polygon collisions to register skincare and makeup aisle interactions.
   * `cam_billing.py` (CAM_5): Manages billing queues and detects abandonments.
   * `cam_stockroom.py` (CAM_4): Builds staff appearance profiles.
3. **Event Stream**: Outputs valid events in the specified JSON schema format to `events.jsonl`.
4. **Intelligence API**: Ingests JSONL streams in transactional batches via FastAPI, matching records against loaded POS transactions.
5. **Live Dashboard**: Replays events into the database in simulated real-time and renders store analytics via a rich CLI layout.

---

## 2. Component Decisions

* **SQLite & SQLAlchemy Core**: SQLite is chosen for simplicity and low operational overhead during deployment, abstracting access through SQLAlchemy Core so that migration to high-throughput servers (like PostgreSQL) requires only a connection string adjustment.
* **FastAPI**: Provides high-performance, asynchronous REST routing and validation, enabling validation error logging without dropping valid items.
* **Torso HSV Color Histogram (staff_id.py)**: Avoids complex neural networks by analyzing the top 45% torso segment for dark colors to identify black staff uniforms.
* **Upper Body Descriptor (reid.py)**: Extracts a 48-dimensional normalized Hue-Saturation-Value histogram from upper body crops to trace client reentries without requiring GPU models.
* **Dedicated Stockroom (cam_stockroom.py)**: Ensures CAM_4 is separated from client zones and leverages stockroom occurrences to dynamically update staff flags on other cameras.

---

## AI-Assisted Decisions

### 1. Staff Detection — Colour Approach Overridden
The initial AI suggestion was to use HSV colour analysis alone to detect staff (black uniform = high black pixel fraction). After running actual pixel analysis on CAM_2 frames from the Brigade Road footage, I found staff black% ranged 5–20% while dark-clothed customers reached 31% — too much overlap for a reliable threshold. I overrode this with a 3-signal composite requiring 2 of 3 signals: colour fraction, first appearance position (staff enter from x>400, customers from x<120), and lateral movement pattern (staff walk along shelf wall). This was my decision, not the AI suggestion.

### 2. Timezone Correction — IST Not UTC
The initial prompt suggested treating POS timestamps as UTC to align with the clip-start timestamp. I identified this was wrong by reading the timestamp overlay on the actual camera footage (20:10:27 on screen = IST wall clock). The POS data (12:15–21:39) also reflects IST store hours. I corrected both the clip_start and POS loader to use UTC+5:30. Without this fix, all POS correlation would have been offset by 5h30m.

### 3. SQLite Over PostgreSQL
AI recommended PostgreSQL for production-readiness. I chose SQLite with SQLAlchemy Core abstraction because this challenge demonstrates a single-store system — adding a separate database container increases docker-compose complexity with no benefit at demo scale. The abstraction layer means switching to PostgreSQL requires only a connection string change. I documented SQLite as the first scaling bottleneck for 40-store production in the Known Limitations section.

---

## Known Limitations

- Camera clips are ~2 minutes (`20:10` to `20:12` IST). The nearest POS transaction falls 13 minutes outside this window, so conversion_rate correctly returns 0.0. Full day feeds would show real conversion data.
- Visitor_id propagation across cameras uses FIFO time-window matching. When multiple customers enter within the same window, misassignment is possible. Production would use cross-camera Re-ID embeddings.
- YOLOv8n may miss partially occluded people behind the Summer display stand in CAM_2. Confidence is reported accurately — never suppressed.
- 7-day conversion baseline for anomaly detection is simulated at 0.28 since only one day of POS data is available.
