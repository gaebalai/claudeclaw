"""ClaudeClaw 공통 유틸리티 함수."""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

try:
    from .config import CONFIG_FILE, SOCKET_PATH
except ImportError:
    import sys

    _pkg_root = str(Path(__file__).parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import CONFIG_FILE, SOCKET_PATH


def atomic_write_json(path: Path, data: Any, dir_path: Path | None = None, indent: int | None = None) -> None:
    """JSON 데이터를 원자적으로 기록한다.

    Args:
        path: 기록 대상 파일 경로.
        data: JSON 직렬화 가능한 데이터.
        dir_path: 임시 파일을 생성할 디렉터리. None인 경우 path의 부모 디렉터리를 사용.
        indent: JSON 인덴트 폭. None인 경우 최소화 출력.
    """
    target_dir = dir_path or path.parent
    fd, tmp = tempfile.mkstemp(dir=str(target_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError as cleanup_err:
            _logger.debug("Failed to remove temp file %s: %s", tmp, cleanup_err)
        raise


def load_config() -> dict[str, Any]:
    """config.json을 읽어들인다. 파일이 존재하지 않는 경우 빈 dict를 반환한다."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        _logger.debug("config.json not found, returning empty config")
    except Exception as e:
        _logger.warning("Failed to load config: %s", e)
    return {}


def save_config(data: dict[str, Any]) -> None:
    """config.json에 원자적으로 기록한다."""
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(CONFIG_FILE, data, indent=2)
    except Exception as e:
        _logger.warning("Failed to save config: %s", e)


async def daemon_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Unix 소켓 경유로 데몬에 JSON 요청을 보내고, 단일 응답을 반환한다.

    Args:
        payload: 데몬에 전송할 JSON 페이로드.

    Returns:
        데몬으로부터의 JSON 응답. 빈 응답의 경우 빈 dict.

    Raises:
        FileNotFoundError: 소켓 파일이 존재하지 않는 경우.
        ConnectionRefusedError: 접속이 거부된 경우.
    """
    reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    try:
        writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()
        line = await reader.readline()
        if not line:
            return {}
        return json.loads(line.decode("utf-8").strip())  # type: ignore[no-any-return]
    finally:
        writer.close()
        await writer.wait_closed()
