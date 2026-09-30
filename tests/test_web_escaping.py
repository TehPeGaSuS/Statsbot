"""Every value the stats page shows that came from IRC must be HTML-escaped.

The test plants a uniquely tagged hostile value  <x-LABEL a="'>  in every database field the page
can display (nicks, quotes, URLs, kick lines, topics, words, smileys, karma, example lines, other
channel names), renders the real page, and fails for each label that shows up as a raw tag."""
import pytest

from conftest import CHAN, NET, make_config

LABELS = ["nickA", "nickB", "quote", "url", "kicker", "kickreason", "topic", "setby", "word", "lastusedby",
          "smiley", "refby", "refnick", "karmanick", "capsex", "foulex", "actionex", "violentex",
          "attackedex", "sibling", "relnick"]


def hostile(label):
    return f'<x-{label} a="\'>'


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    from database import models as db
    db.set_db_path(str(tmp_path_factory.mktemp("esc") / "data" / "stats.db"))
    db.init_db()
    a, b = hostile("nickA"), hostile("nickB")
    ids = {}
    for nick, lines in ((a, 100), (b, 90)):
        ids[nick] = db.get_or_create_nick(nick, NET, CHAN, "u@h")
        nid = ids[nick]
        for stat, value in (("lines", lines), ("words", lines * 5), ("letters", lines * 30), ("questions", 20),
                            ("caps", 20), ("foul", 20), ("smileys", 20), ("sad", 20), ("violent", 5),
                            ("attacked", 5), ("kicks", 3), ("kick_given", 3), ("actions", 9), ("monologues", 2),
                            ("joins", 4), ("op_given", 2), ("op_taken", 2), ("voice_given", 2), ("voice_taken", 2),
                            ("halfop_given", 2), ("halfop_taken", 2), ("minutes", 30)):
            db.incr(nid, stat, value)
    with db.get_conn() as c:
        for nid in ids.values():
            c.execute("UPDATE stats SET caps_ex=?, foul_ex=?, action_ex=?, violent_ex=?, attacked_ex=? "
                      "WHERE nick_id=? AND period=0",
                      (hostile("capsex"), hostile("foulex"), hostile("actionex"), hostile("violentex"),
                       hostile("attackedex"), nid))
    db.add_quote(ids[a], NET, CHAN, hostile("quote") + " padded so it is long enough to be a quote")
    db.add_quote(ids[b], NET, CHAN, hostile("quote") + " padded so it is long enough to be a quote")
    with db.get_conn() as c:
        c.execute("INSERT INTO urls(nick_id,network,channel,url,count,ts) VALUES(?,?,?,?,?,?)",
                  (ids[a], NET, CHAN, f'http://example.com/{hostile("url")}', 2, 1000))
        c.execute("INSERT INTO kick_log(network,channel,kicker,victim,reason,context,ts) VALUES(?,?,?,?,?,?,?)",
                  (NET, CHAN, hostile("kicker"), a, hostile("kickreason"), "[]", 1000))
        c.execute("INSERT INTO topics(network,channel,topic,set_by,ts) VALUES(?,?,?,?,?)",
                  (NET, CHAN, hostile("topic"), hostile("setby"), 1000))
        c.execute("INSERT INTO channel_words(network,channel,word,count,last_used_by,display_word) VALUES(?,?,?,?,?,?)",
                  (NET, CHAN, hostile("word"), 50, hostile("lastusedby"), hostile("word")))
        c.execute("INSERT INTO smiley_freq(nick_id,network,channel,smiley,count) VALUES(?,?,?,?,?)",
                  (ids[a], NET, CHAN, hostile("smiley"), 9))
        c.execute("INSERT INTO nick_refs(network,channel,mentioned,by_nick,count) VALUES(?,?,?,?,?)",
                  (NET, CHAN, hostile("refnick"), hostile("refby"), 9))
        c.execute("INSERT INTO karma(network,channel,nick,score) VALUES(?,?,?,?)", (NET, CHAN, hostile("karmanick"), 5))
        c.execute("INSERT INTO bot_channels(network,channel,enabled) VALUES(?,?,1)", (NET, CHAN))
        c.execute("INSERT INTO bot_channels(network,channel,enabled) VALUES(?,?,1)", (NET, "#" + hostile("sibling")))
    # the relation map and the closest-pairs table (three hostile people talking to each other)
    third = hostile("relnick")
    for _ in range(4):
        db.add_pair(NET, CHAN, a, b); db.add_pair(NET, CHAN, b, third); db.add_pair(NET, CHAN, third, a)
    # a second word so the "most used words" table is not filtered away, and a nick that mentions
    cfg = make_config(pisg={"BigNumbersThreshold": 1, "WordLength": 3, "ShowOps": True, "ShowVoice": True,
                            "ShowHalfops": True, "ShowTime": True, "MinQuote": 1, "MaxQuote": 500})
    cfg["web"] = {}
    from web.pisg_page import build_page
    return build_page(NET, CHAN, 0, cfg)


def test_the_page_really_shows_the_planted_data(page):
    """Guards the guard: if the test data stopped reaching the page, the checks below would pass vacuously."""
    shown = [label for label in LABELS if f"&lt;x-{label}" in page]
    missing = sorted(set(LABELS) - set(shown))
    # a few fields are legitimately not rendered in every layout; most must be
    assert len(shown) >= 15, f"only {len(shown)} of {len(LABELS)} planted fields were displayed; missing: {missing}"


@pytest.mark.parametrize("label", LABELS)
def test_field_is_escaped(page, label):
    assert f"<x-{label}" not in page, f"{label!r} reached the page as a raw HTML tag"


class TestHelpers:
    def test_e_escapes_quotes_and_angle_brackets_and_handles_non_strings(self):
        from web.pisg_page import _e
        assert _e('<a href="x">&\'') == "&lt;a href=&quot;x&quot;&gt;&amp;&#x27;"
        assert _e(None) == "" and _e(5) == "5"

    def test_js_values_cannot_close_the_script_element(self):
        from web.pisg_page import _js
        assert "</script>" not in _js("</script><script>alert(1)</script>")
        assert "<!--" not in _js("<!-- x")
