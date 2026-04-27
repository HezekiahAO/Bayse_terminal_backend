"""
AfriSignal — Main FastAPI Application
======================================
Entry point that:
  - Configures the FastAPI app (CORS, docs, versioning)
  - Registers all routers (REST API + WebSocket)
  - Starts the Redis listener and ping tasks on startup
  - Provides a health check endpoint
"""
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as v1_router
from app.api.websocket import periodic_ping, redis_listener, router as ws_router
from app.config import get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

# ── App instance ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="AfriSignal",
    description=(
        "African macroeconomic signal engine for Bayse prediction markets. "
        "Monitors African economic indicators, detects anomalies, and auto-generates "
        "prediction market events with AI-priced opening probabilities."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(v1_router)    # REST: /api/v1/signals, /api/v1/events
app.include_router(ws_router)    # WebSocket: /ws


# ── Lifecycle ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    logger.info("AfriSignal starting up...")
    # Start background tasks in the event loop
    asyncio.create_task(redis_listener())  # Redis → WebSocket fan-out
    asyncio.create_task(periodic_ping())   # 30s keepalive pings
    logger.info("Background tasks started: redis_listener, periodic_ping")


@app.on_event("shutdown")
async def shutdown():
    logger.info("AfriSignal shutting down...")


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health():
    """
    Simple liveness probe for load balancers and container orchestration.
    For a full readiness check you would also ping the DB and Redis here.
    """
    return {
        "status": "ok",
        "service": "AfriSignal",
        "version": "1.0.0",
        "environment": settings.APP_ENV,
    }


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "AfriSignal",
        "docs": "/docs",
        "websocket": "ws://localhost:8000/ws",
        "api": "/api/v1",
    }
