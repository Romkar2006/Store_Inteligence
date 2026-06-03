import os
from dotenv import load_dotenv
# Load .env file robustly from project root using absolute path relative to this file
_current_dir = os.path.dirname(os.path.abspath(__file__))
_dotenv_path = os.path.join(_current_dir, "..", ".env")
load_dotenv(_dotenv_path)
import time
import uuid
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog
from sqlalchemy.exc import OperationalError, InterfaceError

from app.db import init_db

# Configure standard logging to not conflict with structlog
logging.basicConfig(level=logging.INFO)

# Configure structlog for structured JSON logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    wrapper_class=structlog.BoundLogger,
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize database tables and load POS CSV
    try:
        init_db()
    except Exception as e:
        logger.error("Failed to initialize database", error=str(e))
    yield
    # Shutdown: clean up resources if needed

app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for all origins for demo (complying with specs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom structured JSON logging middleware
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    
    start_time = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - start_time) * 1000)
    
    # Extract store_id from path if available
    path_params = request.path_params
    store_id = path_params.get("store_id", "N/A")
    
    # Log information in structured format
    log_data = {
        "trace_id": trace_id,
        "endpoint": request.url.path,
        "method": request.method,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "store_id": store_id
    }
    
    # If this is the ingestion endpoint, try to read the event count
    if request.url.path == "/events/ingest" and request.method == "POST":
        log_data["event_count"] = getattr(request.state, "event_count", 0)
        log_data["accepted"] = getattr(request.state, "accepted", 0)
        log_data["rejected"] = getattr(request.state, "rejected", 0)

    logger.info("API request completed", **log_data)
    
    response.headers["X-Trace-ID"] = trace_id
    return response

from fastapi import WebSocket, WebSocketDisconnect
from app.websocket import manager

@app.websocket("/stores/{store_id}/ws")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await manager.connect(websocket, store_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, store_id)
    except Exception as e:
        logger.error("WebSocket connection error", store_id=store_id, error=str(e))
        manager.disconnect(websocket, store_id)

# Global Exception Handler (Correction 3 & specification check)
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", str(uuid.uuid4()))
    logger.error("Unhandled exception occurred", trace_id=trace_id, error=str(exc), exc_info=True)
    
    # Return 503 Service Unavailable for database/critical errors
    if isinstance(exc, (OperationalError, InterfaceError)) or "database" in str(exc).lower():
        return JSONResponse(
            status_code=503,
            content={
                "status": "DEGRADED",
                "db_connected": False,
                "error": "Database unavailable"
            }
        )
        
    return JSONResponse(
        status_code=500,
        content={
            "status": "ERROR",
            "trace_id": trace_id,
            "error": "An internal server error occurred"
        }
    )

# Import routers after app initialization to prevent circular dependencies
from app.ingestion import router as ingestion_router
from app.metrics import router as metrics_router
from app.funnel import router as funnel_router
from app.heatmap import router as heatmap_router
from app.anomalies import router as anomalies_router
from app.health import router as health_router
from app.ask import router as ask_router
from app.flow import router as flow_router

# Register routers
app.include_router(ingestion_router)
app.include_router(metrics_router)
app.include_router(funnel_router)
app.include_router(heatmap_router)
app.include_router(anomalies_router)
app.include_router(health_router)
app.include_router(ask_router)
app.include_router(flow_router)
