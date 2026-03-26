"""ClaudeClaw 프로세스 관리 - 데몬의 시작·정지·상태 확인과 엔트리포인트.

cli.py에서 사용되는 함수들과, `python -m src.process`로 기동하는 엔트리포인트를 제공한다.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

try:
    from .config import BASE_DIR, DAEMON_LOG, DEFAULT_PORT, PID_FILE, SOCKET_PATH, setup_logging
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import BASE_DIR, DAEMON_LOG, DEFAULT_PORT, PID_FILE, SOCKET_PATH, setup_logging


# ---------------------------------------------------------------------------
# 프로세스 관리
# ---------------------------------------------------------------------------


def start_daemon_process(port: int = DEFAULT_PORT) -> None:
    """데몬을 분리된 백그라운드 프로세스로 기동한다.

    Args:
        port: API 서버가 리슨할 포트 번호.
    """
    python = sys.executable
    with open(str(DAEMON_LOG), "a") as log:
        subprocess.Popen(  # noqa: S603
            [python, "-m", "src.process", "--port", str(port)],
            cwd=str(BASE_DIR),
            stdout=log,
            stderr=log,
            start_new_session=True,
        )


def stop_daemon_process() -> bool:
    """소켓 경유로 정지 요청을 보내거나, PID 파일로 SIGTERM을 전송한다.

    Returns:
        정지 요청 전송에 성공한 경우 True.
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(str(SOCKET_PATH))
        sock.sendall((json.dumps({"type": "stop"}) + "\n").encode("utf-8"))
        resp_raw = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp_raw += chunk
            if b"\n" in resp_raw:
                break
        sock.close()
        return True
    except Exception as e:
        _logger.debug("stop_daemon_process: socket stop failed, falling back to PID: %s", e)

    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError, OSError) as e:
        _logger.debug("stop_daemon_process: PID fallback failed: %s", e)

    return False


def get_daemon_status() -> tuple[str, int | None]:
    """데몬의 상태와 PID를 반환한다.

    Returns:
        tuple: (status_string, pid_or_None)
            status_string은 'running', 'stopped', 'stale' 중 하나.
    """
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return "stopped", None

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "stale", pid
    except PermissionError as e:
        _logger.debug("get_daemon_status: cannot signal PID %d (no permission): %s", pid, e)

    if SOCKET_PATH.exists():
        return "running", pid

    return "stale", pid


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------


async def _main(port: int) -> None:
    """데몬과 API 서버를 기동하고 셧다운까지 대기한다.

    Args:
        port: API 서버가 리슨할 포트 번호.
    """
    os.chdir(str(BASE_DIR))
    setup_logging()

    try:
        import uvicorn  # noqa: PLC0415

        from .api import app as api_app  # noqa: PLC0415
    except ImportError as e:
        _logger.warning("API server disabled (fastapi/uvicorn not installed): %s", e)
        from .daemon import OpenClaudeDaemon  # noqa: PLC0415

        daemon = OpenClaudeDaemon()
        await daemon.start()
        return

    class _NoSignalServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass  # daemon 측의 시그널 핸들러를 유지하기 위해 아무것도 하지 않음

    from .daemon import OpenClaudeDaemon  # noqa: PLC0415
    from .discord_bot import create_discord_bot  # noqa: PLC0415
    from .slack_bot import create_slack_bot  # noqa: PLC0415

    daemon = OpenClaudeDaemon()
    api_config = uvicorn.Config(api_app, host="0.0.0.0", port=port, log_level="info")  # noqa: S104
    api_server = _NoSignalServer(api_config)
    discord_bot = create_discord_bot()
    slack_bot = create_slack_bot()

    async def _run_daemon() -> None:
        await daemon.start()
        if discord_bot is not None:
            await discord_bot.stop()
        if slack_bot is not None:
            await slack_bot.stop()
        api_server.should_exit = True

    _logger.info("ClaudeClaw API server will start on port %d", port)
    coros: list[Any] = [_run_daemon(), api_server.serve()]
    if discord_bot is not None:
        coros.append(discord_bot.start())
    if slack_bot is not None:
        coros.append(slack_bot.start())
    await asyncio.gather(*coros)


if __name__ == "__main__":
    _parser = argparse.ArgumentParser(description="ClaudeClaw Daemon")
    _parser.add_argument("--port", type=int, default=DEFAULT_PORT, metavar="PORT")
    _args = _parser.parse_args()
    asyncio.run(_main(_args.port))
