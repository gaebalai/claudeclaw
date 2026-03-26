"""ClaudeClaw Cron 스케줄러 - apscheduler를 이용한 정기 작업 관리.

CronJob 데이터 클래스와 스케줄러를 제공한다.
작업 정의는 jobs.json에 영속화되며, 데몬 재시작 후에도 자동 복원된다.
"""

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

_logger = logging.getLogger(__name__)

try:
    from .config import CRON_DIR, CRON_JOBS_FILE, CRON_RUNS_DIR
    from .utils import atomic_write_json
except ImportError:
    _pkg_root = str(Path(__file__).parent.parent)
    import sys

    if _pkg_root not in sys.path:
        sys.path.insert(0, _pkg_root)
    from src.config import CRON_DIR, CRON_JOBS_FILE, CRON_RUNS_DIR
    from src.utils import atomic_write_json


# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------


@dataclass
class CronJob:
    """Cron 작업의 정의를 나타내는 데이터 클래스."""

    id: str
    name: str
    schedule: str
    session_id: str
    message: str
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    last_run_at: str | None = None
    last_run_status: str | None = None  # "success" | "error" | None


# ---------------------------------------------------------------------------
# 스케줄러
# ---------------------------------------------------------------------------


class CronScheduler:
    """apscheduler를 사용하여 Cron 작업을 관리하는 클래스."""

    def __init__(self, execute_fn: Callable[[str, str, str], Awaitable[None]]) -> None:
        """스케줄러를 초기화한다.

        Args:
            execute_fn: 작업 실행 시 호출할 비동기 함수.
                        시그니처: execute_fn(job_id, session_id, message)
        """
        self._execute_fn = execute_fn
        self._jobs: dict[str, CronJob] = {}
        self._scheduler = AsyncIOScheduler()

    async def start(self) -> None:
        """영속화 파일에서 작업을 읽어들여 스케줄러를 시작한다."""
        CRON_DIR.mkdir(parents=True, exist_ok=True)
        CRON_RUNS_DIR.mkdir(parents=True, exist_ok=True)

        for job in self._load_jobs():
            self._jobs[job.id] = job
            if job.enabled:
                self._register_job(job)

        self._scheduler.start()
        _logger.info("CronScheduler started with %d job(s)", len(self._jobs))

    async def stop(self) -> None:
        """스케줄러를 정지한다."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        _logger.info("CronScheduler stopped")

    def add_job(self, name: str | None, schedule: str, session_id: str, message: str) -> CronJob:
        """작업을 추가하여 스케줄러에 등록하고 영속화한다.

        Args:
            name: 작업의 표시명. None인 경우 "job-<id>"를 사용한다.
            schedule: 5필드 cron 식 (예: "0 9 * * *").
            session_id: 전송 대상 세션 alias.
            message: 전송 메시지 본문.

        Returns:
            추가된 CronJob.

        Raises:
            ValueError: cron 식이 올바르지 않은 경우.
        """
        # 유효성 검증 겸 트리거 생성 (잘못된 cron 식은 ValueError를 발생시킴)
        trigger = CronTrigger.from_crontab(schedule)

        job_id = secrets.token_hex(4)
        job = CronJob(
            id=job_id,
            name=name if name is not None else f"job-{job_id}",
            schedule=schedule,
            session_id=session_id,
            message=message,
        )
        self._jobs[job_id] = job
        self._register_job(job, trigger)
        self._save_jobs()

        _logger.info("cron_add: id=%s, name=%s, schedule=%s, session=%s", job_id, job.name, schedule, session_id)
        return job

    def list_jobs(self) -> list[CronJob]:
        """작업 목록을 반환한다."""
        return list(self._jobs.values())

    def update_job(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        """작업의 필드를 부분 업데이트하고 영속화한다.

        Args:
            job_id: 업데이트할 작업의 ID.
            patch: 변경할 필드와 값의 딕셔너리.
                   허용 키: name, schedule, session_id, message, enabled.

        Returns:
            업데이트된 CronJob.

        Raises:
            ValueError: job_id가 존재하지 않거나, patch가 비어 있거나, cron 식이 잘못된 경우.
        """
        if job_id not in self._jobs:
            raise ValueError(f"Job not found: {job_id}")
        if not patch:
            raise ValueError("patch is empty")

        job = self._jobs[job_id]
        new_schedule = patch.get("schedule")
        new_enabled = patch.get("enabled")

        # schedule 변경 시 유효성 검증 (잘못된 cron 식은 ValueError를 발생시킴)
        trigger: CronTrigger | None = None
        if new_schedule is not None:
            trigger = CronTrigger.from_crontab(new_schedule)

        # 허용 필드만 반영 (id, created_at 등은 변경하지 않음)
        allowed = {"name", "schedule", "session_id", "message", "enabled"}
        for key, val in patch.items():
            if key in allowed:
                setattr(job, key, val)

        # apscheduler의 등록 상태를 업데이트
        if new_schedule is not None:
            if self._scheduler.get_job(job_id) is not None:
                self._scheduler.remove_job(job_id)
            if job.enabled:
                self._register_job(job, trigger)
        elif new_enabled is not None:
            if new_enabled and self._scheduler.get_job(job_id) is None:
                self._register_job(job)
            elif not new_enabled and self._scheduler.get_job(job_id) is not None:
                self._scheduler.remove_job(job_id)

        self._save_jobs()
        _logger.info("cron_update: id=%s, patch_keys=%s", job_id, list(patch.keys()))
        return job

    def delete_job(self, job_id: str) -> None:
        """작업을 스케줄러에서 삭제하고, 영속화 파일에서도 삭제한다.

        Args:
            job_id: 삭제할 작업의 ID.

        Raises:
            ValueError: job_id가 존재하지 않는 경우.
        """
        if job_id not in self._jobs:
            raise ValueError(f"Job not found: {job_id}")

        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

        del self._jobs[job_id]
        self._save_jobs()
        _logger.info("cron_delete: id=%s", job_id)

    async def run_job_now(self, job_id: str) -> None:
        """작업을 비동기로 즉시 실행한다.

        Args:
            job_id: 실행할 작업의 ID.

        Raises:
            ValueError: job_id가 존재하지 않는 경우.
        """
        if job_id not in self._jobs:
            raise ValueError(f"Job not found: {job_id}")

        asyncio.create_task(self._execute_job(job_id))
        _logger.info("cron_run: id=%s (manual trigger)", job_id)

    # ------------------------------------------------------------------
    # 내부 메서드
    # ------------------------------------------------------------------

    def _register_job(self, job: CronJob, trigger: CronTrigger | None = None) -> None:
        """작업을 apscheduler에 등록한다.

        Args:
            job: 등록할 작업.
            trigger: 사용할 CronTrigger. None인 경우 job.schedule에서 생성한다 (복원 시).
        """
        if trigger is None:
            trigger = CronTrigger.from_crontab(job.schedule)
        self._scheduler.add_job(
            self._execute_job, trigger, id=job.id, args=[job.id], replace_existing=True, misfire_grace_time=60
        )

    async def _execute_job(self, job_id: str) -> None:
        """스케줄러에서 호출되는 작업 실행 함수.

        execute_fn을 호출하고, last_run_at / last_run_status를 업데이트한다.
        실행 이력을 runs/<job_id>.jsonl에 추기한다.
        """
        job = self._jobs.get(job_id)
        if job is None:
            _logger.warning("cron_execute: job not found: %s", job_id)
            return

        started_at = datetime.now(UTC).isoformat()
        _logger.info("cron_execute: start, id=%s, session=%s", job_id, job.session_id)

        status: str
        error_msg: str | None = None
        try:
            await self._execute_fn(job_id, job.session_id, job.message)
            status = "success"
        except Exception as e:
            status = "error"
            error_msg = str(e)
            _logger.error("cron_execute: error, id=%s, error=%s", job_id, e)

        finished_at = datetime.now(UTC).isoformat()

        # last_run_at / last_run_status 업데이트
        if job_id in self._jobs:
            self._jobs[job_id].last_run_at = finished_at
            self._jobs[job_id].last_run_status = status
            self._save_jobs()

        # 실행 이력을 JSONL에 추기
        record: dict[str, Any] = {
            "job_id": job_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": status,
        }
        if error_msg is not None:
            record["error"] = error_msg
        self._append_run_log(job_id, record)

        _logger.info("cron_execute: done, id=%s, status=%s", job_id, status)

    def _load_jobs(self) -> list[CronJob]:
        """jobs.json에서 작업 목록을 읽어들인다."""
        try:
            data = json.loads(CRON_JOBS_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            jobs = []
            for item in data:
                try:
                    jobs.append(
                        CronJob(
                            id=item["id"],
                            name=item["name"],
                            schedule=item["schedule"],
                            session_id=item["session_id"],
                            message=item["message"],
                            enabled=item.get("enabled", True),
                            created_at=item.get("created_at", ""),
                            last_run_at=item.get("last_run_at"),
                            last_run_status=item.get("last_run_status"),
                        )
                    )
                except (KeyError, TypeError) as e:
                    _logger.warning("cron_load: skipping malformed job entry: %s", e)
            return jobs
        except FileNotFoundError:
            _logger.debug("cron_load: jobs.json not found, starting with empty jobs")
        except Exception as e:
            _logger.warning("cron_load: failed to load jobs: %s", e)
        return []

    def _save_jobs(self) -> None:
        """self._jobs를 jobs.json에 원자적으로 기록한다."""
        try:
            atomic_write_json(CRON_JOBS_FILE, [asdict(job) for job in self._jobs.values()], dir_path=CRON_DIR)
        except Exception as e:
            _logger.warning("cron_save: failed to save jobs: %s", e)

    def _append_run_log(self, job_id: str, record: dict[str, Any]) -> None:
        """runs/<job_id>.jsonl에 실행 레코드를 추기한다.

        Args:
            job_id: 작업 ID.
            record: 추기할 실행 레코드.
        """
        try:
            log_path = CRON_RUNS_DIR / f"{job_id}.jsonl"
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            _logger.warning("cron_run_log: failed to append run log for %s: %s", job_id, e)
