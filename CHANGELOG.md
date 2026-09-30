# Changelog

All notable changes to Statsbot. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/). Everything released before this
file existed is treated as 1.0.0. Check what you are running with `python main.py --version`
(it is also shown in the footer of every stats page).

## [1.4.1] - 2026-10-01

### Changed
- "These didn't make it to the top" is now a compact grid of `nick (number)`, like pisg's, instead of
  a second copy of the big table. The number is the one the table is ranked by (words by default).
  It also saves one database query per listed nick.

## [1.4.0] - 2026-10-01

### Added
- **Pick a nick on the "Who talks to whom" map.** Click a nick, or choose it in the new "Focus on"
  list, and it moves to the middle with the people it talks to around it: the more they talk, the
  closer they sit. Everyone else fades. Click another nick to move on, click the middle one (or
  choose "everyone") to go back to the ring. Needs JavaScript; without it the map and the
  Closest pairs table work as before.
- Dots on the map are sized by how many lines each nick said (before: by how many mentions it is part of).
- **Other interesting numbers**: the nick that changed its nick most ("can't settle on a name"),
  shown when someone changed it at least twice. Translated into all five languages.

### Changed
- The map's hint text now mentions the new focus view (translated).

## [1.3.1] - 2026-10-01

### Changed
- The "Who talks to whom" map is folded away by default ("Show the map (N nicks)"): it is a big picture
  and pushed the rest of the page down. The compact **Closest pairs** table now comes first. The new
  option `RelationMap` (`closed` by default, `open`, or `off`) changes that per channel.
- With fewer than four nicks there is no ring at all, only the pairs table; a smaller channel gets a
  smaller picture; a slider that could not move (few nicks, all links equally strong) is not shown.

## [1.3.0] - 2026-10-01

### Added
- **"Who talks to whom"**: a map of the most connected nicks and a "Closest pairs" table. It is
  meant for busy channels, where a free-form graph turns into a hairball: it draws only the core
  (40 nicks by default, 60 at most) on a ring, groups the people who mostly talk to each other into
  colour-coded arcs, orders them so that strong links are short, and fades weak links. Hover or click a
  nick to see only its links; two sliders show fewer nicks or only the stronger links. The layout is
  computed by the bot, so the drawing stays small however many people talk. It follows the
  all-time / today / week / month tabs.
- Options `ShowRelations`, `RelationNicks`, `RelationMinWeight` and `RelationPairs` (see DOCS.md);
  the setting `stats.pair_keep_days` (default 60) says how long a pair seen fewer than 3 times is kept.
- Translations of the new section (pt, fr, it, nl).

### Upgrade notes
- A new table (`nick_pairs`) is created automatically. The map has no history: pairs are counted
  from the moment you update, so it fills up as people talk (a mention of a nick that is in the
  channel counts as a link).

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
