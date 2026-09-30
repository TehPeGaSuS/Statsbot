"""web/relations.py and the "Who talks to whom" section of the page."""
import math
import random
import re
import shutil
import subprocess
import tempfile
import time

import pytest

from web import relations
from conftest import CHAN, NET, make_config


def row(a, b, n=1):
    return {"from_nick": a, "to_nick": b, "n": n}


def big_channel(users=600, groups=6, seed=7):
    """A synthetic busy channel: friend groups, a few hubs everybody mentions, some cross-talk."""
    rng = random.Random(seed)
    per = users // groups
    nicks = [[f"g{g}_u{i:03d}" for i in range(per)] for g in range(groups)]
    pairs = []
    for g in range(groups):
        for i, a in enumerate(nicks[g]):
            for _ in range(6):
                b = nicks[g][min(int(rng.paretovariate(1.2)) - 1, per - 1)]
                if a != b:
                    pairs.append(row(a, b, rng.randint(1, 20) * (3 if i < 12 else 1)))
            if rng.random() < 0.4:
                pairs.append(row(a, rng.choice(rng.choice(nicks)), rng.randint(1, 3)))
            for hub in ("OpBot", "Genni", "TheMyth"):
                if rng.random() < 0.15:
                    pairs.append(row(a, hub, rng.randint(1, 8)))
    return pairs


def dense_channel(n=35, p=0.5, seed=3):
    rng = random.Random(seed)
    nicks = [f"nick{i:02d}" for i in range(n)]
    return [row(a, b, max(1, int(rng.paretovariate(1.1) * (3 if j < 6 else 1))))
            for i, a in enumerate(nicks) for j, b in enumerate(nicks) if i != j and rng.random() < p]


class TestGraphData:
    def test_nothing_to_draw(self):
        assert relations.build_graph([]) is None
        assert relations.build_graph([row("a", "a", 5), row("a", "b", 0)]) is None

    def test_two_people_one_link(self):
        g = relations.build_graph([row("alice", "bob", 3)])
        assert [n["nick"] for n in g["nodes"]] == ["alice", "bob"] or {n["nick"] for n in g["nodes"]} == {"alice", "bob"}
        assert len(g["edges"]) == 1 and g["edges"][0]["w"] == 3

    def test_both_directions_are_added_up_and_kept_apart(self):
        g = relations.build_graph([row("alice", "bob", 3), row("bob", "alice", 2)])
        edge = g["edges"][0]
        assert edge["w"] == 5 and sorted((edge["ab"], edge["ba"])) == [2, 3]

    def test_casing_is_one_person(self):
        g = relations.build_graph([row("Alice", "bob", 1), row("ALICE", "Bob", 1)])
        assert len(g["nodes"]) == 2 and g["edges"][0]["w"] == 2

    def test_the_most_connected_nicks_are_chosen(self):
        pairs = [row("hub", f"n{i:02d}", 10) for i in range(30)] + [row("x", "y", 1)]
        g = relations.build_graph(pairs, max_nodes=5)
        assert "hub" in {n["nick"] for n in g["nodes"]} and "x" not in {n["nick"] for n in g["nodes"]}

    def test_the_number_of_nicks_is_clamped(self):
        pairs = dense_channel(80, 0.3)
        assert len(relations.build_graph(pairs, max_nodes=1000)["nodes"]) == relations.MAX_NODES
        assert len(relations.build_graph(pairs, max_nodes=1)["nodes"]) == relations.MIN_NODES

    def test_everybody_drawn_keeps_at_least_one_link(self):
        g = relations.build_graph(big_channel(), 40)
        linked = {e["a"] for e in g["edges"]} | {e["b"] for e in g["edges"]}
        assert linked == set(range(len(g["nodes"])))

    def test_a_lightly_connected_nick_is_not_left_floating_when_the_cap_fills_up(self):
        # 30 very busy friends (435 heavy links, far above the cap) and 10 people who each spoke to one of them once
        crowd = [f"busy{i:02d}" for i in range(30)]
        pairs = [row(a, b, 100) for i, a in enumerate(crowd) for b in crowd[i + 1:]]
        pairs += [row(f"quiet{i}", crowd[i], 1) for i in range(10)]
        g = relations.build_graph(pairs, 40)
        assert len(g["nodes"]) == 40 and len(g["edges"]) <= relations.EDGES_PER_NODE * 40
        quiet = {i for i, n in enumerate(g["nodes"]) if n["nick"].startswith("quiet")}
        linked = {e["a"] for e in g["edges"]} | {e["b"] for e in g["edges"]}
        assert quiet and quiet <= linked

    def test_it_is_deterministic(self):
        pairs = big_channel()
        assert relations.build_graph(pairs, 40) == relations.build_graph(list(reversed(pairs)), 40)

    def test_the_visible_links_at_the_start_follow_min_weight_or_the_automatic_choice(self):
        g = relations.build_graph(dense_channel(), 40)
        visible = sum(1 for e in g["edges"] if e["w"] >= g["start_weight"])
        assert visible >= 1 and visible <= relations.SHOWN_EDGES_PER_NODE * len(g["nodes"]) * 1.6
        assert relations.build_graph(dense_channel(), 40, min_weight=25)["start_weight"] == 25


class TestLayout:
    def test_nodes_sit_on_the_ring_at_distinct_increasing_angles(self):
        g = relations.build_graph(big_channel(), 40)
        angles = [n["angle"] for n in g["nodes"]]
        assert angles == sorted(angles) and len(set(angles)) == len(angles)
        for n in g["nodes"]:
            assert math.isclose(math.hypot(n["x"], n["y"]), relations.RING, abs_tol=0.5)

    def test_friend_groups_become_communities_in_their_own_arcs(self):
        pairs = []
        for group in ("a", "b"):
            names = [f"{group}{i}" for i in range(8)]
            pairs += [row(x, y, 10) for x in names for y in names if x != y]
        pairs.append(row("a0", "b0", 1))                                    # a thin bridge
        g = relations.build_graph(pairs, 40)
        assert g["clusters"] == 2
        by_group = {}
        for i, n in enumerate(g["nodes"]):
            by_group.setdefault(n["nick"][0], []).append((i, n["cluster"]))
        for group, members in by_group.items():
            assert len({c for _, c in members}) == 1                        # one colour per group
            idx = sorted(i for i, _ in members)
            assert idx == list(range(idx[0], idx[0] + len(idx)))            # and one contiguous arc
        assert by_group["a"][0][1] != by_group["b"][0][1]

    def test_six_groups_in_a_channel_of_600_are_found(self):
        assert relations.build_graph(big_channel(), 40)["clusters"] >= 5

    def test_ordering_never_makes_the_chords_longer_and_usually_shortens_them(self):
        pairs = dense_channel()
        g = relations.build_graph(pairs, 40)
        weights = {}
        for e in g["edges"]:
            weights[(g["nodes"][e["a"]]["key"], g["nodes"][e["b"]]["key"])] = e["w"]
        blocks_before, seen = [], {}
        for n in sorted(g["nodes"], key=lambda n: (n["cluster"], -n["strength"], n["key"])):
            seen.setdefault(n["cluster"], []).append(n["key"])
        blocks_before = list(seen.values())
        before = relations._ring_cost(blocks_before, weights)
        after = relations._ring_cost(relations._arrange(blocks_before, weights), weights)
        assert after <= before + 1e-9
        assert after < before * 0.95                                        # a real improvement on a dense graph
        # and the layout that build_graph really produces is that optimised one
        drawn, last = [], object()
        for n in g["nodes"]:
            if n["cluster"] != last:
                drawn.append([]); last = n["cluster"]
            drawn[-1].append(n["key"])
        assert relations._ring_cost(drawn, weights) < before * 0.95


class TestScale:
    def test_a_channel_of_600_is_reduced_to_a_bounded_drawing(self):
        g = relations.build_graph(big_channel(600), 40)
        assert len(g["nodes"]) == 40 and g["everyone"] > 500
        assert len(g["edges"]) <= relations.EDGES_PER_NODE * 40
        html_out = relations.render(g, {})
        assert len(html_out) < 80_000
        assert html_out.count('class="node"') == 40 and html_out.count('class="edge') <= 160

    def test_the_worst_case_is_still_bounded_and_quick(self):
        pairs = dense_channel(200, 0.4)                                    # ~16000 rows, every pair busy
        start = time.time()
        g = relations.build_graph(pairs, 1000)
        out = relations.render(g, {})
        assert time.time() - start < 3.0
        assert len(g["nodes"]) == relations.MAX_NODES and len(out) < 200_000
        assert len(g["edges"]) <= relations.EDGES_PER_NODE * relations.MAX_NODES


class TestClosestPairs:
    def test_strongest_first_with_the_busier_direction_first(self):
        rows = relations.closest_pairs([row("a", "b", 2), row("b", "a", 9), row("c", "d", 5)], 10)
        assert rows[0] == {"a": "b", "b": "a", "w": 11, "ab": 9, "ba": 2}
        assert [r["w"] for r in rows] == [11, 5]

    def test_limit(self):
        assert len(relations.closest_pairs(dense_channel(), 7)) == 7
        assert relations.closest_pairs(dense_channel(), 0) == []


class TestRendering:
    def test_nothing_renders_nothing(self):
        assert relations.render(None, {}) == ""

    def test_hostile_nicks_are_escaped_everywhere(self):
        evil = '<x-evil a="\'>'
        g = relations.build_graph([row(evil, "bob", 5), row("bob", evil, 2), row("bob", "alice", 3), row("alice", "carol", 2)])
        out = relations.render(g, {"hint": "<x-hint>", "caption": "<x-caption>", "nicks": "<x-n>", "links": "<x-l>"})
        assert "<x-" not in out
        assert "&lt;x-evil" in out and "&lt;x-hint" in out

    def test_a_translation_with_a_wrong_placeholder_cannot_break_the_page(self):
        g = relations.build_graph([row("alice", "bob", 5), row("bob", "carol", 3), row("carol", "dave", 2)])
        out = relations.render(g, {"link_title": "{nope}", "nick_title": "{x} {y}"})
        assert "alice" in out and "5 mentions" in out                     # fell back to the English text

    def test_the_sliders_reflect_the_drawing(self):
        g = relations.build_graph(big_channel(), 40)
        out = relations.render(g, {})
        assert f'id="rel-k" type="range" min="5" max="40" value="40"' in out
        assert f'max="{g["max_weight"]}"' in out

    def test_without_javascript_the_picture_is_complete(self):
        g = relations.build_graph(big_channel(), 40)
        out = relations.render(g, {})
        assert "off" not in re.findall(r'class="(?:node|edge)[^"]*"', out)   # nothing hidden by default in the markup

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_the_script_is_valid_javascript(self, tmp_path):
        script = tmp_path / "map.js"
        script.write_text(relations.JS, encoding="utf-8")
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


@pytest.fixture
def client(db, tmp_path):
    from web import dashboard
    cfg = make_config(); cfg["web"] = {}
    dashboard.set_config(cfg, str(tmp_path / "data" / "stats.db"))
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


@pytest.fixture
def talking_channel(sensors, db):
    names = ["alice", "bob", "carol", "dave", "erin"]
    for n in names:
        sensors.on_join(n, f"{n}@h", CHAN)
        sensors.on_privmsg(n, f"{n}@h", CHAN, "hello everyone, good to be here today")
    for a, b in (("alice", "bob"), ("bob", "alice"), ("alice", "bob"), ("carol", "dave"), ("erin", "alice")):
        sensors.on_privmsg(a, f"{a}@h", CHAN, f"{b}: what do you think about that")
    return names


class TestOnThePage:
    def html(self, client, url="/Net/chan/"):
        r = client.get(url)
        assert r.status_code == 200
        return r.get_data(as_text=True)

    def test_the_section_and_the_pairs_table_appear(self, client, talking_channel):
        page = self.html(client)
        assert "Who talks to whom" in page and 'id="rel"' in page and "Closest pairs" in page
        assert "alice &harr; bob" in page

    def test_no_conversation_no_section(self, client, sensors):
        sensors.on_privmsg("alice", "a@h", CHAN, "just me talking to nobody at all")
        page = self.html(client)
        assert "Who talks to whom" not in page and 'id="rel"' not in page

    def test_it_can_be_switched_off_per_channel(self, client, talking_channel, db):
        db.set_channel_config(NET, CHAN, "pisg.ShowRelations", "0")
        assert 'id="rel"' not in self.html(client)

    def test_the_number_of_nicks_can_be_changed_per_channel(self, client, talking_channel, db):
        db.set_channel_config(NET, CHAN, "pisg.RelationNicks", "5")
        assert self.html(client).count('class="node"') == 5

    def test_a_nonsense_setting_falls_back_to_the_default(self, client, talking_channel, db):
        db.set_channel_config(NET, CHAN, "pisg.RelationNicks", "lots")
        assert self.html(client).count('class="node"') == 5                 # only 5 people talk to each other

    def test_the_period_tabs_use_their_own_counts(self, client, talking_channel, sensors):
        sensors.on_daily_reset()
        assert 'id="rel"' in self.html(client, "/Net/chan/?period=0")
        assert 'id="rel"' not in self.html(client, "/Net/chan/?period=1")

    def test_the_text_is_translated(self, client, talking_channel):
        assert "Qui parle à qui" in self.html(client, "/Net/chan/?lang=fr_FR")
        assert "Chi parla con chi" in self.html(client, "/Net/chan/?lang=it_IT")

    def test_dots_follow_lines_said(self, client, talking_channel):
        sizes = re.findall(r'data-r="([\d.]+)"', self.html(client))
        assert sizes and len(set(sizes)) > 1

    def test_the_options_can_be_set_over_pm(self):
        from web.pisg_config_page import _PISG_DEFAULTS
        for key in relations.OPTION_DEFAULTS:
            assert key in _PISG_DEFAULTS


class TestWithoutJavaScript:
    """Progressive enhancement: the picture is complete on its own; only the interactive bits need JS."""

    def test_the_hint_and_the_sliders_are_hidden_until_the_script_runs(self):
        out = relations.render(relations.build_graph(big_channel(), 40), {"hint": "hover it"})
        assert '<p class="rel-hint js-only">' in out and '<div class="rel-controls js-only">' in out
        assert "#rel .js-only{display:none}" in relations.CSS                # hidden by default...
        assert "root.classList.add('js')" in relations.JS                    # ...and shown by the script
        assert "#rel.js .rel-controls{display:flex" in relations.CSS

    def test_the_caption_stays_visible_without_javascript(self):
        out = relations.render(relations.build_graph(big_channel(), 40), {"caption": "colours are groups"})
        assert 'class="rel-hint">colours are groups' in out                   # no js-only on it

    def test_every_node_and_link_is_in_the_markup(self):
        g = relations.build_graph(big_channel(), 40)
        out = relations.render(g, {})
        assert out.count('class="node"') == len(g["nodes"])
        assert out.count("<title>") == len(g["nodes"]) + len(g["edges"])       # tooltips need no script either


def test_the_page_tells_readers_without_javascript_what_is_missing(client, talking_channel):
    page = client.get("/Net/chan/").get_data(as_text=True)
    assert "<noscript>" in page and "JavaScript is off" in page
    assert "JavaScript est désactivé" in client.get("/Net/chan/?lang=fr_FR").get_data(as_text=True)


class TestSmallChannels:
    """A ring for two or three people is a silly picture: it is only drawn when it means something."""

    def test_no_ring_for_fewer_than_four_nicks(self):
        assert relations.render(relations.build_graph([row("alice", "bob", 9)]), {}) == ""
        assert relations.render(relations.build_graph([row("a", "b", 5), row("b", "c", 2)]), {}) == ""

    def test_four_nicks_are_enough(self):
        out = relations.render(relations.build_graph([row("a", "b", 5), row("b", "c", 2), row("c", "d", 1)]), {})
        assert 'id="rel"' in out and out.count('class="node"') == 4

    def test_the_picture_grows_with_the_channel(self):
        small = relations.render(relations.build_graph([row("a", "b", 5), row("b", "c", 2), row("c", "d", 1)]), {})
        big = relations.render(relations.build_graph(big_channel(), 40), {})
        width = lambda html: int(re.search(r'style="max-width:(\d+)px"', html).group(1))
        assert width(small) < 450 < width(big) <= 820

    def test_a_slider_that_cannot_move_is_not_shown(self):
        few = relations.render(relations.build_graph([row("a", "b", 1), row("b", "c", 1), row("c", "d", 1)]), {})
        assert 'id="rel-k"' not in few and 'id="rel-w"' not in few and 'id="rel-pick"' in few    # 4 nicks, all weight 1: only the picker
        five = relations.render(relations.build_graph([row("a", "b", 4), row("b", "c", 2), row("c", "d", 1), row("d", "e", 1)]), {})
        assert 'id="rel-k"' not in five and 'id="rel-w"' in five                                 # weights differ, nicks do not
        many = relations.render(relations.build_graph(big_channel(), 40), {})
        assert 'id="rel-k"' in many and 'id="rel-w"' in many

    def test_the_script_copes_with_missing_sliders(self):
        assert "data-start-weight" in relations.render(relations.build_graph(dense_channel(6, 0.9)), {})
        assert "if(kIn)kIn.addEventListener" in relations.JS and "wIn?+wIn.value:w0" in relations.JS


def test_a_tiny_conversation_shows_the_table_but_no_ring(client, sensors):
    for n in ("alice", "bob"):
        sensors.on_join(n, f"{n}@h", CHAN)
        sensors.on_privmsg(n, f"{n}@h", CHAN, "hello everyone, good to be here")
    sensors.on_privmsg("alice", "alice@h", CHAN, "bob: how are you doing today")
    page = client.get("/Net/chan/").get_data(as_text=True)
    assert 'id="rel"' not in page and "Who talks to whom" not in page
    assert "Closest pairs" in page and "alice &harr; bob" in page


class TestFoldedMap:
    """The map is a big picture: folded away by default, with the compact pairs table first."""

    def html(self, client):
        return client.get("/Net/chan/").get_data(as_text=True)

    def test_the_map_is_closed_by_default_and_the_pairs_come_first(self, client, talking_channel):
        page = self.html(client)
        assert "<details><summary>Show the map (5 nicks)</summary>" in page
        assert page.index("Closest pairs") < page.index("Who talks to whom")

    def test_it_can_be_opened_by_default_per_channel(self, client, talking_channel, db):
        db.set_channel_config(NET, CHAN, "pisg.RelationMap", "open")
        assert "<details open><summary>" in self.html(client)

    def test_it_can_be_left_out_while_the_table_stays(self, client, talking_channel, db):
        db.set_channel_config(NET, CHAN, "pisg.RelationMap", "off")
        page = self.html(client)
        assert 'id="rel"' not in page and "Closest pairs" in page

    def test_an_unknown_value_means_closed(self, client, talking_channel, db):
        db.set_channel_config(NET, CHAN, "pisg.RelationMap", "sideways")
        assert "<details><summary>" in self.html(client)

    def test_the_summary_is_translated(self, client, talking_channel):
        assert "Afficher la carte (5 pseudos)" in client.get("/Net/chan/?lang=fr_FR").get_data(as_text=True)

    def test_the_folded_map_still_works_without_javascript(self):
        out = relations.render(relations.build_graph(big_channel(), 40), {})
        assert out.count("<details") == 1 and "</details>" in out and out.index("<details") < out.index("<svg")
        assert out.count('class="node"') == 40                                # everything is in the markup, just folded


class TestFocusView:
    """Picking a nick puts it in the middle with the people it talks to around it."""

    def graph(self, activity=None):
        return relations.build_graph([row("a", "b", 9), row("b", "c", 3), row("c", "d", 1), row("d", "e", 1)], 40, 0, activity)

    def test_dots_are_sized_by_lines_said_when_known(self):
        by_mentions = {n["key"]: n["size"] for n in self.graph()["nodes"]}
        by_lines = {n["key"]: n["size"] for n in self.graph({"e": 500, "a": 5, "b": 5, "c": 5, "d": 5})["nodes"]}
        assert by_mentions["b"] > by_mentions["e"]                         # b is in more mentions
        assert by_lines["e"] == max(by_lines.values()) and by_lines["e"] > by_lines["a"]

    def test_unknown_activity_falls_back_to_mentions(self):
        assert self.graph({"nobody": 9}) == self.graph()
        assert self.graph({}) == self.graph()

    def test_a_silent_nick_still_gets_a_visible_dot(self):
        sizes = [n["size"] for n in self.graph({"a": 40})["nodes"]]
        assert min(sizes) >= 4

    def test_the_markup_carries_what_the_script_needs(self):
        out = relations.render(self.graph(), {})
        assert len(re.findall(r'<g class="node" data-rank="\d+" data-a="-?[\d.]+" data-r="[\d.]+"', out)) == 5

    def test_there_is_a_picker_with_every_nick(self):
        out = relations.render(self.graph(), {"focus": "Focus on", "everyone": "everyone"})
        picker = re.search(r'<select id="rel-pick">(.*?)</select>', out).group(1)
        assert picker.count("<option") == 6 and ">everyone<" in picker
        assert re.findall(r'<option value="(\d+)">', picker) and "Focus on" in out

    def test_the_picker_is_only_for_people_with_javascript(self):
        out = relations.render(self.graph(), {})
        assert re.search(r'class="rel-controls js-only".*id="rel-pick"', out)

    def test_the_script_has_a_way_back(self):
        js = relations.JS
        for needle in ("function ego(", "function rest(", "homeD", "'M0 0L'", "pinned===i"):
            assert needle in js
        assert js.index("rest();") < js.index("if(pinned===i)ego(i)")      # always start from the ring

    def test_nicks_in_the_picker_are_html_escaped(self):
        g = relations.build_graph([row("<b>x</b>", "b", 9), row("b", "c", 3), row("c", "d", 1)])
        out = relations.render(g, {})
        assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out
