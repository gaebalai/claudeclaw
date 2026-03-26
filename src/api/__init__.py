"""ClaudeClaw API 패키지.

`from src.api import app` 으로 FastAPI 앱 인스턴스를 가져올 수 있다.

단독 실행:
    python3 -m src.api          (~/.claudeclaw/ 에서)
    python3 -m src.api --port 8080
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)

try:
    from .routes import app
    from ..config import DEFAULT_PORT, WEBHOOK_PID_FILE, setup_logging
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.api.routes import app
    from src.config import DEFAULT_PORT, WEBHOOK_PID_FILE, setup_logging

__all__ = ["app"]


async def _main(port: int) -> None:
    """API 서버를 시작하고 종료될 때까지 대기한다."""
    import uvicorn  # noqa: PLC0415

    setup_logging()
    WEBHOOK_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _logger.info("ClaudeClaw API server starting on port %d (PID: %d)", port, os.getpid())

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")  # noqa: S104
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        WEBHOOK_PID_FILE.unlink(missing_ok=True)
        _logger.info("ClaudeClaw API server stopped.")


if __name__ == "__main__":
    _parser = argparse.ArgumentParser(description="ClaudeClaw API Server")
    _parser.add_argument("--port", type=int, default=DEFAULT_PORT, metavar="PORT")
    _args = _parser.parse_args()
    asyncio.run(_main(_args.port))
