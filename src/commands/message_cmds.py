"""메시지 전송 명령어 - cmd_message와 스트리밍 헬퍼."""

import asyncio
import json
import logging
import sys
from typing import Any, cast

_CRAB = "🦀"
_logger = logging.getLogger(__name__)

try:
    from ..config import DEFAULT_PORT, SOCKET_PATH
    from ..process import get_daemon_status, start_daemon_process
    from ..utils import load_config
    from .config_cmds import config_get_nested
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _pkg_root = str(_Path(__file__).parent.parent.parent)
    if _pkg_root not in _sys.path:
        _sys.path.insert(0, _pkg_root)
    from src.config import DEFAULT_PORT, SOCKET_PATH
    from src.process import get_daemon_status, start_daemon_process
    from src.utils import load_config
    from src.commands.config_cmds import config_get_nested


def _is_daemon_up() -> bool:
    status, _ = get_daemon_status()
    return status == "running"


def resolve_message(message_arg: str | None) -> str | None:
    """커맨드라인 인수와 stdin으로부터 메시지를 결정한다.

    stdin이 파이프/리다이렉트인 경우 stdin의 내용을 읽어들인다.
    - message_arg가 None인 경우: stdin의 내용을 그대로 메시지로 사용한다.
    - message_arg가 있는 경우: stdin의 내용을 앞에 붙이고 message_arg를 뒤에 결합한다.
    """
    if sys.stdin.isatty():
        return message_arg

    stdin_text = sys.stdin.read().strip()
    if not stdin_text:
        return message_arg

    return stdin_text if message_arg is None else stdin_text + "\n\n" + message_arg


async def _read_json(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line:
        return {}
    return cast(dict[str, Any], json.loads(line.decode("utf-8").strip()))


async def cmd_message(session_id: str, message: str) -> None:
    """에이전트에 메시지를 전송하고 응답을 스트리밍으로 표시한다."""
    if not _is_daemon_up():
        print("Starting ClaudeClaw daemon...")
        _cfg_port = config_get_nested(load_config(), "default.port") or DEFAULT_PORT
        start_daemon_process(_cfg_port)
        for _ in range(150):
            await asyncio.sleep(0.1)
            if SOCKET_PATH.exists():
                break
        else:
            print(
                "ERROR: Daemon did not start. Check daemon.log for details.",
                file=sys.stderr,
            )
            sys.exit(1)

    try:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    except (FileNotFoundError, ConnectionRefusedError) as e:
        print(f"ERROR: Cannot connect to daemon: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        print(f"{_CRAB} ClaudeClaw（{session_id}）")
        print("│")
        print("◇")

        request = {"type": "query", "session_id": session_id, "message": message}
        writer.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()

        while True:
            response = await _read_json(reader)
            resp_type = response.get("type")

            if resp_type == "chunk":
                text = response.get("text", "")
                print(text, end="", flush=True)

            elif resp_type == "done":
                print()
                break

            elif resp_type == "error":
                print()
                print(f"ERROR: {response.get('message')}", file=sys.stderr)
                sys.exit(1)

            else:
                _logger.debug("cmd_message: unknown response type: %s", resp_type)

    finally:
        writer.close()
        await writer.wait_closed()
