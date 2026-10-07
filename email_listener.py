import os
import json
import re
import imaplib
import email
from email.header import decode_header
from hashlib import sha256
from bs4 import BeautifulSoup
from core.jobs import JobRepository, match_email_to_job
from core.storage import atomic_write_json, DATA_DIR, load_json_safe
from config import APP_BASE_URL
import time
from datetime import datetime, timedelta

from config import (
    GMAIL_USER, GMAIL_APP_PASS,
    EMAIL_USER, EMAIL_APP_PASS, EMAIL_IMAP_HOST, EMAIL_IMAP_PORT,
    SEEN_EMAILS_FILE, update_source_status,
)
from notifications import send_notification
from sheets import fetch_google_sheet_csv, update_google_sheet_via_webhook, get_detailed_applications, normalize_company
from core.storage import (
    add_pending_email_update,
    load_pending_email_updates,
    save_pending_email_updates,
)

GENERIC_DOMAINS = {
    "gmail", "yahoo", "hotmail", "outlook", "icloud", "proton", "mail",
    "googlemail", "live", "msn", "me", "comcast", "aol",
    "greenhouse", "lever", "ashbyhq", "myworkdayjobs", "icims", "smartrecruiters"
}
GENERIC_SUBJECT_COMPANY_WORDS = {
    "complete an online assessment", "online assessment", "assessment", "application",
    "application update", "your application", "invitation", "complete", "status update",
}


def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        try:
            with open(SEEN_EMAILS_FILE, "r", encoding="utf-8") as handle:
                return set(json.load(handle))
        except Exception:
            pass
    return set()


def save_seen_emails(seen):
    atomic_write_json(SEEN_EMAILS_FILE, sorted(seen))

def _decode_header_value(value):
    out = []
    for chunk, encoding in decode_header(value or ""):
        if isinstance(chunk, bytes):
            out.append(chunk.decode(encoding or "utf-8", errors="ignore"))
        else:
            out.append(str(chunk))
    return "".join(out)


def extract_company_name(subject, from_sender, body_text=""):
    """Extract an employer, preferring forwarded-message body evidence over the Fwd subject."""
    subject = subject or ""
    body_text = body_text or ""

    body_patterns = (
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Talent Acquisition Team\b",
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Recruitment Team\b",
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Early Careers Team\b",
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Email Classification\s*:",
    )
    for pattern in body_patterns:
        match = re.search(pattern, body_text)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip(" -–|,.")
            if 2 < len(candidate) <= 50:
                return candidate

    domain_match = re.search(r"@([a-zA-Z0-9\-]+)\.", from_sender or "")
    if domain_match:
        domain = domain_match.group(1).lower()
        if domain not in GENERIC_DOMAINS and len(domain) > 2:
            if domain in {"marshallwace", "mwc"}:
                return "Marshall Wace"
            if domain == "the-trackr":
                return "Trackr"
            return domain.capitalize()

    # Subjects such as "Fwd: Invitation to complete an online assessment" used to
    # incorrectly produce "Complete An Online Assessment" as the company.
    sub_match = (
        re.search(r"\b(?:at|with|for|to)\s+([A-Z][a-zA-Z0-9\s&]+?)(?=\s+[-–|]|[.,!?]|$)", subject, re.I)
        or re.search(r"([A-Z][a-zA-Z0-9\s&]+?)\s+Application\b", subject)
    )
    if sub_match:
        candidate = re.sub(r"\s+", " ", sub_match.group(1)).strip()
        if (
            len(candidate) > 2
            and candidate.lower() not in {"your", "the", "a", "an", "our", "us"}
            and candidate.lower() not in GENERIC_SUBJECT_COMPANY_WORDS
            and not any(word in candidate.lower() for word in ("assessment", "interview", "application"))
        ):
            return candidate.title()

    return "Application Company"


def latest_message_text(text):
    """Discard reply history and quoted lines; keep an intentionally forwarded message."""
    text = str(text or '').replace('\r\n', '\n')
    boundary = re.search(r"(?im)^\s*(?:on .{5,150}wrote:|[-_]{2,}\s*original message|previous (?:email|message)\s*:|from:.*\n(?:sent:|date:))", text)
    # Plain one-line HTML email exports can still contain an explicit history label.
    boundary = boundary or re.search(r"(?i)\bprevious (?:email|message)\s*:", text)
    if boundary and text[:boundary.start()].strip():
        text = text[:boundary.start()]
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('>'))


def classify_email_stage(text):
    text_lower = latest_message_text(text).lower()
    # A provider name, newsletter or preparation resource is not a stage event.
    recruitment = re.search(r"\b(?:your application|application (?:for|received|submitted|status|was)|thank you for applying|candidate|recruitment|interview|offer of employment|offer letter|pleased to offer|complete your assessment|assessment invitation|online assessment)\b", text_lower)
    if not recruitment:
        return None
    rejection = re.finditer(r"regret to inform|unable to offer|not moving forward|(?:proceed|progress|move forward|moving forward) with other candidates|unsuccessful|application was rejected|decided not to proceed|will not be proceeding", text_lower)
    for match in rejection:
        before = text_lower[max(0, match.start()-45):match.start()]
        # Avoid negated outcomes and references to hypothetical rejection.
        if re.search(r"\b(?:not|never|isn't|wasn't|hasn't|haven't)(?:\s+\w+){0,3}\s*$|\bif (?:you are|your application is)\s*$", before):
            continue
        return "Rejected"
    offers = re.finditer(r"offer of employment|pleased to offer|congratulations on your offer|formal offer|offer letter|we would like to offer", text_lower)
    for match in offers:
        before = text_lower[max(0, match.start()-45):match.start()]
        if not re.search(r"\b(?:not|no|never|isn't|cannot)(?:\s+\w+){0,3}\s*$", before):
            return "Offer"
    if re.search(r"(?:your interview|interview (?:has been|is)).{0,30}\b(?:cancelled|canceled)\b", text_lower):
        return "Application Update"
    invitation = re.search(
        r"\b(?:invitation to (?:an? )?interview|interview invitation|"
        r"(?:invite|inviting|invited) you.{0,100}\b(?:interview|assessment cent(?:re|er))|"
        r"your (?:[a-z0-9-]+\s+){0,3}interview|"
        r"(?:schedule|book|reschedule|confirm).{0,45}\b(?:an? |your )?interview|"
        r"interview (?:has been |is )?(?:scheduled|confirmed)|"
        r"(?:first|second|third|final) round interview)\b", text_lower, re.S)
    if invitation:
        round_match = re.search(r"(?:interview|round)\s*(\d+)", text_lower)
        return f"Interview {round_match.group(1)}" if round_match else "Interview"
    if any(k in text_lower for k in ("online test", "coding assessment", "online assessment", "numerical reasoning", "logic test", "take-home", "complete your assessment", "assessment invitation", "experience platform")):
        # Generic practice/promotional messages must never advance an application.
        if not re.search(r"\b(?:practice|preparation|prepare for|sample|mock)\b", text_lower) or re.search(r"\b(?:invite|invited|invitation|must complete|please complete|your application)\b", text_lower):
            return "Online Assessment"
    if any(k in text_lower for k in ("thank you for applying", "application received", "received your application", "confirming your application", "application submitted", "successfully submitted")):
        return "Applied"
    if any(k in text_lower for k in ("application status", "update regarding your", "regarding your application")):
        return "Application Update"
    return None


def _tokens(value):
    return {x for x in re.findall(r"[a-z0-9]+", (value or "").lower()) if len(x) > 2}


def _rank_apps_from_email(subject, body_text, apps):
    haystack = f"{subject or ''} {body_text or ''}".lower()
    compact_haystack = re.sub(r"[^a-z0-9]", "", haystack)
    normalized_haystack = re.sub(r"[^a-z0-9]+", " ", haystack)
    hay_tokens = _tokens(haystack)
    ranked = []
    for app in apps:
        company = str(app.get("company") or "")
        role = str(app.get("role") or "")
        score = 0
        company_norm = normalize_company(company)
        if company_norm and company_norm in compact_haystack:
            score += 10
        elif company.lower() and company.lower() in haystack:
            score += 10
        role_tokens = _tokens(role)
        score += min(10, len(role_tokens & hay_tokens))
        role_norm = re.sub(r"[^a-z0-9]+", " ", role.lower()).strip()
        if role_norm and role_norm in normalized_haystack:
            score += 14
        if score:
            ranked.append((score, app))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def _app_options(apps):
    seen = set()
    options = []
    for app in apps:
        company = str(app.get("company") or "").strip()
        role = str(app.get("role") or "Software/Quant Role").strip()
        key = (normalize_company(company), role.lower())
        if company and key not in seen:
            seen.add(key)
            options.append({"company": company, "role": role})
    return options


def _get_sheet_apps_with_retry():
    """Fetch current applications robustly; keep a short retry for transient published-CSV failures."""
    apps = []
    for attempt in range(3):
        apps = get_detailed_applications(force_refresh=True)
        if apps:
            return apps
        if attempt < 2:
            time.sleep(1)
    return apps


def refresh_pending_role_choices():
    """Repair old pending cards in persistent /data using the current Sheet applications."""
    pending = load_pending_email_updates()
    if not isinstance(pending, list) or not pending:
        return 0
    apps = _get_sheet_apps_with_retry()
    fallback = _app_options(apps)
    if not fallback:
        print("  ├── ⚠️ Pending role-choice repair skipped: Google Sheet returned no applications.")
        return 0

    changed = 0
    for item in pending:
        if not isinstance(item, dict):
            continue
        options = item.get("options")
        if not isinstance(options, list) or not options:
            item["options"] = list(fallback)
            changed += 1
    if changed:
        save_pending_email_updates(pending)
        print(f"  ├── 🔧 Repaired {changed} pending email update(s) with {len(fallback)} logged role choice(s).")
    return changed


def handle_incoming_email_update(company_name, detected_stage, subject="", from_sender="", body_text="", event_id=None):
    from scrapers_engine.audit import load_discovered_jobs
    event_id = event_id or sha256(f"{from_sender}|{subject}|{body_text}".encode()).hexdigest()
    repo = JobRepository()
    jobs = repo.sync(load_discovered_jobs(), _get_sheet_apps_with_retry(), reconcile=bool(fetch_google_sheet_csv()))
    job, candidates, reason = match_email_to_job(jobs, company_name, subject, from_sender, body_text)
    if job and repo.email_synced(job, event_id):
        return True
    if job:
        repo.record_email(job, event_id, detected_stage, subject)
        # Ordinary follow-up emails must not manufacture additional interview rounds.
        same_stage = detected_stage == 'Application Update' or detected_stage in job.stages or (detected_stage == 'Interview' and any(s.lower().startswith('interview') for s in job.stages)) or (detected_stage == 'Online Assessment' and any(s.lower().startswith(('assessment', 'online assessment')) for s in job.stages))
        # Catch-up mail must not silently undo a later round or reopen a finished application.
        def phase(stage):
            value = stage.lower()
            if any(word in value for word in ('reject', 'offer', 'withdraw', 'ghost')): return 3
            if 'interview' in value: return 2
            if any(word in value for word in ('assessment', 'online test', 'oa')): return 1
            return 0
        current_stage = job.stages[-1] if job.stages else 'Applied'
        needs_review = not same_stage and (phase(current_stage) == 3 or phase(detected_stage) < phase(current_stage))
        if needs_review:
            reason = 'Email conflicts with the current Sheet stage; review before changing the application'
        else:
            success = update_google_sheet_via_webhook(job.company, detected_stage, role=job.title,
                link=job.link, resolve_sequential=False) if not same_stage else True
            if success:
                repo.record_email(job, event_id, detected_stage, subject, synced=True)
                if not same_stage or detected_stage == 'Application Update':
                    send_notification(f"Update Logged: {job.company} ({detected_stage})",
                        f"{job.title}: {detected_stage}", link=f"{APP_BASE_URL}/")
                return True
            reason = 'Sheet update failed; retry or assign on the dashboard'
    add_pending_email_update({
        "id": 'email_' + sha256(event_id.encode()).hexdigest()[:24],
        "event_id": event_id, "job_id": job.id if job else None,
        "company": job.company if job else company_name, "stage": detected_stage,
        "subject": subject[:200], "reason": reason,
        "date_received": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "options": [{"company": j.company, "role": j.title, "job_id": j.id} for j in (candidates or jobs) if j.stages]
    })
    send_notification(f"Assign email: {company_name} ({detected_stage})",
        f"{subject[:150]} — {reason}", link=f"{APP_BASE_URL}/", tags="warning", priority=4)
    # Durably queued is a successful ingestion, even though assignment is pending.
    return True


def _configured_imap_inboxes():
    inboxes = []
    if GMAIL_USER and GMAIL_APP_PASS:
        inboxes.append({
            "key": "gmail", "label": "Gmail", "host": "imap.gmail.com", "port": 993,
            "user": GMAIL_USER, "password": GMAIL_APP_PASS,
        })
    if EMAIL_USER and EMAIL_APP_PASS and EMAIL_IMAP_HOST:
        inboxes.append({
            "key": "imap", "label": "IMAP", "host": EMAIL_IMAP_HOST, "port": EMAIL_IMAP_PORT,
            "user": EMAIL_USER, "password": EMAIL_APP_PASS,
        })
    return inboxes


def _extract_plain_text(msg):
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_disposition() == 'attachment':
            continue
        content_type = part.get_content_type()
        if content_type not in ('text/plain', 'text/html'):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or 'utf-8'
        try:
            text = payload.decode(charset, errors='replace')
        except LookupError:
            text = payload.decode('utf-8', errors='replace')
        if content_type == 'text/plain':
            plain.append(text)
        else:
            soup = BeautifulSoup(text, 'html.parser')
            for quoted in soup.select("blockquote, .gmail_quote, .yahoo_quoted, #divRplyFwdMsg"):
                quoted.decompose()
            for tag in soup(['script', 'style']):
                tag.decompose()
            html.append(soup.get_text(' ', strip=True) + ' ' + ' '.join(a.get('href', '') for a in soup.find_all('a')))
    return '\n'.join(plain or html)

def _process_message(label, seen_key, subject, from_sender, body_text, seen_emails):
    if seen_key in seen_emails:
        return 0
    body_text = latest_message_text(body_text)
    stage = classify_email_stage(f"{subject} {body_text}")
    if stage:
        company = extract_company_name(subject, from_sender, body_text)
        print(f"  │   ↳ {label}: detected {company} → {stage} | {subject[:70]}")
        handle_incoming_email_update(company, stage, subject, from_sender, body_text, event_id=seen_key)
    seen_emails.add(seen_key)
    save_seen_emails(seen_emails)
    return 1


MAIL_CHECKPOINTS_FILE = str(DATA_DIR / 'mail_checkpoints.json')


def _check_one_imap_inbox(account, seen_emails):
    label = account["label"]
    source_name = f"{label} Inbox Listener"
    print(f"  ├── 📬 Checking {label} Inbox for application updates (Read & Unread)...")
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(account["host"], account["port"], timeout=12)
        mail.login(account["user"], account["password"])
        status, _ = mail.select(account.get('folder', 'INBOX'), readonly=True)
        if status != 'OK':
            raise RuntimeError('Configured mail folder could not be selected')
        validity = mail.response("UIDVALIDITY")[1]
        generation = validity[0].decode() if validity and validity[0] else "unknown"
        checkpoints = load_json_safe(MAIL_CHECKPOINTS_FILE, {})
        checkpoint_key = sha256(f"{account['host']}|{account['user']}|{account.get('folder', 'INBOX')}".encode()).hexdigest()
        checkpoint = checkpoints.get(checkpoint_key, {})
        last_check = checkpoint.get('completed_at', time.time() - 30 * 86400)
        since_date = datetime.fromtimestamp(last_check - 86400).strftime("%d-%b-%Y")
        status, messages = mail.uid("search", None, f'(SINCE "{since_date}")')
        if status != "OK":
            raise RuntimeError('Mailbox search failed')
        complete = True

        processed = 0
        account_key = sha256(f"{account['host']}|{account['user']}".encode()).hexdigest()
        for e_id in (messages[0] or b'').split():
            raw_id = e_id.decode()
            seen_key = f"uid:{checkpoint_key}:{generation}:{raw_id}"
            if seen_key in seen_emails:
                continue
            status, msg_data = mail.uid("fetch", e_id, "(BODY.PEEK[])")
            if status != "OK":
                complete = False
                continue
            fetched = False
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    fetched = True
                    msg = email.message_from_bytes(response_part[1])
                    legacy_key = f"message:{account['key']}:{msg.get('Message-ID') or seen_key}"
                    if legacy_key in seen_emails:
                        seen_emails.add(seen_key)
                        break
                    processed += _process_message(
                        label, f"message:{account_key}:{msg.get('Message-ID') or seen_key}",
                        _decode_header_value(msg.get("Subject", "")),
                        _decode_header_value(msg.get("From", "")),
                        _extract_plain_text(msg), seen_emails,
                    )
                    seen_emails.add(seen_key)
                    save_seen_emails(seen_emails)
                    break
            if not fetched:
                complete = False
        save_seen_emails(seen_emails)
        if complete:
            checkpoints[checkpoint_key] = {'completed_at': time.time(), 'uidvalidity': generation}
            atomic_write_json(MAIL_CHECKPOINTS_FILE, checkpoints)
        from config import update_scraper_status
        update_scraper_status('mail_health', {'ok': complete, 'checked_at': datetime.now().astimezone().isoformat(), 'evaluated': processed})
        update_source_status(source_name, f"🟢 Active • {processed} new messages evaluated this check")
        print(f"  ├── ✅ {label} check complete ({processed} new messages evaluated).")
        return processed
    except Exception as exc:
        from config import update_scraper_status
        update_scraper_status('mail_health', {'ok': False, 'checked_at': datetime.now().astimezone().isoformat(), 'error': type(exc).__name__})
        update_source_status(source_name, f"⚠️ Connection/check error ({type(exc).__name__})")
        print(f"  ├── ⚠️ {label} listener error: {type(exc).__name__}")
        return 0
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


def check_email_inbox():
    inboxes = _configured_imap_inboxes()
    folders = [f.strip() for f in os.getenv('EMAIL_FOLDERS', 'INBOX').split(',') if f.strip()]
    inboxes = [{**account, 'folder': folder, 'label': account['label'] + ' · ' + folder} for account in inboxes for folder in folders]

    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 📧 EMAIL INBOX AUTOMATION & STATUS LISTENER                            │
└────────────────────────────────────────────────────────────────────────┘""")

    # Repair persisted pending cards even when there are no new emails this cycle.
    try:
        refresh_pending_role_choices()
    except Exception as exc:
        print(f"  ├── ⚠️ Could not repair pending role choices: {exc}")

    if not inboxes:
        update_source_status("Email Inbox Listener", "⚪ Offline (No inbox credentials configured)")
        return 0

    seen = load_seen_emails()
    total = 0
    from config import SCRAPER_STATUS, update_scraper_status
    checks = []
    for account in inboxes:
        total += _check_one_imap_inbox(account, seen)
        checks.append(dict(SCRAPER_STATUS.get("mail_health", {})))
    update_scraper_status("mail_health", {"ok": all(c.get("ok", False) for c in checks), "checked_at": datetime.now().astimezone().isoformat(), "folders_checked": len(checks), "failed_folders": sum(not c.get("ok", False) for c in checks), "evaluated": total})

    # Try again after inbox processing in case the first Sheet fetch was transient.
    try:
        refresh_pending_role_choices()
    except Exception as exc:
        print(f"  ├── ⚠️ Could not repair pending role choices after polling: {exc}")

    save_seen_emails(seen)
    from config import SCRAPER_STATUS
    ok = SCRAPER_STATUS.get('mail_health', {}).get('ok', False)
    update_source_status("Email Inbox Listener", f"{'🟢 Active' if ok else '⚠️ Needs attention'} • {len(inboxes)} folder(s) • {total} new messages this check")
    print(f"  └── ✅ Email listener cycle complete ({total} new messages evaluated across {len(inboxes)} inbox(es)).")
    return total
