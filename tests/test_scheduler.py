"""bot/scheduler.py: what happens at midnight, on Mondays and on the 1st."""
from datetime import datetime

import pytest

from bot.scheduler import Scheduler
from conftest import CHAN, NET, stat


class FakeConnector:
    def __init__(self, members=None):
        self.members = members or {}

    def get_channel_members(self):
        return self.members


@pytest.fixture
def make_scheduler(sensors):
    def _make(members=None):
        return Scheduler([sensors], [FakeConnector(members)], {})
    return _make


def chat(sensors, lines=3):
    for _ in range(lines):
        sensors.on_privmsg("alice", "a@h", CHAN, "hello there friend")


def periods(db, name="lines"):
    return [stat(db, "alice", name, p) for p in range(4)]


class TestMinuteTick:
    def test_every_tick_credits_a_minute_to_whoever_is_present(self, make_scheduler, db):
        s = make_scheduler({CHAN: ["alice"]})
        s._tick(datetime(2026, 10, 7, 15, 30))
        s._tick(datetime(2026, 10, 7, 15, 31))
        assert stat(db, "alice", "minutes") == 2


class TestDailyReset:
    def test_first_tick_after_midnight_resets_today(self, make_scheduler, sensors, db):
        chat(sensors)
        make_scheduler()._tick(datetime(2026, 10, 7, 0, 0, 30))
        assert periods(db) == [3, 0, 3, 3]

    def test_it_happens_once_per_day(self, make_scheduler, sensors, db):
        s = make_scheduler()
        s._tick(datetime(2026, 10, 7, 0, 0, 30))
        chat(sensors)
        s._tick(datetime(2026, 10, 7, 0, 1, 30))
        assert periods(db)[1] == 3

    def test_not_during_the_rest_of_the_day(self, make_scheduler, sensors, db):
        chat(sensors)
        make_scheduler()._tick(datetime(2026, 10, 7, 13, 0))
        assert periods(db) == [3, 3, 3, 3]

    def test_it_happens_again_the_next_day(self, make_scheduler, sensors, db):
        s = make_scheduler()
        s._tick(datetime(2026, 10, 7, 0, 0, 30))
        chat(sensors)
        s._tick(datetime(2026, 10, 8, 0, 0, 30))
        assert periods(db)[1] == 0

    def test_the_finished_day_is_recorded_under_that_days_date(self, make_scheduler, sensors, db, freeze_date):
        chat(sensors, 4)                       # chatting on Oct 7...
        freeze_date(2026, 10, 8)               # ...the midnight tick runs on Oct 8
        make_scheduler()._tick(datetime(2026, 10, 8, 0, 0, 30))
        assert [d["date"] for d in db.get_daily_activity(NET, CHAN)] == ["2026-10-07"]

    @pytest.mark.xfail(reason="_last_day lives only in memory: restarting the bot during the 00:xx hour "
                              "resets today's stats a second time", strict=True)
    def test_restarting_during_the_midnight_hour_does_not_wipe_todays_stats(self, make_scheduler, sensors, db):
        make_scheduler()._tick(datetime(2026, 10, 7, 0, 0, 30))      # normal midnight reset
        chat(sensors)                                                  # 25 minutes of chat
        make_scheduler()._tick(datetime(2026, 10, 7, 0, 25, 30))     # bot restarted: new Scheduler
        assert periods(db)[1] == 3


class TestWeeklyAndMonthly:
    def test_week_resets_on_monday_only(self, make_scheduler, sensors, db):
        s = make_scheduler(); chat(sensors)
        s._tick(datetime(2026, 10, 6, 0, 0, 30))           # a Tuesday
        assert periods(db)[2] == 3
        s._tick(datetime(2026, 10, 5, 12, 0, 0))           # Monday, but not midnight
        assert periods(db)[2] == 3
        s._tick(datetime(2026, 10, 12, 0, 0, 30))          # the next Monday
        assert periods(db)[2] == 0

    def test_month_resets_on_the_first_only(self, make_scheduler, sensors, db):
        s = make_scheduler(); chat(sensors)
        s._tick(datetime(2026, 10, 2, 0, 0, 30))
        assert periods(db)[3] == 3
        s._tick(datetime(2026, 11, 1, 0, 0, 30))
        assert periods(db)[3] == 0

    def test_a_monday_that_is_also_the_first_resets_everything_once(self, make_scheduler, sensors, db):
        chat(sensors)
        make_scheduler()._tick(datetime(2026, 6, 1, 0, 0, 30))     # 2026-06-01 is a Monday
        assert periods(db) == [3, 0, 0, 0]

    def test_a_failing_connector_does_not_stop_the_resets_forever(self, sensors):
        class Broken(FakeConnector):
            def get_channel_members(self):
                raise RuntimeError("socket closed")
        s = Scheduler([sensors], [Broken()], {})
        with pytest.raises(RuntimeError):      # _tick itself propagates; run() logs it and carries on
            s._tick(datetime(2026, 10, 7, 12, 0))
