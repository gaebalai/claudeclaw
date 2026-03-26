"""ClaudeClaw API - FastAPI 엔드포인트 정의와 헬퍼 함수."""

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

_logger = logging.getLogger(__name__)

try:
    from ..config import CRON_RUNS_DIR, SOCKET_PATH
    from ..utils import daemon_request
    from .models import (
        CleanupResponse,
        CronAddRequest,
        CronJobResponse,
        CronListResponse,
        CronRunRecord,
        CronRunsResponse,
        CronUpdateRequest,
        DeleteSessionResponse,
        MessageRequest,
        MessageResponse,
        SessionInfo,
        SessionsResponse,
        StatusResponse,
    )
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.api.models import (
        CleanupResponse,
        CronAddRequest,
        CronJobResponse,
        CronListResponse,
        CronRunRecord,
        CronRunsResponse,
        CronUpdateRequest,
        DeleteSessionResponse,
        MessageRequest,
        MessageResponse,
        SessionInfo,
        SessionsResponse,
        StatusResponse,
    )
    from src.config import CRON_RUNS_DIR, SOCKET_PATH
    from src.utils import daemon_request

# ---------------------------------------------------------------------------
# FastAPI 앱
# ---------------------------------------------------------------------------

app = FastAPI(title="ClaudeClaw API", version="0.1.0")

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


async def _request_daemon(payload: dict[str, Any]) -> dict[str, Any]:
    """Unix 소켓 경유로 데몬에 JSON 요청을 보내고, 단일 응답을 반환한다.

    Raises:
        HTTPException: 데몬이 기동되어 있지 않은 경우 (503).
    """
    try:
        resp = await daemon_request(payload)
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise HTTPException(status_code=503, detail=f"Daemon is not running: {e}") from e
    if not resp:
        raise HTTPException(status_code=503, detail="Empty response from daemon")
    return resp


def _sse_event(data: dict[str, Any]) -> str:
    r"""Dict를 SSE 이벤트 문자열로 변환한다.

    Returns:
        `data: {...}\\n\\n` 형식의 SSE 이벤트 문자열.
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_query_payload(request: MessageRequest) -> dict[str, Any]:
    """MessageRequest에서 데몬에 전송할 쿼리 페이로드를 구성한다."""
    return {"type": "query", "session_id": request.session_id, "message": request.message}


async def _stream_message_generator(request: MessageRequest) -> AsyncGenerator[str, None]:
    r"""Unix 소켓 경유로 데몬과 통신하고, SSE 이벤트를 yield한다.

    Yields:
        SSE 포맷의 문자열 (`data: {...}\n\n`).
    """
    try:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    except (FileNotFoundError, ConnectionRefusedError) as e:
        yield _sse_event({"type": "error", "message": f"Daemon is not running: {e}"})
        return

    try:
        writer.write((json.dumps(_build_query_payload(request), ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()

        while True:
            line = await reader.readline()
            if not line:
                break
            resp = json.loads(line.decode("utf-8").strip())
            resp_type = resp.get("type")

            if resp_type == "chunk":
                yield _sse_event({"type": "chunk", "text": resp.get("text", "")})
            elif resp_type == "done":
                yield _sse_event(resp)
                break
            elif resp_type == "error":
                yield _sse_event({"type": "error", "message": resp.get("message", "Unknown error")})
                break
    finally:
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------------------
# 엔드포인트
# ---------------------------------------------------------------------------


@app.post("/message/stream")
async def post_message_stream(request: MessageRequest) -> StreamingResponse:
    r"""데몬에 메시지를 전달하고, SSE 스트리밍으로 응답을 반환한다.

    Returns:
        SSE 스트리밍 응답 (`text/event-stream`).
        각 이벤트는 `data: {...}\n\n` 형식이며, type은 `chunk` / `done` / `error`.
    """
    return StreamingResponse(
        _stream_message_generator(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/message")
async def post_message(request: MessageRequest) -> MessageResponse:
    """데몬에 메시지를 전달하고, 완전한 응답을 반환한다.

    Raises:
        HTTPException: 데몬이 기동되어 있지 않은 경우 (503) 또는 데몬이 에러를 반환한 경우 (500).
    """
    chunks: list[str] = []
    done_resp: dict[str, Any] = {}

    try:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    except (FileNotFoundError, ConnectionRefusedError) as e:
        raise HTTPException(status_code=503, detail=f"Daemon is not running: {e}") from e

    try:
        writer.write((json.dumps(_build_query_payload(request), ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()

        while True:
            line = await reader.readline()
            if not line:
                break
            resp = json.loads(line.decode("utf-8").strip())
            resp_type = resp.get("type")

            if resp_type == "chunk":
                chunks.append(resp.get("text", ""))
            elif resp_type == "done":
                done_resp = resp
                break
            elif resp_type == "error":
                raise HTTPException(status_code=500, detail=resp.get("message", "Unknown error"))
    finally:
        writer.close()
        await writer.wait_closed()

    return MessageResponse(
        session_id=request.session_id,
        response="".join(chunks),
        stop_reason=done_resp.get("stop_reason"),
        model=done_resp.get("model"),
        input_tokens=done_resp.get("input_tokens"),
        output_tokens=done_resp.get("output_tokens"),
        total_cost_usd=done_resp.get("total_cost_usd"),
        num_turns=done_resp.get("num_turns"),
    )


@app.get("/cron")
async def get_cron() -> CronListResponse:
    """Cron 작업 목록을 가져온다.

    Raises:
        HTTPException: 데몬이 기동되어 있지 않은 경우 (503) 또는 에러가 발생한 경우 (500).
    """
    resp = await _request_daemon({"type": "cron_list"})
    if resp.get("type") == "error":
        raise HTTPException(status_code=500, detail=resp.get("message", "Unknown error"))
    jobs = [CronJobResponse(**j) for j in resp.get("jobs", [])]
    return CronListResponse(jobs=jobs, total=len(jobs))


@app.post("/cron", status_code=201)
async def post_cron(request: CronAddRequest) -> CronJobResponse:
    """Cron 작업을 추가한다.

    Raises:
        HTTPException: 잘못된 cron 식 (422), 데몬 미기동 (503), 에러 (500).
    """
    resp = await _request_daemon(
        {
            "type": "cron_add",
            "name": request.name,
            "schedule": request.schedule,
            "session_id": request.session_id,
            "message": request.message,
        }
    )
    if resp.get("type") == "error":
        msg = resp.get("message", "Unknown error")
        status_code = 422 if "invalid cron" in msg.lower() else 500
        raise HTTPException(status_code=status_code, detail=msg)
    return CronJobResponse.model_validate(resp)


@app.patch("/cron/{job_id}")
async def update_cron(job_id: str, request: CronUpdateRequest) -> CronJobResponse:
    """Cron 작업을 부분 업데이트한다.

    Raises:
        HTTPException: 작업을 찾을 수 없는 경우 (404), 잘못된 cron 식 / patch가 빈 경우 (422),
                       데몬 미기동 (503), 에러 (500).
    """
    patch: dict[str, Any] = {
        k: v
        for k, v in {
            "name": request.name,
            "schedule": request.schedule,
            "session_id": request.session_id,
            "message": request.message,
            "enabled": request.enabled,
        }.items()
        if v is not None
    }
    if not patch:
        raise HTTPException(status_code=422, detail="patch is empty")

    resp = await _request_daemon({"type": "cron_update", "job_id": job_id, "patch": patch})
    if resp.get("type") == "error":
        msg = resp.get("message", "Unknown error")
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "invalid cron" in msg.lower() or "patch is empty" in msg.lower():
            raise HTTPException(status_code=422, detail=msg)
        raise HTTPException(status_code=500, detail=msg)
    return CronJobResponse.model_validate(resp)


@app.delete("/cron/{job_id}")
async def delete_cron(job_id: str) -> dict[str, str]:
    """Cron 작업을 삭제한다.

    Raises:
        HTTPException: 작업을 찾을 수 없는 경우 (404), 데몬 미기동 (503), 에러 (500).
    """
    resp = await _request_daemon({"type": "cron_delete", "job_id": job_id})
    if resp.get("type") == "error":
        msg = resp.get("message", "Unknown error")
        status_code = 404 if "not found" in msg.lower() else 500
        raise HTTPException(status_code=status_code, detail=msg)
    return {"job_id": resp.get("job_id", job_id)}


@app.post("/cron/{job_id}/run")
async def run_cron(job_id: str) -> dict[str, str]:
    """Cron 작업을 수동으로 즉시 실행한다.

    Raises:
        HTTPException: 작업을 찾을 수 없는 경우 (404), 데몬 미기동 (503), 에러 (500).
    """
    resp = await _request_daemon({"type": "cron_run", "job_id": job_id})
    if resp.get("type") == "error":
        msg = resp.get("message", "Unknown error")
        status_code = 404 if "not found" in msg.lower() else 500
        raise HTTPException(status_code=status_code, detail=msg)
    return {"job_id": resp.get("job_id", job_id), "status": "started"}


@app.get("/cron/{job_id}/runs")
async def get_cron_runs(job_id: str, limit: int = 20) -> CronRunsResponse:
    """지정한 Cron 작업의 실행 이력을 반환한다 (파일 직접 읽기).

    Raises:
        HTTPException: 파일 읽기 에러의 경우 (500).
    """
    log_path = CRON_RUNS_DIR / f"{job_id}.jsonl"
    records: list[dict] = []
    if log_path.exists():
        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    total = len(records)
    runs = [CronRunRecord(**r) for r in reversed(records)][:limit]
    return CronRunsResponse(job_id=job_id, runs=runs, total=total, limit=limit)


@app.get("/status")
async def get_status() -> StatusResponse:
    """데몬의 상태와 PID를 반환한다.

    Returns:
        데몬의 상태 (항상 running)와 PID.
    """
    return StatusResponse(status="running", pid=os.getpid())


@app.get("/sessions")
async def get_sessions() -> SessionsResponse:
    """데몬에서 세션 목록을 가져와서 반환한다.

    Raises:
        HTTPException: 데몬이 기동되어 있지 않은 경우 (503) 또는 에러가 발생한 경우 (500).
    """
    resp = await _request_daemon({"type": "sessions"})
    if resp.get("type") == "error":
        raise HTTPException(status_code=500, detail=resp.get("message", "Unknown error"))
    sessions = [SessionInfo(**s) for s in resp.get("sessions", [])]
    return SessionsResponse(sessions=sessions, total=len(sessions))


@app.delete("/sessions")
async def cleanup_sessions() -> CleanupResponse:
    """전체 세션을 삭제한다.

    Raises:
        HTTPException: 데몬이 기동되어 있지 않은 경우 (503) 또는 에러가 발생한 경우 (500).
    """
    resp = await _request_daemon({"type": "cleanup_sessions"})
    if resp.get("type") == "error":
        raise HTTPException(status_code=500, detail=resp.get("message", "Unknown error"))
    return CleanupResponse(
        deleted_count=resp.get("deleted_count", 0),
        failed=resp.get("failed", []),
    )


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> DeleteSessionResponse:
    """지정한 세션을 삭제한다.

    Raises:
        HTTPException: 세션을 찾을 수 없는 경우 (404), 데몬 미기동 (503), 에러 (500).
    """
    resp = await _request_daemon({"type": "delete_session", "session_id": session_id})
    if resp.get("type") == "error":
        msg = resp.get("message", "Unknown error")
        status_code = 404 if "not found" in msg.lower() else 500
        raise HTTPException(status_code=status_code, detail=msg)
    return DeleteSessionResponse(
        session_id=resp.get("session_id", session_id),
        deleted_file=resp.get("deleted_file"),
        failed=resp.get("failed"),
    )
