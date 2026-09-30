# Changelog

All notable changes to Statsbot. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Everything released before this
file existed is treated as 1.0.0. Check what you are running with `python main.py --version`
(it is also shown in the footer of every stats page).

## [1.2.0] - 2026-09-30

### Added
- Documentation site: https://statsbot-docs.v-m-m-l.workers.dev/ (generated from README.md, DOCS.md,
  TRANSLATE.md and CHANGELOG.md by `tools/build_docs.py`).
- A "Docs" link next to the version in the footer of every stats page. The new optional setting
  `web.docs_url` changes the address; set it to `""` to hide the link.

## [1.1.0] - 2026-09-30

### Security
- **Stored cross-site scripting on the stats page.** Text that came from IRC — quotes, topics,
  kick reasons, `/me` and example lines, words, URLs, smileys, nicks and channel names — was
  written into the page without being escaped, so anyone who could talk in a tracked channel
  could run script in the browser of everyone who opened the page. Everything is now escaped
  where it is written, and values placed in the page's inline script can no longer close the
  script tag.
- **Host-mask auto-login never worked, and matched too much.** It crashed with a `KeyError` the
  moment a mask matched, and a mask without a host part (a bare nick) would have matched on the
  nick alone — anybody can take a nick on IRC. A mask now has to contain an `@`
  (for example `*!*@your.host.example`).
- `?period=` outside 0–3 is answered with `400`, and `?limit=` is limited to 1–100 (`-1` meant
  "no limit" to SQLite and returned the whole table).

### Fixed
- Every line's `letters` were counted twice.
- The daily activity chart filed a finished day under the next day's date, so today's bar showed
  yesterday's numbers.
- `master list` over PM failed with an internal error.
- Karma was ignored for someone who had spoken for the first time less than a minute earlier.
- `-l`, `-L`, `-j` and `-f` in a mode string wrongly took the next argument.
- A restart during the midnight hour reset the day (and on Mondays / the 1st the week / month)
  a second time. The last reset of each kind is now stored in the database, and the week check
  includes the year.
- The "got beaten" example showed a line about somebody else.
- `www.` links on the page were relative links.
- `?period=abc` and `?limit=abc` returned HTTP 500.

### Changed
- **`##channel` and `###channel` have their own page URLs.** `#lifeline` stays
  `/Network/lifeline/`; `##lifeline` is `/Network/2/lifeline/` and `###lifeline`
  `/Network/3/lifeline/` (before, all three shared one address and the wrong channel was shown).
  Other spellings redirect to the canonical URL.
- The footer shows the version.

### Added
- `python main.py --version`, `version.py`, this changelog.
- A test suite (`python -m pytest`) and CI on Python 3.11–3.14, a weekly dependency audit and
  Dependabot.

### Upgrade notes
- `git pull`, `pip install -r requirements.txt`, restart the bot. The database updates itself
  (one new table, `scheduler_state`). No manual step.
- Statistics that were already stored are not rewritten: the `letters` totals collected before
  1.1.0 stay doubled (so "chars per line" of older data is too high), and earlier days on the
  activity chart stay one day late.
- If you set up host-mask auto-login before, check that each mask contains an `@`.

## [1.0.0]
Everything up to the introduction of version numbers (the last such commit is `d3538ac`,
2026-07-28). An older bot cannot report a version, so check the date of its latest commit: from
2026-09-30 on it has the 1.1.0 fixes.

```bash
git log -1 --format='%h %cs'
[[ $(git log -1 --format=%cs) < 2026-09-30 ]] && echo "OUTDATED: update now" || echo "up to date"
```
