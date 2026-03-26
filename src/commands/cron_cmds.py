"""Cron 관리 명령어 - cron add / list / delete / run / runs."""

import json
import logging
import sys
from datetime import datetime

_CRAB = "🦀"
_logger = logging.getLogger(__name__)

try:
    from ..config import CRON_JOBS_FILE, CRON_RUNS_DIR
    from ..process import get_daemon_status
    from ..utils import daemon_request
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path

    _pkg_root = str(_Path(__file__).parent.parent.parent)
    if _pkg_root not in _sys.path:
        _sys.path.insert(0, _pkg_root)
    from src.config import CRON_JOBS_FILE, CRON_RUNS_DIR
    from src.process import get_daemon_status
    from src.utils import daemon_request


def _is_daemon_up() -> bool:
    status, _ = get_daemon_status()
    return status == "running"


async def cmd_cron_add(schedule: str, name: str | None, session: str, message: str) -> None:
    """Cron 작업을 추가한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    try:
        response = await daemon_request(
            {"type": "cron_add", "name": name, "schedule": schedule, "session_id": session, "message": message}
        )
        if response.get("type") == "cron_added":
            print(f"Cron job added: {response['id']} ({response['name']})")
            print(f"  schedule: {response['schedule']}")
            print(f"  session:  {response['session_id']}")
            print(f"  message:  {response['message']}")
        elif response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


async def cmd_cron_list() -> None:
    """Cron 작업 목록을 표시한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    try:
        response = await daemon_request({"type": "cron_list"})
        if response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    jobs = response.get("jobs", [])
    print(f"{_CRAB} ClaudeClaw Cron Jobs\n")
    if not jobs:
        print("No cron jobs registered.")
        return

    print(f"Jobs: {len(jobs)}\n")
    col_id = max(max(len(j["id"]) for j in jobs), 8)
    col_name = max(max(len(j["name"]) for j in jobs), 12)
    col_sched = max(max(len(j["schedule"]) for j in jobs), 10)
    col_session = max(max(len(j["session_id"]) for j in jobs), 7)
    col_status = max(max(len(j.get("last_run_status") or "-") for j in jobs), 6)
    header = (
        f"{'id':<{col_id}}  {'name':<{col_name}}  {'schedule':<{col_sched}}  "
        f"{'session':<{col_session}}  {'enabled':<7}  {'status':<{col_status}}  message"
    )
    print(header)
    for j in jobs:
        enabled_str = "True" if j.get("enabled", True) else "False"
        msg_preview = j["message"][:40] + ("..." if len(j["message"]) > 40 else "")
        row = (
            f"{j['id']:<{col_id}}  {j['name']:<{col_name}}  {j['schedule']:<{col_sched}}  "
            f"{j['session_id']:<{col_session}}  {enabled_str:<7}  {(j.get('last_run_status') or '-'):<{col_status}}  {msg_preview}"  # noqa: E501
        )
        print(row)


async def cmd_cron_delete(job_id: str) -> None:
    """Cron 작업을 삭제한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    try:
        response = await daemon_request({"type": "cron_delete", "job_id": job_id})
        if response.get("type") == "cron_deleted":
            print(f"Deleted cron job: {job_id}")
        elif response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


async def cmd_cron_run(job_id: str) -> None:
    """Cron 작업을 수동으로 즉시 실행한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    try:
        response = await daemon_request({"type": "cron_run", "job_id": job_id})
        if response.get("type") == "cron_run_started":
            print(f"Cron job started: {job_id}")
        elif response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _read_run_log(job_id: str) -> list[dict]:
    """CRON_RUNS_DIR/<job_id>.jsonl의 전체 레코드를 반환한다 (오래된 순)."""
    log_path = CRON_RUNS_DIR / f"{job_id}.jsonl"
    if not log_path.exists():
        return []
    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                _logger.warning("_read_run_log: skipping malformed line in %s: %s", job_id, e)
                continue
    return records


async def cmd_cron_runs(job_id: str | None, limit: int) -> None:
    """Cron 작업의 실행 이력을 표시한다. daemon 경유 불필요 (파일 직접 읽기)."""
    if job_id is not None:
        # --- 특정 작업의 상세 이력 ---
        try:
            data = json.loads(CRON_JOBS_FILE.read_text(encoding="utf-8")) if CRON_JOBS_FILE.exists() else []
        except Exception:
            data = []
        known_ids = {j["id"] for j in data if isinstance(j, dict)}
        if known_ids and job_id not in known_ids:
            print(f"Job not found: {job_id}", file=sys.stderr)
            sys.exit(1)

        records = _read_run_log(job_id)
        total = len(records)
        display = list(reversed(records))[:limit]

        print(f"Cron Job Runs: {job_id}\n")
        if not display:
            print(f"No run history for job: {job_id}")
            return

        print(f"Showing last {len(display)} runs (total: {total})\n")
        print(f"  {'#':<3} {'started_at':<25} {'finished_at':<25} {'duration':<10} status")
        for i, r in enumerate(display, 1):
            try:
                st = datetime.fromisoformat(r["started_at"])
                ft = datetime.fromisoformat(r["finished_at"])
                duration = f"{(ft - st).total_seconds():.1f}s"
            except Exception:
                duration = "-"
            status = r.get("status", "-")
            try:
                started_disp = st.strftime("%Y-%m-%dT%H:%M:%S%z")
                finished_disp = ft.strftime("%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                started_disp = r.get("started_at", "")
                finished_disp = r.get("finished_at", "")
            print(f"  {i:<3} {started_disp:<25} {finished_disp:<25} {duration:<10} {status}")
            if status == "error" and r.get("error"):
                print(f"    Error: {r['error']}")
    else:
        # --- 전체 작업 요약 ---
        try:
            data = json.loads(CRON_JOBS_FILE.read_text(encoding="utf-8")) if CRON_JOBS_FILE.exists() else []
        except Exception:
            data = []
        if not data:
            print("No cron jobs registered.")
            return

        print("Cron Job Run Summary\n")
        col_id = max(max((len(j.get("id", "")) for j in data), default=0), 8)
        col_name = max(max((len(j.get("name", "")) for j in data), default=0), 12)
        print(f"{'job-id':<{col_id}}  {'name':<{col_name}}  {'last run':<32} {'status':<8} runs")
        for j in data:
            jid = j.get("id", "")
            records = _read_run_log(jid)
            run_count = len(records)
            last = records[-1] if records else None
            last_run = last.get("finished_at", "-") if last else "(never)"
            last_status = last.get("status", "-") if last else "-"
            print(f"{jid:<{col_id}}  {j.get('name', ''):<{col_name}}  {last_run:<32} {last_status:<8} {run_count}")


async def cmd_cron_edit(
    job_id: str,
    name: str | None,
    schedule: str | None,
    session: str | None,
    message: str | None,
    enable: bool | None,
) -> None:
    """Cron 작업의 필드를 부분 업데이트한다."""
    if not _is_daemon_up():
        print("ClaudeClaw daemon is not running.")
        return

    patch: dict = {}
    if name is not None:
        patch["name"] = name
    if schedule is not None:
        patch["schedule"] = schedule
    if session is not None:
        patch["session_id"] = session
    if message is not None:
        patch["message"] = message
    if enable is not None:
        patch["enabled"] = enable

    if not patch:
        print("No fields to update specified.", file=sys.stderr)
        sys.exit(1)

    try:
        response = await daemon_request({"type": "cron_update", "job_id": job_id, "patch": patch})
        if response.get("type") == "cron_updated":
            print(f"Cron job updated: {response['id']} ({response['name']})")
            print(f"  schedule: {response['schedule']}")
            print(f"  session:  {response['session_id']}")
            print(f"  enabled:  {response['enabled']}")
            print(f"  message:  {response['message']}")
        elif response.get("type") == "error":
            print(f"ERROR: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
