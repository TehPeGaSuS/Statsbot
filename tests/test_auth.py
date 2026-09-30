"""bot/auth.py: passwords, sessions and host-mask auto-authentication."""
import pytest

from bot.auth import AuthManager, hash_password, verify_password
from conftest import NET


@pytest.fixture
def auth():
    return AuthManager()


class TestPasswords:
    def test_roundtrip(self):
        assert verify_password("s3cret pass", hash_password("s3cret pass"))

    def test_wrong_password(self):
        assert not verify_password("nope", hash_password("s3cret pass"))

    def test_hash_is_salted_and_not_the_password(self):
        h1, h2 = hash_password("same"), hash_password("same")
        assert h1 != h2 and "same" not in h1

    def test_garbage_hash_is_a_failed_login_not_a_crash(self):
        assert verify_password("x", "not-a-bcrypt-hash") is False
        assert verify_password("x", "") is False

    def test_unicode_passwords(self):
        assert verify_password("pässwörd✓", hash_password("pässwörd✓"))


class TestSessions:
    def test_session_belongs_to_one_network_and_is_case_insensitive(self, auth):
        auth.create_session("A", "Alice", "a@h", "boss")
        assert auth.is_authed("A", "alice")
        assert not auth.is_authed("B", "alice")

    def test_nick_change_moves_the_session(self, auth):
        auth.create_session(NET, "alice", "a@h", "boss")
        auth.on_nick_change(NET, "alice", "alice2")
        assert auth.is_authed(NET, "alice2") and not auth.is_authed(NET, "alice")
        assert auth.get_session(NET, "alice2")["master"] == "boss"

    def test_quit_ends_the_session(self, auth):
        auth.create_session(NET, "alice", "a@h", "boss")
        auth.on_quit(NET, "alice")
        assert not auth.is_authed(NET, "alice")

    def test_part_keeps_the_session(self, auth):
        auth.create_session(NET, "alice", "a@h", "boss")
        auth.on_part(NET, "alice")
        assert auth.is_authed(NET, "alice")

    def test_ending_a_session_that_does_not_exist_is_fine(self, auth):
        auth.destroy_session(NET, "nobody")


class TestIdentify:
    @pytest.fixture(autouse=True)
    def _master(self, db):
        db.add_master_with_password("Boss", hash_password("correct horse"))

    def test_correct_password(self, auth):
        ok, _ = auth.identify(NET, "alice", "a@h", "boss", "correct horse")
        assert ok and auth.is_authed(NET, "alice")

    def test_master_name_is_case_insensitive(self, auth):
        assert auth.identify(NET, "alice", "a@h", "BOSS", "correct horse")[0]

    def test_wrong_password(self, auth):
        ok, msg = auth.identify(NET, "alice", "a@h", "boss", "wrong")
        assert not ok and not auth.is_authed(NET, "alice")

    def test_unknown_master(self, auth):
        ok, msg = auth.identify(NET, "alice", "a@h", "nobody", "correct horse")
        assert not ok and "Unknown" in msg


class TestAutoAuth:
    @pytest.fixture
    def master_with_masks(self, db):
        def _make(masks):
            db.add_master_with_password("boss", hash_password("pw123456"))
            with db.get_conn() as c:
                c.execute("UPDATE masters SET masks=? WHERE lower(pattern)='boss'", (masks,))
        return _make

    def test_matching_hostmask_logs_the_master_in(self, auth, master_with_masks):
        master_with_masks("*!*@trusted.example")
        assert auth.try_auto_auth(NET, "whoever", "user@trusted.example") is True
        assert auth.get_session(NET, "whoever")["master"] == "boss"

    def test_other_host_is_not_logged_in(self, auth, master_with_masks):
        master_with_masks("*!*@trusted.example")
        assert auth.try_auto_auth(NET, "whoever", "user@evil.example") is False
        assert not auth.is_authed(NET, "whoever")

    def test_a_mask_without_a_host_never_matches_on_the_nick_alone(self, auth, master_with_masks):
        # anyone can take the nick "boss" on IRC: a bare nick must not be enough
        master_with_masks("boss")
        assert auth.try_auto_auth(NET, "boss", "evil@attacker.example") is False

    def test_several_masks(self, auth, master_with_masks):
        master_with_masks("*!*@one.example *!*@two.example")
        assert auth.try_auto_auth(NET, "x", "u@two.example") is True

    def test_no_masks_no_auto_auth(self, auth, db):
        db.add_master_with_password("boss", hash_password("pw123456"))
        assert auth.try_auto_auth(NET, "boss", "u@h") is False

    def test_already_identified_is_not_reported_as_a_new_login(self, auth, master_with_masks):
        master_with_masks("*!*@trusted.example")
        auth.try_auto_auth(NET, "whoever", "user@trusted.example")
        assert auth.try_auto_auth(NET, "whoever", "user@trusted.example") is False
