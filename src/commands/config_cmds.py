"""설정 관리 명령어 - config set / get / show와 도트 표기법 헬퍼."""

import json
import sys
from typing import Any

try:
    from ..utils import load_config, save_config
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _pkg_root = str(_Path(__file__).parent.parent.parent)
    if _pkg_root not in _sys.path:
        _sys.path.insert(0, _pkg_root)
    from src.utils import load_config, save_config


def config_get_nested(data: dict[str, Any], key: str) -> Any:
    """도트 구분 키 (예: 'default.port')로 중첩 dict를 읽는다."""
    parts = key.split(".")
    cur: Any = data
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def config_set_nested(data: dict[str, Any], key: str, value: Any) -> Any:
    """도트 구분 키 (예: 'default.port')로 중첩 dict에 값을 설정한다. 설정한 값을 반환한다."""
    parts = key.split(".")
    cur: dict[str, Any] = data
    for part in parts[:-1]:
        if part not in cur or not isinstance(cur[part], dict):
            cur[part] = {}
        cur = cur[part]
    actual = int(value) if isinstance(value, str) and value.isdigit() else value
    cur[parts[-1]] = actual
    return actual


def cmd_config_set(key: str, value: str) -> None:
    """설정값을 설정하고 config.json에 저장한다."""
    data = load_config()
    actual = config_set_nested(data, key, value)
    save_config(data)
    print(f"{key} = {actual!r}")


def cmd_config_get(key: str) -> None:
    """설정값을 가져와서 표시한다."""
    data = load_config()
    value = config_get_nested(data, key)
    if value is None:
        print(f"{key} is not set", file=sys.stderr)
        sys.exit(1)
    print(value)


def cmd_config_show() -> None:
    """설정 파일의 전체 내용을 표시한다."""
    data = load_config()
    if not data:
        print("(no config)")
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))
