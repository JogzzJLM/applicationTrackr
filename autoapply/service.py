from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from autoapply.browser_agent import run_application
from autoapply.learning import learning_stats
from autoapply.profile import AUTOAPPLY_DIR, ensure_profile, profile_completeness
from core.relevance import evaluate_job
from core.storage import DISCOVERED_JOBS_FILE, atomic_write_json, load_json_safe, load_settings

RUNS_FILE = AUTOAPPLY_DIR / "runs.json"
_RUN_LOCK = threading.Lock()
_ACTIVE: Dict[str, Dict[str, Any]] = {}


def _enabled() -> bool:
    return os.getenv("AUTOAPPLY_ENABLED", "false").lower() in {"1", "true", "yes"}


def _load_runs() -> Dict[str, Any]:
    data = load_json_safe(str(RUNS_FILE), {})
    return data if isinstance(data, dict) else {}


def _save_run(run_id: str, payload: Dict[str, Any]) -> None:
    with _RUN_LOCK:
        data = _load_runs()
        data[run_id] = payload
        ordered = sorted(data.items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)[:100]
        atomic_write_json(str(RUNS_FILE), dict(ordered))
        _ACTIVE[run_id] = payload


def _find_job(job_id: str) -> Optional[Dict[str, Any]]:
    jobs = load_json_safe(DISCOVERED_JOBS_FILE, [])
    for job in jobs if isinstance(jobs, list) else []:
        if str(job.get("id")) == str(job_id):
            return job
    return None


def status() -> Dict[str, Any]:
    profile = ensure_profile()
    completeness, missing = profile_completeness(profile)
    recent = sorted(_load_runs().items(), key=lambda kv: kv[1].get("created_at", 0), reverse=True)[:8]
    return {
        "enabled": _enabled(),
        "auto_submit_enabled": os.getenv("AUTOAPPLY_AUTO_SUBMIT", "false").lower() in {"1", "true", "yes"},
        "profile_path": str(Path(AUTOAPPLY_DIR) / "applicant_profile.json"),
        "profile_completeness": completeness,
        "profile_missing": missing,
        "learning": learning_stats(),
        "recent_runs": [{"id": rid, **payload} for rid, payload in recent],
    }


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    return _ACTIVE.get(run_id) or _load_runs().get(run_id)


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
        if job:
            metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
            decision = evaluate_job(job.get("title", ""), job.get("company", ""), job.get("location", ""), metadata=metadata, settings=load_settings())
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
        _save_run(run_id, {**initial, **result.to_dict(), "created_at": created, "finished_at": time.time(), "job_id": job_id})

    threading.Thread(target=worker, daemon=True, name=f"autoapply-{run_id}").start()
    return run_id
