"""
bot/connector.py
Raw IRC connection handler. Parses RFC 1459 messages and fires sensors.
Uses asyncio for non-blocking I/O, supports TLS.
"""

import asyncio
import socket
import ssl
import logging
import re
import time
from typing import Callable, Optional, List

log = logging.getLogger("connector")

# IRC message parser
MSG_RE = re.compile(
    r'^(?::(?P<prefix>[^\s]+)\s+)?'
    r'(?P<command>[A-Z0-9]+)'
    r'(?P<params>(?:\s+[^:][^\s]*)*)?'
    r'(?:\s+:(?P<trailing>.*))?$'
)


def parse_irc(raw: str):
    m = MSG_RE.match(raw)
    if not m:
        return None
    prefix = m.group("prefix") or ""
    command = m.group("command")
    params_str = (m.group("params") or "").split()
    trailing = m.group("trailing")
    params = params_str + ([trailing] if trailing is not None else [])
    return prefix, command, params


def nick_from_prefix(prefix: str) -> str:
    return prefix.split("!")[0] if "!" in prefix else prefix


def host_from_prefix(prefix: str) -> str:
    return prefix.split("!")[1] if "!" in prefix else ""


class IRCConnector:
    """
    Async IRC connection for one network.
    """

    def __init__(self, config: dict, network_cfg: dict,
                  sensors, commands, scheduler=None, pm_handler=None):
        self.cfg = config
        self.net = network_cfg
        self.sensors = sensors
        self.commands = commands
        self.pm_handler = pm_handler
        self.scheduler = scheduler

        # Identity: per-network overrides global bot: defaults.
        # DB rows may have NULL for optional fields — use `or` to skip None/empty.
        bot = config.get("bot", {})
        self.nick     = (network_cfg.get("nick")     or bot.get("nick")     or "statsbot")
        self.altnick  = (network_cfg.get("altnick")  or bot.get("altnick")  or self.nick + "_")
        self.realname = (network_cfg.get("realname") or bot.get("realname") or "IRC Stats Bot")
        self.ident    = (network_cfg.get("ident")    or bot.get("ident")    or "statsbot")

        self.host = network_cfg["host"]
        self.port = network_cfg.get("port", 6667)
        self.use_ssl = network_cfg.get("ssl", False)
        self.channels: List[str] = network_cfg.get("channels", [])
        self.network: str = network_cfg["name"]

        self._writer: Optional[asyncio.StreamWriter] = None
        self._channel_members: dict = {}   # channel -> set of nicks
        self._nick_hosts: dict = {}        # nick -> user@host (populated by WHO)
        self._whox_pending: set = set()    # channels awaiting WHO response
        self._connected = False
        self._current_nick = self.nick
        self._sasl_authed  = False       # SASL completed
        self._ghost_sent   = False       # already sent GHOST this session
        self.reload_queue  = None        # set by main.py after construction
        self._reclaim_nick = False       # waiting to reclaim primary nick after ghost

        # ── Channel join retry tracking ─────────────────────────────────
        # Per-channel attempt count (lowercased channel name -> int)
        self._join_attempts: dict = {}
        # Channels we have successfully joined (lowercased)
        self._joined_channels: set = set()
        # Pending retry asyncio tasks (lowercased channel -> Task)
        self._join_retry_tasks: dict = {}
        # Pending "did the server ever answer our JOIN?" watchdogs
        # (lowercased channel -> Task)
        self._join_confirm_tasks: dict = {}
        # Channels whose retry ladder is fully exhausted. The periodic channel
        # audit skips these so a permanently banned channel isn't retried
        # forever; cleared on reconnect and on a manual join.
        self._gave_up: set = set()
        # Config: max attempts and delay between them
        self.join_retries = int(
            network_cfg.get("join_retries",
                            (config.get("bot", {}) or {}).get("join_retries", 5))
        )
        self.join_retry_delay = float(
            network_cfg.get("join_retry_delay",
                            (config.get("bot", {}) or {}).get("join_retry_delay", 30))
        )
        # Once join_retries quick attempts are exhausted, keep retrying at this
        # much slower cadence instead of giving up. Networks like Undernet lock
        # a channel with ERR_UNAVAILRESOURCE (437) for a "channel delay" window
        # after a netsplit empties it, which can easily outlast the quick-retry
        # budget above — and if the bot's own server isn't the one that split,
        # its connection never drops, so nothing else would ever retry that
        # channel again for the rest of the session.
        self.join_retry_backoff_delay = float(
            network_cfg.get("join_retry_backoff_delay",
                            (config.get("bot", {}) or {}).get("join_retry_backoff_delay", 300))
        )
        # Cap on slow-backoff attempts, separate from join_retries. Without
        # this the bot would retry a stuck channel forever (e.g. 300s delay
        # -> 12/hr -> ~105,000 attempts/year). Default 36 gives ~3 hours of
        # backoff retries before truly giving up.
        self.join_retry_backoff_retries = int(
            network_cfg.get("join_retry_backoff_retries",
                            (config.get("bot", {}) or {}).get("join_retry_backoff_retries", 36))
        )

        # A JOIN that the server silently swallows produces no reply at all —
        # no JOIN echo, no error numeric. That happens routinely mid-netsplit,
        # when the JOIN reaches a server that is desynced or still merging.
        # Without a watchdog nothing would ever notice, because every retry
        # path below is driven by an error numeric arriving.
        self.join_confirm_timeout = float(
            network_cfg.get("join_confirm_timeout",
                            (config.get("bot", {}) or {}).get("join_confirm_timeout", 30))
        )
        # Periodic sweep that re-JOINs any configured channel we are not in
        # and have no retry pending for. Safety net for anything the paths
        # above miss. 0 disables it.
        self.channel_audit_interval = float(
            network_cfg.get("channel_audit_interval",
                            (config.get("bot", {}) or {}).get("channel_audit_interval", 120))
        )

        # ── Connection liveness ─────────────────────────────────────────
        # A netsplit frequently leaves the TCP connection half-open: the peer
        # stops sending but never sends FIN or RST, so `readline()` blocks
        # forever and the bot never learns it is disconnected. Without an
        # idle timeout the process sits there as a zombie — believing it is
        # connected and joined — until it is restarted by hand.
        #
        # After ping_interval seconds of silence we send our own PING; if the
        # server has not said anything at all ping_timeout seconds after that,
        # the link is declared dead and torn down so auto_reconnect can run.
        self.ping_interval = float(
            network_cfg.get("ping_interval",
                            (config.get("bot", {}) or {}).get("ping_interval", 120))
        )
        self.ping_timeout = float(
            network_cfg.get("ping_timeout",
                            (config.get("bot", {}) or {}).get("ping_timeout", 90))
        )

        self._last_recv: float = 0.0   # monotonic time of last byte from server
        self._ping_sent: bool = False  # PING sent for the current idle period
        self._watchdog_task: Optional[asyncio.Task] = None

        # ── Rejoin on kick ──────────────────────────────────────────────
        # Timestamps of recent self-kicks per channel (lowercased), used to
        # detect a kick war and stop rejoining rather than flap forever.
        self._kick_history: dict = {}
        self.rejoin_on_kick = bool(
            network_cfg.get("rejoin_on_kick",
                            (config.get("bot", {}) or {}).get("rejoin_on_kick", True))
        )
        self.rejoin_delay = float(
            network_cfg.get("rejoin_delay",
                            (config.get("bot", {}) or {}).get("rejoin_delay", 5))
        )
        self.rejoin_max_kicks = int(
            network_cfg.get("rejoin_max_kicks",
                            (config.get("bot", {}) or {}).get("rejoin_max_kicks", 3))
        )
        self.rejoin_kick_window = float(
            network_cfg.get("rejoin_kick_window",
                            (config.get("bot", {}) or {}).get("rejoin_kick_window", 60))
        )

    # ─── Send ─────────────────────────────────────────────────────────────

    def send_raw(self, line: str):
        # Writing to a closed/closing transport is silently discarded by
        # asyncio, so without these guards a JOIN retry on a dead link looks
        # like it succeeded in the log while nothing left the machine.
        if not self._writer or self._writer.is_closing():
            log.warning(f"Dropping outbound line, link to {self.host} is down: {line}")
            return
        data = (line.rstrip("\r\n") + "\r\n").encode("utf-8", errors="replace")
        try:
            self._writer.write(data)
        except Exception as e:
            log.warning(f"Write to {self.host} failed ({e}) — tearing down link.")
            self._force_disconnect(f"write failed: {e}")
            return
        log.debug(f">> {line}")

    def send_msg(self, target: str, text: str):
        # Split long messages
        for chunk in _split_message(text, 400):
            self.send_raw(f"PRIVMSG {target} :{chunk}")

    def send_notice(self, target: str, text: str):
        self.send_raw(f"NOTICE {target} :{text}")

    # ─── Connect / Run ────────────────────────────────────────────────────

    async def connect(self):
        log.info(f"Connecting to {self.host}:{self.port} (ssl={self.use_ssl})")
        ssl_ctx = ssl.create_default_context() if self.use_ssl else None
        try:
            reader, writer = await asyncio.open_connection(
                self.host, self.port, ssl=ssl_ctx)
        except Exception as e:
            log.error(f"Connection failed: {e}")
            raise

        self._writer = writer
        self._connected = True
        self._last_recv = time.monotonic()
        self._ping_sent = False
        self._enable_keepalive(writer)
        log.info(f"Connected to {self.host}")

        # Server password (for BNCs / private servers)
        server_pass = self.net.get("server_password")
        if server_pass:
            self.send_raw(f"PASS {server_pass}")
            log.info("Sent server password.")

        # SASL — request capabilities before NICK/USER
        sasl_cfg = self.net.get("sasl")
        if sasl_cfg:
            self.send_raw("CAP REQ :sasl")
            log.info("Requested SASL capability.")

        # Register
        self.send_raw(f"NICK {self.nick}")
        self.send_raw(f"USER {self.ident} 0 * :{self.realname}")

        self._watchdog_task = asyncio.create_task(
            self._watchdog(), name=f"watchdog-{self.network}"
        )
        try:
            await self._read_loop(reader)
        finally:
            if self._watchdog_task and not self._watchdog_task.done():
                self._watchdog_task.cancel()
            self._watchdog_task = None

    def _enable_keepalive(self, writer: asyncio.StreamWriter):
        """Turn on TCP keepalive as a second line of defence under the
        application-level ping watchdog. The kernel default idle time is two
        hours, which is far too long to be useful on its own, so tighten it."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for opt, val in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 15),
                             ("TCP_KEEPCNT", 4)):
                if hasattr(socket, opt):
                    sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, opt), val)
        except OSError as e:
            log.debug(f"Could not set TCP keepalive on {self.host}: {e}")

    async def _watchdog(self):
        """Detect a silently dead link, and re-JOIN channels we've fallen out of.

        Runs for the lifetime of one connection. Cancelled by connect() when
        the read loop exits.
        """
        # Tick fast enough that the idle check lands inside the PING window
        # rather than stepping straight over it to the timeout.
        tick = max(1.0, min(5.0, self.ping_interval / 4))
        last_audit = time.monotonic()
        try:
            while self._connected:
                await asyncio.sleep(tick)
                if not self._connected:
                    return

                idle = time.monotonic() - self._last_recv
                if idle >= self.ping_interval + self.ping_timeout:
                    log.warning(
                        f"No data from {self.host} for {idle:.0f}s "
                        f"(ping_interval={self.ping_interval}s + "
                        f"ping_timeout={self.ping_timeout}s) — "
                        f"treating the link as dead and reconnecting."
                    )
                    self._force_disconnect("ping timeout")
                    return
                if idle >= self.ping_interval and not self._ping_sent:
                    # Routine on a quiet network — debug, or it would dominate
                    # the log at one line per idle period per network.
                    log.debug(
                        f"No data from {self.host} for {idle:.0f}s — "
                        f"sending PING to check the link is alive."
                    )
                    self.send_raw(f"PING :{self.network}-keepalive")
                    self._ping_sent = True

                now = time.monotonic()
                if (self.channel_audit_interval > 0
                        and now - last_audit >= self.channel_audit_interval):
                    last_audit = now
                    self._audit_channels()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error(f"Watchdog error for {self.host}: {e}", exc_info=True)

    def _force_disconnect(self, reason: str):
        """Tear down the socket so the blocked read loop unblocks and
        auto_reconnect in main.py gets its turn."""
        self._connected = False
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
            except Exception:
                pass
        log.warning(f"Link to {self.host} torn down: {reason}")

    async def _read_loop(self, reader: asyncio.StreamReader):
        while self._connected:
            try:
                raw = await reader.readline()
                if not raw:
                    log.warning("Server closed connection.")
                    break
                # Any byte from the server proves the link is alive.
                self._last_recv = time.monotonic()
                self._ping_sent = False
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                log.debug(f"<< {line}")
                try:
                    self._handle_line(line)
                except Exception as e:
                    # One malformed or unexpected message must not drop the
                    # whole connection — log it and keep reading.
                    log.error(f"Error handling line from {self.host}: {e} "
                              f"(line: {line!r})", exc_info=True)
            except asyncio.CancelledError:
                self._connected = False
                raise
            except (ConnectionResetError, BrokenPipeError, OSError, ssl.SSLError) as e:
                log.warning(f"Read loop ended for {self.host}: {e}")
                break
            except Exception as e:
                log.error(f"Unexpected read error for {self.host}: {e}", exc_info=True)
                break

        self._connected = False
        self._reset_join_state()

    def _handle_line(self, line: str):
        parsed = parse_irc(line)
        if not parsed:
            return
        prefix, command, params = parsed

        if command == "PING":
            self.send_raw(f"PONG :{params[0] if params else ''}")
            return

        if command == "PONG":
            # Reply to our own keepalive PING. _last_recv is already updated
            # by the read loop; nothing else to do.
            return

        if command == "ERROR":
            # Sent by the ircd immediately before it closes the link
            # ("Closing Link", "Ping timeout", netsplit-related quits).
            # Tear down now rather than waiting on the socket to notice.
            log.warning(f"Server error from {self.host}: "
                        f"{params[0] if params else '(no reason)'}")
            self._force_disconnect("server sent ERROR")
            return

        if command == "CAP":
            # CAP * ACK :sasl  — server agreed, start SASL PLAIN
            subcmd = params[1] if len(params) > 1 else ""
            cap_val = params[2] if len(params) > 2 else ""
            if subcmd == "ACK" and "sasl" in cap_val:
                self.send_raw("AUTHENTICATE PLAIN")
            elif subcmd == "NAK":
                log.warning("Server rejected SASL CAP — falling back to NickServ.")
                self.send_raw("CAP END")
                self._do_nickserv_auth()

        elif command == "AUTHENTICATE":
            # Server sent AUTHENTICATE + — send base64(user\0user\0pass)
            if params and params[0] == "+":
                import base64
                sasl_cfg = self.net.get("sasl", {})
                user = sasl_cfg.get("username", self.nick)
                pw   = sasl_cfg.get("password", "")
                blob = f"{user}\0{user}\0{pw}".encode()
                self.send_raw(f"AUTHENTICATE {base64.b64encode(blob).decode()}")

        elif command == "900":  # RPL_LOGGEDIN
            log.info(f"SASL: logged in as {params[2] if len(params) > 2 else '?'}")

        elif command == "903":  # RPL_SASLSUCCESS
            log.info("SASL authentication successful.")
            self._sasl_authed = True
            self.send_raw("CAP END")

        elif command in ("904", "905"):  # ERR_SASLFAIL / ERR_SASLTOOLONG
            log.error("SASL authentication failed.")
            self.send_raw("CAP END")

        elif command == "001":   # RPL_WELCOME
            log.info("Registered on server.")
            log.info(f"[001] net config keys: {list(self.net.keys())}")
            log.info(f"[001] on_connect value: {self.net.get('on_connect')!r}")
            self._current_nick = self.nick
            # NickServ auth (if not using SASL)
            if not self._sasl_authed:
                self._do_nickserv_auth()
            # Join channels (with retry tracking)
            for chan in self.channels:
                self._attempt_join(chan)
            # on_connect commands fire immediately — independent of auth
            self._run_on_connect()

        elif command == "433":  # ERR_NICKNAMEINUSE
            # Always fall back to altnick first so registration can complete
            if self._current_nick != self.altnick:
                self.send_raw(f"NICK {self.altnick}")
                self._current_nick = self.altnick
                log.warning(f"Nick {self.nick} in use — switched to {self.altnick}")

            ns_pass = (self.net.get("nickserv_password") or
                       (self.net.get("sasl") or {}).get("password", ""))
            if not self._ghost_sent and self.net.get("ghost") and ns_pass:
                # DALnet uses RELEASE; most other networks use GHOST.
                # Send both so it works everywhere — services will ignore
                # the one they don't understand.
                ghost_cmd = self.net.get("ghost_command", "GHOST")
                self.send_raw(f"PRIVMSG NickServ :{ghost_cmd} {self.nick} {ns_pass}")
                if ghost_cmd.upper() != "RELEASE":
                    # Also try RELEASE for DALnet-style Enforcer
                    self.send_raw(f"PRIVMSG NickServ :RELEASE {self.nick} {ns_pass}")
                self._ghost_sent   = True
                self._reclaim_nick = True
                log.info(f"Sent {ghost_cmd}+RELEASE for {self.nick} to reclaim after altnick")

        elif command == "353":  # RPL_NAMREPLY — initial member list
            # params: [me, =/@/*, channel, "nick1 nick2 ..."]
            if len(params) >= 4:
                channel = params[2]
                nicks = params[3].split()
                nicks = [n.lstrip("@+%&~!") for n in nicks]
                if channel not in self._channel_members:
                    self._channel_members[channel] = set()
                self._channel_members[channel].update(nicks)

        elif command == "366":  # RPL_ENDOFNAMES — fire WHO now that NAMES is complete
            channel = params[1] if len(params) > 1 else ""
            if channel and channel not in self._whox_pending:
                self._whox_pending.add(channel)
                # Plain WHO — 352 gives us channel, nick, user, host, flags directly
                self.send_raw(f"WHO {channel}")
                log.debug(f"Sent WHO for {channel}")

        elif command == "352":  # RPL_WHOREPLY
            # :server 352 botnick channel user host server nick flags :hops realname
            # params: [botnick, channel, user, host, server, nick, flags, ...]
            if len(params) >= 7:
                who_channel = params[1]
                who_user    = params[2]
                who_host    = params[3]
                # params[4] = server name (skip)
                who_nick    = params[5]
                who_flags   = params[6]   # e.g. 'H', 'HrsB', 'G*~@', etc.
                full_host   = f"{who_user}@{who_host}"

                self._nick_hosts[who_nick.lower()] = full_host

                # Skip ourselves
                if who_nick.lower() == self._current_nick.lower():
                    log.debug(f"WHO: skipping self ({who_nick})")
                # +B flag = bot umode
                elif "B" in who_flags:
                    if self.cfg.get("stats", {}).get("auto_ignore_bots", False):
                        from database.models import add_ignore
                        add_ignore(f"{who_nick}!{full_host}", self.network,
                                   added_by="who-autobot")
                        log.info(f"WHO: auto-ignored +B nick {who_nick} ({full_host})")
                    else:
                        log.info(
                            f"WHO: {who_nick} ({full_host}) has +B — "
                            f"!ignore add {who_nick}!{full_host}"
                        )
                        if who_channel:
                            self.sensors.on_join(who_nick, full_host, who_channel, is_bot=True, populate=True)
                elif who_channel:
                    self.sensors.on_join(who_nick, full_host, who_channel, is_bot=False, populate=True)
                    # Try auto-auth via host masks
                    if self.pm_handler and hasattr(self.pm_handler, 'auth'):
                        self.pm_handler.auth.try_auto_auth(self.network, who_nick, full_host)
                else:
                    from database.models import get_conn
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE nicks SET last_host=?, last_seen=? WHERE nick=? AND network=?",
                            (full_host, int(time.time()), who_nick, self.network)
                        )
                log.debug(f"WHO: {who_nick}!{full_host} flags={who_flags} chan={who_channel}")

        elif command == "315":  # RPL_ENDOFWHO
            channel = params[1] if len(params) > 1 else ""
            self._whox_pending.discard(channel)
            log.debug(f"WHOX complete for {channel}")

        elif command == "JOIN":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            if nick == self._current_nick:
                if channel not in self._channel_members:
                    self._channel_members[channel] = set()
                # Mark channel as successfully joined and cancel any pending retry
                self._mark_join_success(channel)
            else:
                self._channel_members.setdefault(channel, set()).add(nick)
                if host:
                    self._nick_hosts[nick.lower()] = host
                self.sensors.on_join(nick, host, channel)

        elif command in ("471", "473", "474", "475", "477", "437"):
            # Join error numerics:
            #   471 ERR_CHANNELISFULL    (+l)
            #   473 ERR_INVITEONLYCHAN   (+i)
            #   474 ERR_BANNEDFROMCHAN   (banned)
            #   475 ERR_BADCHANNELKEY    (+k)
            #   477 ERR_NEEDREGGEDNICK   (need registration)
            #   437 ERR_UNAVAILRESOURCE  (DALnet temp unavailable / nick or chan)
            # params: [me, channel, :reason]
            err_chan = params[1] if len(params) > 1 else ""
            reason = params[2] if len(params) > 2 else command
            # 437 also fires for nicks during registration — only treat as a
            # join error when the target looks like a channel.
            if err_chan and err_chan[:1] in "#&!+":
                self._schedule_join_retry(err_chan, command, reason)

        elif command == "PART":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            reason = params[1] if len(params) > 1 else ""
            self._channel_members.get(channel, set()).discard(nick)
            if nick == self._current_nick:
                # We left (or were forced out). Drop the joined flag so the
                # channel audit will pull us back in if it's still configured.
                self._channel_members.pop(channel, None)
                self._joined_channels.discard(channel.lower())
            self.sensors.on_part(nick, host, channel, reason)

        elif command == "NOTICE":
            # NickServ sends NOTICE, not PRIVMSG, on most networks
            target = params[0] if params else ""
            text   = params[1] if len(params) > 1 else ""
            sender = nick_from_prefix(prefix).lower()
            if sender in ("nickserv", "enforcer", "q", "x") or sender.endswith("serv"):
                self._handle_nickserv_notice(text)

        elif command == "QUIT":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            reason = params[0] if params else ""
            chans = [ch for ch, m in self._channel_members.items() if nick in m]
            for ch in chans:
                self._channel_members[ch].discard(nick)
            self.sensors.on_quit(nick, host, chans, reason)
            # Destroy auth session on quit
            if self.pm_handler and hasattr(self.pm_handler, 'auth'):
                self.pm_handler.auth.on_quit(self.network, nick)

        elif command == "NICK":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            new_nick = params[0] if params else ""
            chans = [ch for ch, m in self._channel_members.items() if nick in m]
            for ch in chans:
                self._channel_members[ch].discard(nick)
                self._channel_members[ch].add(new_nick)
            self.sensors.on_nick(nick, host, new_nick, chans)
            # Transfer auth session on nick change
            if self.pm_handler and hasattr(self.pm_handler, 'auth'):
                self.pm_handler.auth.on_nick_change(self.network, nick, new_nick)

        elif command == "KICK":
            kicker = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            victim = params[1] if len(params) > 1 else ""
            reason = params[2] if len(params) > 2 else ""
            self._channel_members.get(channel, set()).discard(victim)
            if victim == self._current_nick:
                self._channel_members.pop(channel, None)
                self._joined_channels.discard(channel.lower())
                self._handle_self_kick(channel, kicker, reason)
            self.sensors.on_kick(kicker, host, channel, victim, reason)

        elif command == "MODE":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            target = params[0] if params else ""
            mode_str = params[1] if len(params) > 1 else ""
            # Pass all mode targets (params[2:]) to support multi-target modes
            # like +oov Nick1 Nick2 Nick3
            mode_targets = params[2:] if len(params) > 2 else []
            if target.startswith(('#', '&')):
                self.sensors.on_mode(nick, host, target, mode_str, mode_targets)

        elif command == "TOPIC":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            channel = params[0] if params else ""
            topic = params[1] if len(params) > 1 else ""
            self.sensors.on_topic(nick, host, channel, topic)

        elif command == "PRIVMSG":
            nick = nick_from_prefix(prefix)
            host = host_from_prefix(prefix)
            # Prefer WHO-resolved host (more reliable on cloaked networks)
            host = self._nick_hosts.get(nick.lower(), host)
            target = params[0] if params else ""
            text = params[1] if len(params) > 1 else ""

            # PM to the bot — route to PM command handler
            if target.lower() == self._current_nick.lower():
                if self.pm_handler:
                    self.pm_handler.dispatch(nick, host, text)
                # Also try auto-auth on first PM
                if hasattr(self, '_auth') and self._auth:
                    self._auth.try_auto_auth(self.network, nick, host)
                # Watch for NickServ responses
                nick_l = nick.lower()
                if nick_l in ("nickserv", "enforcer"):
                    self._handle_nickserv_notice(text)
            elif text.startswith("\x01ACTION") and text.endswith("\x01"):
                action_text = text[8:-1]
                self.sensors.on_action(nick, host, target, action_text)
            else:
                self.sensors.on_privmsg(nick, host, target, text)
                self.commands.dispatch(nick, target, text, host=host)

    def get_channel_members(self) -> dict:
        return {c: list(m) for c, m in self._channel_members.items()}

    def _do_nickserv_auth(self):
        """Send NickServ IDENTIFY if configured."""
        ns_pass = self.net.get("nickserv_password")
        if not ns_pass:
            return
        self.send_raw(f"PRIVMSG NickServ :IDENTIFY {self.nick} {ns_pass}")
        log.info("Sent NickServ IDENTIFY.")

    def _handle_nickserv_notice(self, text: str):
        """React to NickServ/services messages."""
        tl = text.lower()

        # ── Nick reclaim after GHOST / RELEASE ────────────────────────────
        if self._reclaim_nick and any(w in tl for w in (
            # Standard / UnrealIRCd / InspIRCd GHOST
            "ghost", "killed", "has been killed", "is not online",
            "disconnected", "ghosted",
            # DALnet RELEASE / Enforcer
            "release", "released", "enforcer",
        )):
            log.info(f"Nick release confirmed. Reclaiming {self.nick}.")
            self.send_raw(f"NICK {self.nick}")
            self._reclaim_nick = False
            self._current_nick = self.nick

        # Auth notices — logged for info only, on_connect already fired on 001
        if any(w in tl for w in (
            "you are now identified", "you are already identified",
            "password accepted", "now recognized", "logged in",
            "you have been identified", "now logged in",
            "password correct", "nick identified",
        )):
            log.info("NickServ: authentication confirmed.")

    def _run_on_connect(self):
        """Send configured on_connect commands."""
        cmds = self.net.get("on_connect") or []
        log.info(f"[on_connect] network={self.network} cmds={cmds!r}")
        if not cmds:
            log.info(f"[on_connect] no commands configured for {self.network} — skipping")
            return
        for cmd in cmds:
            raw = cmd.replace("{nick}", self._current_nick)
            log.info(f"[on_connect] sending: {raw!r}")
            self.send_raw(raw)

    # ─── Channel join with retry ──────────────────────────────────────

    def _attempt_join(self, channel: str, key: Optional[str] = None):
        """Send a JOIN and bump the attempt counter for this channel."""
        ch_l = channel.lower()
        self._join_attempts[ch_l] = self._join_attempts.get(ch_l, 0) + 1
        attempt = self._join_attempts[ch_l]
        if key:
            self.send_raw(f"JOIN {channel} {key}")
        else:
            self.send_raw(f"JOIN {channel}")
        log.info(
            f"JOIN {channel} (attempt {attempt}/{self.join_retries})"
        )
        self._start_join_confirm(channel)

    def _start_join_confirm(self, channel: str):
        """Arm a watchdog for a JOIN we just sent, in case the server never
        answers it at all — no JOIN echo and no error numeric."""
        if self.join_confirm_timeout <= 0:
            return
        ch_l = channel.lower()
        old = self._join_confirm_tasks.pop(ch_l, None)
        if old and not old.done():
            old.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._join_confirm_tasks[ch_l] = loop.create_task(
            self._confirm_join(channel)
        )

    async def _confirm_join(self, channel: str):
        try:
            await asyncio.sleep(self.join_confirm_timeout)
        except asyncio.CancelledError:
            return
        ch_l = channel.lower()
        self._join_confirm_tasks.pop(ch_l, None)
        if ch_l in self._joined_channels or not self._connected:
            return
        # Channel may have been parted while we waited.
        if ch_l not in {c.lower() for c in self.channels}:
            return
        log.warning(
            f"JOIN {channel} got no response within "
            f"{self.join_confirm_timeout}s — server swallowed it, retrying."
        )
        self._schedule_join_retry(channel, "timeout", "no response from server")

    def _cancel_join_confirm(self, channel: str):
        task = self._join_confirm_tasks.pop(channel.lower(), None)
        if task and not task.done():
            task.cancel()

    def _audit_channels(self):
        """Periodic sweep: re-JOIN any configured channel we are not in and
        have no retry pending for.

        This is the backstop for cases the error-numeric and confirm-timeout
        paths don't cover: a retry task that died unexpectedly, a channel
        added to self.channels without a join, or the bot being forced out
        of a channel by a PART it didn't ask for.
        """
        if not self._connected:
            return
        for chan in list(self.channels):
            ch_l = chan.lower()
            if ch_l in self._joined_channels or ch_l in self._gave_up:
                continue
            retry = self._join_retry_tasks.get(ch_l)
            if retry and not retry.done():
                continue
            confirm = self._join_confirm_tasks.get(ch_l)
            if confirm and not confirm.done():
                continue
            log.warning(
                f"Channel audit: not in {chan} and no retry pending — "
                f"re-joining."
            )
            self._attempt_join(chan)

    def _mark_join_success(self, channel: str):
        """Channel joined successfully — clear attempt state and pending retry."""
        ch_l = channel.lower()
        self._joined_channels.add(ch_l)
        self._join_attempts.pop(ch_l, None)
        self._gave_up.discard(ch_l)
        task = self._join_retry_tasks.pop(ch_l, None)
        if task and not task.done():
            task.cancel()
        self._cancel_join_confirm(channel)
        log.info(f"Joined {channel} successfully.")

    def _schedule_join_retry(self, channel: str, code: str, reason: str):
        """Schedule a delayed retry for a failed JOIN.

        Doesn't give up after just join_retries quick attempts: falls back to
        a slow retry cadence (join_retry_backoff_delay) instead of stopping,
        so the bot can recover from transient conditions — e.g. Undernet's
        post-netsplit channel-delay lock — without needing a full reconnect,
        which may not happen for a long time if the bot's own server isn't
        the one that split. That backoff phase is itself capped at
        join_retry_backoff_retries so a truly stuck channel (permanent ban,
        etc.) doesn't get retried forever.
        """
        ch_l = channel.lower()
        if ch_l in self._joined_channels:
            return
        # Avoid stacking multiple pending retries for the same channel
        existing = self._join_retry_tasks.get(ch_l)
        if existing and not existing.done():
            return
        # An answer arrived (or the confirm watchdog itself fired), so the
        # "server never replied" watchdog has done its job.
        self._cancel_join_confirm(channel)
        attempts = self._join_attempts.get(ch_l, 0)
        if attempts < self.join_retries:
            delay = self.join_retry_delay
            log.warning(
                f"JOIN {channel} failed ({code} {reason}) — "
                f"retrying in {delay}s "
                f"(attempt {attempts}/{self.join_retries} done)."
            )
        else:
            backoff_attempt = attempts - self.join_retries
            if backoff_attempt >= self.join_retry_backoff_retries:
                log.warning(
                    f"JOIN {channel} failed ({code} {reason}) — giving up "
                    f"after {self.join_retries} quick + {backoff_attempt} "
                    f"backoff attempts."
                )
                # Remember, so the periodic audit doesn't immediately undo
                # the decision to stop. Cleared on reconnect / manual join.
                self._gave_up.add(ch_l)
                return
            delay = self.join_retry_backoff_delay
            log.warning(
                f"JOIN {channel} still failing ({code} {reason}) after "
                f"{attempts} attempts — backing off to a slow retry "
                f"every {delay}s "
                f"(backoff attempt {backoff_attempt}/{self.join_retry_backoff_retries})."
            )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._join_retry_tasks[ch_l] = loop.create_task(
            self._delayed_join_retry(channel, delay)
        )

    async def _delayed_join_retry(self, channel: str, delay: Optional[float] = None):
        try:
            await asyncio.sleep(delay if delay is not None else self.join_retry_delay)
        except asyncio.CancelledError:
            return
        ch_l = channel.lower()
        self._join_retry_tasks.pop(ch_l, None)
        if ch_l in self._joined_channels:
            return
        if not self._connected:
            return
        self._attempt_join(channel)

    def _handle_self_kick(self, channel: str, kicker: str, reason: str):
        """We were kicked — rejoin after a short delay unless that would
        mean flapping in a kick war (kicked repeatedly in a short window)."""
        if not self.rejoin_on_kick:
            log.info(f"Kicked from {channel} by {kicker} ({reason}) — "
                      f"rejoin_on_kick disabled, not rejoining.")
            return
        ch_l = channel.lower()
        now = time.time()
        cutoff = now - self.rejoin_kick_window
        hits = [t for t in self._kick_history.get(ch_l, []) if t >= cutoff]
        hits.append(now)
        self._kick_history[ch_l] = hits
        if len(hits) > self.rejoin_max_kicks:
            log.warning(
                f"Kicked from {channel} by {kicker} ({reason}) — "
                f"{len(hits)} kicks within {self.rejoin_kick_window}s, "
                f"not rejoining to avoid a kick war."
            )
            return
        log.info(
            f"Kicked from {channel} by {kicker} ({reason}) — "
            f"rejoining in {self.rejoin_delay}s "
            f"({len(hits)}/{self.rejoin_max_kicks} recent kicks)."
        )
        self._join_attempts.pop(ch_l, None)
        # The kick-rejoin path takes over channel state from any pending
        # join-retry task, so there's only ever one rejoin in flight.
        task = self._join_retry_tasks.pop(ch_l, None)
        if task and not task.done():
            task.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._join_retry_tasks[ch_l] = loop.create_task(
            self._delayed_kick_rejoin(channel)
        )

    async def _delayed_kick_rejoin(self, channel: str):
        try:
            await asyncio.sleep(self.rejoin_delay)
        except asyncio.CancelledError:
            return
        ch_l = channel.lower()
        self._join_retry_tasks.pop(ch_l, None)
        if ch_l in self._joined_channels:
            return
        if not self._connected:
            return
        self._attempt_join(channel)

    async def join_channel(self, channel: str):
        """JOIN a channel live and add it to the tracked list."""
        if channel not in self.channels:
            self.channels.append(channel)
        # Reset retry state for a fresh manual join
        ch_l = channel.lower()
        self._joined_channels.discard(ch_l)
        self._join_attempts.pop(ch_l, None)
        self._kick_history.pop(ch_l, None)
        self._gave_up.discard(ch_l)
        task = self._join_retry_tasks.pop(ch_l, None)
        if task and not task.done():
            task.cancel()
        self._cancel_join_confirm(channel)
        self._attempt_join(channel)

    async def part_channel(self, channel: str):
        """PART a channel live and remove it from the tracked list."""
        if channel in self.channels:
            self.channels.remove(channel)
        ch_l = channel.lower()
        self._joined_channels.discard(ch_l)
        self._join_attempts.pop(ch_l, None)
        self._kick_history.pop(ch_l, None)
        self._gave_up.discard(ch_l)
        task = self._join_retry_tasks.pop(ch_l, None)
        if task and not task.done():
            task.cancel()
        self._cancel_join_confirm(channel)
        self.send_raw(f"PART {channel} :Removed by admin")

    def _reset_join_state(self):
        """Clear all join tracking when connection is lost.
        
        This ensures that on reconnection, the bot will treat all JOINs
        as fresh attempts rather than skipping them because they appear
        already-joined from the previous session. This is critical for
        proper recovery from network splits.
        """
        self._joined_channels.clear()
        self._join_attempts.clear()
        self._channel_members.clear()
        self._kick_history.clear()
        self._gave_up.clear()
        self._whox_pending.clear()
        # Cancel any pending retry tasks
        for task in self._join_retry_tasks.values():
            if not task.done():
                task.cancel()
        self._join_retry_tasks.clear()
        for task in self._join_confirm_tasks.values():
            if not task.done():
                task.cancel()
        self._join_confirm_tasks.clear()
        # Per-session registration flags. These are not join state, but they
        # must reset too: _ghost_sent in particular is only ever set, so
        # without this the *second* and every later reconnect would skip
        # GHOST entirely and strand the bot on its altnick.
        self._sasl_authed  = False
        self._ghost_sent   = False
        self._reclaim_nick = False
        self._current_nick = self.nick
        log.info("Cleared join state on disconnect — will rejoin fresh on reconnection")

    async def disconnect(self):
        self._connected = False
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None
        self._reset_join_state()
        if self._writer:
            self.send_raw("QUIT :statsbot shutting down")
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass


def _split_message(text: str, max_len: int = 400) -> List[str]:
    """Split long messages into chunks."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
