"""
slugs.py
Channel name <-> URL path, in one place.

    #lifeline    ->  lifeline        (unchanged: every existing link keeps working)
    ##lifeline   ->  2/lifeline
    ###lifeline  ->  3/lifeline
    &local       ->  0/&local        (no '#' at all)

A leading "<n>/" means "n hashes". That cannot clash with a channel that merely starts with a
digit (#4chan, #2600: no slash), and a one-hash channel whose *name* starts with "<digits>/"
(#2/x) is written with the explicit "1/" form (1/2/x), so two channels never share a URL.

IRC allows almost any character in a channel name (RFC 2812 excludes only space, comma, colon
at the start, NUL, BEL, CR and LF) but '/' is rare and most networks do not use it. One
limit remains: a name that ENDS in '/' cannot be told from the URL's own trailing slash.
"""
import re
from urllib.parse import quote

_COUNT_RE = re.compile(r"^(\d{1,2})/(.+)$", re.DOTALL)


def channel_slug(channel: str) -> str:
    """The path segment(s) for a channel name (not URL-quoted, not lowercased)."""
    if not channel:
        return ""
    hashes = len(channel) - len(channel.lstrip("#"))
    rest = channel[hashes:]
    if hashes == 1 and not _COUNT_RE.match(rest):
        return rest
    return f"{hashes}/{rest}"


def channel_from_slug(slug: str) -> str:
    """The channel name a URL path stands for (it may still differ in case from the stored one)."""
    slug = (slug or "").rstrip("/")
    if slug.startswith("#"):                 # old style: /Net/%23%23lifeline/ is the full name
        return slug
    match = _COUNT_RE.match(slug)
    if match:
        return "#" * int(match.group(1)) + match.group(2)
    return "#" + slug


def channel_url_path(channel: str) -> str:
    """channel_slug(), lowercased (the site's canonical form) and quoted for use in a URL."""
    return quote(channel_slug(channel).lower(), safe="/")
