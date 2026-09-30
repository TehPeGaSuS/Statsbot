"""irc/pm_commands.py: the admin interface people use over private message."""
import pytest

from bot.auth import AuthManager, hash_password
from irc.pm_commands import PMCommandHandler
from conftest import CHAN, NET, count_rows, make_config

# every command that changes or reveals configuration
PROTECTED = [
    "status", "ignore add spammer", "ignore list", "ignore del spammer", "ignore purge spammer",
    "master list", "master add mallory", "master del boss", "set page http://x.example",
    "rehash", "addchan #new", "delchan #chan", "join #new", "part #chan", "addnet foo irc.example.org",
    "delnet foo", "reload", "nets", "chans", "setlang fr_FR", "pisg #chan list",
]


class Bot:
    """A PM handler wired to a real AuthManager, recording everything it says."""
    def __init__(self):
        self.said = []
        self.auth = AuthManager()
        self.handler = PMCommandHandler(NET, self.auth, lambda nick, text: self.said.append((nick, text)),
                                        make_config())

    def pm(self, nick, text, host="user@host"):
        self.said.clear()
        self.handler.dispatch(nick, host, text)
        return " | ".join(t for _, t in self.said)

    def login(self, nick="alice"):
        self.auth.create_session(NET, nick, "user@host", "boss")


@pytest.fixture
def bot(db):
    db.add_master_with_password("boss", hash_password("correct horse"))
    return Bot()


class TestAuthenticationIsRequired:
    @pytest.mark.parametrize("command", PROTECTED)
    def test_strangers_are_turned_away(self, bot, command):
        reply = bot.pm("mallory", command)
        assert "not identified" in reply.lower(), f"{command!r} answered: {reply!r}"

    def test_strangers_change_nothing(self, bot, db):
        for command in ("ignore add victim", "addchan #evil", "master add mallory", "master del boss"):
            bot.pm("mallory", command)
        assert db.list_ignores(NET) == []
        assert db.get_master_by_nick("boss") is not None and db.get_master_by_nick("mallory") is None

    def test_public_commands_need_no_login(self, bot):
        assert "identify" in bot.pm("mallory", "help").lower()
        assert "not identified" in bot.pm("mallory", "whoami").lower()

    def test_unknown_command(self, bot):
        assert "unknown command" in bot.pm("mallory", "frobnicate").lower()

    def test_blank_message_is_ignored(self, bot):
        assert bot.pm("mallory", "   ") == ""


class TestIdentify:
    def test_login_logout_whoami(self, bot):
        assert "Identified as boss" in bot.pm("alice", "identify boss correct horse")
        assert "boss" in bot.pm("alice", "whoami")
        assert "Logged out" in bot.pm("alice", "logout")
        assert "not identified" in bot.pm("alice", "whoami").lower()

    def test_wrong_password_and_missing_arguments(self, bot):
        assert "Wrong password" in bot.pm("alice", "identify boss nope")
        assert "usage" in bot.pm("alice", "identify boss").lower()

    def test_a_login_on_one_nick_does_not_authorise_another(self, bot):
        bot.pm("alice", "identify boss correct horse")
        assert "not identified" in bot.pm("mallory", "ignore add x").lower()


class TestIgnoreCommands:
    def test_add_list_del_network_wide(self, bot, db):
        bot.login()
        assert "Ignored spammer" in bot.pm("alice", "ignore add spammer")
        assert db.is_ignored("spammer", NET, None, CHAN)
        assert "spammer" in bot.pm("alice", "ignore list")
        bot.pm("alice", "ignore del spammer")
        assert not db.is_ignored("spammer", NET, None, CHAN)

    def test_channel_scoped(self, bot, db):
        bot.login()
        bot.pm("alice", "ignore add #chan spammer")
        assert db.is_ignored("spammer", NET, None, "#chan") and not db.is_ignored("spammer", NET, None, "#other")

    def test_add_with_purge_removes_existing_stats(self, bot, db):
        bot.login()
        db.incr(db.get_or_create_nick("spammer", NET, CHAN), "lines", 5)
        bot.pm("alice", "ignore add spammer --purge")
        assert db.get_top(NET, CHAN, "lines") == []

    def test_purge_alone_does_not_ignore(self, bot, db):
        bot.login()
        db.incr(db.get_or_create_nick("spammer", NET, CHAN), "lines", 5)
        bot.pm("alice", "ignore purge spammer")
        assert db.get_top(NET, CHAN, "lines") == [] and not db.is_ignored("spammer", NET, None, CHAN)

    def test_usage_messages(self, bot):
        bot.login()
        assert "usage" in bot.pm("alice", "ignore").lower()
        assert "usage" in bot.pm("alice", "ignore add").lower()
        assert "usage" in bot.pm("alice", "ignore add #chan").lower()


class TestMasterCommands:
    def _add(self, bot, target, password, confirm=None):
        bot.pm("alice", f"master add {target}")
        bot.pm("alice", password)
        return bot.pm("alice", confirm if confirm is not None else password)

    def test_adding_a_master_stores_a_hash_and_the_new_master_can_log_in(self, bot, db):
        bot.login()
        assert "added successfully" in self._add(bot, "newboss", "a good password")
        stored = db.get_master_by_nick("newboss")["password_hash"]
        assert stored != "a good password" and stored.startswith("$2")
        assert "Identified as newboss" in bot.pm("carol", "identify newboss a good password")

    def test_mismatched_confirmation_adds_nobody(self, bot, db):
        bot.login()
        assert "don't match" in self._add(bot, "newboss", "a good password", "another one")
        assert db.get_master_by_nick("newboss") is None

    def test_short_password_is_refused(self, bot, db):
        bot.login()
        bot.pm("alice", "master add newboss")
        assert "too short" in bot.pm("alice", "abc")

    def test_the_password_reply_is_not_run_as_a_command(self, bot, db):
        bot.login()
        bot.pm("alice", "master add newboss")
        reply = bot.pm("alice", "rehash please")   # a password that happens to look like a command
        assert reply == "Confirm password:"

    def test_cancel(self, bot, db):
        bot.login()
        bot.pm("alice", "master add newboss")
        bot.pm("alice", "a good password")
        assert "Cancelled" in bot.pm("alice", "cancel")
        assert db.get_master_by_nick("newboss") is None

    def test_list_shows_the_masters(self, bot):
        bot.login()
        reply = bot.pm("alice", "master list")
        assert "boss" in reply and "internal error" not in reply.lower()

    def test_delete(self, bot, db):
        bot.login()
        bot.pm("alice", "master del boss")
        assert db.get_master_by_nick("boss") is None
