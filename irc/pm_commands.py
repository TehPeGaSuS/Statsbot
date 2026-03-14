"""
irc/pm_commands.py
Private message command handler.
All admin/management commands live here, dispatched via /msg statsbot.

Commands:
  identify <master_nick> <password>
  logout
  whoami
  status

  ignore add [#channel] <pattern>
  ignore del [#channel] <pattern>
  ignore list [#channel]

  master add <nick>         (bot will ask for password interactively via PM)
  master del <nick>
  master list

  set page [#channel] <url>
  rehash
"""

import logging
import time

log = logging.getLogger("pm_commands")


class PMCommandHandler:
    def __init__(self, network: str, auth_manager, send_fn, config: dict,
                  connectors: list = None):
        self.network = network
        self.auth = auth_manager
        self.send = send_fn          # send(nick, text) — sends a NOTICE or PRIVMSG to nick
        self.cfg = config
        self.connectors = connectors or []
        # Pending master add flows: {nick_lower: {"step": 1, "target": master_nick}}
        self._pending_master_add: dict = {}

    def dispatch(self, nick: str, host: str, text: str):
        """Entry point for all PM messages."""
        text = text.strip()
        if not text:
            return

        # Check if we're mid-flow (e.g. waiting for a password for master add)
        if nick.lower() in self._pending_master_add:
            self._handle_pending(nick, host, text)
            return

        parts = text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "identify": lambda: self._cmd_identify(nick, host, args),
            "logout":   lambda: self._cmd_logout(nick),
            "whoami":   lambda: self._cmd_whoami(nick),
            "status":   lambda: self._cmd_status(nick),
            "ignore":   lambda: self._cmd_ignore(nick, args),
            "master":   lambda: self._cmd_master(nick, args),
            "set":      lambda: self._cmd_set(nick, args),
            "rehash":   lambda: self._cmd_rehash(nick),
            "help":     lambda: self._cmd_help(nick),
        }

        handler = handlers.get(cmd)
        if handler:
            try:
                handler()
            except Exception as e:
                log.error(f"PM command error from {nick}: {e}", exc_info=True)
                self.send(nick, "Internal error. Check bot logs.")
        else:
            self.send(nick, f"Unknown command: {cmd}. Try: help")

    # ─── Auth commands ────────────────────────────────────────────────────────

    def _cmd_identify(self, nick: str, host: str, args: str):
        parts = args.split(None, 1)
        if len(parts) < 2:
            self.send(nick, "Usage: identify <master_nick> <password>")
            return
        master_nick, password = parts[0], parts[1]
        ok, msg = self.auth.identify(self.network, nick, host, master_nick, password)
        self.send(nick, msg)

    def _cmd_logout(self, nick: str):
        if self.auth.is_authed(self.network, nick):
            self.auth.destroy_session(self.network, nick)
            self.send(nick, "Logged out.")
        else:
            self.send(nick, "You are not identified.")

    def _cmd_whoami(self, nick: str):
        session = self.auth.get_session(self.network, nick)
        if session:
            self.send(nick, f"You are identified as {session['master']} on {self.network}.")
        else:
            self.send(nick, "You are not identified. Use: identify <master_nick> <password>")

    # ─── Status ───────────────────────────────────────────────────────────────

    def _cmd_status(self, nick: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return
        from database.models import get_channels, count_users
        lines = [f"ircstats — network: {self.network}"]
        for conn in self.connectors:
            chans = conn._channel_members
            for chan, members in chans.items():
                users_db = count_users(conn.network, chan)
                lines.append(f"  {chan}: {len(members)} online, {users_db} tracked")
        for line in lines:
            self.send(nick, line)

    # ─── Ignore commands ──────────────────────────────────────────────────────

    def _cmd_ignore(self, nick: str, args: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified. Use: identify <master_nick> <password>")
            return

        parts = args.split()
        if not parts:
            self.send(nick, "Usage: ignore add|del|list [#channel] <pattern>")
            return

        subcmd = parts[0].lower()

        if subcmd == "list":
            channel = parts[1] if len(parts) > 1 and parts[1].startswith("#") else None
            self._ignore_list(nick, channel)
            return

        if subcmd in ("add", "del"):
            rest = parts[1:]
            if not rest:
                self.send(nick, f"Usage: ignore {subcmd} [#channel] <pattern>")
                return
            # If first arg starts with #, it's a channel
            if rest[0].startswith("#"):
                if len(rest) < 2:
                    self.send(nick, f"Usage: ignore {subcmd} #channel <pattern>")
                    return
                channel = rest[0]
                pattern = rest[1]
            else:
                channel = "*"   # network-wide
                pattern = rest[0]

            if subcmd == "add":
                self._ignore_add(nick, channel, pattern)
            else:
                self._ignore_del(nick, channel, pattern)
            return

        self.send(nick, "Usage: ignore add|del|list [#channel] <pattern>")

    def _ignore_add(self, nick: str, channel: str, pattern: str):
        from database.models import add_ignore
        add_ignore(pattern, self.network, channel=channel, added_by=nick)
        scope = channel if channel != "*" else "network-wide"
        self.send(nick, f"Ignored {pattern} ({scope}).")

    def _ignore_del(self, nick: str, channel: str, pattern: str):
        from database.models import del_ignore
        del_ignore(pattern, self.network, channel=channel)
        self.send(nick, f"Removed ignore: {pattern}.")

    def _ignore_list(self, nick: str, channel: str = None):
        from database.models import list_ignores
        ignores = list_ignores(self.network, channel)
        if not ignores:
            self.send(nick, "Ignore list is empty.")
            return
        self.send(nick, f"Ignores for {self.network}" + (f"/{channel}" if channel else "") + ":")
        for ig in ignores:
            scope = ig["channel"] if ig["channel"] != "*" else "network-wide"
            self.send(nick, f"  [{scope}] {ig['pattern']}  (added by {ig['added_by'] or '?'})")

    # ─── Master commands ──────────────────────────────────────────────────────

    def _cmd_master(self, nick: str, args: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return

        parts = args.split()
        if not parts:
            self.send(nick, "Usage: master add|del|list [nick]")
            return

        subcmd = parts[0].lower()
        target = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            from database.models import list_masters_global
            masters = list_masters_global()
            if not masters:
                self.send(nick, "No masters configured.")
            else:
                for m in masters:
                    masks = m.get("masks") or "(no masks)"
                    self.send(nick, f"  {m['nick']}  masks: {masks}")

        elif subcmd == "add":
            if not target:
                self.send(nick, "Usage: master add <nick>")
                return
            # Start interactive flow — ask for password via PM
            self._pending_master_add[nick.lower()] = {"target": target, "step": 1}
            self.send(nick, f"Adding master {target}. Enter password (will not be echoed):")

        elif subcmd == "del":
            if not target:
                self.send(nick, "Usage: master del <nick>")
                return
            from database.models import del_master_by_nick
            del_master_by_nick(target)
            self.send(nick, f"Removed master {target}.")

        else:
            self.send(nick, "Usage: master add|del|list [nick]")

    def _handle_pending(self, nick: str, host: str, text: str):
        """Handle multi-step flows (e.g. password input for master add)."""
        state = self._pending_master_add.get(nick.lower())
        if not state:
            return

        if state["step"] == 1:
            # First message after "master add" = password
            password = text.strip()
            if len(password) < 6:
                self.send(nick, "Password too short (min 6 chars). Try again or send 'cancel'.")
                return
            if password.lower() == "cancel":
                del self._pending_master_add[nick.lower()]
                self.send(nick, "Cancelled.")
                return
            state["password"] = password
            state["step"] = 2
            self.send(nick, "Confirm password:")

        elif state["step"] == 2:
            # Second message = confirmation
            confirm = text.strip()
            if confirm.lower() == "cancel":
                del self._pending_master_add[nick.lower()]
                self.send(nick, "Cancelled.")
                return
            if confirm != state["password"]:
                self.send(nick, "Passwords don't match. Start over with: master add <nick>")
                del self._pending_master_add[nick.lower()]
                return

            from database.models import add_master_with_password
            from bot.auth import hash_password
            hashed = hash_password(state["password"])
            add_master_with_password(state["target"], hashed, added_by=nick)
            del self._pending_master_add[nick.lower()]
            self.send(nick, f"Master {state['target']} added successfully.")
            log.info(f"Master {state['target']} added by {nick}")

    # ─── Set commands ─────────────────────────────────────────────────────────

    def _cmd_set(self, nick: str, args: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return

        parts = args.split(None, 2)
        if len(parts) < 2:
            self.send(nick, "Usage: set page [#channel] <url>")
            return

        key = parts[0].lower()
        if key == "page":
            rest = parts[1:]
            if rest[0].startswith("#"):
                if len(rest) < 2:
                    self.send(nick, "Usage: set page #channel <url>")
                    return
                channel, url = rest[0], rest[1]
            else:
                channel = "*"
                url = rest[0]
            from database.models import set_channel_config
            set_channel_config(self.network, channel, "stats_url", url)
            self.send(nick, f"Stats URL for {channel} set to {url}")
        else:
            self.send(nick, f"Unknown setting: {key}. Available: page")

    # ─── Rehash ───────────────────────────────────────────────────────────────

    def _cmd_rehash(self, nick: str):
        if not self.auth.is_authed(self.network, nick):
            self.send(nick, "Not identified.")
            return
        self.send(nick, "Rehash not yet implemented — restart the bot to reload config.")

    # ─── Help ─────────────────────────────────────────────────────────────────

    def _cmd_help(self, nick: str):
        lines = [
            "ircstats PM commands:",
            "  identify <master_nick> <password>  — authenticate",
            "  logout  |  whoami  |  status",
            "  ignore add [#chan] <pattern>        — network-wide if no #chan",
            "  ignore del [#chan] <pattern>",
            "  ignore list [#chan]",
            "  master add <nick>  |  master del <nick>  |  master list",
            "  set page [#chan] <url>              — URL for !stats command",
        ]
        for line in lines:
            self.send(nick, line)
