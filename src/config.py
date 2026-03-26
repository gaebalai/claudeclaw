"""ClaudeClaw configuration constants."""

import logging
import sys
from pathlib import Path

# 사용자 디렉터리 하위의 .claudeclaw 디렉터리를 베이스로 각종 파일 경로를 정의
BASE_DIR: Path = Path.home() / ".claudeclaw"

# 데몬 관련 파일 경로
SOCKET_PATH: Path = BASE_DIR / "claudeclaw.sock"
PID_FILE: Path = BASE_DIR / "claudeclaw.pid"
DAEMON_LOG: Path = BASE_DIR / "daemon.log"

# 세션 데이터 저장용 디렉터리/파일/기본 세션 ID
SESSIONS_DIR: Path = BASE_DIR / "sessions"
SESSIONS_JSON: Path = SESSIONS_DIR / "sessions.json"
DEFAULT_SESSION_ID: str = "main"

# 프로젝트별로 세션을 분리하기 위한 디렉터리. 프로젝트명은 현재 디렉터리 경로를 가공하여 생성.
_projects_dir_name = str(BASE_DIR).replace("/", "-").replace(".", "-")
CLAUDE_PROJECTS_DIR: Path = Path.home() / ".claude" / "projects" / _projects_dir_name

# Webhook 서버 관련 파일 경로 (단독 기동 시 사용)
WEBHOOK_PID_FILE: Path = BASE_DIR / "webhook.pid"
DEFAULT_PORT: int = 28789

# Cron 작업 관련 파일 경로
CRON_DIR: Path = BASE_DIR / "cron"
CRON_JOBS_FILE: Path = CRON_DIR / "jobs.json"
CRON_RUNS_DIR: Path = CRON_DIR / "runs"

# 설정 파일 경로
CONFIG_FILE: Path = BASE_DIR / "config.json"

# Heartbeat 관련 파일 경로
HEARTBEAT_MD: Path = BASE_DIR / "HEARTBEAT.md"

# Discord Bot 설정 (환경 변수 폴백용)
DISCORD_BOT_TOKEN_ENV: str = "DISCORD_BOT_TOKEN"  # noqa: S105

# Slack Bot 설정 (환경 변수 폴백용)
SLACK_BOT_TOKEN_ENV: str = "SLACK_BOT_TOKEN"  # noqa: S105
SLACK_APP_TOKEN_ENV: str = "SLACK_APP_TOKEN"  # noqa: S105


# ---------------------------------------------------------------------------
# 로깅 설정
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """데몬·API 서버 공통의 로깅을 설정한다. HH:MM:SS level 메시지 형식으로 stdout에 출력한다."""

    class _LowerLevelFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            original = record.levelname
            record.levelname = original.lower()
            result = super().format(record)
            record.levelname = original
            return result

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_LowerLevelFormatter(fmt="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logging.root.setLevel(logging.INFO)
    logging.root.handlers = [handler]
