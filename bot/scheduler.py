"""
bot/scheduler.py
Periodic task scheduler:
- every minute: fire sensors.on_minute()
- at midnight: daily reset + optional word reset
- on Monday midnight: weekly reset
- on 1st of month midnight: monthly reset
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, sensors_list, connector_list, config):
        """
        sensors_list: list of Sensors instances (one per network)
        connector_list: list of IRCConnector instances
        """
        self.sensors_list = sensors_list
        self.connectors = connector_list
        self.cfg = config
        self._running = False
        # When each reset last ran. Kept in the database, not just in memory: otherwise restarting
        # the bot during the 00:xx hour would reset today's stats a second time.
        self._last_day = self._load("last_daily_reset")
        self._last_week = self._load("last_weekly_reset")
        self._last_month = self._load("last_monthly_reset")

    @staticmethod
    def _load(key):
        try:
            from database.models import get_state
            return get_state(key)
        except Exception:
            return None

    @staticmethod
    def _save(key, value):
        try:
            from database.models import set_state
            set_state(key, value)
        except Exception as e:
            log.error(f"Could not remember {key}: {e}")

    async def run(self):
        self._running = True
        log.info("Scheduler started.")
        while self._running:
            await asyncio.sleep(60)
            now = datetime.now()
            try:
                self._tick(now)
            except Exception as e:
                log.error(f"Scheduler tick error: {e}", exc_info=True)

    def _tick(self, now: datetime):
        # Minutely: fire on_minute for all networks
        for sensors, connector in zip(self.sensors_list, self.connectors):
            members = connector.get_channel_members()
            sensors.on_minute(members)

        # Daily reset at midnight
        today = now.date().isoformat()
        if self._last_day != today and now.hour == 0:
            for sensors in self.sensors_list:
                sensors.on_daily_reset()
            self._last_day = today
            self._save("last_daily_reset", today)

        # Weekly reset on Monday
        weekday = now.weekday()  # Monday=0
        iso = now.isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"        # the year matters: week 1 comes round every year
        if weekday == 0 and self._last_week != week and now.hour == 0:
            for sensors in self.sensors_list:
                sensors.on_weekly_reset()
            self._last_week = week
            self._save("last_weekly_reset", week)

        # Monthly reset on 1st
        month = f"{now.year}-{now.month:02d}"
        if now.day == 1 and self._last_month != month and now.hour == 0:
            for sensors in self.sensors_list:
                sensors.on_monthly_reset()
            self._last_month = month
            self._save("last_monthly_reset", month)

        log.debug(f"Tick at {now.strftime('%H:%M')}")

    def stop(self):
        self._running = False
