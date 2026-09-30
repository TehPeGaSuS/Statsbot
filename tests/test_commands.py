"""irc/commands.py: the !stats / !top / !quote commands anyone can use in a channel."""
import pytest

from irc.commands import CommandHandler
from conftest import CHAN, NET, make_config


@pytest.fixture
def chat(db, cfg):
    class Chat:
        said = []
        handler = CommandHandler(cfg, NET, lambda target, text: Chat.said.append((target, text)))

        @staticmethod
        def cmd(text, nick="alice"):
            Chat.said.clear()
            Chat.handler.dispatch(nick, CHAN, text, "u@h")
            return " | ".join(t for _, t in Chat.said)
    return Chat


def speak(db, nick, lines, words_each=3):
    nid = db.get_or_create_nick(nick, NET, CHAN)
    db.incr(nid, "lines", lines); db.incr(nid, "words", lines * words_each)


class TestTop:
    def test_lists_the_most_talkative_first(self, chat, db):
        speak(db, "quiet", 1); speak(db, "loud", 50)
        reply = chat.cmd("!top 2")
        assert reply.index("loud") < reply.index("quiet")

    def test_default_is_three_and_the_maximum_is_ten(self, chat, db):
        for i in range(15):
            speak(db, f"n{i:02d}", i + 1)
        assert "top 3:" in chat.cmd("!top")
        assert "top 10:" in chat.cmd("!top 99")

    def test_nobody_with_zero_words_is_listed(self, chat, db):
        db.get_or_create_nick("lurker", NET, CHAN)
        assert "lurker" not in chat.cmd("!top") and "No stats yet" in chat.cmd("!top")


class TestDispatch:
    def test_text_without_the_prefix_is_ignored(self, chat):
        assert chat.cmd("top") == ""

    def test_unknown_commands_are_silent(self, chat):
        assert chat.cmd("!frobnicate") == ""

    def test_the_prefix_can_be_overridden_per_network(self, db):
        cfg = make_config()
        cfg["networks"] = [{"name": NET, "cmd_prefix": "."}]
        said = []
        h = CommandHandler(cfg, NET, lambda t, x: said.append(x))
        h.dispatch("a", CHAN, "!top", "u@h"); h.dispatch("a", CHAN, ".top", "u@h")
        assert len(said) == 1

    def test_flooding_the_bot_gets_no_answer(self, chat, db):
        speak(db, "alice", 5)
        answers = [chat.cmd("!top") for _ in range(8)]
        assert [bool(a) for a in answers] == [True] * 5 + [False] * 3

    def test_flood_limit_is_per_channel(self, chat, db):
        speak(db, "alice", 5)
        for _ in range(5):
            chat.cmd("!top")
        chat.said.clear()
        chat.handler.dispatch("a", "#elsewhere", "!top", "u@h")
        assert chat.said


class TestStatsLink:
    def test_link_uses_the_public_url_and_a_lowercase_channel(self, db):
        cfg = make_config(web={"public_url": "https://stats.example/"})
        said = []
        CommandHandler(cfg, "Libera", lambda t, x: said.append(x)).dispatch("a", "#MixedCase", "!stats", "u@h")
        assert "https://stats.example/Libera/mixedcase/" in said[0]

    def test_a_channel_specific_url_wins(self, db):
        db.set_channel_config(NET, CHAN, "stats_url", "https://custom.example/x")
        said = []
        CommandHandler(make_config(), NET, lambda t, x: said.append(x)).dispatch("a", CHAN, "!stats", "u@h")
        assert "https://custom.example/x" in said[0]


class TestQuote:
    def test_random_and_per_nick(self, chat, db):
        nid = db.get_or_create_nick("alice", NET, CHAN)
        db.add_quote(nid, NET, CHAN, "something memorable")
        assert "something memorable" in chat.cmd("!quote")
        assert "something memorable" in chat.cmd("!quote alice")

    def test_nobody_quoted(self, chat, db):
        assert "No quotes for ghost" in chat.cmd("!quote ghost")
        assert "No quotes yet" in chat.cmd("!quote")
