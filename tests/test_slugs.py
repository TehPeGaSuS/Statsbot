"""slugs.py and the URLs built from it: #a, ##a and ###a are three different channels."""
import itertools
import random

import pytest

from slugs import channel_from_slug, channel_slug, channel_url_path
from conftest import NET, make_config


class TestSlugs:
    @pytest.mark.parametrize("channel,slug", [
        ("#lifeline", "lifeline"),               # unchanged: existing links keep working
        ("##lifeline", "2/lifeline"),
        ("###lifeline", "3/lifeline"),
        ("#4chan", "4chan"),                     # starts with a digit: no slash, no clash
        ("#2600", "2600"),
        ("##2600", "2/2600"),
        ("&local", "0/&local"),
        ("#2/lifeline", "1/2/lifeline"),         # a "#2/..." name gets the explicit form
        ("##2/lifeline", "2/2/lifeline"),
    ])
    def test_channel_to_slug_and_back(self, channel, slug):
        assert channel_slug(channel) == slug
        assert channel_from_slug(slug) == channel

    def test_the_three_channels_get_three_urls(self):
        assert len({channel_slug(c) for c in ("#a", "##a", "###a")}) == 3

    def test_the_explicit_one_hash_form_is_understood(self):
        assert channel_from_slug("1/lifeline") == "#lifeline"

    def test_old_style_full_names_still_resolve(self):
        assert channel_from_slug("##lifeline") == "##lifeline"     # /Net/%23%23lifeline/

    def test_trailing_slash_is_ignored(self):
        assert channel_from_slug("2/lifeline/") == "##lifeline"

    def test_a_silly_hash_count_cannot_allocate_a_huge_string(self):
        assert channel_from_slug("999999999/x") == "#999999999/x"

    def test_the_url_form_is_lowercase_and_quoted(self):
        assert channel_url_path("##LifeLine") == "2/lifeline"
        assert channel_url_path("#a b?c") == "a%20b%3Fc"

    def test_no_two_channels_ever_share_a_url(self):
        """Every combination of hashes, digits, slashes and letters: slug() is injective and reversible."""
        alphabet = ["#", "1", "2", "/", "a", "&"]
        seen = {}
        for length in range(1, 7):
            for chars in itertools.product(alphabet, repeat=length):
                name = "".join(chars)
                if not name.startswith(("#", "&")) or name.rstrip("#") == "":
                    continue
                if name.endswith("/"):        # the one documented limit: indistinguishable from the URL's own "/"
                    continue
                slug = channel_slug(name)
                assert slug not in seen, f"{name!r} and {seen[slug]!r} share the URL {slug!r}"
                seen[slug] = name
                assert channel_from_slug(slug) == name, f"{name!r} -> {slug!r} -> {channel_from_slug(slug)!r}"


@pytest.fixture
def client(db, tmp_path):
    from web import dashboard
    cfg = make_config(); cfg["web"] = {}
    dashboard.set_config(cfg, str(tmp_path / "data" / "stats.db"))
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def three_channels(sensors, db):
    for channel, nick in (("#lifeline", "onehash"), ("##lifeline", "twohash"), ("###lifeline", "threehash")):
        sensors.on_join(nick, f"{nick}@h", channel)
        sensors.on_privmsg(nick, f"{nick}@h", channel, f"hello from {nick}, nice to meet you")
        db.add_channel(NET, channel)
    sensors.on_join("lonely", "l@h", "#4chan")
    sensors.on_privmsg("lonely", "l@h", "#4chan", "hello from lonely, nice to meet you")


class TestPagesForDifferentHashCounts:
    @pytest.mark.parametrize("url,present,absent", [
        ("/Net/lifeline/", "onehash", ("twohash", "threehash")),
        ("/Net/2/lifeline/", "twohash", ("onehash", "threehash")),
        ("/Net/3/lifeline/", "threehash", ("onehash", "twohash")),
        ("/Net/4chan/", "lonely", ("onehash",)),
    ])
    def test_each_url_shows_its_own_channel(self, client, three_channels, url, present, absent):
        r = client.get(url)
        assert r.status_code == 200
        html = r.get_data(as_text=True)
        assert present in html and not any(a in html for a in absent)

    @pytest.mark.parametrize("old,new", [
        ("/Net/1/lifeline/", "/Net/lifeline"),                 # explicit one-hash form
        ("/Net/2/LifeLine/", "/Net/2/lifeline"),               # wrong case
        ("/Net/%23%23lifeline/", "/Net/2/lifeline"),           # the old full-name form
        ("/net/2/lifeline/", "/Net/2/lifeline"),               # wrong network case
    ])
    def test_other_spellings_redirect_to_the_one_canonical_url(self, client, three_channels, old, new):
        r = client.get(old)
        assert r.status_code == 301 and r.headers["Location"].endswith(new)

    def test_unknown_hash_count_is_a_404(self, client, three_channels):
        assert client.get("/Net/4/lifeline/").status_code == 404

    def test_the_channel_links_on_a_page_go_to_the_right_channels(self, client, three_channels):
        html = client.get("/Net/lifeline/").get_data(as_text=True)
        assert 'href="/Net/2/lifeline/"' in html and 'href="/Net/3/lifeline/"' in html

    def test_the_network_page_links_every_channel_correctly(self, client, three_channels):
        html = client.get("/Net/").get_data(as_text=True)
        for href in ("/Net/lifeline/", "/Net/2/lifeline/", "/Net/3/lifeline/", "/Net/4chan/"):
            assert f'href="{href}"' in html

    def test_the_live_user_count_script_asks_for_the_right_channel(self, client, three_channels):
        assert 'const chanSlug = "2/lifeline"' in client.get("/Net/2/lifeline/").get_data(as_text=True)


class TestApiForDifferentHashCounts:
    def test_top_and_nick_and_online(self, client, three_channels):
        assert [r["nick"] for r in client.get("/api/Net/2/lifeline/top").get_json()] == ["twohash"]
        assert client.get("/api/Net/3/lifeline/nick/threehash").get_json()["nick"] == "threehash"
        assert client.get("/api/Net/2/lifeline/nick/onehash").status_code == 404
        assert client.get("/api/Net/2/lifeline/online").get_json()["channel"] == "##lifeline"


class TestLinksBuiltByTheBot:
    def test_stats_command_link(self):
        from irc.commands import CommandHandler
        said = []
        cfg = make_config(web={"public_url": "https://stats.example"})
        CommandHandler(cfg, "DALnet", lambda t, x: said.append(x)).dispatch("a", "##LifeLine", "!stats", "u@h")
        assert "https://stats.example/DALnet/2/lifeline/" in said[0]

    def test_stats_command_link_for_a_plain_channel_is_unchanged(self):
        from irc.commands import CommandHandler
        said = []
        cfg = make_config(web={"public_url": "https://stats.example"})
        CommandHandler(cfg, "DALnet", lambda t, x: said.append(x)).dispatch("a", "#LifeLine", "!stats", "u@h")
        assert "https://stats.example/DALnet/lifeline/" in said[0]


class TestUnusualButLegalCharacters:
    @pytest.mark.parametrize("channel", ["#a\\b", "#a[b]", "#a^b", "#{x}|y", "#a%b", "#a?b", "#a b".replace(" ", "~"),
                                        "##a\\b", "#2/a"])
    def test_the_page_and_the_api_work_for_channels_with_odd_characters(self, client, sensors, db, channel):
        sensors.on_join("nick", "n@h", channel)
        sensors.on_privmsg("nick", "n@h", channel, "hello from nick, nice to meet you")
        db.add_channel(NET, channel)
        path = channel_url_path(channel)
        page = client.get(f"/Net/{path}/")
        assert page.status_code == 200, f"{channel!r} -> /Net/{path}/ gave {page.status_code}"
        assert client.get(f"/api/Net/{path}/top").get_json()[0]["nick"] == "nick"
