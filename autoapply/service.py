from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from autoapply.browser_agent import run_application
from autoapply.learning import learning_stats
from autoapply.profile import AUTOAPPLY_DIR, ensure_profile, profile_completeness
from core.relevance import evaluate_job
from core.storage import DISCOVERED_JOBS_FILE, atomic_write_json, load_json_safe, load_settings

RUNS_FILE = AUTOAPPLY_DIR / "runs.json"
_RUN_LOCK = threading.Lock()
_ACTIVE: Dict[str, Dict[str, Any]] = {}
_BATCHES: Dict[str, Dict[str, Any]] = {}


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}


def _enabled() -> bool:
    return _flag("AUTOAPPLY_ENABLED")


def _load_runs() -> Dict[str, Any]:
    data = load_json_safe(str(RUNS_FILE), {})
    return data if isinstance(data, dict) else {}


def _save_run(run_id: str, payload: Dict[str, Any]) -> None:
    with _RUN_LOCK:
        data = _load_runs()
        data[run_id] = payload
        ordered = sorted(data.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)[:300]
        atomic_write_json(str(RUNS_FILE), dict(ordered))
        _ACTIVE[run_id] = payload


def _load_jobs() -> List[Dict[str, Any]]:
    jobs = load_json_safe(DISCOVERED_JOBS_FILE, [])
    return jobs if isinstance(jobs, list) else []


def _find_job(job_id: str) -> Optional[Dict[str, Any]]:
    for job in _load_jobs():
        if str(job.get("id")) == str(job_id):
            return job
    return None


def _decision(job: Dict[str, Any]):
    metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    return evaluate_job(
        job.get("title", ""), job.get("company", ""), job.get("location", ""),
        metadata=metadata, settings=load_settings(),
    )


def _attempted_job_ids() -> set[str]:
    attempted = set()
    for payload in _load_runs().values():
        if payload.get("job_id") and payload.get("status") in {
            "submitted", "ready_for_review", "needs_review", "filled_no_submit_found", "running", "queued"
        }:
            attempted.add(str(payload["job_id"]))
    return attempted


def autopilot_candidates(limit: int = 20) -> List[Dict[str, Any]]:
    """Return strong-fit, eligible jobs that the application agent has not tried."""
    minimum = int(os.getenv("AUTOAPPLY_AUTOPILOT_MIN_SCORE", "82"))
    attempted = _attempted_job_ids()
    rows = []
    for job in _load_jobs():
        if str(job.get("id")) in attempted:
            continue
        decision = _decision(job)
        if not decision.eligible or decision.score < minimum:
            continue
        rows.append({
            "id": job.get("id", ""),
            "company": job.get("company", ""),
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "score": decision.score,
            "tier": decision.tier,
            "url": job.get("link", ""),
            "reasons": decision.reasons[:4],
        })
    rows.sort(key=lambda j: (-int(j.get("score", 0)), str(j.get("company", "")).lower()))
    return rows[: max(1, int(limit))]


def _today_attempt_count() -> int:
    today = datetime.now().date()
    count = 0
    for payload in _load_runs().values():
        created = payload.get("created_at")
        if not created:
            continue
        try:
            if datetime.fromtimestamp(float(created)).date() == today:
                count += 1
        except Exception:
            pass
    return count


def status() -> Dict[str, Any]:
    profile = ensure_profile()
    completeness, missing = profile_completeness(profile)
    recent = sorted(_load_runs().items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)[:8]
    daily_limit = max(1, int(os.getenv("AUTOAPPLY_DAILY_LIMIT", "3")))
    return {
        "enabled": _enabled(),
        "auto_submit_enabled": _flag("AUTOAPPLY_AUTO_SUBMIT"),
        "autopilot_enabled": _flag("AUTOAPPLY_AUTOPILOT_ENABLED"),
        "autopilot_min_score": int(os.getenv("AUTOAPPLY_AUTOPILOT_MIN_SCORE", "82")),
        "daily_limit": daily_limit,
        "attempts_today": _today_attempt_count(),
        "profile_path": str(Path(AUTOAPPLY_DIR) / "applicant_profile.json"),
        "profile_completeness": completeness,
        "profile_missing": missing,
        "learning": learning_stats(),
        "recent_runs": [{"id": rid, **payload} for rid, payload in recent],
        "candidate_count": len(autopilot_candidates(100)),
    }


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _ACTIVE.get(run_id) or _load_runs().get(run_id)


def get_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    return _BATCHES.get(batch_id)


def _execute_run(run_id: str, initial: Dict[str, Any], job: Optional[Dict[str, Any]], target_url: str, auto_submit: bool) -> None:
    if job:
        decision = _decision(job)
        if not decision.eligible:
            _save_run(run_id, {**initial, "status": "blocked", "message": "Job no longer passes relevance gates.", "rejection_reasons": decision.rejection_reasons})
            return
    profile = ensure_profile()
    completeness, missing = profile_completeness(profile)
    if completeness < 60:
        _save_run(run_id, {**initial, "status": "needs_profile", "message": "Applicant profile is too incomplete to run safely.", "missing": missing})
        return
    _save_run(run_id, {**initial, "status": "running", "url": target_url})
    result = run_application(target_url, profile, auto_submit=auto_submit, screenshot_dir=str(Path(AUTOAPPLY_DIR) / "screenshots"))
    _save_run(run_id, {**initial, **result.to_dict(), "created_at": initial["created_at"], "finished_at": time.time(), "job_id": initial.get("job_id", "")})


def start_application_run(job_id: str = "", url: str = "", auto_submit: bool = False) -> str:
    run_id = uuid.uuid4().hex[:12]
    created = time.time()
    initial = {"created_at": created, "status": "queued", "job_id": job_id, "url": url, "auto_submit_requested": bool(auto_submit)}
    _save_run(run_id, initial)

    def worker():
        if not _enabled():
            _save_run(run_id, {**initial, "status": "disabled", "message": "Set AUTOAPPLY_ENABLED=true after configuring your profile."})
            return
        job = _find_job(job_id) if job_id else None
        target_url = url or (job or {}).get("link", "")
        if not target_url.startswith("http"):
            _save_run(run_id, {**initial, "status": "error", "message": "No valid application URL supplied."})
            return
        _execute_run(run_id, initial, job, target_url, auto_submit)

    threading.Thread(target=worker, daemon=True, name=f"autoapply-{run_id}").start()
    return run_id


def start_autopilot_batch(limit: Optional[int] = None, auto_submit: bool = False) -> str:
    """Apply sequentially to the best unattempted jobs, within a hard daily cap.

    Autopilot is separately opt-in. Sensitive/legal/attestation fields still stop
    inside browser_agent, so enabling this never teaches or invents those answers.
    """
    batch_id = "batch-" + uuid.uuid4().hex[:10]
    daily_limit = max(1, int(os.getenv("AUTOAPPLY_DAILY_LIMIT", "3")))
    requested = max(1, int(limit or daily_limit))
    remaining = max(0, daily_limit - _today_attempt_count())
    effective = min(requested, remaining)
    initial = {"id": batch_id, "status": "queued", "created_at": time.time(), "requested": requested, "limit": effective, "run_ids": [], "completed": 0}
    _BATCHES[batch_id] = initial

    def worker():
        if not _enabled() or not _flag("AUTOAPPLY_AUTOPILOT_ENABLED"):
            _BATCHES[batch_id] = {**initial, "status": "disabled", "message": "Enable AUTOAPPLY_ENABLED and AUTOAPPLY_AUTOPILOT_ENABLED first."}
            return
        if effective <= 0:
            _BATCHES[batch_id] = {**initial, "status": "daily_limit_reached"}
            return
        candidates = autopilot_candidates(effective)
        if not candidates:
            _BATCHES[batch_id] = {**initial, "status": "no_candidates"}
            return
        _BATCHES[batch_id] = {**initial, "status": "running", "candidates": candidates}
        run_ids = []
        for candidate in candidates:
            job = _find_job(str(candidate["id"]))
            if not job:
                continue
            run_id = uuid.uuid4().hex[:12]
            run_initial = {
                "created_at": time.time(), "status": "queued", "job_id": str(candidate["id"]),
                "url": candidate["url"], "auto_submit_requested": bool(auto_submit), "batch_id": batch_id,
            }
            _save_run(run_id, run_initial)
            run_ids.append(run_id)
            _BATCHES[batch_id] = {**_BATCHES[batch_id], "run_ids": list(run_ids), "current_job_id": candidate["id"]}
            _execute_run(run_id, run_initial, job, candidate["url"], auto_submit)
            payload = get_run(run_id) or {}
            # Stop the queue when a page needs human input. This prevents a pile
            # of half-completed sessions and gives the learner a clean example.
            if payload.get("status") in {"needs_review", "blocked", "needs_profile"}:
                break
        _BATCHES[batch_id] = {**_BATCHES[batch_id], "status": "finished", "completed": len(run_ids), "finished_at": time.time(), "current_job_id": ""}

    threading.Thread(target=worker, daemon=True, name=f"autoapply-{batch_id}").start()
    return batch_id
