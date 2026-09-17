"""Noise filtering for ApplicationTrackr runtime logs.

The app runs several frequent polling/scraping loops.  Most successful no-op
messages are useful for diagnostics counters but not useful in the human log.
This module keeps errors and state-changing events visible while suppressing
routine heartbeat/progress chatter.
"""
from __future__ import annotations

import builtins
import os
import re
import threading
from typing import Any

_ORIGINAL_PRINT = builtins.print
_LOCK = threading.Lock()
_INSTALLED = False
_LAST_METRICS: dict[str, Any] = {}

# Messages that are expected on virtually every successful cycle and provide no
# new information to a human looking at the logs.
_NOISE_SUBSTRINGS = (
    "EMAIL INBOX AUTOMATION & STATUS LISTENER",
    "Checking Gmail Inbox for application updates",
    "Gmail check complete (0 new messages evaluated)",
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
    "Notification Sent [",
    "Saved updated filter settings to settings.json",
    "No active open schemes to recheck",
)

_FETCHED_ELIGIBLE_RE = re.compile(r"↳\s+.+?:\s+\d+ fetched \(\d+ (?:eligible|relevant)\)", re.I)
_PURGE_NOCHANGE_RE = re.compile(r"Purge Complete: Checked \d+ schemes, removed 0 dead/closed listings", re.I)
_SCRAPER_TOTAL_RE = re.compile(r"Scraper run complete .*?:\s*(\d+) eligible active schemes indexed", re.I)
_TRACKR_SUMMARY_RE = re.compile(r"Trackr Summary: .*?\((\d+) active recent schemes", re.I)


def _should_emit(text: str) -> bool:
    if not text.strip():
        return False

    # Errors/warnings and actual closure findings always win over noise rules.
    lowered = text.lower()
    important_error = (
        "⚠️" in text
        or "❌" in text
        or "🛑" in text
        or "traceback" in lowered
        or "exception" in lowered
        or " error" in lowered
        or "failed" in lowered
        or "failure" in lowered
    )
    if important_error:
        return True

    if any(marker in text for marker in _NOISE_SUBSTRINGS):
        return False
    if _FETCHED_ELIGIBLE_RE.search(text):
        return False
    if _PURGE_NOCHANGE_RE.search(text):
        return False

    # Keep periodic totals only when the value actually changes.  This gives a
    # compact "36 -> 38 schemes" type signal without printing every cycle.
    scraper_match = _SCRAPER_TOTAL_RE.search(text)
    if scraper_match:
        value = int(scraper_match.group(1))
        with _LOCK:
            previous = _LAST_METRICS.get("scraper_total")
            _LAST_METRICS["scraper_total"] = value
        return previous is None or previous != value

    trackr_match = _TRACKR_SUMMARY_RE.search(text)
    if trackr_match:
        value = int(trackr_match.group(1))
        with _LOCK:
            previous = _LAST_METRICS.get("trackr_active")
            _LAST_METRICS["trackr_active"] = value
        return previous is None or previous != value

    return True


def _quiet_print(*args, **kwargs):
    # Preserve print's normal rendering closely enough for existing callers.
    sep = kwargs.get("sep", " ")
    text = sep.join(str(arg) for arg in args)
    if _should_emit(text):
        _ORIGINAL_PRINT(*args, **kwargs)


def install_quiet_logging() -> None:
    """Install the global runtime print filter once.

    Set LOG_VERBOSE=true to restore the old fully verbose output for debugging.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    if os.getenv("LOG_VERBOSE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return
    builtins.print = _quiet_print
