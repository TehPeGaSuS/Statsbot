"""
web/relations.py
The "Who talks to whom" map of the stats page.

A force-directed graph of every speaker is unreadable on a channel with hundreds of them, so this
draws something else:

  * only the CORE: the `max_nodes` (default 40, at most 60) most connected nicks, and their
    strongest links, capped at a few per nick;
  * the nicks are split into COMMUNITIES (people who mostly talk to each other) and sit on a ring,
    each community in its own arc and colour: groups are visible at a glance;
  * links are curves across the ring (a chord diagram): their width is the number of mentions;
  * the layout is computed here, deterministically, so the page needs no physics engine, and the
    drawing has a hard size limit however big the channel is;
  * a little script lets the reader trace one nick (hover / focus / click), and show fewer nicks
    or only the stronger links. Without JavaScript the picture is complete but static.

Everything here is a pure function of the pair counts: no database, no I/O.
"""
import html
import math
from typing import Dict, List, Optional

MIN_NODES = 5
MIN_MAP_NODES = 4           # below this a ring is a silly picture: only the pairs table is shown
MAX_NODES = 60
DEFAULT_NODES = 40
EDGES_PER_NODE = 4          # hard cap on drawn links: EDGES_PER_NODE * number of nicks
SHOWN_EDGES_PER_NODE = 1.8  # how many links are visible to start with (the slider can show more)
RING = 300                  # radius of the ring in the SVG's own units
GAP = 1.6                   # extra room, in node slots, between two communities

# the page options of this section (pisg-style names), with their defaults
OPTION_DEFAULTS = {"ShowRelations": True, "RelationNicks": DEFAULT_NODES, "RelationMinWeight": 0, "RelationPairs": 10,
                   "RelationMap": "closed"}          # "closed" (default), "open" or "off"


def clamp_int(value, default: int, low: int, high: int) -> int:
    """An option as an int inside [low, high]; anything that is not a number gives the default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


# ─── data ────────────────────────────────────────────────────────────────────────────────────

def _aggregate(pairs: List[Dict]):
    """Directed rows -> undirected links. Returns (links, names) where
    links[(a, b)] = {"w": total, "ab": a->b, "ba": b->a} with a < b (lowercase keys)."""
    links: Dict[tuple, dict] = {}
    names: Dict[str, str] = {}
    for row in pairs:
        f, t, n = row["from_nick"], row["to_nick"], int(row["n"])
        fl, tl = f.lower(), t.lower()
        if fl == tl or n <= 0:
            continue
        names.setdefault(fl, f)
        names.setdefault(tl, t)
        a, b = (fl, tl) if fl < tl else (tl, fl)
        link = links.setdefault((a, b), {"w": 0, "ab": 0, "ba": 0})
        link["w"] += n
        link["ab" if fl == a else "ba"] += n
    return links, names


def _communities(nodes: List[str], links: Dict[tuple, dict]) -> Dict[str, int]:
    """Greedy modularity maximisation (Clauset-Newman-Moore) on a small graph. Deterministic:
    ties are broken by the (sorted) community names. Returns {node: community number}."""
    total = sum(link["w"] for link in links.values())
    if total == 0:
        return {n: i for i, n in enumerate(nodes)}
    two_m = 2.0 * total
    degree = {n: 0.0 for n in nodes}
    between: Dict[str, Dict[str, float]] = {n: {} for n in nodes}      # community -> community -> weight
    for (a, b), link in links.items():
        if a in degree and b in degree:
            degree[a] += link["w"]
            degree[b] += link["w"]
            between[a][b] = between[a].get(b, 0.0) + link["w"]
            between[b][a] = between[b].get(a, 0.0) + link["w"]
    members = {n: [n] for n in nodes}
    tot = dict(degree)
    while True:
        best, best_gain = None, 1e-12
        for c1 in sorted(between):
            for c2 in sorted(between[c1]):
                if c2 <= c1:
                    continue
                gain = 2.0 * (between[c1][c2] / two_m - tot[c1] * tot[c2] / (two_m * two_m))
                if gain > best_gain:
                    best, best_gain = (c1, c2), gain
        if best is None:
            break
        keep, gone = best
        members[keep].extend(members.pop(gone))
        tot[keep] += tot.pop(gone)
        for other, w in between.pop(gone).items():
            if other == keep:
                continue
            between[other].pop(gone, None)
            between[keep][other] = between[keep].get(other, 0.0) + w
            between[other][keep] = between[keep][other]
        between[keep].pop(gone, None)
    result: Dict[str, int] = {}
    for number, key in enumerate(sorted(members, key=lambda c: (-tot[c], c))):
        for node in members[key]:
            result[node] = number
    return result


def _ring_cost(blocks, weights) -> float:
    """Sum of weight x distance along the ring over all links: the smaller, the shorter the
    chords, and the fewer of them cross the middle. `blocks` is a list of node lists (one per
    community); a gap of GAP slots separates two blocks."""
    position, at = {}, 0.0
    for block in blocks:
        for n in block:
            position[n] = at
            at += 1
        at += GAP
    span = at
    total = 0.0
    for (a, b), w in weights.items():
        d = abs(position[a] - position[b])
        total += w * min(d, span - d)
    return total


def _arrange(blocks, weights, passes: int = 8):
    """Improve the order of the nicks inside each community, and of the communities themselves,
    by swapping neighbours while that shortens the weighted chords. Deterministic; the cost of
    a pass is small because there are at most MAX_NODES nicks."""
    blocks = [list(b) for b in blocks]
    best = _ring_cost(blocks, weights)
    for _ in range(passes):
        improved = False
        for block in blocks:
            for i in range(len(block) - 1):
                block[i], block[i + 1] = block[i + 1], block[i]
                cost = _ring_cost(blocks, weights)
                if cost < best - 1e-9:
                    best, improved = cost, True
                else:
                    block[i], block[i + 1] = block[i + 1], block[i]
        for i in range(len(blocks) - 1):
            blocks[i], blocks[i + 1] = blocks[i + 1], blocks[i]
            cost = _ring_cost(blocks, weights)
            if cost < best - 1e-9:
                best, improved = cost, True
            else:
                blocks[i], blocks[i + 1] = blocks[i + 1], blocks[i]
        if not improved:
            break
    return blocks


def build_graph(pairs: List[Dict], max_nodes: int = DEFAULT_NODES, min_weight: int = 0,
                activity: Optional[Dict[str, int]] = None) -> Optional[dict]:
    """Directed pair counts -> what to draw. None when there is nothing to draw.

    `activity` (lower-case nick -> lines said) sizes the dots by how much each nick talks; without
    it they are sized by how many mentions they are part of.

    min_weight > 0 fixes the weight a link needs to be visible at first; 0 picks it so that
    about SHOWN_EDGES_PER_NODE links per nick are visible."""
    max_nodes = max(MIN_NODES, min(int(max_nodes), MAX_NODES))
    links, names = _aggregate(pairs)
    if not links:
        return None

    strength: Dict[str, int] = {}
    for (a, b), link in links.items():
        strength[a] = strength.get(a, 0) + link["w"]
        strength[b] = strength.get(b, 0) + link["w"]
    everyone = len(strength)
    chosen = sorted(strength, key=lambda n: (-strength[n], n))[:max_nodes]
    chosen_set = set(chosen)
    inside = {k: v for k, v in links.items() if k[0] in chosen_set and k[1] in chosen_set}

    # the links to draw: everybody keeps their strongest one, then the heaviest fill up to the cap
    cap = EDGES_PER_NODE * len(chosen)
    keep = set()
    for n in chosen:
        mine = [(v["w"], k) for k, v in inside.items() if n in k]
        if mine:
            keep.add(max(mine, key=lambda x: (x[0], x[1][0], x[1][1]))[1])
    for k in sorted(inside, key=lambda k: (-inside[k]["w"], k)):
        if len(keep) >= cap:
            break
        keep.add(k)
    inside = {k: inside[k] for k in keep}

    community = _communities(chosen, inside)
    by_community: Dict[int, List[str]] = {}
    for n in chosen:
        by_community.setdefault(community[n], []).append(n)
    ordered_communities = sorted(by_community, key=lambda c: (-sum(strength[n] for n in by_community[c]), c))
    sized = [c for c in ordered_communities if len(by_community[c]) > 1]
    singles = [c for c in ordered_communities if len(by_community[c]) == 1]
    blocks = [sorted(by_community[c], key=lambda n: (-strength[n], n)) for c in sized]
    colour_of = {n: index for index, block in enumerate(blocks) for n in block}
    if singles:                                                         # loners share one grey arc
        blocks.append(sorted((n for c in singles for n in by_community[c]), key=lambda n: (-strength[n], n)))
        colour_of.update({n: -1 for n in blocks[-1]})
    blocks = _arrange(blocks, {k: v["w"] for k, v in inside.items()})
    ring_order = [n for block in blocks for n in block]
    cluster_of = colour_of

    arcs = len(sized) + (1 if singles else 0)
    slots = len(ring_order) + GAP * arcs
    step = 2 * math.pi / slots
    angle = -math.pi / 2 + GAP * step / 2
    rank = {n: i for i, n in enumerate(chosen)}                          # by strength, 0 = most connected
    weight = strength
    if activity and any(activity.get(n, 0) > 0 for n in chosen):
        weight = {n: max(0, activity.get(n, 0)) for n in chosen}
    top = max(weight[n] for n in chosen)
    nodes = []
    previous = None
    for n in ring_order:
        if previous is not None and cluster_of[n] != cluster_of[previous]:
            angle += GAP * step
        nodes.append({
            "key": n, "nick": names[n], "strength": strength[n], "rank": rank[n],
            "cluster": cluster_of[n], "angle": angle,
            "x": round(RING * math.cos(angle), 1), "y": round(RING * math.sin(angle), 1),
            "size": round(4 + 9 * math.sqrt(weight[n] / top), 1),
            "degree": sum(1 for k in inside if n in k),
        })
        angle += step
        previous = n
    index_of = {node["key"]: i for i, node in enumerate(nodes)}

    edges = []
    for (a, b), link in sorted(inside.items(), key=lambda kv: (-kv[1]["w"], kv[0])):
        edges.append({"a": index_of[a], "b": index_of[b], "w": link["w"],
                      "ab": link["ab"], "ba": link["ba"],
                      "same": nodes[index_of[a]]["cluster"] == nodes[index_of[b]]["cluster"]
                              and nodes[index_of[a]]["cluster"] >= 0})
    weights = sorted((e["w"] for e in edges), reverse=True)
    if min_weight and min_weight > 0:
        start = int(min_weight)
    else:
        wanted = max(1, int(SHOWN_EDGES_PER_NODE * len(nodes)))
        start = weights[wanted - 1] if len(weights) > wanted else 1
    return {
        "nodes": nodes, "edges": edges, "clusters": len(sized),
        "everyone": everyone, "start_weight": max(1, start), "max_weight": weights[0] if weights else 1,
    }


def closest_pairs(pairs: List[Dict], limit: int = 10) -> List[Dict]:
    """The `limit` strongest links (both directions added): [{a, b, w, ab, ba}] with a -> b the
    busier direction."""
    links, names = _aggregate(pairs)
    rows = []
    for (a, b), link in links.items():
        first, second, fwd, back = (a, b, link["ab"], link["ba"]) if link["ab"] >= link["ba"] else (b, a, link["ba"], link["ab"])
        rows.append({"a": names[first], "b": names[second], "w": link["w"], "ab": fwd, "ba": back})
    rows.sort(key=lambda r: (-r["w"], r["a"].lower(), r["b"].lower()))
    return rows[:max(0, int(limit))]


# ─── drawing ─────────────────────────────────────────────────────────────────────────────────

def _colour(cluster: int) -> str:
    if cluster < 0:
        return "hsl(220 9% 55%)"
    return f"hsl({round((cluster * 137.508 + 208) % 360)} 62% 55%)"      # golden-angle hues stay apart


def _e(text) -> str:
    return html.escape(str(text), quote=True)


def _fmt(template: str, fallback: str, **values) -> str:
    """template.format(**values), but a translation with a wrong placeholder falls back to the
    English text instead of breaking the whole page."""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return fallback.format(**values)


def _curve(a: dict, b: dict) -> str:
    """A quadratic curve pulled towards the centre: neighbours barely bend, opposite nodes pass close to it."""
    gap = abs(a["angle"] - b["angle"]) % (2 * math.pi)
    gap = min(gap, 2 * math.pi - gap)
    pull = 0.12 + 0.78 * (gap / math.pi)
    cx, cy = (a["x"] + b["x"]) / 2 * (1 - pull), (a["y"] + b["y"]) / 2 * (1 - pull)
    return f"M{a['x']} {a['y']}Q{round(cx, 1)} {round(cy, 1)} {b['x']} {b['y']}"


SUMMARY = "Show the map ({n} nicks)"
LINK_TITLE = "{a} ↔ {b}: {w} mentions ({a} → {b}: {ab}, {b} → {a}: {ba})"
NICK_TITLE = "{nick}: {links} links, {w} mentions"

CSS = """
#rel{margin:1rem 0}
#rel summary{cursor:pointer;color:var(--blue);padding:.4rem 0}
#rel summary:hover{text-decoration:underline}
#rel svg{display:block;width:100%;max-width:820px;height:auto;margin:0 auto}
#rel .edge{fill:none;stroke-linecap:round;opacity:var(--o,.5);transition:opacity .15s}
#rel .edge.x{stroke:var(--muted)}
#rel .node circle{stroke:var(--bg2);stroke-width:2;cursor:pointer}
#rel .node text{fill:var(--text);font-size:13px;cursor:pointer}
#rel .node:focus{outline:none}#rel .node:focus circle{stroke:var(--blue)}
#rel.dim .edge{opacity:.06}#rel.dim .node{opacity:.28}
#rel.dim .edge.on{opacity:.9}#rel.dim .node.on{opacity:1}
#rel .off,#rel .gone{display:none}
#rel .node.centre circle{stroke:var(--text);stroke-width:4}
#rel .node.centre text{font-weight:bold}
#rel .rel-pick{color:var(--muted);font-size:.85rem}
#rel .rel-pick select{background:var(--bg2);color:var(--text);border:1px solid var(--border,var(--muted));border-radius:4px;padding:.15rem .3rem;max-width:12rem}
#rel .js-only{display:none}
#rel.js .rel-controls{display:flex;align-items:center;flex-wrap:wrap;gap:.6rem 1.6rem;justify-content:center;font-size:.85rem;color:var(--muted);margin-top:.4rem}
#rel.js .rel-hint.js-only{display:block}
#rel .rel-controls input{vertical-align:middle;width:9rem}
#rel .rel-hint{text-align:center;color:var(--muted);font-size:.8rem;margin:.2rem 0}
"""

JS = r"""
(function(){
var root=document.getElementById('rel');if(!root)return;
root.classList.add('js');        /* shows the hint and the sliders: without JavaScript they would do nothing */
var svg=root.querySelector('svg'),nodes=[].slice.call(root.querySelectorAll('.node')),edges=[].slice.call(root.querySelectorAll('.edge'));
var kIn=root.querySelector('#rel-k'),wIn=root.querySelector('#rel-w'),kOut=root.querySelector('#rel-kv'),wOut=root.querySelector('#rel-wv');
var w0=+root.getAttribute('data-start-weight')||1;      /* used when there is no slider for it */
var pinned=null,pick=root.querySelector('#rel-pick');
var home=nodes.map(function(n){var c=n.querySelector('circle'),t=n.querySelector('text');
  return {c:c,t:t,cx:c.getAttribute('cx'),cy:c.getAttribute('cy'),tt:t.getAttribute('transform'),ta:t.getAttribute('text-anchor')}});
var homeD=edges.map(function(e){return e.getAttribute('d')});
function visibleNode(n){return !kIn||+n.getAttribute('data-rank')<+kIn.value}
function apply(){
  var w=wIn?+wIn.value:w0;if(kOut)kOut.textContent=kIn.value;if(wOut)wOut.textContent=w;
  nodes.forEach(function(n){n.classList.toggle('off',!visibleNode(n))});
  edges.forEach(function(e){var a=nodes[+e.getAttribute('data-a')],b=nodes[+e.getAttribute('data-b')];
    e.classList.toggle('off',!(visibleNode(a)&&visibleNode(b)&&+e.getAttribute('data-w')>=w))});
  focusOn(pinned)}
function rest(){
  nodes.forEach(function(n,i){var h=home[i];h.c.setAttribute('cx',h.cx);h.c.setAttribute('cy',h.cy);
    h.t.setAttribute('transform',h.tt);h.t.setAttribute('text-anchor',h.ta);n.classList.remove('centre')});
  edges.forEach(function(e,i){e.setAttribute('d',homeD[i]);e.classList.remove('gone')})}
function put(i,x,y,left){var h=home[i],r=+nodes[i].getAttribute('data-r'),dx=left?-(r+7):(r+7);
  h.c.setAttribute('cx',x);h.c.setAttribute('cy',y);
  h.t.setAttribute('transform','translate('+(x+dx)+' '+y+')');h.t.setAttribute('text-anchor',left?'end':'start')}
/* the nick in the middle, the people it talks to around it: the closer, the more they talk */
function ego(i){
  var near=[],top=1;
  edges.forEach(function(e,k){var a=+e.getAttribute('data-a'),b=+e.getAttribute('data-b');
    if(e.classList.contains('off')||(a!==i&&b!==i)){e.classList.add('gone');return}
    var other=a===i?b:a,w=+e.getAttribute('data-w');top=Math.max(top,w);near.push({k:k,o:other,w:w})});
  if(!near.length)return;
  var base=+nodes[i].getAttribute('data-a'),step=2*Math.PI/near.length;
  near.sort(function(p,q){var u=(+nodes[p.o].getAttribute('data-a')-base+8*Math.PI)%(2*Math.PI),
    v=(+nodes[q.o].getAttribute('data-a')-base+8*Math.PI)%(2*Math.PI);return u-v});
  put(i,0,0,false);nodes[i].classList.add('centre');
  var big=near.length>14;
  near.forEach(function(p,j){
    var ang=-Math.PI/2+j*step,rad=95+(1-Math.sqrt(p.w/top))*(big?175:140)+(big&&j%2?22:0),
        x=Math.round(rad*Math.cos(ang)*10)/10,y=Math.round(rad*Math.sin(ang)*10)/10;
    put(p.o,x,y,x<0);edges[p.k].setAttribute('d','M0 0L'+x+' '+y)})}
function focusOn(i){
  rest();
  root.classList.toggle('dim',i!==null);
  nodes.forEach(function(n){n.classList.remove('on')});edges.forEach(function(e){e.classList.remove('on')});
  if(pick)pick.value=(i===null?'':String(i));
  if(i===null)return;nodes[i].classList.add('on');
  edges.forEach(function(e){var a=+e.getAttribute('data-a'),b=+e.getAttribute('data-b');
    if((a===i||b===i)&&!e.classList.contains('off')){e.classList.add('on');nodes[a].classList.add('on');nodes[b].classList.add('on')}});
  if(pinned===i)ego(i)}
function choose(i){pinned=(pinned===i)?null:i;focusOn(pinned)}
nodes.forEach(function(n,i){
  n.addEventListener('mouseenter',function(){if(pinned===null)focusOn(i)});
  n.addEventListener('mouseleave',function(){if(pinned===null)focusOn(null)});
  n.addEventListener('focus',function(){if(pinned===null)focusOn(i)});
  n.addEventListener('blur',function(){if(pinned===null)focusOn(null)});
  n.addEventListener('click',function(){choose(i)});
  n.addEventListener('keydown',function(ev){if(ev.key==='Enter'||ev.key===' '){ev.preventDefault();choose(i)}})});
if(pick)pick.addEventListener('change',function(){pinned=pick.value===''?null:+pick.value;focusOn(pinned)});
svg.addEventListener('click',function(ev){if(ev.target===svg){pinned=null;focusOn(null)}});
if(kIn)kIn.addEventListener('input',apply);if(wIn)wIn.addEventListener('input',apply);apply();
})();
"""


def render(graph: Optional[dict], labels: Dict[str, str], opened: bool = False) -> str:
    """The map as an HTML fragment (style + svg + controls + script), inside a <details> block that
    is closed unless `opened`: it is a big picture, so the reader asks for it. `labels` holds the
    already-translated texts: summary, hint, caption, nicks, links, nick_title, link_title."""
    if not graph or len(graph["nodes"]) < MIN_MAP_NODES:
        return ""
    nodes, edges = graph["nodes"], graph["edges"]
    heaviest = max(e["w"] for e in edges) if edges else 1
    width = min(820, 360 + 11 * len(nodes))          # a small channel gets a small picture
    summary = _fmt(labels.get("summary", SUMMARY), SUMMARY, n=len(nodes))
    parts = [f'<div id="rel" data-start-weight="{graph["start_weight"]}"><style>', CSS, "</style>",
             f'<details{" open" if opened else ""}><summary>{_e(summary)}</summary>',
             f'<p class="rel-hint js-only">{_e(labels.get("hint", ""))}</p>',
             f'<p class="rel-hint">{_e(labels.get("caption", ""))}</p>' if labels.get("caption") else "",
             '<svg viewBox="-470 -470 940 940" role="img" '
             f'aria-label="{_e(labels.get("aria", "Who talks to whom"))}" style="max-width:{width}px" '
             'xmlns="http://www.w3.org/2000/svg">']
    for e in edges:
        a, b = nodes[e["a"]], nodes[e["b"]]
        width = round(0.8 + 5.2 * math.sqrt(e["w"] / heaviest), 2)
        fade = round(0.28 + 0.5 * math.sqrt(e["w"] / heaviest), 2)          # weak links recede
        style = f' style="--o:{fade}' + (f';stroke:{_colour(a["cluster"])}' if e["same"] else "") + '"'
        title = _fmt(labels.get("link_title", LINK_TITLE), LINK_TITLE,
                     a=a["nick"], b=b["nick"], w=e["w"], ab=e["ab"], ba=e["ba"])
        parts.append(f'<path class="edge{"" if e["same"] else " x"}" data-a="{e["a"]}" data-b="{e["b"]}" '
                     f'data-w="{e["w"]}" d="{_curve(a, b)}" stroke-width="{width}"{style}>'
                     f"<title>{_e(title)}</title></path>")
    for i, n in enumerate(nodes):
        degrees = math.degrees(n["angle"])
        left = math.cos(n["angle"]) < -1e-9
        rotate = degrees + 180 if left else degrees
        reach = n["size"] + 8
        tx, ty = round((RING + reach) * math.cos(n["angle"]), 1), round((RING + reach) * math.sin(n["angle"]), 1)
        title = _fmt(labels.get("nick_title", NICK_TITLE), NICK_TITLE,
                     nick=n["nick"], links=n["degree"], w=n["strength"])
        parts.append(
            f'<g class="node" data-rank="{n["rank"]}" data-a="{round(n["angle"], 4)}" data-r="{n["size"]}" tabindex="0" role="button">'
            f"<title>{_e(title)}</title>"
            f'<circle cx="{n["x"]}" cy="{n["y"]}" r="{n["size"]}" fill="{_colour(n["cluster"])}"/>'
            f'<text transform="translate({tx} {ty}) rotate({round(rotate, 1)})" '
            f'text-anchor="{"end" if left else "start"}" dominant-baseline="central">{_e(n["nick"])}</text></g>')
    parts.append("</svg>")
    shown = len(nodes)
    controls = []
    options = "".join(f'<option value="{i}">{_e(n["nick"])}</option>'
                      for i, n in sorted(enumerate(nodes), key=lambda p: p[1]["nick"].lower()))
    controls.append(f'<label class="rel-pick">{_e(labels.get("focus", "Focus on"))} <select id="rel-pick">'
                    f'<option value="">{_e(labels.get("everyone", "everyone"))}</option>{options}</select></label>')
    if shown > MIN_NODES:                                   # a slider that can only sit still is noise
        controls.append(
            f'<label>{_e(labels.get("nicks", "Nicks"))} <input id="rel-k" type="range" min="{MIN_NODES}" '
            f'max="{shown}" value="{shown}"> <b id="rel-kv">{shown}</b></label>')
    if graph["max_weight"] > 1:
        start = min(graph["start_weight"], graph["max_weight"])
        controls.append(
            f'<label>{_e(labels.get("links", "Links of at least"))} <input id="rel-w" type="range" min="1" '
            f'max="{graph["max_weight"]}" value="{start}"> <b id="rel-wv">{start}</b></label>')
    if controls:
        parts.append('<div class="rel-controls js-only">' + "".join(controls) + "</div>")
    parts.append(f"</details><script>{JS}</script></div>")
    return "".join(parts)
