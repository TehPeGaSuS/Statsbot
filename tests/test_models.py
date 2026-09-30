"""database/models.py against a real (temporary) SQLite file."""
import datetime

import pytest

from conftest import CHAN, NET, count_rows, stat


class TestNicks:
    def test_same_nick_returns_same_id(self, db):
        assert db.get_or_create_nick("alice", NET, CHAN) == db.get_or_create_nick("alice", NET, CHAN)

    def test_nicks_are_case_insensitive(self, db):
        assert db.get_or_create_nick("Alice", NET, CHAN) == db.get_or_create_nick("aLICE", NET, CHAN)

    def test_channel_names_are_case_insensitive(self, db):
        assert db.get_or_create_nick("a", NET, "#Chan") == db.get_or_create_nick("a", NET, "#chan")

    def test_same_nick_on_two_networks_is_two_people(self, db):
        assert db.get_or_create_nick("alice", "A", CHAN) != db.get_or_create_nick("alice", "B", CHAN)

    def test_new_nick_has_a_row_for_every_period(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        assert [db.get_stats(nid, p) is not None for p in range(4)] == [True] * 4

    def test_host_is_kept_when_a_later_call_has_none(self, db):
        db.get_or_create_nick("alice", NET, CHAN, "a@host")
        db.get_or_create_nick("alice", NET, CHAN)
        assert db.get_nick_all_stats("alice", NET, CHAN)["last_host"] == "a@host"

    def test_expire_nicks_removes_only_old_ones(self, db):
        old = db.get_or_create_nick("old", NET, CHAN)
        db.get_or_create_nick("new", NET, CHAN)
        with db.get_conn() as c:
            c.execute("UPDATE nicks SET last_seen=? WHERE id=?", (1, old))
        db.expire_nicks(30)
        assert count_rows(db, "nicks") == 1


class TestIncrement:
    def test_increment_hits_every_period(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr(nid, "words", 7)
        assert [db.get_stats(nid, p)["words"] for p in range(4)] == [7, 7, 7, 7]

    def test_increment_accumulates(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr(nid, "lines"); db.incr(nid, "lines", 2)
        assert db.get_stats(nid, 0)["lines"] == 3

    def test_unknown_stat_is_rejected_not_executed(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        with pytest.raises(ValueError):
            db.incr(nid, "words=0; DROP TABLE stats; --")
        assert count_rows(db, "stats") == 4

    def test_lines_feed_the_hourly_activity_of_every_period(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr(nid, "lines", 2)
        with db.get_conn() as c:
            rows = c.execute("SELECT period, lines FROM hourly_activity WHERE nick_id=?", (nid,)).fetchall()
        assert sorted((r["period"], r["lines"]) for r in rows) == [(0, 2), (1, 2), (2, 2), (3, 2)]


class TestTopAndRank:
    def _fill(self, db):
        for nick, lines in (("a", 30), ("b", 20), ("c", 20), ("d", 5)):
            db.incr(db.get_or_create_nick(nick, NET, CHAN), "lines", lines)

    def test_order_and_limit(self, db):
        self._fill(db)
        top = db.get_top(NET, CHAN, "lines", 0, 2)
        assert [r["nick"] for r in top][0] == "a" and len(top) == 2

    def test_other_channel_and_network_do_not_leak_in(self, db):
        self._fill(db)
        db.incr(db.get_or_create_nick("x", "Other", CHAN), "lines", 999)
        db.incr(db.get_or_create_nick("y", NET, "#other"), "lines", 999)
        assert [r["nick"] for r in db.get_top(NET, CHAN, "lines", 0, 10)][0] == "a"

    def test_unknown_stat_is_rejected(self, db):
        with pytest.raises(ValueError):
            db.get_top(NET, CHAN, "nope")

    def test_words_per_line_needs_more_than_five_lines(self, db):
        db.incr(db.get_or_create_nick("chatty", NET, CHAN), "lines", 10)
        db.incr(db.get_or_create_nick("chatty", NET, CHAN), "words", 100)
        db.incr(db.get_or_create_nick("tiny", NET, CHAN), "lines", 1)
        db.incr(db.get_or_create_nick("tiny", NET, CHAN), "words", 500)
        assert [r["nick"] for r in db.get_top(NET, CHAN, "wpl", 0, 10)] == ["chatty"]

    def test_rank_and_ties(self, db):
        self._fill(db)
        assert db.get_rank("a", NET, CHAN, "lines")[0] == 1
        assert db.get_rank("b", NET, CHAN, "lines")[0] == db.get_rank("c", NET, CHAN, "lines")[0] == 2
        assert db.get_rank("d", NET, CHAN, "lines") == (4, 4)

    def test_rank_of_unknown_nick(self, db):
        assert db.get_rank("ghost", NET, CHAN, "lines") == (0, 0)


class TestWordStats:
    def test_words_are_counted_case_insensitively_and_display_keeps_last_casing(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr_word(nid, NET, CHAN, "Hello", nick="alice")
        db.incr_word(nid, NET, CHAN, "hello", nick="alice")
        assert db.get_top_words_nick(nid) == [{"word": "hello", "count": 2}]
        top = db.get_top_words_channel(NET, CHAN)
        assert len(top) == 1 and top[0]["count"] == 2 and top[0]["word"] == "hello"

    def test_vocabulary_size(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        for w in ("one", "two", "one"):
            db.incr_word(nid, NET, CHAN, w)
        assert db.get_vocables(nid) == 2


class TestIgnores:
    def test_nick_glob(self, db):
        db.add_ignore("bot*", NET, "*")
        assert db.is_ignored("botty", NET, "x@y", CHAN)
        assert not db.is_ignored("alice", NET, "x@y", CHAN)

    def test_matching_is_case_insensitive(self, db):
        db.add_ignore("BadGuy", NET, "*")
        assert db.is_ignored("badguy", NET, "x@y", CHAN)

    def test_full_mask_needs_the_host(self, db):
        db.add_ignore("*!*@evil.example", NET, "*")
        assert db.is_ignored("anyone", NET, "u@evil.example", CHAN)
        assert not db.is_ignored("anyone", NET, "u@good.example", CHAN)
        assert not db.is_ignored("anyone", NET, None, CHAN)

    def test_channel_scoped_ignore_only_applies_there(self, db):
        db.add_ignore("bob", NET, "#a")
        assert db.is_ignored("bob", NET, None, "#a")
        assert not db.is_ignored("bob", NET, None, "#b")

    def test_network_scoped_ignore_does_not_cross_networks(self, db):
        db.add_ignore("bob", "A", "*")
        assert db.is_ignored("bob", "A", None, CHAN)
        assert not db.is_ignored("bob", "B", None, CHAN)

    def test_removing_an_ignore(self, db):
        db.add_ignore("bob", NET, "*")
        db.del_ignore("bob", NET, "*")
        assert not db.is_ignored("bob", NET, None, CHAN)

    def test_ignore_without_channel_context_only_sees_network_wide_entries(self, db):
        db.add_ignore("bob", NET, "#a")
        assert not db.is_ignored("bob", NET)


class TestKarma:
    def test_change_and_read(self, db):
        db.change_karma(NET, CHAN, "bob", +1); db.change_karma(NET, CHAN, "bob", +1); db.change_karma(NET, CHAN, "bob", -1)
        assert db.get_karma_nick(NET, CHAN, "BOB") == 1

    def test_unknown_nick_is_zero(self, db):
        assert db.get_karma_nick(NET, CHAN, "ghost") == 0

    def test_top_and_bottom_are_ranked_and_leave_out_zero(self, db):
        db.change_karma(NET, CHAN, "up", +2)
        db.change_karma(NET, CHAN, "down", -3)
        db.change_karma(NET, CHAN, "even", +1); db.change_karma(NET, CHAN, "even", -1)
        # the page shows one ranking (best first, negatives at the end) plus the worst list
        assert [r["nick"] for r in db.get_karma_top(NET, CHAN)] == ["up", "down"]
        assert [r["nick"] for r in db.get_karma_bottom(NET, CHAN)] == ["down", "up"]

    def test_channels_and_networks_are_separate(self, db):
        db.change_karma(NET, "#a", "bob", +1)
        assert db.get_karma_nick(NET, "#b", "bob") == 0
        assert db.get_karma_nick("Other", "#a", "bob") == 0


class TestPeak:
    def test_peak_only_goes_up(self, db):
        db.update_peak(NET, CHAN, 0, 10); db.update_peak(NET, CHAN, 0, 4)
        assert db.get_peak(NET, CHAN)["peak"] == 10

    def test_peak_records_the_time_of_the_record(self, db):
        db.update_peak(NET, CHAN, 0, 3)
        first = db.get_peak(NET, CHAN)["peak_at"]
        db.update_peak(NET, CHAN, 0, 2)
        assert db.get_peak(NET, CHAN)["peak_at"] == first

    def test_no_peak_yet(self, db):
        assert db.get_peak(NET, CHAN) == {"peak": 0, "peak_at": 0}


class TestChanlog:
    def test_keeps_only_the_last_100_lines_per_channel(self, db):
        for i in range(120):
            db.add_chanlog(NET, CHAN, "a", f"line {i}")
        db.add_chanlog(NET, "#other", "a", "untouched")
        assert count_rows(db, "chanlog", "channel=?", (CHAN,)) == 100
        assert count_rows(db, "chanlog", "channel=?", ("#other",)) == 1

    def test_returns_oldest_first(self, db):
        for i in range(3):
            db.add_chanlog(NET, CHAN, "a", f"line {i}")
        assert [r["line"] for r in db.get_chanlog(NET, CHAN, 10)] == ["line 0", "line 1", "line 2"]


class TestPeriodResets:
    def test_reset_clears_only_that_period(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr(nid, "lines", 4)
        db.reset_period(1)
        assert [db.get_stats(nid, p)["lines"] for p in range(4)] == [4, 0, 4, 4]

    def test_reset_clears_that_periods_hourly_activity_only(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr(nid, "lines", 4)
        db.reset_period(2)
        with db.get_conn() as c:
            periods = sorted(r["period"] for r in c.execute("SELECT period FROM hourly_activity WHERE nick_id=?", (nid,)))
        assert periods == [0, 1, 3]

    def test_reset_keeps_peak_karma_and_quotes(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.update_peak(NET, CHAN, 0, 9); db.change_karma(NET, CHAN, "alice", 1); db.add_quote(nid, NET, CHAN, "hi")
        for p in (1, 2, 3):
            db.reset_period(p)
        assert db.get_peak(NET, CHAN)["peak"] == 9
        assert db.get_karma_nick(NET, CHAN, "alice") == 1
        assert db.get_quote_for_nick("alice", NET, CHAN)["quote"] == "hi"


class TestDailySnapshot:
    """snapshot_daily() runs from the scheduler's first tick after midnight."""

    def _day_of_chatting(self, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.incr(nid, "lines", 10); db.incr(nid, "words", 40)

    def test_the_finished_day_is_stored_under_its_own_date(self, db, freeze_date):
        self._day_of_chatting(db)                 # activity happened on 2026-10-01
        freeze_date(2026, 10, 2)                  # ...the snapshot runs at 00:0x on the 2nd
        db.snapshot_daily()
        assert db.get_daily_activity(NET, CHAN) == [{"date": "2026-10-01", "lines": 10, "words": 40}]

    def test_todays_chart_bar_shows_todays_lines_not_yesterdays(self, db, freeze_date):
        self._day_of_chatting(db)
        freeze_date(2026, 10, 2)
        db.snapshot_daily(); db.reset_period(1)
        db.incr(db.get_or_create_nick("alice", NET, CHAN), "lines", 3)   # a few lines on the 2nd
        db.snapshot_today(NET, CHAN)
        days = {d["date"]: d["lines"] for d in db.get_daily_activity(NET, CHAN)}
        assert days.get("2026-10-02") == 3
        assert days.get("2026-10-01") == 10

    def test_taking_the_snapshot_twice_for_the_same_day_does_not_double_it(self, db, freeze_date):
        self._day_of_chatting(db)
        freeze_date(2026, 10, 2)
        db.snapshot_daily(); db.reset_period(1)      # what the scheduler does...
        db.snapshot_daily()                          # ...and a restart within the same hour
        assert sum(d["lines"] for d in db.get_daily_activity(NET, CHAN)) == 10

    def test_trim_removes_old_days(self, db, freeze_date):
        freeze_date(2026, 10, 2)
        with db.get_conn() as c:
            for day in ("2020-01-01", "2026-09-30"):
                c.execute("INSERT INTO daily_activity(network,channel,date,lines,words) VALUES(?,?,?,1,1)", (NET, CHAN, day))
        db.trim_daily_activity(365)
        assert [d["date"] for d in db.get_daily_activity(NET, CHAN)] == ["2026-09-30"]


class TestPurge:
    def _setup(self, db):
        for nick in ("spam1", "spam2", "alice"):
            db.incr(db.get_or_create_nick(nick, NET, CHAN), "lines", 1)
        db.incr(db.get_or_create_nick("spam1", NET, "#other"), "lines", 1)
        db.incr(db.get_or_create_nick("spam1", "Other", CHAN), "lines", 1)
        db.change_karma(NET, CHAN, "spam1", 5)

    def test_wildcard_purge_in_one_channel(self, db):
        self._setup(db)
        assert db.delete_nick_stats(NET, "spam*", channel=CHAN) == 2
        assert [r["nick"] for r in db.get_top(NET, CHAN, "lines")] == ["alice"]
        assert db.get_karma_nick(NET, CHAN, "spam1") == 0

    def test_purge_is_scoped_to_channel_and_network(self, db):
        self._setup(db)
        db.delete_nick_stats(NET, "spam*", channel=CHAN)
        assert db.get_top(NET, "#other", "lines")[0]["nick"] == "spam1"
        assert db.get_top("Other", CHAN, "lines")[0]["nick"] == "spam1"

    def test_network_wide_purge(self, db):
        self._setup(db)
        assert db.delete_nick_stats(NET, "spam*") == 3
        assert db.get_top("Other", CHAN, "lines")[0]["nick"] == "spam1"

    def test_no_match(self, db):
        self._setup(db)
        assert db.delete_nick_stats(NET, "nobody*") == 0
