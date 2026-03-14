#!/usr/bin/env python3
"""
main.py — ircstats entry point.

Usage:
    python main.py [--config config/config.yml] [--web-only] [--init-db] [--setup]
"""

import asyncio
import argparse
import logging
import os
import sys
import threading

import yaml


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "data/ircstats.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file))
    except Exception:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
    )


def run_setup(db_path: str):
    """Interactive setup wizard — configure master nicks and passwords."""
    from database.models import add_master_with_password, list_masters_global
    from bot.auth import hash_password

    print("\nircstats setup wizard")
    print("=" * 40)
    existing = list_masters_global()
    if existing:
        print(f"Existing masters: {', '.join(m['pattern'] for m in existing)}")
        print()

    while True:
        nick = input("Add master nick (Enter to finish): ").strip()
        if not nick:
            break
        while True:
            import getpass
            pw = getpass.getpass(f"Password for {nick}: ")
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                print("Passwords don't match, try again.")
                continue
            if len(pw) < 6:
                print("Password too short (min 6 chars).")
                continue
            break
        masks = input(f"Host masks for {nick} (space-separated, or Enter for none): ").strip()
        hashed = hash_password(pw)
        add_master_with_password(nick, hashed, added_by="setup")
        # Store masks separately
        if masks:
            from database.models import get_conn
            with get_conn() as conn:
                conn.execute(
                    "UPDATE masters SET masks=? WHERE lower(pattern)=lower(?)",
                    (masks, nick)
                )
        print(f"Master {nick} configured.\n")

    print("Setup complete.")


def main():
    parser = argparse.ArgumentParser(description="IRC Stats Bot")
    parser.add_argument("--config", default="config/config.yml")
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--setup", action="store_true",
                        help="Configure master nicks and passwords")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config)
    log = logging.getLogger("main")

    db_path = config.get("database", {}).get("path", "data/stats.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    from database.models import init_db, set_db_path
    set_db_path(db_path)
    init_db()

    if args.setup:
        run_setup(db_path)
        return

    if args.init_db:
        print("Database initialized.")
        return

    if args.web_only:
        from web.dashboard import run_dashboard
        run_dashboard(config, db_path)
        return

    from bot.sensors import Sensors
    from bot.auth import AuthManager
    from irc.commands import CommandHandler
    from irc.pm_commands import PMCommandHandler
    from bot.connector import IRCConnector
    from bot.scheduler import Scheduler
    from web.dashboard import run_dashboard, set_config, register_connector

    networks = config.get("networks", [])
    if not networks:
        log.error("No networks configured in config.yml!")
        sys.exit(1)

    # Shared auth manager — one instance handles all networks
    auth = AuthManager()

    connectors = []
    sensors_list = []

    for net_cfg in networks:
        network_name = net_cfg["name"]
        sensors = Sensors(config, network_name)
        sensors_list.append(sensors)

        def make_send(connector_ref):
            def send_fn(channel, text):
                connector_ref.send_msg(channel, text)
            return send_fn

        def make_pm_send(connector_ref):
            def pm_send_fn(nick, text):
                connector_ref.send_notice(nick, text)
            return pm_send_fn

        placeholder_send = [None]
        placeholder_pm = [None]

        cmd_handler = CommandHandler(
            config, network_name,
            lambda ch, tx: placeholder_send[0] and placeholder_send[0](ch, tx),
            auth_manager=auth
        )
        pm_handler = PMCommandHandler(
            network_name, auth,
            lambda nick, tx: placeholder_pm[0] and placeholder_pm[0](nick, tx),
            config
        )

        connector = IRCConnector(config, net_cfg, sensors, cmd_handler,
                                  pm_handler=pm_handler)
        placeholder_send[0] = connector.send_msg
        placeholder_pm[0] = connector.send_notice
        pm_handler.connectors = [connector]
        connectors.append(connector)

    scheduler = Scheduler(sensors_list, connectors, config)

    if config.get("web", {}).get("enabled", True):
        set_config(config, db_path)
        web_thread = threading.Thread(
            target=run_dashboard,
            args=(config, db_path),
            daemon=True,
            name="web-dashboard"
        )
        web_thread.start()
        log.info("Web dashboard thread started.")
        for connector in connectors:
            register_connector(connector)

    async def run_all():
        tasks = [asyncio.create_task(scheduler.run(), name="scheduler")]
        for connector in connectors:
            tasks.append(asyncio.create_task(
                auto_reconnect(connector), name=f"irc-{connector.host}"
            ))
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            scheduler.stop()
            for c in connectors:
                await c.disconnect()

    async def auto_reconnect(connector: IRCConnector, delay: int = 30):
        while True:
            try:
                await connector.connect()
            except KeyboardInterrupt:
                break
            except Exception as e:
                log.error(f"Connection error for {connector.host}: {e}. Reconnecting in {delay}s...")
            await asyncio.sleep(delay)

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        log.info("Bye!")


if __name__ == "__main__":
    main()
