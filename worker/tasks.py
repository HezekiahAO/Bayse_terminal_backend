"""
Celery App & Tasks
==================
Celery powers AfriSignal's background processing.

How Celery works in 3 sentences:
  "Beat" is a scheduler — it wakes up at configured times and puts task
  messages into a Redis queue. "Workers" are processes that watch that queue,
  pick up tasks, and execute them. Redis sits in the middle as the message
  broker that connects beat to workers.

Why we use asyncio.run() inside synchronous Celery tasks:
  Celery tasks are synchronous by default. Our database layer (SQLAlchemy)
  and HTTP clients (httpx) are async. We bridge them with asyncio.run(),
  which creates a fresh event loop, runs our async code to completion,
  then returns to Celery. This is simple and correct for our use case.
  (Production systems with very high task volume use celery-pool-asyncio instead.)

Task anatomy:
  @celery_app.task(bind=True)  <- bind=True gives us `self` (the task instance)
  def my_task(self, arg):
      try:
          result = do_work(arg)
          return result
      except Exception as exc:
          raise self.retry(exc=exc)  <- Celery will re-queue and retry

Beat schedule (cron syntax):
  crontab(minute=0, hour="*/6") means "at minute 0 of every 6th hour"
  = runs at 00:00, 06:00, 12:00, 18:00 UTC every day
"""
import asyncio
import json

import redis as sync_redis
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

# ── Celery instance ───────────────────────────────────────────────────────────
#
# We pass the Redis URL as both broker and backend:
#   broker  = where task messages are queued (beat -> worker)
#   backend = where task results are stored  (optional, used for monitoring)
#
celery_app = Celery(
    "afrisignal",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",           # Tasks are serialised as JSON (safe, readable)
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,          # Tasks emit a "started" event (useful for monitoring)
    task_acks_late=True,              # Only acknowledge (remove from queue) AFTER completion
                                      # If the worker crashes mid-task, the task re-queues
    worker_prefetch_multiplier=1,     # Worker only grabs 1 task at a time
                                      # Prevents one slow task starving other workers
)

# ── Beat schedule ─────────────────────────────────────────────────────────────
#
# This dict tells Celery Beat which tasks to schedule and when.
# Beat reads this on startup and sleeps/wakes accordingly.
#
celery_app.conf.beat_schedule = {
    # Run the full ingestion pipeline every 6 hours
    "ingest-african-macro-data": {
        "task": "app.worker.tasks.run_ingestion_pipeline",
        "schedule": crontab(minute=0, hour="*/6"),
    },
}


# ── Task 1: Main ingestion pipeline ──────────────────────────────────────────

@celery_app.task(
    name="app.worker.tasks.run_ingestion_pipeline",
    bind=True,
    max_retries=2,            # Try up to 3 times total (1 original + 2 retries)
    default_retry_delay=60,   # Wait 60 seconds between retries
)
def run_ingestion_pipeline(self):
    """
    Master pipeline task. Runs every 6 hours.

    Orchestrates:
      1. Fetch signals from World Bank  (annual data, broad indicators)
      2. Fetch signals from IMF         (monthly data, financial indicators)
      3. Deduplicate against what is already in Postgres
      4. Save new signals to Postgres
      5. Run anomaly detection on each new signal
      6. For each anomaly: publish WS alert + queue event generation task
    """

    async def _run() -> dict:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.ingestion.imf import IMFClient
        from app.ingestion.world_bank import WorldBankClient
        from app.models.signal import MacroSignal
        from app.services.signal_detector import SignalDetector

        engine = create_async_engine(settings.DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        detector = SignalDetector()

        # ── Step 1 & 2: Fetch from all sources ───────────────────────────────
        #
        # We fetch from both sources before opening a DB session so we can
        # batch all the DB writes together efficiently.
        # Both clients are used as async context managers so HTTP connections
        # are properly closed afterward (even if an exception occurs).

        all_signals = []

        print("[Pipeline] Fetching World Bank signals...")
        async with WorldBankClient() as wb_client:
            wb_signals = await wb_client.fetch_all_african_signals()
            all_signals.extend(wb_signals)
            print(f"[Pipeline] World Bank: {len(wb_signals)} signals fetched")

        print("[Pipeline] Fetching IMF signals...")
        async with IMFClient() as imf_client:
            imf_signals = await imf_client.fetch_all_african_signals()
            all_signals.extend(imf_signals)
            print(f"[Pipeline] IMF: {len(imf_signals)} signals fetched")

        print(f"[Pipeline] Total signals to process: {len(all_signals)}")

        # ── Step 3, 4, 5: Deduplicate, save, detect anomalies ────────────────
        #
        # We open ONE database session for all signals. This means all the
        # inserts happen in a single transaction that commits at the end.
        # If something fails halfway, nothing is committed (atomicity).

        anomaly_signal_ids: list[int] = []
        new_signal_count = 0

        async with session_factory() as db:
            for signal in all_signals:

                # ── Deduplication check ───────────────────────────────────────
                #
                # We define a "duplicate" as: same country + indicator + source
                # + observation_date. This is the natural composite key.
                #
                existing = await db.execute(
                    select(MacroSignal.id).where(
                        MacroSignal.country_code == signal.country_code,
                        MacroSignal.indicator == signal.indicator,
                        MacroSignal.source == signal.source,
                        MacroSignal.observation_date == signal.observation_date,
                    )
                )
                if existing.first():
                    continue  # Skip -- we already have this exact data point

                # ── Persist the raw signal ────────────────────────────────────
                #
                # db.add() stages the object for insertion.
                # db.flush() sends the INSERT to Postgres without committing.
                # We need flush() before analyse() so the signal has an ID
                # and is visible to the historical lookup query inside analyse().
                #
                db.add(signal)
                await db.flush()
                new_signal_count += 1

                # ── Run anomaly detection ─────────────────────────────────────
                #
                # analyse() fetches the last 30 data points for this
                # country/indicator combination from Postgres, computes the
                # rolling z-score, and updates the signal's fields in-place.
                #
                analysed = await detector.analyse(signal, db)

                if analysed.is_anomaly:
                    anomaly_signal_ids.append(signal.id)
                    print(
                        f"[Pipeline] ANOMALY: {signal.country_code}/{signal.indicator} "
                        f"value={signal.value} z={signal.z_score:.2f}"
                    )
                    # Best-effort WebSocket alert (failure here does not stop pipeline)
                    _publish_ws_alert(signal, analysed.z_score)

            # ── Commit all new signals in one transaction ─────────────────────
            await db.commit()

        await engine.dispose()

        return {
            "signals_fetched": len(all_signals),
            "signals_new": new_signal_count,
            "anomalies_found": len(anomaly_signal_ids),
            "anomaly_ids": anomaly_signal_ids,  # <-- fixed: was "anomalies" before
        }

    try:
        result = asyncio.run(_run())

        # ── Step 6: Queue event generation for each anomaly ──────────────────
        #
        # We do this AFTER asyncio.run() returns because Celery's .delay()
        # is synchronous -- it just puts a message in Redis.
        # Each anomaly gets its own independent task so failures are isolated.
        #
        for signal_id in result.get("anomaly_ids", []):
            generate_event_for_signal.delay(signal_id)
            print(f"[Pipeline] Queued event generation for signal {signal_id}")

        print(
            f"[Pipeline] Complete. "
            f"Fetched={result['signals_fetched']} "
            f"New={result['signals_new']} "
            f"Anomalies={result['anomalies_found']}"
        )
        return result

    except Exception as exc:
        print(f"[Pipeline] Failed: {exc}")
        raise self.retry(exc=exc)


# ── Task 2: AI event generation ───────────────────────────────────────────────

@celery_app.task(
    name="app.worker.tasks.generate_event_for_signal",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def generate_event_for_signal(self, signal_id: int):
    """
    Generate a Bayse prediction market event for one anomalous signal.

    This is a separate task from the pipeline so that:
      - Claude API failures do not affect the ingestion pipeline
      - Each event generation can be retried independently
      - We can manually trigger event generation for any signal via the REST API

    Args:
        signal_id: integer primary key of the MacroSignal to generate an event for
    """

    async def _run() -> dict:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.models.signal import MacroSignal
        from app.services.event_generator import EventGenerator

        engine = create_async_engine(settings.DATABASE_URL, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        generator = EventGenerator()

        async with session_factory() as db:
            result = await db.execute(
                select(MacroSignal).where(
                    MacroSignal.id == signal_id
                )
            )
            signal = result.scalar_one_or_none()

            if not signal:
                print(f"[EventGen] Signal {signal_id} not found -- skipping")
                return {"error": f"Signal {signal_id} not found"}

            print(
                f"[EventGen] Generating event for {signal.country_code}/"
                f"{signal.indicator} (z={signal.z_score:.2f if signal.z_score else 'N/A'})"
            )

            event = await generator.generate(signal, db)
            await db.commit()

            print(f"[EventGen] Created draft event: '{event.title[:80]}'")
            print(f"[EventGen] Opening probability: {event.opening_probability:.1%}")

            _publish_ws_event_drafted(
                str(event.id),
                event.title,
                event.opening_probability,
                event.country_code,
            )

            return {"event_id": str(event.id), "title": event.title}

    try:
        return asyncio.run(_run())
    except Exception as exc:
        print(f"[EventGen] Failed for signal {signal_id}: {exc}")
        raise self.retry(exc=exc)


# ── Redis pub/sub helpers ─────────────────────────────────────────────────────
#
# These functions publish messages to a Redis pub/sub channel.
# The FastAPI WebSocket server has a background coroutine subscribed
# to this channel that forwards every message to all connected WS clients.
#
# We use the synchronous redis client here (not aioredis) because these
# helpers are called from within synchronous Celery tasks.
#
# "Best effort" means: if Redis is down, we log and move on.
# We never want a WebSocket notification failure to crash the ingestion pipeline.

REDIS_WS_CHANNEL = "afrisignal:ws"


def _get_redis() -> sync_redis.Redis:
    """Get a synchronous Redis connection. Used only within Celery tasks."""
    return sync_redis.from_url(settings.REDIS_URL, decode_responses=True)


def _publish_ws_alert(signal, z_score: float | None) -> None:
    """Publish a signal anomaly alert to all connected WebSocket clients."""
    try:
        r = _get_redis()
        payload = json.dumps({
            "type": "signal_alert",
            "signal_id": str(signal.id),
            "country_code": signal.country_code,
            "indicator": signal.indicator,
            "value": signal.value,
            "z_score": round(z_score or 0.0, 3),
            "source": signal.source.value,
            "message": (
                f"Anomaly detected: {signal.country_code} {signal.indicator} "
                f"= {signal.value} "
                f"(z={z_score:.2f if z_score else 'N/A'})"
            ),
        })
        r.publish(REDIS_WS_CHANNEL, payload)
    except Exception as exc:
        print(f"[WS] Failed to publish signal alert: {exc}")


def _publish_ws_event_drafted(
    event_id: str, title: str, prob: float, country: str
) -> None:
    """Publish a new draft event notification to all connected WebSocket clients."""
    try:
        r = _get_redis()
        payload = json.dumps({
            "type": "event_drafted",
            "event_id": event_id,
            "title": title,
            "opening_probability": prob,
            "country_code": country,
        })
        r.publish(REDIS_WS_CHANNEL, payload)
    except Exception as exc:
        print(f"[WS] Failed to publish event drafted: {exc}")
