"""The version number, the changelog and the README must agree."""
import re
from pathlib import Path

from version import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_the_version_is_semantic():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_the_changelog_has_a_section_for_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M)


def test_the_newest_changelog_section_is_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M).group(1)
    assert first == __version__


def test_the_page_footer_shows_the_version(db, sensors):
    from conftest import CHAN, NET, make_config
    from web.pisg_page import build_page
    sensors.on_privmsg("alice", "a@h", CHAN, "hello there everyone")
    cfg = make_config(); cfg["web"] = {}
    assert f"Statsbot {__version__}" in build_page(NET, CHAN, 0, cfg)


def test_the_readme_starts_with_the_update_warning():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    head = readme.split("\n", 4)
    assert head[0] == "# Statsbot" and head[2] == "> [!WARNING]"
    assert "2026-09-30" in readme.split("A modern IRC statistics bot")[0]   # the cut-off date for unversioned bots
    assert "CHANGELOG.md" in readme.split("A modern IRC statistics bot")[0]


def test_every_page_that_links_to_the_repository_shows_the_version(db, sensors, tmp_path):
    from conftest import CHAN, NET, make_config
    from web import dashboard
    sensors.on_privmsg("alice", "a@h", CHAN, "hello there everyone")
    cfg = make_config(); cfg["web"] = {}
    dashboard.set_config(cfg, str(tmp_path / "data" / "stats.db"))
    client = dashboard.app.test_client()
    for url in ("/", f"/{NET}/", f"/{NET}/chan/"):
        html = client.get(url).get_data(as_text=True)
        assert f"Statsbot {__version__}</a>" in html, f"{url} does not show the version next to the repository link"


def test_the_unused_channel_template_also_shows_the_version():
    from web import dashboard
    assert "Statsbot {{ statsbot_version }}</a>" in dashboard.CHANNEL_TMPL


class TestDocsLink:
    def _pages(self, db, sensors, tmp_path, web):
        from conftest import CHAN, NET, make_config
        from web import dashboard
        sensors.on_privmsg("alice", "a@h", CHAN, "hello there everyone")
        cfg = make_config(); cfg["web"] = web
        dashboard.set_config(cfg, str(tmp_path / "data" / "stats.db"))
        client = dashboard.app.test_client()
        return [client.get(u).get_data(as_text=True) for u in ("/", f"/{NET}/", f"/{NET}/chan/")]

    def test_every_page_links_to_the_docs_by_default(self, db, sensors, tmp_path):
        from version import DEFAULT_DOCS_URL
        for html in self._pages(db, sensors, tmp_path, {}):
            assert f'<a href="{DEFAULT_DOCS_URL}"' in html and ">Docs</a>" in html

    def test_the_link_can_be_pointed_elsewhere_and_is_escaped(self, db, sensors, tmp_path):
        for html in self._pages(db, sensors, tmp_path, {"docs_url": 'https://docs.example/"x'}):
            assert 'href="https://docs.example/&#34;x"' in html or 'href="https://docs.example/&quot;x"' in html

    def test_an_empty_setting_hides_the_link(self, db, sensors, tmp_path):
        for html in self._pages(db, sensors, tmp_path, {"docs_url": ""}):
            assert ">Docs</a>" not in html


def test_the_readme_and_the_example_config_mention_the_documentation():
    from version import DEFAULT_DOCS_URL
    assert DEFAULT_DOCS_URL in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs_url" in (ROOT / "config" / "config.yml.example").read_text(encoding="utf-8")
    assert "docs_url" in (ROOT / "DOCS.md").read_text(encoding="utf-8")
