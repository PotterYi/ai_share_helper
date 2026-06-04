"""
Task scheduler for periodic news fetching and reporting.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, time
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class Scheduler:
    """Schedule periodic news collection and reporting tasks."""

    def __init__(self, engine, daily_time: str = "09:00"):
        """
        Args:
            engine: The Engine instance to use for tasks.
            daily_time: Time for daily report (HH:MM format).
        """
        self.engine = engine
        self.daily_time = daily_time
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Parse daily time
        hour, minute = map(int, daily_time.split(":"))
        self._daily_hour = hour
        self._daily_minute = minute

    async def start(self, scrape_interval_hours: int = 4) -> None:
        """Start the scheduler with periodic scraping."""
        self._running = True
        logger.info(
            "Scheduler started: scrape every %dh, daily report at %s",
            scrape_interval_hours,
            self.daily_time,
        )

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        # Run initial collection immediately
        logger.info("Running initial collection...")
        await self.engine.run_full_pipeline()

        # Schedule periodic tasks
        while self._running:
            now = datetime.now()

            # Calculate next scrape time
            next_scrape = now.replace(
                hour=(now.hour // scrape_interval_hours + 1) * scrape_interval_hours % 24,
                minute=0, second=0, microsecond=0,
            )
            if next_scrape <= now:
                next_scrape = now.replace(
                    hour=((now.hour + scrape_interval_hours) // scrape_interval_hours)
                    * scrape_interval_hours % 24,
                    minute=0, second=0, microsecond=0,
                )

            # Calculate next daily report time
            daily_time = now.replace(
                hour=self._daily_hour, minute=self._daily_minute, second=0, microsecond=0
            )
            if daily_time <= now:
                from datetime import timedelta
                daily_time += timedelta(days=1)

            # Determine what triggers next
            next_run = min(next_scrape, daily_time)
            sleep_seconds = (next_run - now).total_seconds()

            if sleep_seconds > 0:
                logger.info(
                    "Next event at %s (%s), sleeping %.0fs...",
                    next_run.strftime("%H:%M"),
                    "daily report" if next_run == daily_time else "scrape",
                    sleep_seconds,
                )
                await asyncio.sleep(min(sleep_seconds, 3600))  # Check every hour max
                continue

            # Execute the appropriate task
            if now >= daily_time and now < daily_time.replace(minute=daily_time.minute + 1):
                logger.info("Generating daily report...")
                await self.engine.report_only()
            else:
                logger.info("Running scheduled scrape...")
                await self.engine.run_full_pipeline()

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        logger.info("Stopping scheduler...")
        self._running = False
        self.engine.close()


def run_scheduler(
    backend: str = "auto",
    analyze: bool = True,
    notify: bool = False,
    daily_time: str = "09:00",
    scrape_interval_hours: int = 4,
):
    """Entry point to run the scheduler (blocking)."""
    from .engine import Engine

    async def _run():
        engine = Engine(backend=backend, analyze=analyze, notify=notify)
        scheduler = Scheduler(engine, daily_time=daily_time)
        try:
            await scheduler.start(scrape_interval_hours=scrape_interval_hours)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")
        finally:
            await scheduler.stop()

    asyncio.run(_run())
