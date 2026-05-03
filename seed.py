"""
seed.py — Populate AfriSignal database with real African economic data
======================================================================

What this script does:
  1. Connects to your0 000l0ocal Postgres database
  2. Fetches real data from the World Bank API
  3. Runs anomaly detection on every signal
  4. Saves everything to the database
  5. Prints a summary of what was found

This is the same logic as the Celery pipeline but runs once,
synchronously, so you can see exactly what is happening step by step.
You do NOT need Celery or Redis running to use this script.
"""
import asyncio
import sys

sys.path.insert(0, ".")


async def main():
    print("\n" + "=" * 60)
    print("  AfriSignal Database Seeder")
    print("=" * 60)

    # ── 1. Connect to database ────────────────────────────────────
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.ingestion.world_bank import WorldBankClient
    from app.models.signal import MacroSignal
    from app.services.signal_detector import SignalDetector

    settings = get_settings()

    print(f"\n[1/4] Connecting to database...")
    print(f"      {settings.DATABASE_URL}")

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Quick connection test
    try:
        from sqlalchemy import text
        async with session_factory() as db:
            await db.execute(text("SELECT 1"))
        print("      Connected OK")
    except Exception as e:
        print(f"\n ERROR: Cannot connect to database.")
        print(f"  Make sure Postgres is running and your DATABASE_URL in .env is correct.")
        print(f"  Details: {e}")
        return

    # ── 2. Fetch from World Bank ──────────────────────────────────
    print(f"\n[2/4] Fetching data from World Bank API...")
    print(f"      This may take 30-60 seconds (10 countries x 6 indicators)...")

    all_signals = []
    async with WorldBankClient() as client:
        for country in ["NG", "KE", "ZA", "GH", "EG"]:  # Start with 5 countries
            for wb_code, name in [
                ("FP.CPI.TOTL.ZG", "inflation_rate"),
                ("NY.GDP.MKTP.KD.ZG", "gdp_growth"),
                ("SL.UEM.TOTL.ZS", "unemployment_rate"),
            ]:
                try:
                    signals = await client.fetch_indicator(country, wb_code)
                    all_signals.extend(signals)
                    print(f"      {country}/{name}: {len(signals)} data points")
                except Exception as e:
                    print(f"      {country}/{name}: skipped ({type(e).__name__})")

    print(f"\n      Total fetched: {len(all_signals)} signals")

    # ── 3. Save and detect anomalies ─────────────────────────────
    print(f"\n[3/4] Saving to database and running anomaly detection...")

    detector = SignalDetector()
    new_count = 0
    anomaly_count = 0
    anomalies = []

    async with session_factory() as db:
        for signal in all_signals:
            # Check for duplicates
            existing = await db.execute(
                select(MacroSignal.id).where(
                    MacroSignal.country_code == signal.country_code,
                    MacroSignal.indicator == signal.indicator,
                    MacroSignal.source == signal.source,
                    MacroSignal.observation_date == signal.observation_date,
                )
            )
            if existing.first():
                continue

            db.add(signal)
            await db.flush()
            new_count += 1

            # Run anomaly detection
            analysed = await detector.analyse(signal, db)
            if analysed.is_anomaly:
                anomaly_count += 1
                anomalies.append(analysed)

        await db.commit()
        print(f"      Saved {new_count} new signals")
        print(f"      Detected {anomaly_count} anomalies")

    # ── 4. Print summary ──────────────────────────────────────────
    print(f"\n[4/4] Summary")
    print("-" * 60)

    if anomalies:
        print(f"\n  ANOMALIES DETECTED (these will become Bayse events):")
        for s in anomalies:
            direction = "above" if (s.z_score or 0) > 0 else "below"
            rolling_mean_str = f"{s.rolling_mean:.2f}" if s.rolling_mean is not None else "N/A"
            print(
                f"  • {s.country_code} | {s.indicator} | "
                f"value={s.value:.2f} | z={s.z_score:.2f} "
                f"({direction} rolling mean of {rolling_mean_str})"
            )
    else:
        print("  No anomalies detected in this dataset.")
        print("  (This is normal — anomalies are statistically rare)")

    print(f"\n  Database now contains:")
    async with session_factory() as db:
        from sqlalchemy import func
        count = await db.execute(select(func.count()).select_from(MacroSignal))
        total = count.scalar()
        print(f"    {total} total signals")

    print(f"\n  Next steps:")
    print(f"    1. Open http://localhost:8000/docs")
    print(f"    2. Try GET /api/v1/signals — you should now see real data")
    print(f"    3. Try GET /api/v1/signals?anomalies_only=true")
    if anomalies:
        print(f"    4. Try POST /api/v1/events/generate?signal_id={anomalies[0].id}")
        print(f"       This will call Claude and draft your first prediction market event")

    print("\n" + "=" * 60)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())