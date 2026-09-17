"""Human-friendly runtime log compaction for ApplicationTrackr.

The service has frequent polling and scraper loops. This module keeps meaningful
state changes, actions and errors visible while compacting repetitive successful
no-op chatter. Set LOG_VERBOSE=true to restore the original fully verbose logs.
"""
from __future__ import annotations

import builtins
import os
import re
import threading

_ORIGINAL_PRINT = builtins.print
_LOCK = threading.Lock()
_INSTALLED = False

_STATE = {
    "gmail_idle_checks": 0,
    "scraper_total": None,
    "scraper_same_cycles": 0,
    "trackr_active": None,
}
_ERROR_COUNTS: dict[str, int] = {}

_NOISE_SUBSTRINGS = (
    "EMAIL INBOX AUTOMATION & STATUS LISTENER",
    "Checking Gmail Inbox for application updates",
    "Email listener cycle complete (0 new messages evaluated",
    "UK SCHEME PARALLEL MULTI-THREADED SCRAPER ENGINE",
    "JOB LINK HEALTH CHECK & DEAD LISTING PURGE",
    "[Greenhouse API] Scanning ",
    "[Lever API] Scanning ",
    "[Ashby API] Scanning ",
    "[SmartRecruiters API] Scanning ",
    "[The Trackr API] Loading UK Tech schemes",
    "[Gradcracker] Scanning UK STEM",
    "[Gradcracker] Recent successful scan",
    "Trackr: cooldown active; using ",
    "Relevance cleanup: all indexed listings still qualify",
    "Sent Watchdog Heartbeat Ping",
    "Saved updated filter settings to settings.json",
    "No active open schemes to recheck",
)

_GMAIL_COMPLETE_RE = re.compile(r"Gmail check complete \((\d+) new messages evaluated\)", re.I)
_FETCHED_ELIGIBLE_RE = re.compile(r"↳\s+.+?:\s+\d+ fetched \(\d+ (?:eligible|relevant)\)", re.I)
_PURGE_NOCHANGE_RE = re.compile(r"Purge Complete: Checked \d+ schemes, removed 0 dead/closed listings", re.I)
_SCRAPER_TOTAL_RE = re.compile(r"Scraper run complete .*?:\s*(\d+) eligible active schemes indexed", re.I)
_TRACKR_SUMMARY_RE = re.compile(r"Trackr Summary: .*?\((\d+) active recent schemes", re.I)


def _is_error(text: str) -> bool:
    lowered = text.lower()
    return (
        "⚠️" in text
        or "❌" in text
        or "🛑" in text
        or "traceback" in lowered
        or "exception" in lowered
        or " error" in lowered
        or "failed" in lowered
        or "failure" in lowered
    )


def _compact_error(text: str) -> str | None:
    signature = re.sub(r"\s+", " ", text).strip()
    with _LOCK:
        count = _ERROR_COUNTS.get(signature, 0) + 1
        _ERROR_COUNTS[signature] = count
    if count == 1:
        return text
    if count % 5 == 0:
        return f"{text}  [same error ×{count}]"
    return None


def _filter_text(text: str) -> str | None:
    if not text.strip():
        return None

    gmail_match = _GMAIL_COMPLETE_RE.search(text)
    if gmail_match:
        new_count = int(gmail_match.group(1))
        if new_count > 0:
            with _LOCK:
                _STATE["gmail_idle_checks"] = 0
            return text
        with _LOCK:
            _STATE["gmail_idle_checks"] += 1
            count = _STATE["gmail_idle_checks"]
        if count == 1 or count % 12 == 0:
            return f"📧 Gmail listener healthy — {count} idle check{'s' if count != 1 else ''}, 0 new messages"
        return None

    if _is_error(text):
        return _compact_error(text)

    if any(marker in text for marker in _NOISE_SUBSTRINGS):
        return None
    if _FETCHED_ELIGIBLE_RE.search(text):
        return None
    if _PURGE_NOCHANGE_RE.search(text):
        return None

    trackr_match = _TRACKR_SUMMARY_RE.search(text)
    if trackr_match:
        value = int(trackr_match.group(1))
        with _LOCK:
            previous = _STATE["trackr_active"]
            _STATE["trackr_active"] = value
        return text if previous is None or previous != value else None

    scraper_match = _SCRAPER_TOTAL_RE.search(text)
    if scraper_match:
        value = int(scraper_match.group(1))
        with _LOCK:
            previous = _STATE["scraper_total"]
            if previous is None or previous != value:
                _STATE["scraper_total"] = value
                _STATE["scraper_same_cycles"] = 0
                return text
            _STATE["scraper_same_cycles"] += 1
            same_cycles = _STATE["scraper_same_cycles"]
        if same_cycles % 6 == 0:
            return f"📊 Scraper stable ×{same_cycles} cycles — {value} eligible schemes; no index-size change"
        return None

    return text


def _quiet_print(*args, **kwargs):
    sep = kwargs.get("sep", " ")
    text = sep.join(str(arg) for arg in args)
    filtered = _filter_text(text)
    if filtered is None:
        return
    if filtered != text:
        clean_kwargs = dict(kwargs)
        clean_kwargs.pop("sep", None)
        _ORIGINAL_PRINT(filtered, **clean_kwargs)
    else:
        _ORIGINAL_PRINT(*args, **kwargs)


def install_quiet_logging() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    if os.getenv("LOG_VERBOSE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return
    builtins.print = _quiet_print
