# Technical Decision Choices & Trade-offs (Step 21)

This document log details the three key technical decisions made during the design and construction of the Store Intelligence system.

---

## Decision 1: Detection Model — YOLOv8n vs RT-DETR vs MediaPipe

### Options Considered:
1. **YOLOv8n**: Single-stage object detector from Ultralytics, highly optimized, runs fast on CPU, and has ByteTrack tracking support integrated out of the box.
2. **RT-DETR**: Real-Time DEtection TRansformer, excellent accuracy and handling of occlusion, but requires heavy GPU resources and is slow to process frames on standard host servers.
3. **MediaPipe Pose**: High-quality skeleton marker model, but primarily designed for front-facing, single-person streams and not suited for top-down, wide-angle store security CCTV cameras.

### What AI Suggested:
The AI suggested using **YOLOv8s** (small) as a balanced middle-ground for detection accuracy and speed on general hardware.

### What Was Chosen:
**YOLOv8n** (nano) was chosen, with an optional `--quality=high` runtime flag to elevate to **YOLOv8s** if desired.

### Reason:
Our retail target contains customers walking at standard indoor paces. The high-speed inferencing of YOLOv8n allows processing feeds at ~10fps on normal CPUs without causing frame drop backlogs. Since the ByteTrack configuration maps movement cleanly, the transformer-based RT-DETR was rejected to minimize local execution latency and Docker container footprints during validation.

---

## Decision 2: Event Schema Design

### Options Considered:
1. **Flat Schema**: All attributes (confidence, coordinates, queue parameters, sequence tags) stored at the top level of the JSON body.
2. **Nested Metadata**: Basic tracking properties kept at the top level, with auxiliary data (such as queue depths, item zones, and sequence indices) stored inside a `metadata` object.
3. **Polymorphic Schemas**: Dynamic structures defined for each event type (e.g., an Entry schema containing entry directions, and a Billing schema containing queue depth parameters).

### What AI Suggested:
The AI suggested using a **Nested Metadata** structure to group auxiliary parameters, which was adopted.

### Reason:
A nested structure keeps the validator uniform. Using Pydantic V2, we only need to maintain a single `EventIn` schema model for all eight event categories, saving processing overhead and validation complexity. Downstream consumers can ingest the top-level parameters uniformly while extracting optional details from the nested `metadata` field based on `event_type`.

---

## Decision 3: Storage — SQLite vs PostgreSQL

### Options Considered:
1. **SQLite**: Self-contained, single-file serverless relational database, perfect for local demo deployment and testing.
2. **PostgreSQL**: Production-grade database supporting highly concurrent connections and horizontal scaling, but requires a separate running service container.
3. **In-Memory Cache (Redis/Dictionary)**: Fast access speeds, but loses data upon server restarts or application crashes.

### What AI Suggested:
The AI suggested **PostgreSQL** to build a production-ready application layout.

### What Was Chosen:
**SQLite** with SQLAlchemy Core was chosen.

### Reason:
Since the hiring challenge is assessed on a single-store demonstration (ST1008), SQLite eliminates the complexity of coordinating and linking external container networks. SQLAlchemy Core is utilized instead of an ORM to keep SQL transactions explicit and lightweight. Because access is abstract, upgrading the data source to PostgreSQL is simple and only requires changing the connection string in the environment variables, avoiding database scaling blockers.
