"""ClaudeClaw CLI - 명령어 인수의 파싱과 실행.

명령어 목록:
    claudeclaw start [--port PORT]
    claudeclaw stop
    claudeclaw restart [--port PORT]
    claudeclaw status
    claudeclaw logs [--tail N]
    claudeclaw sessions
    claudeclaw sessions cleanup
    claudeclaw sessions delete SESSION_ID
    claudeclaw [--session-id ID] --message TEXT
    claudeclaw [--session-id ID] -m TEXT
    echo "질문" | claudeclaw
    claudeclaw < file.txt
    cat file.txt | claudeclaw -m "이것을 요약해 줘"
"""

import argparse
import asyncio
import sys
from pathlib import Path

import argcomplete

try:
    from .commands.config_cmds import cmd_config_get, cmd_config_set, cmd_config_show, config_get_nested
    from .commands.cron_cmds import (
        cmd_cron_add,
        cmd_cron_delete,
        cmd_cron_edit,
        cmd_cron_list,
        cmd_cron_run,
        cmd_cron_runs,
    )
    from .commands.daemon_cmds import cmd_logs, cmd_restart, cmd_start, cmd_status, cmd_stop
    from .commands.message_cmds import cmd_message, resolve_message
    from .commands.session_cmds import cmd_sessions, cmd_sessions_cleanup, cmd_sessions_delete
    from .config import DEFAULT_PORT, DEFAULT_SESSION_ID
    from .utils import load_config
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.commands.config_cmds import cmd_config_get, cmd_config_set, cmd_config_show, config_get_nested
    from src.commands.cron_cmds import (
        cmd_cron_add,
        cmd_cron_delete,
        cmd_cron_edit,
        cmd_cron_list,
        cmd_cron_run,
        cmd_cron_runs,
    )
    from src.commands.daemon_cmds import cmd_logs, cmd_restart, cmd_start, cmd_status, cmd_stop
    from src.commands.message_cmds import cmd_message, resolve_message
    from src.commands.session_cmds import cmd_sessions, cmd_sessions_cleanup, cmd_sessions_delete
    from src.config import DEFAULT_PORT, DEFAULT_SESSION_ID
    from src.utils import load_config


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI의 엔트리포인트."""
    cli = OpenClaudeCLI()
    cli.run()


# ---------------------------------------------------------------------------
# CLI 클래스
# ---------------------------------------------------------------------------


class OpenClaudeCLI:
    """명령줄 인수를 파싱하여 데몬 조작과 메시지 전송을 수행하는 클래스."""

    def run(self) -> None:  # noqa: C901
        """인수를 파싱하여 대응하는 명령어를 실행한다."""
        parser = self._build_parser()
        argcomplete.autocomplete(parser)
        args = parser.parse_args()

        if args.command == "start":
            cmd_start(getattr(args, "port", DEFAULT_PORT))
        elif args.command == "stop":
            cmd_stop()
        elif args.command == "restart":
            cmd_restart(getattr(args, "port", DEFAULT_PORT))
        elif args.command == "status":
            cmd_status()
        elif args.command == "logs":
            cmd_logs(getattr(args, "tail", None))
        elif args.command == "sessions":
            if getattr(args, "sessions_command", None) == "cleanup":
                asyncio.run(cmd_sessions_cleanup())
            elif getattr(args, "sessions_command", None) == "delete":
                asyncio.run(cmd_sessions_delete(args.session_id))
            else:
                asyncio.run(cmd_sessions())
        elif args.command == "cron":
            cron_cmd = getattr(args, "cron_command", None)
            if cron_cmd == "add":
                asyncio.run(cmd_cron_add(args.schedule, args.name, args.session, args.message))
            elif cron_cmd == "list":
                asyncio.run(cmd_cron_list())
            elif cron_cmd == "delete":
                asyncio.run(cmd_cron_delete(args.job_id))
            elif cron_cmd == "run":
                asyncio.run(cmd_cron_run(args.job_id))
            elif cron_cmd == "edit":
                enable_flag: bool | None = True if args.enable else (False if args.disable else None)
                asyncio.run(
                    cmd_cron_edit(args.job_id, args.name, args.schedule, args.session, args.message, enable_flag)
                )
            elif cron_cmd == "runs":
                asyncio.run(cmd_cron_runs(args.job_id, args.limit))
            else:
                parser.parse_args(["cron", "--help"])
        elif args.command == "config":
            config_cmd = getattr(args, "config_command", None)
            if config_cmd == "set":
                cmd_config_set(args.key, args.value)
            elif config_cmd == "get":
                cmd_config_get(args.key)
            elif config_cmd == "show":
                cmd_config_show()
            else:
                parser.parse_args(["config", "--help"])
        else:
            message = resolve_message(args.message)
            if message is not None:
                asyncio.run(cmd_message(args.session_id, message))
            else:
                parser.print_help()

    def _build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="claudeclaw",
            description="ClaudeClaw - Resident AI Agent System",
        )

        subparsers = parser.add_subparsers(dest="command")
        _default_port = config_get_nested(load_config(), "default.port") or DEFAULT_PORT
        start_parser = subparsers.add_parser("start", help="Start the ClaudeClaw daemon")
        start_parser.add_argument(
            "--port",
            type=int,
            default=_default_port,
            metavar="PORT",
            help=f"Port for the API server (default: {_default_port})",
        )
        subparsers.add_parser("stop", help="Stop the ClaudeClaw daemon")
        restart_parser = subparsers.add_parser("restart", help="Restart the ClaudeClaw daemon")
        restart_parser.add_argument(
            "--port",
            type=int,
            default=_default_port,
            metavar="PORT",
            help=f"Port for the API server (default: {_default_port})",
        )
        subparsers.add_parser("status", help="Show daemon status")
        logs_parser = subparsers.add_parser("logs", help="Show daemon log")
        logs_parser.add_argument(
            "--tail",
            type=int,
            default=None,
            metavar="N",
            help="Show last N lines (default: show all)",
        )
        sessions_parser = subparsers.add_parser("sessions", help="Manage conversation sessions")
        sessions_sub = sessions_parser.add_subparsers(dest="sessions_command")
        sessions_sub.add_parser("cleanup", help="Clean up all sessions")
        delete_parser = sessions_sub.add_parser("delete", help="Delete a specific session")
        delete_parser.add_argument("session_id", metavar="SESSION_ID", help="Session alias to delete")

        cron_parser = subparsers.add_parser("cron", help="Manage cron jobs")
        cron_sub = cron_parser.add_subparsers(dest="cron_command")

        cron_add_parser = cron_sub.add_parser("add", help="Add a new cron job")
        cron_add_parser.add_argument("schedule", metavar="CRON", help='5-field cron expression e.g. "0 9 * * *"')
        cron_add_parser.add_argument("--name", "-n", default=None, metavar="NAME", help="Job display name")
        cron_add_parser.add_argument(
            "--session", "-s", default=DEFAULT_SESSION_ID, metavar="SESSION_ID", help="Target session alias"
        )
        cron_add_parser.add_argument("--message", "-m", required=True, metavar="MESSAGE", help="Message to send")

        cron_sub.add_parser("list", help="List all cron jobs")

        cron_delete_parser = cron_sub.add_parser("delete", help="Delete a cron job")
        cron_delete_parser.add_argument("job_id", metavar="JOB_ID", help="Job ID to delete")

        cron_run_parser = cron_sub.add_parser("run", help="Manually trigger a cron job")
        cron_run_parser.add_argument("job_id", metavar="JOB_ID", help="Job ID to run")

        cron_edit_parser = cron_sub.add_parser("edit", help="Edit an existing cron job")
        cron_edit_parser.add_argument("job_id", metavar="JOB_ID", help="Job ID to edit")
        cron_edit_parser.add_argument("--name", "-n", default=None, metavar="NAME", help="New job display name")
        cron_edit_parser.add_argument("--schedule", default=None, metavar="CRON", help="New 5-field cron expression")
        cron_edit_parser.add_argument(
            "--session", "-s", default=None, metavar="SESSION_ID", help="New target session alias"
        )
        cron_edit_parser.add_argument("--message", "-m", default=None, metavar="MESSAGE", help="New message to send")
        _enable_group = cron_edit_parser.add_mutually_exclusive_group()
        _enable_group.add_argument("--enable", action="store_true", default=False, help="Enable the job")
        _enable_group.add_argument("--disable", action="store_true", default=False, help="Disable the job")

        cron_runs_parser = cron_sub.add_parser("runs", help="Show cron job run history")
        cron_runs_parser.add_argument(
            "job_id",
            nargs="?",
            default=None,
            metavar="JOB_ID",
            help="Job ID (omit to show summary of all jobs)",
        )
        cron_runs_parser.add_argument(
            "--limit",
            "-l",
            type=int,
            default=20,
            metavar="N",
            help="Maximum number of runs to display (default: 20)",
        )

        config_parser = subparsers.add_parser("config", help="Manage persistent configuration")
        config_sub = config_parser.add_subparsers(dest="config_command")

        config_set_parser = config_sub.add_parser("set", help="Set a config value")
        config_set_parser.add_argument("key", metavar="KEY", help="Config key in dot notation (e.g. default.port)")
        config_set_parser.add_argument("value", metavar="VALUE", help="Value to set")

        config_get_parser = config_sub.add_parser("get", help="Get a config value")
        config_get_parser.add_argument("key", metavar="KEY", help="Config key in dot notation (e.g. default.port)")

        config_sub.add_parser("show", help="Show all config values")

        parser.add_argument(
            "--session-id",
            default=DEFAULT_SESSION_ID,
            metavar="SESSION_ID",
            help=f"Session identifier (default: {DEFAULT_SESSION_ID})",
        )
        parser.add_argument(
            "--message",
            "-m",
            default=None,
            metavar="MESSAGE",
            help="Message to send to the agent",
        )
        return parser
