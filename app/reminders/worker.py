from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def reminder_loop(app) -> None:
    """Run due reminder policy periodically; database dedupe makes restarts safe."""
    interval = app.state.settings.reminder_interval_seconds
    if interval <= 0:
        logger.info("reminder_worker_disabled")
        return
    logger.info("reminder_worker_started interval_seconds=%s", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            with app.state.session_factory.begin() as db:
                created = app.state.reminder_service.run(db)
                retried = app.state.telegram_client.retry_due(db)
            logger.info(
                "reminder_worker_cycle processed=%s delivery_retries=%s",
                len(created),
                len(retried),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reminder_worker_cycle_failed")
