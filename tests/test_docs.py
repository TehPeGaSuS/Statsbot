"""The documentation site (docs/index.html) is generated from the markdown files: keep it fresh and sound."""
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("markdown")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import build_docs  # noqa: E402
from version import __version__  # noqa: E402

PAGE = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")


def test_the_committed_page_is_up_to_date():
    assert build_docs.build() == PAGE, "docs/index.html is stale: run  python tools/build_docs.py"


def test_every_source_file_is_a_section_and_the_version_is_shown():
    for group_id, _title, filename in build_docs.GROUPS:
        assert (ROOT / filename).exists()
        assert f'<section class="group" id="{group_id}">' in PAGE
    assert f"<title>Statsbot {__version__} — Documentation</title>" in PAGE
    assert f'<span class="ver">v{__version__}</span>' in PAGE


def test_ids_are_unique_and_every_in_page_link_resolves():
    ids = re.findall(r'\sid="([^"]+)"', PAGE)
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    broken = sorted({h for h in re.findall(r'href="#([^"]+)"', PAGE) if h not in set(ids) and "+" not in h})
    assert not broken, f"links to headings that do not exist: {broken}"


def test_no_markdown_leaked_into_the_page():
    body = PAGE.split("<main>", 1)[1].split("</main>", 1)[0]
    text = re.sub(r"<pre>.*?</pre>", "", body, flags=re.S)          # code may show anything
    assert "[!WARNING]" not in text and "[!NOTE]" not in text
    assert "```" not in text and 'markdown="1"' not in text
    assert "Table of contents" not in body
    assert not re.search(r"<h[1-6][^>]*>\s*#", body), "a code comment turned into a heading"


def test_the_update_warning_is_a_warning_box_with_real_code_blocks():
    guide = PAGE.split('id="guide"', 1)[1].split('id="reference"', 1)[0]
    warning = re.search(r'<div class="alert warning">.*?</div>\s*(?=<p>A modern|<p>)', guide, re.S)
    assert warning and guide.count('class="alert warning"') == 1
    assert "<pre><code" in guide.split('class="alert warning"', 1)[1].split("A modern IRC statistics bot", 1)[0]


def test_the_page_is_self_contained():
    """No external script, stylesheet, font or image: it must work behind the strict CSP in docs/_headers."""
    assert not re.search(r"<script[^>]+src=", PAGE)
    assert not re.search(r"<link[^>]+rel=[\"']?stylesheet", PAGE)
    assert not re.search(r"@import|url\(\s*[\"']?https?:", PAGE)
    assert not re.search(r"<img[^>]+src=[\"']https?:", PAGE)


def test_external_links_are_safe():
    for tag in re.findall(r'<a [^>]*href="https?://[^"]+"[^>]*>', PAGE):
        assert 'rel="noopener"' in tag, tag


def test_the_search_index_points_at_real_headings():
    raw = re.search(r'<script type="application/json" id="search-index">(.*?)</script>', PAGE, re.S).group(1)
    index = json.loads(raw)
    ids = set(re.findall(r'\sid="([^"]+)"', PAGE))
    assert len(index) > 30
    assert all(e["id"] in ids and e["title"] and e["group"] for e in index)
    assert any("karma" in e["title"].lower() for e in index)
    assert any("auto-login" in e["text"].lower() or "autologin" in e["text"].lower() for e in index)


def test_the_generator_check_mode_passes(capsys):
    sys.argv = ["build_docs.py", "--check"]
    assert build_docs.main() == 0


class TestCloudflareSetup:
    def test_wrangler_config_serves_the_docs_folder_under_the_expected_name(self):
        config = json.loads(re.sub(r"^\s*//.*$", "", (ROOT / "wrangler.jsonc").read_text(), flags=re.M))
        assert config["name"] == "statsbot-docs"
        assert config["assets"]["directory"] == "./docs"
        assert (ROOT / "docs" / "index.html").exists()

    def test_only_the_page_and_the_headers_file_are_published(self):
        assert sorted(p.name for p in (ROOT / "docs").iterdir()) == ["_headers", "index.html"]

    def test_the_security_headers_do_not_allow_anything_external(self):
        headers = (ROOT / "docs" / "_headers").read_text()
        csp = re.search(r"Content-Security-Policy: (.*)", headers).group(1)
        assert "default-src 'none'" in csp and "http" not in csp and "*" not in csp
        assert "X-Content-Type-Options: nosniff" in headers and "frame-ancestors 'none'" in csp
