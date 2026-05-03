"""
web/pisg_config_page.py
Read-only pisg configuration viewer for a channel.
Reached via /network/channel/pisg?token=...
"""

# Built-in defaults — single source of truth for type coercion and display
_PISG_DEFAULTS = {
    "DailyActivity":           30,
    "ActiveNicks":             25,
    "ActiveNicks2":            10,
    "SortByWords":             True,
    "ShowWords":               True,
    "ShowLines":               True,
    "ShowWpl":                 True,
    "ShowCpl":                 False,
    "ShowLastSeen":            True,
    "ShowRandQuote":           True,
    "MinQuote":                25,
    "MaxQuote":                65,
    "ShowBigNumbers":          True,
    "BigNumbersThreshold":     "sqrt",
    "ViolentWords":            ["slaps","beats","kicks","hits","smacks","stabs","hugs","pokes"],
    "FoulWords":               ["ass","fuck","shit","bitch","cunt","cock","dick"],
    "ShowMostActiveByHour":    True,
    "ShowSmileys":             True,
    "ShowMrn":                 True,
    "ShowOps":                 True,
    "ShowActiveTimes":         True,
    "ShowActiveNicks":         True,
    "ShowMuw":                 True,
    "ShowTopics":              True,
    "ShowTime":                True,
    "ShowMostActiveByHourGraph": True,
    "ShowVoice":               False,
    "ShowHalfops":             False,
    "ShowKarma":               True,
    "KarmaHistory":            10,
    "ShowMru":                 True,
    "ShowLegend":              True,
    "TopicHistory":            5,
    "UrlHistory":              10,
    "WordHistory":             10,
    "WordLength":              4,
    "IgnoreWords":             [],
    "NickHistory":             5,
    "SmileyHistory":           10,
    "ActiveNicksByHour":       10,
}


def _type_label(v) -> str:
    if isinstance(v, bool):   return "bool"
    if isinstance(v, int):    return "int"
    if isinstance(v, list):   return "list"
    return "str"


def _fmt(v) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v) if v else "(empty)"
    return str(v)


def build_pisg_config_page(network: str, channel: str, config: dict) -> str:
    from database.models import get_pisg_channel_overrides

    global_pisg = config.get("pisg", {})
    overrides   = get_pisg_channel_overrides(network, channel)

    rows_html = []
    for key, builtin_default in _PISG_DEFAULTS.items():
        type_str    = _type_label(builtin_default)
        global_val  = global_pisg.get(key, builtin_default)
        override_v  = overrides.get(key)          # raw string or None
        has_override = override_v is not None

        # Coerce override string back to typed value for display
        if has_override:
            if isinstance(builtin_default, bool):
                eff_val = override_v.lower() in ("1","true","yes","on")
            elif isinstance(builtin_default, int):
                try: eff_val = int(override_v)
                except ValueError: eff_val = override_v
            elif isinstance(builtin_default, list):
                eff_val = [w.strip() for w in override_v.split(",") if w.strip()]
            else:
                eff_val = override_v
            source = "channel"
        elif key in global_pisg:
            eff_val = global_val
            source  = "global"
        else:
            eff_val = builtin_default
            source  = "default"

        source_badge = {
            "channel": '<span class="badge chan">channel</span>',
            "global":  '<span class="badge glob">global</span>',
            "default": '<span class="badge def">default</span>',
        }[source]

        rows_html.append(f"""
        <tr class="{'override' if has_override else ''}">
          <td class="key"><code>{key}</code></td>
          <td class="type">{type_str}</td>
          <td class="val">{_fmt(eff_val)}</td>
          <td class="src">{source_badge}</td>
        </tr>""")

    chan_display = channel
    title        = f"pisg config — {network} {chan_display}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg:    #0d0f1a; --bg2: #13162a; --bg3: #1e2235;
    --fg:    #c8cfe8; --muted: #6b7394; --blue: #4a7ab5;
    --green: #4a9b5e; --yellow: #b5963a; --red: #b54a4a;
    --chan-col: #4a9b5e; --glob-col: #b5963a; --def-col: #6b7394;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--fg); font: 14px/1.5 'Segoe UI', system-ui, sans-serif; padding: 2rem; }}
  h1 {{ font-size: 1.1rem; letter-spacing: .08em; text-transform: uppercase;
        color: var(--blue); border-bottom: 1px solid var(--bg3); padding-bottom: .6rem; margin-bottom: 1.2rem; }}
  .meta {{ font-size: .8rem; color: var(--muted); margin-bottom: 1.5rem; }}
  .meta b {{ color: var(--fg); }}
  table {{ width: 100%; border-collapse: collapse; font-size: .88rem; }}
  th {{ background: var(--bg3); color: var(--blue); text-align: left;
        padding: .45rem .8rem; font-size: .78rem; text-transform: uppercase; letter-spacing: .05em; }}
  td {{ padding: .38rem .8rem; border-bottom: 1px solid var(--bg3); }}
  tr.override td {{ background: #151a2e; }}
  td.key code {{ color: var(--fg); font-size: .85rem; }}
  td.type {{ color: var(--muted); font-size: .8rem; }}
  td.val {{ font-family: monospace; color: var(--fg); }}
  .badge {{ font-size: .72rem; padding: .15rem .5rem; border-radius: 3px;
            font-weight: 600; letter-spacing: .03em; text-transform: uppercase; }}
  .badge.chan {{ background: #1a3a25; color: var(--chan-col); }}
  .badge.glob {{ background: #2e2a14; color: var(--yellow); }}
  .badge.def  {{ background: var(--bg3); color: var(--muted); }}
  .legend {{ margin-top: 1.5rem; font-size: .8rem; color: var(--muted); display: flex; gap: 1.2rem; flex-wrap: wrap; }}
  .legend span {{ display: flex; align-items: center; gap: .4rem; }}
  .hint {{ margin-top: 1.2rem; font-size: .8rem; color: var(--muted);
           background: var(--bg2); border: 1px solid var(--bg3);
           border-radius: 4px; padding: .7rem 1rem; }}
  .hint code {{ color: var(--fg); }}
</style>
</head>
<body>
<h1>pisg config — {network} / {chan_display}</h1>
<p class="meta">Effective configuration for this channel.
  Highlighted rows have a <b>channel-level override</b> that takes precedence over the global config.</p>
<table>
  <thead><tr><th>Key</th><th>Type</th><th>Effective value</th><th>Source</th></tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>
<div class="legend">
  <span><span class="badge chan">channel</span> per-channel override (DB)</span>
  <span><span class="badge glob">global</span> from config.yml pisg: section</span>
  <span><span class="badge def">default</span> built-in default</span>
</div>
<div class="hint">
  To change a value: <code>/msg {'{bot}'} pisg {chan_display} set &lt;key&gt; &lt;value&gt;</code><br>
  To remove an override: <code>/msg {'{bot}'} pisg {chan_display} reset &lt;key&gt;</code><br>
  To remove all overrides: <code>/msg {'{bot}'} pisg {chan_display} reset</code>
</div>
</body>
</html>"""
