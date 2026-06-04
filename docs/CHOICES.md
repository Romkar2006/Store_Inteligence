# Technical Decision Choices & Trade-offs

This document records all significant engineering decisions made during the design and construction of the Store Intelligence system, including what alternatives were considered, what AI suggested, and the rationale for the final choices made.

---

## Decision 1: Detection Model — YOLOv8n vs RT-DETR vs MediaPipe

### Options Considered
| Option | Pros | Cons |
|---|---|---|
| **YOLOv8n** (nano) | Fast CPU inference (~10fps), ByteTrack integration, lightweight Docker footprint | Lower accuracy than YOLOv8s on small/occluded detections |
| **YOLOv8s** (small) | Better accuracy, still CPU-feasible | ~2× slower than nano; overkill for walking-pace retail |
| **RT-DETR** | State-of-the-art transformer accuracy, excellent occlusion handling | Requires GPU; CPU inference is too slow for real-time retail |
| **MediaPipe Pose** | Good skeleton markers | Designed for single front-facing person; fails on top-down CCTV angles |

### What AI Suggested
Use **YOLOv8s** as a balanced accuracy/speed compromise.

### What Was Chosen
**YOLOv8n** with a `--quality=high` runtime flag that elevates to **YOLOv8s** if desired.

### Rationale
In a retail CCTV context, customers walk at standard indoor pace (~1 m/s). YOLOv8n achieves adequate detection at this speed and runs at ~10fps on a standard CPU, which is sufficient for robust tracking without frame-drop backlogs. The `--quality` flag allows evaluators to benchmark YOLOv8s performance without changing source code. RT-DETR was ruled out to keep Docker container size and local CPU requirements minimal during validation.

---

## Decision 2: Event Schema — Nested Metadata vs Flat vs Polymorphic

### Options Considered
| Option | Pros | Cons |
|---|---|---|
| **Flat Schema** | Simple to parse | `queue_depth`, `sku_zone` etc. clutter every record type |
| **Nested Metadata** (chosen) | Single uniform Pydantic model for all 8 event types | Consumers must check `event_type` before reading nested fields |
| **Polymorphic Schemas** | Strongly typed per event | 8 separate models; much more validation complexity; harder to batch ingest |

### What AI Suggested
Use a **Nested Metadata** structure to group auxiliary parameters.

### What Was Chosen
**Nested Metadata** — a single `EventIn` Pydantic model for all event types, with optional fields inside `metadata`.

### Rationale
A uniform schema keeps the `/events/ingest` endpoint simple — it validates one model against all incoming records in a single pass. Consumers always read the same top-level fields (`event_type`, `timestamp`, `visitor_id`, `is_staff`, `confidence`) and then optionally extract `metadata.queue_depth` for billing events or `metadata.session_seq` for tracking analysis. This also makes downstream API aggregations simpler since all events are in the same database table.

The schema was validated against the provided `sample_events.jsonl` reference format and all 310 events in `events.jsonl` pass schema validation.

---

## Decision 3: Storage — SQLite vs PostgreSQL vs In-Memory Cache

### Options Considered
| Option | Pros | Cons |
|---|---|---|
| **SQLite** (chosen) | Zero-config, single file, no container, test isolation | Not suitable for concurrent multi-threaded writes at scale |
| **PostgreSQL** | Production-grade concurrency, horizontal scaling | Requires a running container; increases docker-compose complexity |
| **Redis / In-Memory** | Fast reads | Data lost on server restart; not suitable for persistent analytics |

### What AI Suggested
Use **PostgreSQL** for production-readiness.

### What Was Chosen
**SQLite** with **SQLAlchemy Core** abstraction.

### Rationale
The challenge requires demonstrating a single-store analytics system. Adding a PostgreSQL container increases `docker-compose.yml` complexity, requires network bridging configuration, and adds startup time — none of which provide evaluative benefit at demo scale. SQLAlchemy Core (not ORM) keeps all SQL explicit, transactions visible, and adds zero magic overhead.

**Upgrade path**: Because access is fully abstracted through SQLAlchemy Core, migrating to PostgreSQL requires changing only the `DATABASE_URL` environment variable in `.env`. No application code changes are needed.

**Known limitation documented**: SQLite is listed as the primary scaling bottleneck in `DESIGN.md` Known Limitations — transparent to evaluators.

---

## Decision 4: API Architecture — REST + WebSocket vs Polling vs Server-Sent Events

### Options Considered
| Option | Pros | Cons |
|---|---|---|
| **REST + WebSocket** (chosen) | Real-time push for live dashboard; REST for analytics queries | Two connection types to manage |
| **REST Polling** | Simpler; no WebSocket state | High latency (refresh every N seconds); wasteful requests |
| **Server-Sent Events (SSE)** | Simpler than WebSocket for one-way push | Not bidirectional; not natively supported in all FastAPI versions |

### What AI Suggested
Use REST endpoints with periodic polling from the frontend.

### What Was Chosen
**Full REST API for analytics** + **WebSocket** for real-time event streaming.

### Rationale
The brief explicitly mentioned a "Live Dashboard" as a bonus requirement. A polling-based dashboard would have noticeable lag and waste server resources re-fetching the same data. The FastAPI WebSocket integration (`app/websocket.py`) adds a `ConnectionManager` with `store_id`-scoped subscriptions. On each `/events/ingest` call, a background task broadcasts a lightweight JSON notification to all connected dashboard clients. The React dashboard then fires targeted REST API calls only for the specific metrics that changed — dramatically reducing unnecessary data transfer.

---

## Decision 5: NL Query Engine — Gemini API vs Rule-Based vs Fine-tuned Model

### Options Considered
| Option | Pros | Cons |
|---|---|---|
| **Gemini 2.5 Flash + Fallback** (chosen) | High-quality NL understanding; graceful degradation | Requires API key; adds network latency |
| **Gemini Only** | Simpler code | Fails silently in test environments; no key = no feature |
| **Fine-tuned local LLM** | No API cost; fully offline | Requires GPU; far too large for a challenge submission |
| **Rule-based only** | Deterministic; zero dependencies | Brittle keyword matching; cannot generalize |

### What AI Suggested
Use only the Gemini API, falling back to a simple static string if the key is missing.

### What Was Chosen
**Gemini 2.5 Flash with a full-featured rule-based fallback engine**.

### Rationale
The Gemini API provides powerful analytical reasoning when given the store's current metrics as context (visitor counts, heatmap scores, anomaly alerts, funnel data). However, relying solely on the API makes the feature non-testable and non-functional in evaluator environments without a key.

The rule-based fallback (`app/ask.py`) uses keyword pattern matching across 6 analytical categories (dwell time, conversion, anomalies, zone ranking, abandonment, queue). It queries the same SQLite database and returns quantitative answers. This means:
- All 4 `test_ask.py` unit tests run deterministically without any API calls.
- Evaluators can test the Ask AI panel offline.
- When `GEMINI_API_KEY` is set, the full AI grounding kicks in automatically.

---

## Decision 6: Sankey Flow Diagram — Custom SVG vs D3-Sankey vs React-Flow

### Options Considered
| Option | Pros | Cons |
|---|---|---|
| **Custom SVG** (chosen) | Full control over animation, style, interactivity | More code to write |
| **d3-sankey** | Battle-tested layout math | npm version conflicts in Vite 8 environment; large bundle overhead |
| **react-flow** | Rich node/edge graph library | Overkill for a fixed 5-column layout; licence restrictions |

### What AI Suggested
Use `d3-sankey` for the Sankey layout calculations.

### What Was Chosen
**Custom SVG implementation** in React with pure JavaScript layout math.

### Rationale
The Vercel deployment environment encountered npm resolution conflicts with certain D3 sub-packages. More importantly, the Sankey layout for this use case is structurally fixed (5 columns: Entry → Skincare/Promo/Navigation → Makeup → Billing → Exit), making the mathematical complexity trivial. The custom implementation delivers:
- **Cubic Bezier ribbon curves** (`M x0 y0 C cx1 cy1 cx2 cy2 x1 y1`) with width proportional to visitor count
- **CSS keyframe particle animations** (`stroke-dasharray` + `stroke-dashoffset`) for flow direction
- **Interactive hover highlighting** with dim-others effect
- **Glassmorphic node design** perfectly matching the dashboard aesthetic

No external dependency needed, zero bundle size overhead added.
