"""ClaudeClaw 데몬 - asyncio Unix 소켓 서버.

세션 관리를 수행하고, claude-agent-sdk로의 요청을 프록시한다.
프로세스 관리 및 시작 엔트리포인트는 src/process.py를 참조.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

try:
    from .config import BASE_DIR, PID_FILE, SESSIONS_DIR, SOCKET_PATH
    from .cron import CronScheduler
    from .heartbeat import HeartbeatScheduler
    from .session_store import SessionStore
    from .stream import handle_assistant_message, handle_result_message, handle_stream_event, send_json
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent)
    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import BASE_DIR, PID_FILE, SESSIONS_DIR, SOCKET_PATH
    from src.cron import CronScheduler
    from src.heartbeat import HeartbeatScheduler
    from src.session_store import SessionStore
    from src.stream import handle_assistant_message, handle_result_message, handle_stream_event, send_json


class OpenClaudeDaemon:
    """Unix 소켓 서버로 동작하는 상주 데몬."""

    def __init__(self) -> None:
        """세션 스토어, 서버 상태, Cron 스케줄러를 초기화한다."""
        self._store = SessionStore()
        self._server: asyncio.AbstractServer | None = None
        self._shutdown_event = asyncio.Event()
        self._cron: CronScheduler = CronScheduler(self._execute_for_cron)
        self._heartbeat: HeartbeatScheduler = HeartbeatScheduler(self._execute_for_heartbeat)

    async def start(self) -> None:
        """Unix 소켓 서버를 시작하고, 셧다운까지 대기한다."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        SOCKET_PATH.unlink(missing_ok=True)

        self._server = await asyncio.start_unix_server(self.handle_client, path=str(SOCKET_PATH))
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown_event.set)

        await self._cron.start()
        await self._heartbeat.start()
        _logger.info("ClaudeClaw daemon started (PID: %d)", os.getpid())

        async with self._server:
            await self._shutdown_event.wait()

        await self._cron.stop()
        await self._heartbeat.stop()
        SOCKET_PATH.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)
        _logger.info("ClaudeClaw daemon stopped.")

    # ------------------------------------------------------------------
    # 클라이언트 핸들러
    # ------------------------------------------------------------------

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:  # noqa: C901
        """클라이언트 접속을 받아들이고, 요청 종류에 따라 핸들러로 분기한다."""
        try:
            line = await reader.readline()
            if not line:
                return
            request = json.loads(line.decode("utf-8").strip())
            req_type = request.get("type")

            if req_type == "query":
                await self.handle_query(request, writer)
            elif req_type == "sessions":
                await self.handle_sessions(writer)
            elif req_type == "cleanup_sessions":
                await self.handle_cleanup_sessions(writer)
            elif req_type == "delete_session":
                await self.handle_delete_session(request, writer)
            elif req_type == "stop":
                await self.handle_stop(writer)
            elif req_type == "cron_add":
                await self.handle_cron_add(request, writer)
            elif req_type == "cron_list":
                await self.handle_cron_list(writer)
            elif req_type == "cron_delete":
                await self.handle_cron_delete(request, writer)
            elif req_type == "cron_run":
                await self.handle_cron_run(request, writer)
            elif req_type == "cron_update":
                await self.handle_cron_update(request, writer)
            else:
                await send_json(writer, {"type": "error", "message": f"Unknown type: {req_type}"})
        except json.JSONDecodeError as e:
            _logger.error("Invalid JSON from client: %s", e)
            try:
                await send_json(writer, {"type": "error", "message": f"Invalid JSON: {e}"})
            except Exception as send_err:
                _logger.debug("Failed to send JSON decode error to client: %s", send_err)
        except Exception as e:
            _logger.error("Unhandled error in handle_client: %s", e)
            try:
                await send_json(writer, {"type": "error", "message": str(e)})
            except Exception as send_err:
                _logger.debug("Failed to send error response to client: %s", send_err)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as close_err:
                _logger.debug("Failed to close writer: %s", close_err)

    # ------------------------------------------------------------------
    # 요청 핸들러
    # ------------------------------------------------------------------

    async def handle_query(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        """claude-agent-sdk를 호출하여 응답 청크를 CLI에 스트리밍한다."""
        try:
            from claude_agent_sdk import (  # noqa: PLC0415
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                query,
            )
            from claude_agent_sdk.types import StreamEvent  # noqa: PLC0415
        except ImportError as e:
            await send_json(writer, {"type": "error", "message": f"claude_agent_sdk not installed: {e}"})
            return

        session_alias = request.get("session_id", "main")
        user_message = request.get("message", "")

        if not user_message.strip():
            await send_json(writer, {"type": "error", "message": "Empty message"})
            return

        _logger.info("query: session=%s, message_len=%d", session_alias, len(user_message))
        sdk_session_id = self._store.get(session_alias)

        options = ClaudeAgentOptions(
            setting_sources=["project"],
            permission_mode="acceptEdits",
            cwd=str(BASE_DIR),
            include_partial_messages=True,
            resume=sdk_session_id,
        )

        current_model: str | None = None
        full_text: str = ""
        has_stream_events: bool = False

        try:
            async for message in query(prompt=user_message, options=options):
                if hasattr(message, "subtype") and message.subtype == "init":
                    self._handle_init_event(message, session_alias)
                elif isinstance(message, StreamEvent):
                    full_text, has_stream_events = await handle_stream_event(
                        message, writer, full_text, has_stream_events
                    )
                elif isinstance(message, AssistantMessage):
                    current_model, full_text = await handle_assistant_message(
                        message, writer, has_stream_events, full_text
                    )
                elif isinstance(message, ResultMessage):
                    await handle_result_message(message, writer, current_model)
        except Exception as e:
            _logger.error("query error: session=%s, error=%s", session_alias, e)
            await send_json(writer, {"type": "error", "message": str(e)})

    def _handle_init_event(self, message: Any, session_alias: str) -> None:
        """세션 초기화 메시지에서 sdk_session_id를 가져와 저장한다."""
        new_id = (message.data or {}).get("session_id")
        if new_id:
            self._store[session_alias] = new_id
            self._store.save()

    async def handle_sessions(self, writer: asyncio.StreamWriter) -> None:
        """메모리상의 세션 목록을 JSON으로 반환한다."""
        sessions = []
        for alias, sid in self._store.items():
            stats = self._store.read_stats(sid) if sid else {"last_active": None, "total_tokens": 0}
            sessions.append(
                {
                    "session_id": alias,
                    "sdk_session_id": sid,
                    "last_active": stats["last_active"],
                    "total_tokens": stats["total_tokens"],
                }
            )
        await send_json(writer, {"type": "sessions_list", "sessions": sessions})

    async def handle_stop(self, writer: asyncio.StreamWriter) -> None:
        """정지 응답을 반환한 후, 데몬을 셧다운한다."""
        await send_json(writer, {"type": "stopped"})
        asyncio.get_running_loop().call_later(0.2, self._shutdown_event.set)

    async def handle_cleanup_sessions(self, writer: asyncio.StreamWriter) -> None:
        """전체 세션의 메모리·sessions.json·JSONL 파일을 삭제한다."""
        _logger.info("cleanup_sessions: start, count=%d", len(self._store))
        deleted_files: list[str] = []
        failed_files: list[str] = []

        for sdk_session_id in list(self._store.values()):
            if sdk_session_id:
                deleted, error = self._store.delete_jsonl(sdk_session_id)
                if deleted:
                    deleted_files.append(deleted)
                if error:
                    failed_files.append(error)

        self._store.clear()
        self._store.save()

        _logger.info("cleanup_sessions: done, deleted=%d, failed=%d", len(deleted_files), len(failed_files))
        await send_json(
            writer,
            {
                "type": "cleanup_done",
                "deleted_count": len(deleted_files),
                "failed": failed_files,
            },
        )

    async def handle_delete_session(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        """지정한 alias의 세션 메모리·sessions.json·JSONL 파일을 삭제한다."""
        session_alias = request.get("session_id", "")

        if not session_alias:
            await send_json(writer, {"type": "error", "message": "session_id is required"})
            return

        _logger.info("delete_session: session=%s", session_alias)
        sdk_session_id = self._store.get(session_alias)
        if sdk_session_id is None:
            _logger.warning("delete_session: session not found: %s", session_alias)
            await send_json(writer, {"type": "error", "message": f"Session not found: {session_alias}"})
            return

        deleted_file, failed = self._store.delete_jsonl(sdk_session_id)
        del self._store[session_alias]
        self._store.save()

        _logger.info("delete_session: done, session=%s, deleted_file=%s", session_alias, deleted_file)
        await send_json(
            writer,
            {
                "type": "delete_done",
                "session_id": session_alias,
                "deleted_file": deleted_file,
                "failed": failed,
            },
        )

    # ------------------------------------------------------------------
    # Cron 핸들러
    # ------------------------------------------------------------------

    async def handle_cron_add(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        """Cron 작업을 추가한다."""
        name = request.get("name")
        schedule = request.get("schedule", "")
        session_id = request.get("session_id", "main")
        message = request.get("message", "")

        if not schedule:
            await send_json(writer, {"type": "error", "message": "schedule is required"})
            return
        if not message.strip():
            await send_json(writer, {"type": "error", "message": "message is required"})
            return

        try:
            job = self._cron.add_job(name=name, schedule=schedule, session_id=session_id, message=message)
        except ValueError as e:
            await send_json(writer, {"type": "error", "message": f"Invalid cron expression: {e}"})
            return

        await send_json(writer, {"type": "cron_added", **asdict(job)})

    async def handle_cron_list(self, writer: asyncio.StreamWriter) -> None:
        """Cron 작업 목록을 반환한다."""
        jobs = [asdict(job) for job in self._cron.list_jobs()]
        await send_json(writer, {"type": "cron_list", "jobs": jobs})

    async def handle_cron_delete(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        """Cron 작업을 삭제한다."""
        job_id = request.get("job_id", "")
        if not job_id:
            await send_json(writer, {"type": "error", "message": "job_id is required"})
            return

        try:
            self._cron.delete_job(job_id)
        except ValueError as e:
            await send_json(writer, {"type": "error", "message": str(e)})
            return

        await send_json(writer, {"type": "cron_deleted", "job_id": job_id})

    async def handle_cron_run(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        """Cron 작업을 수동으로 즉시 실행한다. 실행은 비동기로 시작하며, 즉시 응답을 반환한다."""
        job_id = request.get("job_id", "")
        if not job_id:
            await send_json(writer, {"type": "error", "message": "job_id is required"})
            return

        try:
            await self._cron.run_job_now(job_id)
        except ValueError as e:
            await send_json(writer, {"type": "error", "message": str(e)})
            return

        await send_json(writer, {"type": "cron_run_started", "job_id": job_id})

    async def handle_cron_update(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        """Cron 작업의 필드를 부분 업데이트한다."""
        job_id = request.get("job_id", "")
        patch = request.get("patch", {})

        if not job_id:
            await send_json(writer, {"type": "error", "message": "job_id is required"})
            return
        if not patch:
            await send_json(writer, {"type": "error", "message": "patch is empty"})
            return

        try:
            job = self._cron.update_job(job_id, patch)
        except ValueError as e:
            await send_json(writer, {"type": "error", "message": str(e)})
            return

        await send_json(writer, {"type": "cron_updated", **asdict(job)})

    async def _run_sdk_query(self, session_id: str, message: str) -> str:
        """claude-agent-sdk를 호출하여 로그만으로 실행하고 응답 텍스트를 반환하는 공통 헬퍼.

        Cron 작업과 Heartbeat 양쪽에서 사용된다. writer=None으로 스트리밍 없음.

        Args:
            session_id: 실행할 세션의 별칭.
            message: 에이전트에 보낼 프롬프트.

        Returns:
            에이전트의 응답 텍스트 전문.
        """
        try:
            from claude_agent_sdk import (  # noqa: PLC0415
                AssistantMessage,
                ClaudeAgentOptions,
                ResultMessage,
                query,
            )
            from claude_agent_sdk.types import StreamEvent  # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(f"claude_agent_sdk not installed: {e}") from e

        sdk_session_id = self._store.get(session_id)

        options = ClaudeAgentOptions(
            setting_sources=["project"],
            permission_mode="acceptEdits",
            cwd=str(BASE_DIR),
            include_partial_messages=True,
            resume=sdk_session_id,
        )

        full_text: str = ""
        has_stream_events: bool = False
        current_model: str | None = None

        async for msg in query(prompt=message, options=options):
            if hasattr(msg, "subtype") and msg.subtype == "init":
                self._handle_init_event(msg, session_id)
            elif isinstance(msg, StreamEvent):
                full_text, has_stream_events = await handle_stream_event(msg, None, full_text, has_stream_events)
            elif isinstance(msg, AssistantMessage):
                current_model, full_text = await handle_assistant_message(msg, None, has_stream_events, full_text)
            elif isinstance(msg, ResultMessage):
                await handle_result_message(msg, None, current_model)

        return full_text

    async def _execute_for_cron(self, job_id: str, session_id: str, message: str) -> None:
        """CronScheduler에서 호출되는 작업 실행 콜백."""
        _logger.info("cron_execute_query: job=%s, session=%s, message_len=%d", job_id, session_id, len(message))
        full_text = await self._run_sdk_query(session_id, message)
        _logger.info("cron_execute_query result: job=%s, text_len=%d", job_id, len(full_text))

    async def _execute_for_heartbeat(self, session_id: str, prompt: str) -> str | None:
        """HeartbeatScheduler에서 호출되는 턴 실행 콜백."""
        _logger.info("heartbeat_execute_query: session=%s, message_len=%d", session_id, len(prompt))
        full_text = await self._run_sdk_query(session_id, prompt)
        _logger.info("heartbeat_execute_query result: text_len=%d", len(full_text))
        return full_text
