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
