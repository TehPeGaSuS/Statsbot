#!/usr/bin/env python3
"""
tools/build_docs.py — build the documentation site (docs/index.html) from the markdown files.

    python tools/build_docs.py            # write docs/index.html
    python tools/build_docs.py --check    # exit 1 if docs/index.html is out of date

The single source of truth stays README.md, DOCS.md, TRANSLATE.md and CHANGELOG.md; this turns
them into ONE self-contained page (no external scripts, fonts or images) with a sidebar, search,
copy buttons and a light/dark theme. The result is committed, so the Cloudflare deploy needs no
build step, and tests/test_docs.py fails if the committed page is stale.

Needs the "markdown" package (requirements-dev.txt).
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

import markdown
from markdown.extensions.toc import slugify as _default_slugify

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402

OUTPUT = ROOT / "docs" / "index.html"
REPO_URL = "https://github.com/TehPeGaSuS/Statsbot"

# (id, sidebar title, file)
GROUPS = [
    ("guide", "Guide", "README.md"),
    ("reference", "Configuration reference", "DOCS.md"),
    ("translating", "Translating", "TRANSLATE.md"),
    ("changelog", "Changelog", "CHANGELOG.md"),
]

# links between the markdown files become links between the sections of the page
FILE_LINKS = {"README.md": "#guide", "DOCS.md": "#reference",
              "TRANSLATE.md": "#translating", "CHANGELOG.md": "#changelog"}


# ─── markdown -> html ────────────────────────────────────────────────────────────────────────

FENCE_RE = re.compile(r"^(?:>\s*)?(```|~~~)")
ALERT_START_RE = re.compile(r"^> \[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$")


def _backslash_breaks(text: str) -> str:
    """GitHub turns a backslash at the end of a line into a line break; python-markdown does not."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and line.endswith("\\") and not line.endswith("\\\\"):
            line = line[:-1] + "  "
        out.append(line)
    return "\n".join(out)


def _alert_blocks(text: str) -> str:
    """GitHub's "> [!WARNING]" blocks become real containers, so lists and code blocks work inside."""
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        match = ALERT_START_RE.match(lines[i])
        if not match:
            out.append(lines[i])
            i += 1
            continue
        kind = match.group(1)
        i += 1
        block = []
        while i < len(lines) and lines[i].startswith(">"):
            block.append(re.sub(r"^> ?", "", lines[i]))
            i += 1
        out += [f'<div class="alert {kind.lower()}" markdown="1">',
                f'<p class="alert-title">{kind.capitalize()}</p>', "", *block, "", "</div>", ""]
    return "\n".join(out)


def _prepare(group_id: str, text: str) -> str:
    """Cut what makes no sense on a web page: page titles, badges, the table of contents."""
    lines = text.split("\n")
    if lines and lines[0].startswith("# "):            # the file's own title: the page has a header
        lines = lines[1:]
    text = _backslash_breaks("\n".join(lines))
    text = re.sub(r"^!\[[^\]]*\]\(https?://[^)]*\)\s*$", "", text, flags=re.M)      # badge images
    text = re.sub(r"^\*\*Documentation:\*\*.*$", "", text, flags=re.M)                 # this page IS the documentation
    if group_id == "reference":                          # the sidebar replaces the printed ToC
        text = re.sub(r"^## Table of contents\n.*?(?=^## )", "", text, flags=re.S | re.M)
    if group_id == "translating":                        # the English text; the rest is on GitHub
        head, _, rest = text.partition("## English")
        body = rest.split("\n## ", 1)[0]
        body = re.sub(r"^### ", "## ", body, flags=re.M)
        text = (head.strip() + "\n\n> [!NOTE]\n> This page shows the English text. "
                f"The guide is also written in Português, Français, Italiano and Nederlands: "
                f"see [TRANSLATE.md]({REPO_URL}/blob/main/TRANSLATE.md).\n\n" + body.strip() + "\n")
    return _alert_blocks(text)


def _slugifier(prefix: str):
    def slugify(value, separator):
        value = value.strip()
        version = re.match(r"^\[(\d+(?:\.\d+)+)\]", value)          # "[1.1.0] - 2026-09-30" -> v1-1-0
        base = ("v" + version.group(1).replace(".", "-")) if version else _default_slugify(value, separator)
        return f"{prefix}-{base}" if base else prefix
    return slugify


def _flatten(tokens):
    for token in tokens:
        yield token
        yield from _flatten(token.get("children", []))


def _decorate(body: str, group_id: str, ids: set) -> str:
    # links between files and to headings of the same file
    body = re.sub(r'href="([A-Za-z]+\.md)"', lambda m: f'href="{FILE_LINKS.get(m.group(1), m.group(0)[6:-1])}"', body)

    def anchor(match):
        target = match.group(1)
        return f'href="#{group_id}-{target}"' if f"{group_id}-{target}" in ids else match.group(0)
    body = re.sub(r'href="#([^"]+)"', anchor, body)
    body = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" target="_blank" rel="noopener"', body)
    body = re.sub(r"<pre><code", '<div class="code"><button type="button" class="copy">Copy</button><pre><code', body)
    body = body.replace("</code></pre>", "</code></pre></div>")
    body = body.replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")
    return body


def render_group(group_id: str, path: Path):
    """-> (html of the section body, toc tokens, set of heading ids)"""
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists", "md_in_html"],
        extension_configs={"toc": {"slugify": _slugifier(group_id), "permalink": "#", "toc_depth": "2-3"}},
    )
    body = md.convert(_prepare(group_id, path.read_text(encoding="utf-8")))
    ids = {t["id"] for t in _flatten(md.toc_tokens)}
    return _decorate(body, group_id, ids), md.toc_tokens, ids


# ─── page pieces ─────────────────────────────────────────────────────────────────────────────

def _sidebar(groups) -> str:
    out = []
    for group_id, title, tokens in groups:
        out.append(f'<div class="nav-group"><a class="nav-title" href="#{group_id}">{html.escape(title)}</a>')
        for h2 in tokens:
            out.append(f'<div class="nav-h2"><a href="#{h2["id"]}">{html.escape(_plain(h2["name"]))}</a>')
            for h3 in h2.get("children", []):
                out.append(f'<a class="nav-h3" href="#{h3["id"]}">{html.escape(_plain(h3["name"]))}</a>')
            out.append("</div>")
        out.append("</div>")
    return "\n".join(out)


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text))


HEADING_RE = re.compile(r'<h([23]) id="([^"]+)">(.*?)</h\1>', re.S)


def _search_index(sections) -> list:
    """One entry per h2/h3: its title and the text below it, for the client-side search."""
    entries = []
    for group_title, body in sections:
        pieces = HEADING_RE.split(body)
        # pieces = [intro, level, id, title, text, level, id, title, text, ...]
        for i in range(1, len(pieces) - 3, 4):
            _, hid, title, text = pieces[i:i + 4]
            text = re.sub(r"<button.*?</button>", "", text, flags=re.S)
            text = re.sub(r"\s+", " ", _plain(re.sub(r"<[^>]+>", " ", text))).strip()
            entries.append({"id": hid, "title": _plain(title).rstrip("#").strip(),
                            "group": group_title, "text": text[:1500]})
    return entries


CSS = r"""
:root{--bg:#0d0d1a;--bg2:#1a1a2e;--bg3:#1e2245;--border:#2a3860;--text:#c8d3f5;--muted:#8a94c0;--faint:#3d4a6b;
--blue:#7aa2f7;--green:#9ece6a;--yellow:#e0af68;--red:#f7768e;--cyan:#7dcfff;--accent:#3850B8;--code:#11142b}
@media (prefers-color-scheme:light){:root:not([data-theme]){--bg:#f5f6fa;--bg2:#fff;--bg3:#e8eaf0;--border:#c5cade;
--text:#1e2245;--muted:#5b6390;--faint:#9299b8;--blue:#3558d6;--green:#3a7d0e;--yellow:#a06000;--red:#c0132a;
--cyan:#0077aa;--accent:#3558d6;--code:#eef0f7}}
:root[data-theme=light]{--bg:#f5f6fa;--bg2:#fff;--bg3:#e8eaf0;--border:#c5cade;--text:#1e2245;--muted:#5b6390;
--faint:#9299b8;--blue:#3558d6;--green:#3a7d0e;--yellow:#a06000;--red:#c0132a;--cyan:#0077aa;--accent:#3558d6;--code:#eef0f7}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:72px}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.65 'Segoe UI',Tahoma,system-ui,sans-serif}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:14px;padding:10px 20px;
background:var(--bg2);border-bottom:1px solid var(--border)}
header .brand{font-weight:700;font-size:1.1rem;color:var(--text)}header .brand span{color:var(--blue)}
.ver{font-size:.75rem;padding:2px 9px;border-radius:12px;border:1px solid var(--border);color:var(--muted)}
header .sp{flex:1}
#q{width:min(340px,40vw);padding:7px 11px;border-radius:8px;border:1px solid var(--border);background:var(--bg);
color:var(--text);font:inherit}#q:focus{outline:2px solid var(--accent)}
button.icon{background:none;border:1px solid var(--border);color:var(--text);border-radius:8px;padding:6px 10px;
cursor:pointer;font:inherit}
#menu{display:none}
#results{position:absolute;right:20px;top:54px;width:min(520px,calc(100vw - 40px));max-height:70vh;overflow:auto;
background:var(--bg2);border:1px solid var(--border);border-radius:10px;box-shadow:0 10px 30px #0006;display:none}
#results a{display:block;padding:9px 14px;border-bottom:1px solid var(--border);color:var(--text)}
#results a:hover,#results a.sel{background:var(--bg3);text-decoration:none}
#results b{color:var(--blue)}#results small{display:block;color:var(--muted)}
#results mark{background:#e0af6855;color:inherit;border-radius:3px}
.layout{display:flex;align-items:flex-start}
nav{position:sticky;top:54px;flex:0 0 290px;height:calc(100vh - 54px);overflow:auto;padding:18px 12px 40px 20px;
border-right:1px solid var(--border);font-size:.86rem}
.nav-title{display:block;margin:16px 0 4px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
font-size:.72rem;color:var(--muted)}
.nav-h2>a:first-child{display:block;padding:3px 8px;border-radius:6px;color:var(--text)}
.nav-h2>a:first-child:hover{background:var(--bg3);text-decoration:none}
.nav-h3{display:none;padding:1px 8px 1px 22px;color:var(--muted);font-size:.82rem}
.nav-h2.open>.nav-h3{display:block}.nav-h2.open>a:first-child{background:var(--bg3);color:var(--blue)}
main{flex:1;min-width:0;padding:8px 40px 80px;max-width:980px}
section.group{padding-top:8px}
.group-title{font-size:1.9rem;margin:38px 0 6px;padding-bottom:8px;border-bottom:2px solid var(--accent)}
h2{font-size:1.4rem;margin:42px 0 8px;padding-bottom:5px;border-bottom:1px solid var(--border)}
h3{font-size:1.1rem;margin:28px 0 6px;color:var(--cyan)}
.headerlink{opacity:0;margin-left:8px;font-size:.8em;color:var(--faint)}
h2:hover .headerlink,h3:hover .headerlink{opacity:1;text-decoration:none}
p,ul,ol{margin:.7em 0}li{margin:.25em 0}
code{font:.88em ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);
padding:.12em .4em;border-radius:5px;border:1px solid var(--border)}
.code{position:relative;margin:1em 0}
pre{margin:0;padding:14px 16px;overflow:auto;background:var(--code);border:1px solid var(--border);border-radius:10px}
pre code{background:none;border:0;padding:0;font-size:.86rem;line-height:1.55}
.copy{position:absolute;top:8px;right:8px;opacity:0;background:var(--bg3);color:var(--text);
border:1px solid var(--border);border-radius:6px;padding:2px 9px;font-size:.75rem;cursor:pointer}
.code:hover .copy,.copy:focus{opacity:1}
.table-scroll{overflow-x:auto;margin:1em 0}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{border:1px solid var(--border);padding:7px 11px;text-align:left;vertical-align:top}
th{background:var(--bg3)}tr:nth-child(even) td{background:#ffffff08}
blockquote{margin:1em 0;padding:.2em 1em;border-left:4px solid var(--faint);color:var(--muted)}
.alert{margin:1.2em 0;padding:.7em 1.1em;border-radius:10px;border:1px solid var(--border);border-left-width:5px;background:var(--bg2)}
.alert p{margin:.4em 0}.alert-title{font-weight:700;text-transform:uppercase;font-size:.75rem;letter-spacing:.06em}
.alert.note{border-left-color:var(--blue)}.alert.note .alert-title{color:var(--blue)}
.alert.tip{border-left-color:var(--green)}.alert.tip .alert-title{color:var(--green)}
.alert.important{border-left-color:var(--cyan)}.alert.important .alert-title{color:var(--cyan)}
.alert.warning{border-left-color:var(--yellow);background:#e0af6812}.alert.warning .alert-title{color:var(--yellow)}
.alert.caution{border-left-color:var(--red);background:#f7768e12}.alert.caution .alert-title{color:var(--red)}
footer{margin:60px 0 0;padding-top:18px;border-top:1px solid var(--border);color:var(--muted);font-size:.85rem}
hr{border:0;border-top:1px solid var(--border);margin:2em 0}
@media (max-width:860px){
#menu{display:inline-block}nav{position:fixed;left:0;top:54px;z-index:15;width:min(310px,88vw);background:var(--bg2);
transform:translateX(-105%);transition:transform .2s}body.nav-open nav{transform:none;box-shadow:0 0 40px #0008}
main{padding:4px 18px 60px}.layout{display:block}#q{width:40vw}}
"""

JS = r"""
(function(){
var root=document.documentElement;
try{var t=localStorage.getItem('statsbot-docs-theme');if(t)root.setAttribute('data-theme',t)}catch(e){}
document.getElementById('theme').addEventListener('click',function(){
  var light=root.getAttribute('data-theme')?root.getAttribute('data-theme')==='light'
    :window.matchMedia('(prefers-color-scheme: light)').matches;
  var next=light?'dark':'light';root.setAttribute('data-theme',next);
  try{localStorage.setItem('statsbot-docs-theme',next)}catch(e){}});
document.getElementById('menu').addEventListener('click',function(){document.body.classList.toggle('nav-open')});
document.querySelector('nav').addEventListener('click',function(e){if(e.target.tagName==='A')document.body.classList.remove('nav-open')});
document.querySelectorAll('.copy').forEach(function(b){b.addEventListener('click',function(){
  var code=b.parentNode.querySelector('code').innerText;
  var done=function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy'},1200)};
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(code).then(done,done)}else{done()}})});
/* sidebar: open the section being read */
var heads=[].slice.call(document.querySelectorAll('main h2[id]'));
var navs={};document.querySelectorAll('.nav-h2').forEach(function(d){navs[d.firstChild.getAttribute('href').slice(1)]=d});
function spy(){var cur=null,y=window.scrollY+90;heads.forEach(function(h){if(h.offsetTop<=y)cur=h.id});
  Object.keys(navs).forEach(function(k){navs[k].classList.toggle('open',k===cur)})}
window.addEventListener('scroll',spy,{passive:true});spy();
/* search */
var index=JSON.parse(document.getElementById('search-index').textContent);
var q=document.getElementById('q'),box=document.getElementById('results'),sel=-1;
function esc(s){return s.replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function mark(s,terms){s=esc(s);terms.forEach(function(t){if(t)s=s.replace(new RegExp('('+t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','ig'),'<mark>$1</mark>')});return s}
function search(){
  var terms=q.value.toLowerCase().split(/\s+/).filter(Boolean);sel=-1;
  if(!terms.length){box.style.display='none';return}
  var hits=[];index.forEach(function(e){var title=e.title.toLowerCase(),text=e.text.toLowerCase(),score=0,ok=true;
    terms.forEach(function(t){var a=title.indexOf(t)>=0,b=text.indexOf(t)>=0;if(!a&&!b)ok=false;score+=a?10:0;score+=b?1:0});
    if(ok)hits.push([score,e])});
  hits.sort(function(a,b){return b[0]-a[0]});hits=hits.slice(0,10);
  if(!hits.length){box.innerHTML='<a><small>No results</small></a>';box.style.display='block';return}
  box.innerHTML=hits.map(function(h){var e=h[1],at=Math.max(0,e.text.toLowerCase().indexOf(terms[0])-40);
    return'<a href="#'+e.id+'"><b>'+mark(e.title,terms)+'</b> <small>'+esc(e.group)+'</small><small>'
      +mark(e.text.substr(at,150),terms)+'</small></a>'}).join('');
  box.style.display='block'}
q.addEventListener('input',search);
box.addEventListener('click',function(){box.style.display='none'});
document.addEventListener('keydown',function(e){
  if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus();return}
  if(document.activeElement!==q)return;
  var items=[].slice.call(box.querySelectorAll('a[href]'));
  if(e.key==='Escape'){q.value='';box.style.display='none';q.blur()}
  else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();if(!items.length)return;
    sel=(sel+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items.forEach(function(a,i){a.classList.toggle('sel',i===sel)})}
  else if(e.key==='Enter'&&items.length){location.hash=items[Math.max(sel,0)].getAttribute('href');box.style.display='none'}});
})();
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Statsbot @@VERSION@@ — Documentation</title>
<meta name="description" content="Statsbot: a modern IRC statistics bot, pisg for the 21st century. Real-time stats, live web dashboard, multi-network. Installation, configuration and command reference.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%233850B8'/%3E%3Cpath d='M7 22h4v-7H7zm7 0h4V9h-4zm7 0h4v-10h-4z' fill='%23fff'/%3E%3C/svg%3E">
<style>@@CSS@@</style>
</head>
<body>
<header>
  <button class="icon" id="menu" type="button" aria-label="Menu">&#9776;</button>
  <a class="brand" href="#guide">Stats<span>bot</span></a><span class="ver">v@@VERSION@@</span>
  <span class="sp"></span>
  <input id="q" type="search" placeholder="Search the docs  ( / )" autocomplete="off" aria-label="Search">
  <button class="icon" id="theme" type="button" title="Light / dark">&#9681;</button>
  <a href="@@REPO@@" target="_blank" rel="noopener">GitHub</a>
  <div id="results"></div>
</header>
<div class="layout">
<nav aria-label="Contents">
@@SIDEBAR@@
</nav>
<main>
@@SECTIONS@@
<footer>Statsbot @@VERSION@@ &middot; MIT licence &middot; <a href="@@REPO@@" target="_blank" rel="noopener">source on GitHub</a>
&middot; inspired by <a href="https://pisg.github.io/" target="_blank" rel="noopener">pisg</a>.
This page is generated from README.md, DOCS.md, TRANSLATE.md and CHANGELOG.md.</footer>
</main>
</div>
<script type="application/json" id="search-index">@@INDEX@@</script>
<script>@@JS@@</script>
</body>
</html>
"""


def build() -> str:
    sidebar_groups, sections, search_sections = [], [], []
    for group_id, title, filename in GROUPS:
        body, tokens, _ids = render_group(group_id, ROOT / filename)
        sidebar_groups.append((group_id, title, tokens))
        section = (f'<section class="group" id="{group_id}"><h1 class="group-title">{html.escape(title)}</h1>\n'
                   f"{body}\n</section>")
        sections.append(section)
        search_sections.append((title, body))
    index = json.dumps([e for title, body in search_sections for e in _search_index([(title, body)])],
                       ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = PAGE
    for token, value in (("@@CSS@@", CSS), ("@@JS@@", JS), ("@@SIDEBAR@@", _sidebar(sidebar_groups)),
                         ("@@SECTIONS@@", "\n".join(sections)), ("@@INDEX@@", index),
                         ("@@REPO@@", REPO_URL), ("@@VERSION@@", __version__)):
        page = page.replace(token, value)
    return page


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--check", action="store_true", help="fail if docs/index.html is not up to date")
    args = parser.parse_args()
    page = build()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != page:
            print("docs/index.html is out of date: run  python tools/build_docs.py", file=sys.stderr)
            return 1
        print("docs/index.html is up to date")
        return 0
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({len(page):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
