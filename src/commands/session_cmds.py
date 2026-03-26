"""세션 관리 명령어 - sessions / sessions cleanup / sessions delete."""

import sys
from typing import Any, cast

_CRAB = "🦀"

try:
    from ..config import CLAUDE_PROJECTS_DIR
    from ..process import get_daemon_status
    from ..utils import daemon_request
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _pkg_root = str(_Path(__file__).parent.parent.parent)
    if _pkg_root not in _sys.path:
        _sys.path.insert(0, _pkg_root)
    from src.config import CLAUDE_PROJECTS_DIR
    from src.process import get_daemon_status
    from src.utils import daemon_request


def _is_daemon_up() -> bool:
    status, _ = get_daemon_status()
    return status == "running"


async def fetch_sessions() -> list[dict[str, Any]]:
    """데몬에 접속하여 세션 목록을 가져온다. 데몬 미실행 시 빈 리스트를 반환한다."""
    if not _is_daemon_up():
        return []
    try:
        response = await daemon_request({"type": "sessions"})
        if response.get("type") == "sessions_list":
            return cast(list[dict[str, Any]], response.get("sessions", []))
        return []
    except Exception:
        return []


async def cmd_sessions() -> None:
    """데몬에서 세션 목록을 가져와 표시한다."""
    sessions = await fetch_sessions()

    print(f"{_CRAB} ClaudeClaw\n")

    if not sessions:
        print("Sessions: 0")
        return

    print(f"Sessions: {len(sessions)}")
    print(f"Sessions Path: {CLAUDE_PROJECTS_DIR}\n")

    col_id = max(max(len(s["session_id"]) for s in sessions), 10)
    col_sdk = max(max(len(s.get("sdk_session_id") or "-") for s in sessions), 14)
    col_la = max(max(len(s.get("last_active") or "-") for s in sessions), 11)
    print(f"{'session-id':<{col_id}}  {'sdk_session_id':<{col_sdk}}  {'last_active':<{col_la}}  total_tokens")
    for s in sessions:
        alias = s["session_id"]
        sdk_id = s.get("sdk_session_id") or "-"
        last_active = s.get("last_active") or "-"
        total_tokens = s.get("total_tokens", 0)
        print(f"{alias:<{col_id}}  {sdk_id:<{col_sdk}}  {last_active:<{col_la}}  {total_tokens}")


async def cmd_sessions_cleanup() -> None:
    """전체 세션을 클린업한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    try:
        response = await daemon_request({"type": "cleanup_sessions"})
        if response.get("type") == "cleanup_done":
            count = response.get("deleted_count", 0)
            print(f"Cleaned up {count} session(s).")
            for f in response.get("failed", []):
                print(f"  [warn] {f}", file=sys.stderr)
        elif response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


async def cmd_sessions_delete(session_id: str) -> None:
    """지정한 세션을 삭제한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    try:
        response = await daemon_request({"type": "delete_session", "session_id": session_id})
        if response.get("type") == "delete_done":
            print(f"Deleted session: {session_id}")
            if response.get("failed"):
                print(f"  [warn] {response['failed']}", file=sys.stderr)
        elif response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
