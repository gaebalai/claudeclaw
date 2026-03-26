"""ClaudeClaw API - Pydantic 요청/응답 모델 정의."""

import sys
from pathlib import Path

try:
    from ..config import DEFAULT_SESSION_ID
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import DEFAULT_SESSION_ID

from pydantic import BaseModel, ConfigDict, Field


class MessageRequest(BaseModel):
    """POST /message 의 요청 본문."""

    session_id: str = DEFAULT_SESSION_ID
    message: str = Field(min_length=1)


class MessageResponse(BaseModel):
    """POST /message 의 응답 본문."""

    session_id: str
    response: str
    stop_reason: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_cost_usd: float | None = None
    num_turns: int | None = None


class SessionInfo(BaseModel):
    """세션 정보."""

    session_id: str
    sdk_session_id: str | None = None
    last_active: str | None = None
    total_tokens: int = 0


class SessionsResponse(BaseModel):
    """GET /sessions 의 응답 본문."""

    sessions: list[SessionInfo]
    total: int


class CleanupResponse(BaseModel):
    """DELETE /sessions 의 응답 본문."""

    deleted_count: int
    failed: list[str] = []


class DeleteSessionResponse(BaseModel):
    """DELETE /sessions/{session_id} 의 응답 본문."""

    session_id: str
    deleted_file: str | None = None
    failed: str | None = None


class StatusResponse(BaseModel):
    """GET /status 의 응답 본문."""

    status: str
    pid: int


class CronAddRequest(BaseModel):
    """POST /cron 의 요청 본문."""

    name: str | None = None
    schedule: str
    session_id: str = DEFAULT_SESSION_ID
    message: str = Field(min_length=1)


class CronUpdateRequest(BaseModel):
    """PATCH /cron/{job_id} 의 요청 본문."""

    name: str | None = None
    schedule: str | None = None
    session_id: str | None = None
    message: str | None = None
    enabled: bool | None = None


class CronJobResponse(BaseModel):
    """Cron 작업 정보."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    schedule: str
    session_id: str
    message: str
    enabled: bool
    created_at: str
    last_run_at: str | None = None
    last_run_status: str | None = None


class CronListResponse(BaseModel):
    """GET /cron 의 응답 본문."""

    jobs: list[CronJobResponse]
    total: int


class CronRunRecord(BaseModel):
    """Cron 작업의 단일 실행 레코드."""

    job_id: str
    started_at: str
    finished_at: str
    status: str
    error: str | None = None


class CronRunsResponse(BaseModel):
    """GET /cron/{job_id}/runs 의 응답 본문."""

    job_id: str
    runs: list[CronRunRecord]
    total: int
    limit: int
