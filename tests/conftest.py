"""Shared fixtures. Every test gets its own throw-away SQLite file, so the real
data/stats.db is never touched and tests cannot influence each other."""
import datetime

import pytest

from database import models

NET = "Net"
CHAN = "#chan"


def make_config(**overrides):
    cfg = {
        "bot": {"nick": "StatsBot", "altnick": "StatsBot_"},
        "stats": {
            "happy_smileys": [":)", ":D", ":-)"],
            "sad_smileys": [":(", ":'(", ":-("],
            "quote_frequency": 5,
            "kick_context": 5,
            "log_wordstats": True,
        },
        "pisg": {
            "WordLength": 4,
            "UrlHistory": 10,
            "ViolentWords": ["slaps", "kicks"],
            "FoulWords": ["damn"],
        },
        "commands": {"prefix": "!", "max_cmds": 5, "max_cmds_window": 60},
    }
    for section, values in overrides.items():
        cfg.setdefault(section, {}).update(values)
    return cfg


@pytest.fixture
def db(tmp_path):
    """A fresh, initialised database. Returns the models module."""
    models.set_db_path(str(tmp_path / "data" / "stats.db"))
    models.init_db()
    return models


@pytest.fixture
def cfg():
    return make_config()


@pytest.fixture
def sensors(db, cfg):
    from bot.sensors import Sensors
    return Sensors(cfg, NET)


@pytest.fixture
def freeze_date(monkeypatch):
    """freeze_date(2026, 10, 2) makes datetime.date.today() return that day."""
    def _freeze(year, month, day):
        class FakeDate(datetime.date):
            @classmethod
            def today(cls):
                return cls(year, month, day)
        monkeypatch.setattr(datetime, "date", FakeDate)
    return _freeze


def stat(db, nick, name, period=0, network=NET, channel=CHAN):
    """Value of one stats column for a nick (0 if the nick does not exist)."""
    nick_id = db.get_or_create_nick(nick, network, channel)
    return db.get_stats(nick_id, period)[name]


def count_rows(db, table, where="1=1", params=()):
    with db.get_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]
