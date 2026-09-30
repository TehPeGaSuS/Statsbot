"""web/dashboard.py and web/pisg_page.py: the public pages."""
import re
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


class TestAttackedExample:
    """The 'got beaten' line must be an example of THAT nick being attacked."""

    def test_the_example_belongs_to_the_most_attacked_nick(self, client, sensors):
        for n in ("alice", "bob", "carol", "dave", "erin", "frank"):
            sensors.on_join(n, f"{n}@h", CHAN)
            sensors.on_privmsg(n, f"{n}@h", CHAN, "hello there everyone, good day")
        # alice is the biggest attacker (3 victims); dave is the most attacked (twice, by frank)
        for victim in ("bob", "carol", "erin"):
            sensors.on_action("alice", "alice@h", CHAN, f"slaps {victim} around with a trout")
        for weapon in ("fish", "boot"):
            sensors.on_action("frank", "frank@h", CHAN, f"slaps dave around with a {weapon}")
        html = client.get(f"/{NET}/chan/").get_data(as_text=True)
        cell = html.split("They got beaten", 1)[1].split("</td>", 1)[0]     # the "unliked" cell
        assert "frank slaps dave" in cell
        assert "alice slaps bob" not in cell            # that is the attacker's example, shown elsewhere


class TestNickChanges:
    """"Other interesting numbers" names the nick that changed its nick most."""

    def page(self, client):
        r = client.get("/Net/chan/")
        assert r.status_code == 200
        return r.get_data(as_text=True)

    def test_a_restless_nick_is_named(self, client, sensors):
        sensors.on_join("alice", "a@h", CHAN)
        sensors.on_privmsg("alice", "a@h", CHAN, "hello there everybody")
        for new in ("alice_", "alice__", "alice___"):
            sensors.on_nick("alice", "a@h", new, [CHAN])
        page = self.page(client)
        assert "can&#x27;t settle on a name" in page or "can't settle on a name" in page
        assert "3 nick changes" in page

    def test_one_change_is_not_worth_a_line(self, client, sensors):
        sensors.on_join("alice", "a@h", CHAN)
        sensors.on_privmsg("alice", "a@h", CHAN, "hello there everybody")
        sensors.on_nick("alice", "a@h", "alice_", [CHAN])
        page = self.page(client)
        assert "settle on a name" not in page

    def test_the_line_is_translated(self, client, sensors):
        sensors.on_join("alice", "a@h", CHAN)
        sensors.on_privmsg("alice", "a@h", CHAN, "hello there everybody")
        for new in ("alice_", "alice__"):
            sensors.on_nick("alice", "a@h", new, [CHAN])
        r = client.get("/Net/chan/?lang=fr_FR")
        assert "changements de pseudo" in r.get_data(as_text=True)


class TestRestOfTheActiveNicks:
    """"These didn't make it to the top" is a compact list: the nick and the number it is ranked by."""

    def test_a_compact_grid_with_the_ranking_number(self, client, sensors, db):
        cfg_rows = ["u%02d" % i for i in range(30)]
        for i, n in enumerate(cfg_rows):
            sensors.on_privmsg(n, f"{n}@h", CHAN, "word " * (40 - i) + "end")
        page = client.get("/Net/chan/").get_data(as_text=True)
        assert 'class="rest-grid"' in page
        grid = page.split('class="rest-grid"')[1].split("</div>")[0]
        assert "<b>u25</b>" in grid and "<b>u00</b>" not in grid          # u00 is in the main table
        assert re.search(r"<b>u25</b> <i>\((\d+)\)</i>", grid)             # words, the default ranking

    def test_no_grid_when_everybody_fits_in_the_top_table(self, client, sensors):
        sensors.on_privmsg("alice", "a@h", CHAN, "hello there everybody")
        assert 'class="rest-grid"' not in client.get("/Net/chan/").get_data(as_text=True)

    def test_nicks_in_the_grid_are_escaped(self, client, sensors):
        for i in range(30):
            sensors.on_privmsg("<i>x%02d" % i, "a@h", CHAN, "word " * (40 - i) + "end")
        page = client.get("/Net/chan/").get_data(as_text=True)
        assert "<i>x29" not in page and "&lt;i&gt;x29" in page
