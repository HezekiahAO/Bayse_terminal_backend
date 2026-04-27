# AfriSignal 🌍

> African macroeconomic signal engine for Bayse prediction markets.  
> Monitors African economic indicators, detects anomalies, and auto-generates  
> prediction market events with AI-priced opening probabilities.

---

## What it does

1. **Ingests** African macro data from World Bank, IMF, and news APIs on a cron schedule  
2. **Detects** statistical anomalies using a rolling z-score model (|z| ≥ 2.0 = flagged)  
3. **Generates** a well-formed Bayse prediction market question using Claude AI  
4. **Prices** the opening probability using a Beta-distribution Bayesian model  
5. **Publishes** signal alerts and draft events in real-time over WebSocket  
6. **Exposes** a REST API for Bayse to query approved events and display them to traders  

---

## Architecture

```
World Bank / IMF / News APIs
         │
         ▼
  Celery beat (every 6h)
         │
         ▼
  Celery worker
    ├── WorldBankClient  (httpx, async fetching)
    ├── SignalDetector   (z-score anomaly detection)
    └── EventGenerator   (Claude AI + ProbabilityPricer)
         │
         ├──► PostgreSQL  (signals, events, history)
         ├──► Redis       (job queue + pub/sub)
         │
         ▼
  FastAPI server
    ├── REST API  /api/v1/signals  /api/v1/events
    └── WebSocket /ws  ◄── Redis pub/sub fan-out
         │
         ▼
  Bayse Platform / Admin Dashboard
```

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Web framework | FastAPI | Async-native, automatic OpenAPI docs, WebSocket support |
| Task queue | Celery + Redis | Scheduled ingestion, background AI generation |
| Database | PostgreSQL + SQLAlchemy (async) | JSONB for raw payloads, composite indexes, migrations |
| Cache / Broker | Redis | Celery broker, WebSocket pub/sub fan-out |
| AI | Anthropic Claude | Event drafting + reasoning |
| Quant model | Beta distribution (scipy) | Principled probability priors |
| HTTP client | httpx | Async, retry-capable |
| Migrations | Alembic | Schema versioning |

---

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose
- An Anthropic API key (`claude-sonnet-4-20250514`)
- (Optional) A NewsAPI key for news signals

### 2. Environment setup

```bash
cp .env.example .env
# Edit .env and set:
#   ANTHROPIC_API_KEY=sk-ant-...
#   NEWS_API_KEY=...  (optional)
```

### 3. Start all services

```bash
make up
```

This starts: PostgreSQL, Redis, FastAPI server (port 8000), Celery worker, Celery beat.

### 4. Run migrations

```bash
make migrate
```

### 5. Seed with real data

```bash
make seed
```

This runs the full World Bank ingestion pipeline once immediately, populating
the DB with real African economic data and running anomaly detection.

### 6. Explore the API

Open http://localhost:8000/docs — interactive Swagger UI with all endpoints.

---

## API Reference

### Signals

```
GET  /api/v1/signals                      List all signals (paginated)
GET  /api/v1/signals?anomalies_only=true  List only anomalous signals
GET  /api/v1/signals?country_code=NG      Filter by country
GET  /api/v1/signals/{id}                 Get single signal with full detail
POST /api/v1/signals/trigger-scan         Re-run anomaly detection manually
```

### Prediction Events

```
GET  /api/v1/events                       List events (paginated)
GET  /api/v1/events?status=draft          Pending human review
GET  /api/v1/events?status=approved       Ready for Bayse
GET  /api/v1/events/{id}                  Get event with Beta params + reasoning
POST /api/v1/events/{id}/review           Approve or reject a draft event
POST /api/v1/events/generate?signal_id=  Manually trigger AI event generation
GET  /api/v1/events/{id}/probability-detail  Full quant breakdown + CI
```

### WebSocket

```
ws://localhost:8000/ws
```

Messages pushed to clients:
```json
// When an anomaly is detected
{"type": "signal_alert", "country_code": "NG", "indicator": "inflation_rate", "value": 32.7, "z_score": 2.84}

// When AI drafts a new event
{"type": "event_drafted", "event_id": "...", "title": "Will Nigeria's inflation exceed 35%...", "opening_probability": 0.62}

// Keepalive every 30s
{"type": "ping", "connections": 3}
```

---

## Key Backend Concepts Learned

### Rolling Z-Score Anomaly Detection
```python
z = (current_value - rolling_mean) / rolling_std
# |z| >= 2.0  → 95th percentile → anomaly flagged
```

### Beta Distribution Probability Pricing
```
Prior: Beta(α₀, β₀) where mean = historical_base_rate
Update: α += sqrt(z_score) * 2  (if z > 0, increases P(YES))
Opening probability = α / (α + β)
```

### Redis Fan-out WebSocket Pattern
```
Celery task → redis.publish("afrisignal:ws", payload)
FastAPI startup → asyncio.create_task(redis_listener())
redis_listener → manager.broadcast(payload) → all WS clients
```

### FastAPI Async Session Pattern
```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

## Development Commands

```bash
make up          # Start all Docker services
make down        # Stop services
make api         # Run FastAPI locally (hot reload)
make worker      # Run Celery worker locally
make beat        # Run Celery beat scheduler locally
make migrate     # Apply DB migrations
make migration NAME=add_news_signals  # Create new migration
make seed        # Populate DB with real World Bank data
make lint        # Run ruff linter
make test        # Run pytest
make logs        # Tail all Docker logs
```

---

## Run the full program

### 1. Install dependencies

```powershell
Set-Location 'd:\Code 2026\afrisignal'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 2. Start the backend services

If you have the Docker setup in place:

```powershell
make up
```

If you want to run only the FastAPI server locally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

### 3. Run the ingestion and anomaly pipeline

If you are using Docker/Celery:

```powershell
make seed
```

If you want to run the pipeline manually from Python, use the Celery task or a script that calls the ingestion flow.

### 4. What you should see

- `make up` starts Redis, PostgreSQL, the FastAPI app, and Celery services.
- `make seed` runs the ingestion pipeline once and logs:
  - how many IMF/World Bank signals were fetched
  - how many new signals were written to Postgres
  - how many anomalies were detected
- When an anomaly is found, the worker logs a message like:

```text
[Pipeline] ANOMALY: NG/inflation_rate value=32.7 z=2.84
[Pipeline] Queued event generation for signal 123
```

- The FastAPI server will be available at:

```
http://localhost:8000
```

- The Swagger docs will be available at:

```
http://localhost:8000/docs
```

### 5. Expected API response

If the app is running, the health endpoint returns:

```json
{
  "status": "ok",
  "service": "AfriSignal",
  "version": "1.0.0",
  "environment": "development"
}
```

The `/api/v1/signals` endpoint returns a JSON list of signals:

```json
{
  "signals": []
}
```

The `/api/v1/events` endpoint returns a JSON list of events:

```json
{
  "events": []
}
```

---

## Project Structure

```
afrisignal/
├── app/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Pydantic settings
│   ├── database.py                # Async SQLAlchemy engine + session
│   ├── models/
│   │   ├── signal.py              # MacroSignal ORM model
│   │   └── event.py               # PredictionEvent ORM model
│   ├── schemas/
│   │   ├── signal.py              # Pydantic request/response schemas
│   │   └── event.py               # Pydantic request/response schemas
│   ├── services/
│   │   ├── signal_detector.py     # Z-score anomaly detection
│   │   ├── probability_pricer.py  # Beta distribution pricing
│   │   └── event_generator.py     # Claude AI event drafting
│   ├── ingestion/
│   │   └── world_bank.py          # World Bank API client
│   ├── api/
│   │   ├── websocket.py           # WebSocket + Redis listener
│   │   └── v1/
│   │       ├── signals.py         # Signals REST endpoints
│   │       ├── events.py          # Events REST endpoints
│   │       └── router.py          # API v1 router
│   └── worker/
│       └── tasks.py               # Celery tasks + beat schedule
├── alembic/
│   ├── env.py                     # Alembic async config
│   └── versions/
│       └── 001_initial.py         # Initial schema migration
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── requirements.txt
└── .env.example
```
