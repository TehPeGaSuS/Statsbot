"""web/dashboard.py and web/pisg_page.py: the public pages."""
import pytest

from conftest import CHAN, NET, make_config

pytest.importorskip("flask")

HOSTILE = {
    "quote":  '</div><script>alert(1)</script>',
    "topic":  '<img src=x onerror=alert(2)>',
    "kick":   '<script>alert(3)</script>',
    "action": '<b onmouseover=alert(4)>hover</b>',
}


@pytest.fixture
def client(db, tmp_path):
    from web import dashboard
    cfg = make_config(); cfg["web"] = {}
    dashboard.set_config(cfg, str(tmp_path / "data" / "stats.db"))
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def busy_channel(sensors):
    for nick in ("alice", "bob"):
        sensors.on_join(nick, f"{nick}@h", CHAN)
        sensors.on_privmsg(nick, f"{nick}@h", CHAN, f"hello from {nick}, nice to meet everyone")
    sensors.on_privmsg("alice", "alice@h", CHAN, "bob++ for the help")
    return sensors


class TestRouting:
    def test_page_renders(self, client, busy_channel):
        r = client.get(f"/{NET}/chan/")
        assert r.status_code == 200 and b"alice" in r.data

    def test_unknown_network_and_channel_are_404(self, client, busy_channel):
        assert client.get("/Nowhere/chan/").status_code == 404
        assert client.get(f"/{NET}/nochannel/").status_code == 404

    def test_wrong_casing_redirects_to_the_canonical_lowercase_url(self, client, busy_channel):
        r = client.get(f"/{NET.upper()}/CHAN/")
        assert r.status_code == 301 and r.headers["Location"].endswith(f"/{NET}/chan")

    @pytest.mark.parametrize("period", [0, 1, 2, 3])
    def test_every_period_renders(self, client, busy_channel, period):
        assert client.get(f"/{NET}/chan/?period={period}").status_code == 200

    def test_a_nonsense_period_is_not_a_server_error(self, client, busy_channel):
        assert client.get(f"/{NET}/chan/?period=abc").status_code < 500

    def test_unknown_language_falls_back(self, client, busy_channel):
        assert client.get(f"/{NET}/chan/?lang=../../etc/passwd").status_code == 200

    def test_an_empty_channel_page_does_not_crash(self, client, db, sensors):
        sensors.on_join("lurker", "l@h", "#quiet")
        assert client.get(f"/{NET}/quiet/").status_code == 200


class TestApi:
    def test_top(self, client, busy_channel):
        rows = client.get(f"/api/{NET}/chan/top?stat=lines&limit=5").get_json()
        assert {r["nick"] for r in rows} == {"alice", "bob"}

    def test_unknown_stat_is_a_client_error(self, client, busy_channel):
        assert client.get(f"/api/{NET}/chan/top?stat=passwords").status_code == 400

    def test_a_nonsense_limit_is_not_a_server_error(self, client, busy_channel):
        assert client.get(f"/api/{NET}/chan/top?limit=abc").status_code < 500

    def test_a_period_outside_the_four_is_a_client_error(self, client, busy_channel):
        assert client.get(f"/{NET}/chan/?period=9").status_code == 400
        assert client.get(f"/api/{NET}/chan/top?period=-1").status_code == 400

    def test_a_negative_limit_does_not_dump_the_whole_table(self, client, busy_channel):
        # LIMIT -1 means "no limit" in SQLite
        assert len(client.get(f"/api/{NET}/chan/top?limit=-1").get_json()) == 1

    def test_the_limit_has_a_ceiling(self, client, busy_channel, sensors):
        for i in range(120):
            sensors.on_privmsg(f"n{i:03d}", "u@h", CHAN, "hello there everyone")
        assert len(client.get(f"/api/{NET}/chan/top?limit=1000").get_json()) == 100

    def test_a_stat_name_cannot_smuggle_sql(self, client, busy_channel, db):
        client.get(f"/api/{NET}/chan/top?stat=lines;DROP TABLE stats")
        assert db.get_top(NET, CHAN, "lines")

    def test_nick_stats_and_unknown_nick(self, client, busy_channel):
        assert client.get(f"/api/{NET}/chan/nick/alice").get_json()["nick"] == "alice"
        assert client.get(f"/api/{NET}/chan/nick/ghost").status_code == 404


class TestPisgConfigToken:
    def test_no_token_no_page(self, client, busy_channel):
        assert client.get(f"/{NET}/chan/pisg").status_code == 403

    def test_a_guessed_token_is_refused(self, client, busy_channel):
        assert client.get(f"/{NET}/chan/pisg?token=abcdef").status_code == 403

    def test_token_works_once(self, client, busy_channel):
        from web.dashboard import generate_pisg_token
        token = generate_pisg_token(NET, CHAN)
        assert client.get(f"/{NET}/chan/pisg?token={token}").status_code == 200
        assert client.get(f"/{NET}/chan/pisg?token={token}").status_code == 403

    def test_token_is_only_valid_for_its_own_channel(self, client, busy_channel, sensors):
        from web.dashboard import generate_pisg_token
        sensors.on_join("x", "x@h", "#other")
        token = generate_pisg_token(NET, "#other")
        assert client.get(f"/{NET}/chan/pisg?token={token}").status_code == 403

    def test_expired_token(self, client, busy_channel, monkeypatch):
        from web import dashboard
        token = dashboard.generate_pisg_token(NET, CHAN)
        dashboard._pisg_tokens[token]["expires"] = 0
        assert client.get(f"/{NET}/chan/pisg?token={token}").status_code == 403


class TestChatTextIsNeverMarkup:
    """Everything on the page that came from IRC is attacker-controlled: whoever can talk in a
    tracked channel could otherwise run script in the browser of everybody reading the stats."""

    @pytest.fixture
    def hostile_page(self, client, sensors):
        sensors.on_join("mallory", "m@h", CHAN)
        sensors.on_privmsg("mallory", "m@h", CHAN, HOSTILE["quote"] + " and enough words to be a quote")
        sensors.on_topic("mallory", "m@h", CHAN, HOSTILE["topic"])
        sensors.on_kick("mallory", "m@h", CHAN, "victim", HOSTILE["kick"])
        sensors.on_action("mallory", "m@h", CHAN, HOSTILE["action"])
        return client.get(f"/{NET}/chan/").get_data(as_text=True)

    @pytest.mark.parametrize("kind,needle", [
        ("quote", "<script>alert(1)"),
        ("topic", "<img src=x onerror=alert(2)>"),
        ("kick reason", "<script>alert(3)</script>"),
        ("action example", "<b onmouseover=alert(4)>"),
    ])
    def test_hostile_text_is_escaped(self, hostile_page, kind, needle):
        assert needle not in hostile_page, f"a {kind} from IRC was written into the page as raw HTML"

    def test_the_text_is_still_shown(self, hostile_page):
        assert "alert(1)" in hostile_page          # escaped, but visible to the reader
