"""ClaudeClaw Heartbeat 스케줄러 - 정기 폴링.

config.json의 heartbeat.every 설정에 따라 정기적으로 메인 세션에서
에이전트 턴을 실행하여 HEARTBEAT.md의 체크리스트를 처리한다.
HEARTBEAT_OK만의 응답은 로그만 남기고 완료하며, 배포를 수행하지 않는다.
"""

import asyncio
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, time
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

try:
    from .config import CONFIG_FILE, HEARTBEAT_MD
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import CONFIG_FILE, HEARTBEAT_MD

DEFAULT_HEARTBEAT_PROMPT: str = (
    "Read HEARTBEAT.md if it exists (workspace context). "
    "Follow it strictly. Do not infer or repeat old tasks from prior chats. "
    "If nothing needs attention, reply HEARTBEAT_OK."
)
HEARTBEAT_OK_TOKEN: str = "HEARTBEAT_OK"  # noqa: S105
DEFAULT_ACK_MAX_CHARS: int = 300


def parse_duration_to_seconds(every: str) -> int | None:
    """기간 문자열("30m", "1h" 등)을 초 단위로 변환한다.

    "0m" / "0h" / 빈 문자열 → None (비활성화)

    Args:
        every: 기간 문자열. "30m"(분) 또는 "1h"(시간) 형식.

    Returns:
        초 단위(int). 비활성화의 경우 None.
    """
    if not every:
        return None
    every = every.strip()
    match = re.fullmatch(r"(\d+)([mh])", every)
    if not match:
        _logger.warning("heartbeat: invalid every format: %r", every)
        return None
    value, unit = int(match.group(1)), match.group(2)
    if value == 0:
        return None
    if unit == "m":
        return value * 60
    return value * 3600


def is_heartbeat_ok(text: str, ack_max_chars: int = DEFAULT_ACK_MAX_CHARS) -> bool:
    """HEARTBEAT_OK 판정 로직.

    판정 규칙:
    1. strip() 후 HEARTBEAT_OK만 → True
    2. 선두에 HEARTBEAT_OK가 있고, 나머지 문자 수가 ack_max_chars 이하 → True
    3. 말미에 HEARTBEAT_OK가 있고, 선행 문자 수가 ack_max_chars 이하 → True
    4. 중간에 있는 경우 → False

    Args:
        text: 에이전트의 응답 텍스트.
        ack_max_chars: 허용하는 추가 문자 수의 상한.

    Returns:
        ACK(억제)해야 하는 경우 True.
    """
    stripped = text.strip()

    # 규칙1: HEARTBEAT_OK만
    if stripped == HEARTBEAT_OK_TOKEN:
        return True

    # 규칙2: 선두에 HEARTBEAT_OK
    if stripped.startswith(HEARTBEAT_OK_TOKEN):
        remainder = stripped[len(HEARTBEAT_OK_TOKEN) :]
        if len(remainder.strip()) <= ack_max_chars:
            return True

    # 규칙3: 말미에 HEARTBEAT_OK
    if stripped.endswith(HEARTBEAT_OK_TOKEN):
        preceding = stripped[: -len(HEARTBEAT_OK_TOKEN)]
        if len(preceding.strip()) <= ack_max_chars:
            return True

    # 규칙4: 중간에 있는 (또는 없는) 경우
    return False


def is_heartbeat_md_empty(path: Path) -> bool:
    """HEARTBEAT.md가 실질적으로 비어 있는지 확인한다.

    빈 줄·Markdown 헤더(# 시작)·HTML 코멘트 줄만 있으면 True.
    파일이 존재하지 않거나 읽을 수 없는 경우 False (건너뛰지 않음).

    Args:
        path: HEARTBEAT.md의 경로.

    Returns:
        실질적으로 비어 있는 경우 True.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        return False
    return True


class HeartbeatScheduler:
    """정기 폴링 스케줄러.

    config.json의 heartbeat 설정에 따라 일정 간격으로 메인 세션의
    에이전트 턴을 실행한다.
    """

    def __init__(
        self,
        execute_fn: Callable[[str, str], Awaitable[str | None]],
    ) -> None:
        """초기화.

        Args:
            execute_fn: 실행 콜백.
                        시그니처: execute_fn(session_id, prompt) -> response_text | None
        """
        self._execute_fn = execute_fn
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """config.json을 읽어들여 유효한 경우 정기 실행 태스크를 시작한다."""
        config = self._load_config()
        heartbeat_cfg: dict[str, Any] = config.get("heartbeat", {})

        # disabled 플래그 확인
        disabled = heartbeat_cfg.get("disabled", False)
        if disabled is True or str(disabled).lower() == "true":
            _logger.info("Heartbeat disabled (disabled flag)")
            return

        # every 설정 확인
        interval = parse_duration_to_seconds(str(heartbeat_cfg.get("every", "")))
        if interval is None:
            _logger.info("Heartbeat disabled (every not set)")
            return

        _logger.info("Heartbeat scheduler started (interval=%ds)", interval)
        self._task = asyncio.create_task(self._run_loop(interval))

    async def stop(self) -> None:
        """정기 실행 태스크를 취소하여 정지한다."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                _logger.debug("heartbeat: task cancelled")
        self._task = None

    def _load_config(self) -> dict[str, Any]:
        """config.json을 읽어들여 반환한다."""
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except (OSError, json.JSONDecodeError):
            return {}

    def _is_in_active_hours(self, active_hours: dict[str, Any]) -> bool:
        """현재 시각이 active_hours 범위 내인지 확인한다. 설정 없음 → True (항상 활성)."""
        start_str = active_hours.get("start", "")
        end_str = active_hours.get("end", "")
        if not start_str or not end_str:
            return True

        try:
            now = datetime.now().time()
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            return time(start_h, start_m) <= now <= time(end_h, end_m)
        except (ValueError, AttributeError):
            _logger.warning("heartbeat: invalid active_hours format, ignoring")
            return True

    async def _run_loop(self, interval_seconds: int) -> None:
        """지정 간격으로 _execute_heartbeat()를 반복하는 asyncio 무한 루프.

        첫 실행은 interval_seconds 대기 후 (데몬 기동 직후에는 실행하지 않음).
        """
        while True:
            await asyncio.sleep(interval_seconds)
            await self._execute_heartbeat()

    async def _execute_heartbeat(self) -> None:
        """1회의 Heartbeat 턴을 실행한다."""
        config = self._load_config()
        heartbeat_cfg: dict[str, Any] = config.get("heartbeat", {})

        # 활성 시간대 확인
        if not self._is_in_active_hours(heartbeat_cfg.get("active_hours", {})):
            _logger.debug("heartbeat: skipped (outside active_hours)")
            return

        # HEARTBEAT.md가 실질적으로 비어 있으면 건너뜀
        if is_heartbeat_md_empty(HEARTBEAT_MD):
            _logger.debug("heartbeat: skipped (HEARTBEAT.md is empty)")
            return

        prompt: str = str(heartbeat_cfg.get("prompt", DEFAULT_HEARTBEAT_PROMPT))
        ack_max_chars: int = int(heartbeat_cfg.get("ack_max_chars", DEFAULT_ACK_MAX_CHARS))

        try:
            response = await self._execute_fn("main", prompt)
        except Exception as e:
            _logger.error("heartbeat: execute error: %s", e)
            return

        if response is None:
            _logger.warning("heartbeat: no response received")
            return

        if is_heartbeat_ok(response, ack_max_chars):
            _logger.info("HEARTBEAT_OK (suppressed)")
        else:
            _logger.info("Heartbeat alert (len=%d)", len(response))
