"""bot/sensors.py: what the bot records for each IRC event."""
import json

import pytest

from conftest import CHAN, NET, count_rows, stat


def join(sensors, *nicks, channel=CHAN):
    for n in nicks:
        sensors.on_join(n, f"{n}@host", channel)


def say(sensors, nick, text, channel=CHAN):
    sensors.on_privmsg(nick, f"{nick}@host", channel, text)


class TestPrivmsgCounting:
    def test_one_line_is_counted_once_in_every_period(self, sensors, db):
        say(sensors, "alice", "hello world")
        for period in range(4):
            assert stat(db, "alice", "lines", period) == 1
            assert stat(db, "alice", "words", period) == 2

    def test_letters_are_counted_once(self, sensors, db):
        say(sensors, "alice", "hello world")
        assert stat(db, "alice", "letters") == len("hello world")

    def test_letters_add_up_over_several_lines(self, sensors, db):
        say(sensors, "alice", "hello")
        say(sensors, "alice", "world!")
        assert stat(db, "alice", "letters") == len("hello") + len("world!")

    def test_private_messages_are_not_channel_stats(self, sensors, db):
        sensors.on_privmsg("alice", "alice@host", "StatsBot", "hello there")
        assert db.get_top(NET, CHAN, "lines") == []
        assert count_rows(db, "nicks") == 0

    def test_command_lines_are_not_counted(self, sensors, db):
        say(sensors, "alice", "!top 5")
        assert count_rows(db, "nicks") == 0

    def test_ignored_nick_is_not_counted(self, sensors, db):
        db.add_ignore("spammer", NET, "*")
        say(sensors, "spammer", "buy stuff now")
        assert count_rows(db, "nicks") == 0

    def test_the_bots_own_nick_is_ignored_automatically(self, sensors, db):
        say(sensors, "StatsBot", "hello there")
        say(sensors, "StatsBot_", "hello there")
        assert count_rows(db, "nicks") == 0

    def test_same_person_with_different_casing_is_one_nick(self, sensors, db):
        say(sensors, "Alice", "one two")
        say(sensors, "alice", "three four")
        assert count_rows(db, "nicks") == 1
        assert stat(db, "alice", "lines") == 2

    def test_networks_are_kept_apart(self, db, cfg):
        from bot.sensors import Sensors
        a, b = Sensors(cfg, "NetA"), Sensors(cfg, "NetB")
        a.on_privmsg("alice", "a@h", CHAN, "hello there")
        assert stat(db, "alice", "lines", network="NetA") == 1
        assert stat(db, "alice", "lines", network="NetB") == 0

    def test_questions_smileys_caps_and_foul_language(self, sensors, db):
        say(sensors, "alice", "really? :)")
        say(sensors, "alice", "well :(")
        say(sensors, "alice", "STOP SHOUTING")
        say(sensors, "alice", "damn it")
        assert stat(db, "alice", "questions") == 1
        assert stat(db, "alice", "smileys") == 1
        assert stat(db, "alice", "sad") == 1
        assert stat(db, "alice", "caps") == 1
        assert stat(db, "alice", "foul") == 1

    def test_first_example_line_is_kept(self, sensors, db):
        say(sensors, "alice", "FIRST SHOUT HERE")
        say(sensors, "alice", "SECOND SHOUT HERE")
        assert db.get_example(db.get_or_create_nick("alice", NET, CHAN), "caps_ex") == "FIRST SHOUT HERE"

    def test_per_smiley_frequency(self, sensors, db):
        say(sensors, "alice", ":) :) :D")
        assert {r["smiley"]: r["total"] for r in db.get_top_smileys(NET, CHAN)} == {":)": 2, ":D": 1}

    def test_words_are_tracked_per_person_and_channel(self, sensors, db):
        say(sensors, "alice", "elephants elephants giraffes")
        top = db.get_top_words_channel(NET, CHAN)
        assert {r["word"]: r["count"] for r in top} == {"elephants": 2, "giraffes": 1}

    def test_word_tracking_can_be_switched_off(self, db, cfg):
        from bot.sensors import Sensors
        cfg["stats"]["log_wordstats"] = False
        s = Sensors(cfg, NET)
        say(s, "alice", "elephants everywhere")
        assert db.get_top_words_channel(NET, CHAN) == []

    def test_urls_are_logged(self, sensors, db):
        say(sensors, "alice", "see http://example.com/page please")
        assert [u["url"] for u in db.get_recent_urls(NET, CHAN)] == ["http://example.com/page"]


class TestMonologues:
    def test_five_in_a_row_is_one_monologue(self, sensors, db):
        for _ in range(5):
            say(sensors, "alice", "blah blah")
        assert stat(db, "alice", "monologues") == 1

    def test_a_longer_streak_is_still_one_monologue(self, sensors, db):
        for _ in range(9):
            say(sensors, "alice", "blah blah")
        assert stat(db, "alice", "monologues") == 1

    def test_four_is_not_enough(self, sensors, db):
        for _ in range(4):
            say(sensors, "alice", "blah blah")
        assert stat(db, "alice", "monologues") == 0

    def test_someone_else_speaking_resets_the_streak(self, sensors, db):
        for _ in range(3):
            say(sensors, "alice", "blah blah")
        say(sensors, "bob", "interrupting")
        for _ in range(3):
            say(sensors, "alice", "blah blah")
        assert stat(db, "alice", "monologues") == 0

    def test_two_streaks_are_two_monologues(self, sensors, db):
        for _ in range(5):
            say(sensors, "alice", "blah blah")
        say(sensors, "bob", "interrupting")
        for _ in range(5):
            say(sensors, "alice", "blah blah")
        assert stat(db, "alice", "monologues") == 2

    def test_channels_have_separate_streaks(self, sensors, db):
        for i in range(6):
            say(sensors, "alice", "blah blah", channel="#a" if i % 2 else "#b")
        assert stat(db, "alice", "monologues", channel="#a") == 0


class TestKarma:
    def test_plus_plus_and_minus_minus(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "bob++")
        say(sensors, "alice", "bob++")
        say(sensors, "alice", "bob--")
        assert db.get_karma_nick(NET, CHAN, "bob") == 1

    def test_only_people_in_the_channel_get_karma(self, sensors, db):
        join(sensors, "alice")
        say(sensors, "alice", "ghost++")
        assert db.get_karma_nick(NET, CHAN, "ghost") == 0

    def test_you_cannot_give_yourself_karma(self, sensors, db):
        join(sensors, "alice")
        say(sensors, "alice", "alice++")
        assert db.get_karma_nick(NET, CHAN, "alice") == 0

    def test_target_casing_is_normalised(self, sensors, db):
        join(sensors, "alice", "Bob")
        say(sensors, "alice", "BOB++")
        assert db.get_karma_nick(NET, CHAN, "Bob") == 1
        assert [r["nick"] for r in db.get_karma_top(NET, CHAN)] == ["Bob"]

    def test_trailing_punctuation_is_ignored(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "thanks bob++, really")
        assert db.get_karma_nick(NET, CHAN, "bob") == 1

    def test_nick_that_itself_contains_dashes(self, sensors, db):
        join(sensors, "alice", "Mike--")
        say(sensors, "alice", "Mike----")
        assert db.get_karma_nick(NET, CHAN, "Mike--") == -1

    def test_a_plain_word_ending_in_dashes_is_not_karma(self, sensors, db):
        join(sensors, "alice", "bob")
        say(sensors, "alice", "wait-- what")
        assert db.get_karma_top(NET, CHAN) == [] and db.get_karma_bottom(NET, CHAN) == []

    def test_karma_is_per_channel(self, sensors, db):
        join(sensors, "alice", "bob")
        join(sensors, "alice", "bob", channel="#other")
        say(sensors, "alice", "bob++")
        assert db.get_karma_nick(NET, "#other", "bob") == 0

    def test_someone_who_only_just_spoke_can_receive_karma(self, sensors, db):
        # alice speaks (this builds the nick cache), bob speaks for the first time
        # right after, then alice thanks him: bob is in the channel, so it counts
        say(sensors, "alice", "hello everyone")
        say(sensors, "bob", "hi alice")
        say(sensors, "alice", "bob++")
        assert db.get_karma_nick(NET, CHAN, "bob") == 1


class TestActions:
    def test_an_action_is_one_line_and_one_action(self, sensors, db):
        sensors.on_action("alice", "alice@host", CHAN, "waves at everyone")
        assert stat(db, "alice", "actions") == 1
        assert stat(db, "alice", "lines") == 1

    def test_an_action_is_not_counted_twice(self, sensors, db):
        sensors.on_action("alice", "alice@host", CHAN, "waves at everyone")
        assert stat(db, "alice", "words") == 3
        assert stat(db, "alice", "letters") == len("waves at everyone")

    def test_violent_action_attacks_the_first_nick_after_the_verb(self, sensors, db):
        join(sensors, "bob", "carol")
        say(sensors, "bob", "hello there")
        say(sensors, "carol", "hello there")
        sensors.on_action("alice", "alice@host", CHAN, "slaps bob around with carol")
        assert stat(db, "alice", "violent") == 1
        assert stat(db, "bob", "attacked") == 1
        assert stat(db, "carol", "attacked") == 0

    def test_ignored_nick_actions_are_not_counted(self, sensors, db):
        db.add_ignore("alice", NET, "*")
        sensors.on_action("alice", "alice@host", CHAN, "waves")
        assert count_rows(db, "nicks") == 0


class TestKicks:
    def test_kicker_and_victim(self, sensors, db):
        say(sensors, "alice", "one")
        sensors.on_kick("op", "op@host", CHAN, "alice", "bye")
        assert stat(db, "op", "kick_given") == 1
        assert stat(db, "alice", "kicks") == 1

    def test_kick_is_logged_with_recent_lines_as_context(self, sensors, db):
        say(sensors, "alice", "the last thing said")
        sensors.on_kick("op", "op@host", CHAN, "alice", "bye")
        kick = db.get_recent_kicks(NET, CHAN)[0]
        assert kick["victim"] == "alice" and kick["reason"] == "bye"
        assert "the last thing said" in json.dumps(kick)


class TestModes:
    def test_op_and_deop(self, sensors, db):
        sensors.on_mode("op", "op@host", CHAN, "+o", ["bob"])
        sensors.on_mode("op", "op@host", CHAN, "-o", ["bob"])
        assert (stat(db, "op", "op_given"), stat(db, "op", "op_taken")) == (1, 1)
        assert (stat(db, "bob", "op_got"), stat(db, "bob", "deop_got")) == (1, 1)

    def test_several_modes_in_one_line_use_the_right_targets(self, sensors, db):
        sensors.on_mode("op", "op@host", CHAN, "+oov", ["b1", "b2", "b3"])
        assert stat(db, "b1", "op_got") == 1 and stat(db, "b2", "op_got") == 1
        assert stat(db, "b3", "voice_got") == 1 and stat(db, "b3", "op_got") == 0

    def test_halfop_and_voice(self, sensors, db):
        sensors.on_mode("op", "op@host", CHAN, "+hv", ["h", "v"])
        assert stat(db, "h", "halfop_got") == 1 and stat(db, "v", "voice_got") == 1

    def test_bans_are_counted_but_their_mask_is_not_a_nick(self, sensors, db):
        sensors.on_mode("op", "op@host", CHAN, "+b", ["*!*@evil.example"])
        assert stat(db, "op", "bans") == 1
        assert count_rows(db, "nicks", "nick LIKE '%evil%'") == 0

    def test_a_channel_key_does_not_shift_the_targets(self, sensors, db):
        sensors.on_mode("op", "op@host", CHAN, "+kv", ["secretkey", "bob"])
        assert stat(db, "bob", "voice_got") == 1

    def test_removing_the_user_limit_takes_no_parameter(self, sensors, db):
        # "-l" has no argument on any IRC server, so "bob" belongs to the +v
        sensors.on_mode("op", "op@host", CHAN, "-l+v", ["bob"])
        assert stat(db, "bob", "voice_got") == 1

    def test_user_modes_are_not_channel_stats(self, sensors, db):
        sensors.on_mode("op", "op@host", "StatsBot", "+i")
        assert count_rows(db, "nicks") == 0


class TestJoinPartNick:
    def test_join_is_counted_but_populate_is_not(self, sensors, db):
        sensors.on_join("alice", "a@h", CHAN)
        sensors.on_join("bob", "b@h", CHAN, populate=True)
        assert stat(db, "alice", "joins") == 1 and stat(db, "bob", "joins") == 0

    def test_bots_are_flagged(self, sensors, db):
        sensors.on_join("robot", "r@h", CHAN, is_bot=True)
        assert db.get_nick_list(NET, CHAN)[0].get("is_bot") in (1, True)

    def test_nick_change_counts_for_the_old_nick(self, sensors, db):
        say(sensors, "alice", "hello there")
        sensors.on_nick("alice", "a@h", "alice2", [CHAN])
        assert stat(db, "alice", "nicks") == 1

    def test_topic_change(self, sensors, db):
        sensors.on_topic("alice", "a@h", CHAN, "welcome!")
        assert stat(db, "alice", "topics") == 1
        assert db.get_recent_topics(NET, CHAN)[0]["topic"] == "welcome!"


class TestQuotes:
    def test_a_new_speakers_first_line_is_always_kept(self, sensors, db):
        say(sensors, "alice", "first line here")
        say(sensors, "bob", "bobs first line")
        assert count_rows(db, "quotes") == 2

    def test_afterwards_one_line_in_five_is_kept(self, sensors, db):
        for i in range(10):
            say(sensors, "alice", f"line number {i}")
        assert count_rows(db, "quotes") == 3     # 1st + 5th + 10th


class TestMinutesAndPeak:
    def test_everyone_present_gets_a_minute_once(self, sensors, db):
        sensors.on_minute({CHAN: ["alice", "bob", "alice"]})
        assert stat(db, "alice", "minutes") == 1 and stat(db, "bob", "minutes") == 1

    def test_ignored_nicks_get_no_minutes_but_count_for_the_peak(self, sensors, db):
        db.add_ignore("robot", NET, "*")
        sensors.on_minute({CHAN: ["alice", "robot"]})
        assert count_rows(db, "nicks", "nick='robot'") == 0
        assert db.get_peak(NET, CHAN)["peak"] == 2

    def test_peak_is_never_lowered(self, sensors, db):
        sensors.on_minute({CHAN: ["a", "b", "c"]})
        sensors.on_minute({CHAN: ["a"]})
        assert db.get_peak(NET, CHAN)["peak"] == 3


class TestScheduledResets:
    def test_daily_reset_keeps_the_total_and_snapshots_the_day(self, sensors, db):
        say(sensors, "alice", "hello there friend")
        sensors.on_daily_reset()
        assert stat(db, "alice", "lines", 1) == 0 and stat(db, "alice", "lines", 0) == 1
        assert sum(d["lines"] for d in db.get_daily_activity(NET, CHAN)) == 1

    def test_weekly_and_monthly_reset_only_their_own_period(self, sensors, db):
        say(sensors, "alice", "hello there friend")
        sensors.on_weekly_reset()
        assert [stat(db, "alice", "lines", p) for p in range(4)] == [1, 1, 0, 1]
        sensors.on_monthly_reset()
        assert [stat(db, "alice", "lines", p) for p in range(4)] == [1, 1, 0, 0]
