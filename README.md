# ircstats

A modern IRC statistics bot inspired by [pisg](https://pisg.github.io/), built
for the 21st century. Instead of parsing log files after the fact, ircstats
connects to IRC as a bot, collects statistics in real time, and serves a
live web dashboard — no log files, no cron jobs, no static HTML generation.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Why ircstats instead of pisg?

| | pisg | ircstats |
|---|---|---|
| Data source | Parse log files | Live IRC connection |
| Setup | Configure bot logging + cron + pisg | Just run ircstats |
| Log format support | 30+ parsers to maintain | Not needed |
| Output | Static HTML, regenerated periodically | Live web server |
| Stats periods | One fixed window | All-time, today, week, month |
| Peak users | ✗ | ✓ with timestamp |
| Live user count | ✗ | ✓ updates every 30s |
| Multi-network | ✗ | ✓ |
| Admin via IRC | ✗ | ✓ via PM commands |

If you already know pisg, the `pisg:` section in `config.yml` uses the same
option names — `ActiveNicks`, `ShowBigNumbers`, `WordHistory`, etc. — so the
[pisg docs](https://pisg.github.io/docs/) apply directly.

---

## Features

- Tracks **words, lines, letters, actions, kicks, modes, bans, joins, topics,
  minutes online, smileys (happy + sad separately), questions, CAPS lines,
  violent actions, foul language, monologues**
- **Big numbers** — questions, shouting %, CAPS %, violence + victim tracking
  with example lines, smiles %, sad %, line lengths, monologues, words per line
- **Other interesting numbers** — kicks given/received, most actions, most joins,
  foul language %
- **Most active by hour** — pisg-style 4-band table (0–5, 6–11, 12–17, 18–23)
- **Most used words** — filterable by length and ignore list, with last-used-by nick
- **Most referenced nicks** — who gets mentioned most in conversation
- **Smiley frequency table** — which specific smiley used most, and by whom
- **Most referenced URLs** — deduplicated with use count and last poster
- **Latest topics** with setter and timestamp
- **Random quotes** in the main table — length-filtered (MinQuote/MaxQuote),
  first message always logged so new speakers never show blank
- **Period tabs** — all-time, today, this week, this month
- **Peak users** with date
- **Live user count** badge, updates every 30 seconds
- **Multi-network** — connect to Libera, Undernet, PTirc simultaneously
- **PM admin interface** — identify, ignore management, master management
- **bcrypt password auth** — session lasts until disconnect, works from any nick
- **Auto-auth** via hostmask — silent authentication on join if host matches
- **Channel-scoped ignores** — per-channel or network-wide
- **Automatic DB migrations** — upgrading never requires manual schema changes

---

## Requirements

- Python 3.11+
- `pip install flask pyyaml bcrypt`

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/yourusername/ircstats.git
cd ircstats

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp config/config.yml.example config/config.yml
nano config/config.yml          # set your networks, channels, nick

# 4. Set up master password(s) — interactive wizard
python main.py --setup

# 5. Run
python main.py
```

The web dashboard is at `http://localhost:8033/` by default.

---

## Configuration overview

```yaml
bot:        # nick, altnick, realname, ident
networks:   # list of IRC servers and channels
stats:      # tracking options, ignore lists, smiley lists
web:        # dashboard host/port/public_url
commands:   # prefix, flood protection
pisg:       # page layout (pisg-compatible option names)
database:   # SQLite path
logging:    # level and log file
```

See **[DOCS.md](DOCS.md)** for the complete reference.

---

## IRC commands

### Channel commands

| Command | Description |
|---------|-------------|
| `!stats` | Link to the stats page for this channel |
| `!top [n]` | Top N users by words (default 3, max 10) |
| `!quote [nick]` | Random quote, optionally from a specific nick |

### PM commands (`/msg statsbot <command>`)

| Command | Description |
|---------|-------------|
| `identify <master> <password>` | Authenticate — works from any nick on any network |
| `logout` / `whoami` / `status` | Session management |
| `ignore add [#chan] <pattern>` | Add ignore (network-wide if no `#chan`) |
| `ignore del [#chan] <pattern>` | Remove ignore |
| `ignore list [#chan]` | List ignores |
| `master add <nick>` | Add master (bot asks for password interactively) |
| `master del <nick>` / `master list` | Manage masters |
| `set page [#chan] <url>` | Override `!stats` URL for a channel |

---

## Stats page URL structure

```
http://yourserver:8033/                        — all networks
http://yourserver:8033/<network>/              — channels on a network
http://yourserver:8033/<network>/<channel>/    — full pisg-style stats page
http://yourserver:8033/<network>/<channel>/?period=1   — today
```

Period values: `0` = all-time (default), `1` = today, `2` = this week, `3` = this month.

Set `web.public_url` in config so `!stats` generates proper external links:

```yaml
web:
  public_url: "https://stats.yourserver.org"
```

---

## Running as a service

```ini
# /etc/systemd/system/ircstats.service
[Unit]
Description=ircstats IRC statistics bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/ircstats
ExecStart=/home/youruser/virtualenv/bin/python main.py
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now ircstats
```

---

## Project structure

```
ircstats/
├── main.py                  # Entry point, --setup wizard
├── config/
│   └── config.yml           # All configuration
├── bot/
│   ├── auth.py              # Master auth — sessions, bcrypt, auto-auth via mask
│   ├── connector.py         # Async IRC connection, RFC 1459 parser, WHO handler
│   ├── parser.py            # Message parsing: words, smileys, caps, violent, foul
│   ├── sensors.py           # Event handlers — stats, quotes, monologues, victims
│   └── scheduler.py         # Daily/weekly/monthly stat resets
├── database/
│   └── models.py            # SQLite schema, all queries, auto-migrations
├── irc/
│   ├── commands.py          # Channel commands: !stats, !top, !quote
│   └── pm_commands.py       # PM admin: identify, ignore, master, set
└── web/
    ├── dashboard.py         # Flask: landing, network pages, JSON API
    └── pisg_page.py         # Full pisg-style channel stats page
```

---

## Contributing

Issues and pull requests are welcome. If you're adding a feature, please:

- Follow the existing code style (no external deps beyond `requirements.txt`)
- Add a test in the relevant `python -c` style check if possible
- Update `DOCS.md` if you add or change a config option

If you're familiar with pisg, feature parity PRs are especially welcome —
see the "not yet implemented" table below for what's missing.

## What's not yet implemented vs pisg

| pisg feature | Status |
|---|---|
| User pictures | Not yet |
| Karma (`nick++` / `nick--`) | Not yet |
| Gender stats | Not yet |
| Daily activity graph (lines per day) | Not yet |
| NickTracking / nick aliases | Not yet |
| Music charts (`now playing:`) | Not yet |
| Op/voice/halfop statistics | Not yet |
| `ShowTime` (when-active time bar) | Not yet |

All other pisg features are implemented. Contributions welcome.
