"""세션 관리 - alias → sdk_session_id 매핑의 영속화와 JSONL 조작."""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterator

_logger = logging.getLogger(__name__)

try:
    from .config import CLAUDE_PROJECTS_DIR, SESSIONS_DIR, SESSIONS_JSON
    from .utils import atomic_write_json
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import CLAUDE_PROJECTS_DIR, SESSIONS_DIR, SESSIONS_JSON
    from src.utils import atomic_write_json


class SessionStore:
    """세션 alias → sdk_session_id 매핑을 관리한다."""

    def __init__(self) -> None:
        """sessions.json에서 읽어들여 초기화한다."""
        self._sessions: dict[str, str] = self._load()

    # ------------------------------------------------------------------
    # dict-like 인터페이스
    # ------------------------------------------------------------------

    def get(self, alias: str) -> str | None:
        """Alias에 대응하는 sdk_session_id를 반환한다. 없으면 None."""
        return self._sessions.get(alias)

    def items(self) -> Iterator[tuple[str, str]]:
        """(alias, sdk_session_id) 이터레이터를 반환한다."""
        return iter(self._sessions.items())

    def values(self) -> Iterator[str]:
        """sdk_session_id 이터레이터를 반환한다."""
        return iter(self._sessions.values())

    def __setitem__(self, alias: str, sdk_id: str) -> None:
        """Alias와 sdk_session_id를 설정한다."""
        self._sessions[alias] = sdk_id

    def __delitem__(self, alias: str) -> None:
        """Alias를 삭제한다."""
        del self._sessions[alias]

    def __len__(self) -> int:
        """세션 수를 반환한다."""
        return len(self._sessions)

    def __contains__(self, alias: object) -> bool:
        """Alias가 존재하는지 확인한다."""
        return alias in self._sessions

    def clear(self) -> None:
        """전체 세션을 메모리에서 삭제한다."""
        self._sessions = {}

    # ------------------------------------------------------------------
    # 영속화
    # ------------------------------------------------------------------

    def save(self) -> None:
        """sessions.json에 원자적으로 기록한다."""
        try:
            atomic_write_json(SESSIONS_JSON, self._sessions, dir_path=SESSIONS_DIR)
        except Exception as e:
            _logger.warning("Failed to save sessions: %s", e)

    def _load(self) -> dict[str, str]:
        """sessions.json에서 alias → sdk_session_id를 읽어들인다."""
        try:
            data = json.loads(SESSIONS_JSON.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except FileNotFoundError:
            _logger.debug("sessions.json not found, starting with empty sessions")
        except Exception as e:
            _logger.warning("Failed to load sessions: %s", e)
        return {}

    # ------------------------------------------------------------------
    # JSONL 조작
    # ------------------------------------------------------------------

    def delete_jsonl(self, sdk_session_id: str) -> tuple[str | None, str | None]:
        """sdk_session_id에 대응하는 JSONL 파일을 삭제하고 (deleted_name, error_message)를 반환한다."""
        jsonl_path = CLAUDE_PROJECTS_DIR / f"{sdk_session_id}.jsonl"
        try:
            jsonl_path.unlink()
            return jsonl_path.name, None
        except FileNotFoundError:
            _logger.debug("JSONL already absent: %s", jsonl_path.name)
            return None, None
        except Exception as e:
            return None, f"{jsonl_path.name}: {e}"

    def read_stats(self, sdk_session_id: str) -> dict[str, Any]:
        """sdk_session_id에 대응하는 JSONL에서 last_active와 total_tokens를 반환한다."""
        jsonl_path = CLAUDE_PROJECTS_DIR / f"{sdk_session_id}.jsonl"
        last_active: str | None = None
        total_tokens = 0

        try:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return {"last_active": None, "total_tokens": 0}

        for raw in lines:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError as e:
                _logger.warning("Skipping malformed JSONL line in %s: %s", jsonl_path.name, e)
                continue
            if "timestamp" in entry:
                last_active = entry["timestamp"]
            msg = entry.get("message")
            if isinstance(msg, dict) and msg.get("stop_reason"):
                usage = msg.get("usage") or {}
                total_tokens += usage.get("input_tokens", 0)
                total_tokens += usage.get("cache_creation_input_tokens", 0)
                total_tokens += usage.get("cache_read_input_tokens", 0)
                total_tokens += usage.get("output_tokens", 0)

        return {"last_active": last_active, "total_tokens": total_tokens}
