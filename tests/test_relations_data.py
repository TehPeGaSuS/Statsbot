"""Who talks to whom: recording pairs (models + sensors)."""
import time

import pytest

from conftest import CHAN, NET, count_rows


def join(sensors, *nicks, channel=CHAN):
    for n in nicks:
        sensors.on_join(n, f"{n}@host", channel)


def say(sensors, nick, text, channel=CHAN):
    sensors.on_privmsg(nick, f"{nick}@host", channel, text)


def pair(db, a, b, period=0, channel=CHAN, network=NET):
    for row in db.get_pairs(network, channel, period):
        if row["from_nick"].lower() == a.lower() and row["to_nick"].lower() == b.lower():
            return row["n"]
    return 0


class TestRecording:
    def test_a_mention_counts_for_that_direction_only(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "hey bob, how are you")
        assert pair(db, "alice", "bob") == 1 and pair(db, "bob", "alice") == 0

    def test_one_line_counts_once_however_often_the_nick_appears(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "bob bob bob bob")
        assert pair(db, "alice", "bob") == 1

    def test_several_people_in_one_line(self, sensors, db):
        join(sensors, "alice", "bob", "carol")
        say(sensors, "alice", "bob: and carol: hello")
        assert pair(db, "alice", "bob") == 1 and pair(db, "alice", "carol") == 1

    def test_mentioning_yourself_is_not_a_pair(self, sensors, db):
        join(sensors, "alice")
        say(sensors, "alice", "alice is talking to herself")
        assert db.get_pairs(NET, CHAN) == []

    def test_only_people_in_the_channel_count(self, sensors, db):
        join(sensors, "alice")
        say(sensors, "alice", "ghost: are you there")
        assert db.get_pairs(NET, CHAN) == []

    def test_casing_does_not_matter(self, sensors, db):
        join(sensors, "alice", "Bob")
        say(sensors, "alice", "BOB: hi"); say(sensors, "alice", "bob: hi")
        assert pair(db, "alice", "bob") == 2

    def test_an_ignored_person_is_left_out(self, sensors, db):
        join(sensors, "alice", "robot")
        db.add_ignore("robot", NET, "*")
        say(sensors, "alice", "robot: help")
        assert db.get_pairs(NET, CHAN) == []

    def test_lines_of_an_ignored_speaker_are_not_recorded(self, sensors, db):
        join(sensors, "alice", "bob")
        db.add_ignore("alice", NET, "*")
        say(sensors, "alice", "bob: hi")
        assert db.get_pairs(NET, CHAN) == []

    def test_command_lines_are_not_conversation(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "!karma bob")
        assert db.get_pairs(NET, CHAN) == []

    def test_channels_and_networks_are_kept_apart(self, sensors, db, cfg):
        from bot.sensors import Sensors
        join(sensors, "alice", "bob"); join(sensors, "alice", "bob", channel="#other")
        say(sensors, "alice", "bob: hi")
        assert pair(db, "alice", "bob", channel="#other") == 0
        other = Sensors(cfg, "OtherNet"); other.on_join("alice", "a@h", CHAN); other.on_join("bob", "b@h", CHAN)
        other.on_privmsg("alice", "a@h", CHAN, "bob: hi")
        assert pair(db, "alice", "bob", network="OtherNet") == 1 and pair(db, "alice", "bob") == 1


class TestPeriods:
    def test_every_period_counts(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "bob: hi")
        assert [pair(db, "alice", "bob", p) for p in range(4)] == [1, 1, 1, 1]

    def test_resets_clear_only_their_own_period(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "bob: hi")
        sensors.on_weekly_reset()
        assert [pair(db, "alice", "bob", p) for p in range(4)] == [1, 1, 0, 1]
        sensors.on_daily_reset(); sensors.on_monthly_reset()
        assert [pair(db, "alice", "bob", p) for p in range(4)] == [1, 0, 0, 0]

    def test_an_unknown_period_is_refused(self, db):
        with pytest.raises(ValueError):
            db.get_pairs(NET, CHAN, 7)


class TestBoundedGrowth:
    def test_get_pairs_is_limited_and_heaviest_first(self, db):
        for i in range(50):
            for _ in range(i + 1):
                db.add_pair(NET, CHAN, "a", f"b{i:02d}")
        rows = db.get_pairs(NET, CHAN, 0, limit=5)
        assert [r["to_nick"] for r in rows] == ["b49", "b48", "b47", "b46", "b45"]

    def test_old_one_off_pairs_are_forgotten_but_strong_and_recent_ones_stay(self, db):
        db.add_pair(NET, CHAN, "a", "old_once")
        db.add_pair(NET, CHAN, "a", "recent_once")
        for _ in range(5):
            db.add_pair(NET, CHAN, "a", "old_strong")
        long_ago = int(time.time()) - 200 * 86400
        with db.get_conn() as c:
            c.execute("UPDATE nick_pairs SET last_at=? WHERE to_nick IN ('old_once','old_strong')", (long_ago,))
        assert db.prune_pairs(keep_days=60) == 1
        assert {r["to_nick"] for r in db.get_pairs(NET, CHAN)} == {"recent_once", "old_strong"}

    def test_the_daily_reset_prunes(self, sensors, db):
        db.add_pair(NET, CHAN, "a", "b")
        with db.get_conn() as c:
            c.execute("UPDATE nick_pairs SET last_at=1")
        sensors.on_daily_reset()
        assert count_rows(db, "nick_pairs") == 0


class TestCleanup:
    def test_purging_a_nick_removes_it_in_both_directions(self, db):
        db.add_pair(NET, CHAN, "spam", "alice"); db.add_pair(NET, CHAN, "alice", "spam"); db.add_pair(NET, CHAN, "alice", "bob")
        db.get_or_create_nick("spam", NET, CHAN)
        db.delete_nick_stats(NET, "spam", channel=CHAN)
        assert [(r["from_nick"], r["to_nick"]) for r in db.get_pairs(NET, CHAN)] == [("alice", "bob")]

    def test_deleting_a_channel_or_network_removes_its_pairs(self, db):
        db.add_channel(NET, CHAN); db.add_pair(NET, CHAN, "a", "b"); db.add_pair(NET, "#keep", "a", "b")
        db.delete_channel(NET, CHAN)
        assert count_rows(db, "nick_pairs", "channel=?", (CHAN,)) == 0 and count_rows(db, "nick_pairs") == 1
        db.add_network(NET, "irc.example.org"); db.delete_network(NET)
        assert count_rows(db, "nick_pairs") == 0
