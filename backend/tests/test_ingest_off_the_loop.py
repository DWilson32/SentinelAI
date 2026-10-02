"""Storing an ingest runs off the event loop, so the server keeps answering meanwhile."""

import asyncio
import time

from app.services.ingestion_service import ingestion_service


def test_the_event_loop_keeps_running_while_an_ingest_is_stored(monkeypatch):
    def slow_store(db, provider, sources):
        time.sleep(0.5)  # scoring and embedding a batch, which took over a minute on the free server
        return f"stored {len(sources)}"

    monkeypatch.setattr(ingestion_service, "_persist_sources", slow_store)

    async def scenario():
        ticks = 0

        async def health_checks():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        checker = asyncio.create_task(health_checks())
        result = await ingestion_service.ingest_mock(db=None)
        checker.cancel()
        return result, ticks

    result, ticks = asyncio.run(scenario())

    assert result == "stored 2"
    # About 25 in half a second; none if storing blocked the loop, as it did.
    assert ticks >= 10
