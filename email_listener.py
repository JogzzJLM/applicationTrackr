import os
import json
import re
import imaplib
import email
from email.header import decode_header
import time
from datetime import datetime, timedelta

from config import (
    GMAIL_USER, GMAIL_APP_PASS,
    EMAIL_USER, EMAIL_APP_PASS, EMAIL_IMAP_HOST, EMAIL_IMAP_PORT,
    SEEN_EMAILS_FILE, update_source_status,
)
from notifications import send_notification
from sheets import update_google_sheet_via_webhook, get_detailed_applications, normalize_company
from core.storage import (
    add_pending_email_update,
    load_pending_email_updates,
    save_pending_email_updates,
)

GENERIC_DOMAINS = {
    "gmail", "yahoo", "hotmail", "outlook", "icloud", "proton", "mail",
    "googlemail", "live", "msn", "me", "comcast", "aol"
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
    try:
        with open(SEEN_EMAILS_FILE, "w", encoding="utf-8") as handle:
            json.dump(list(seen), handle)
    except Exception:
        pass


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


def classify_email_stage(text):
    text_lower = (text or "").lower()
    if any(k in text_lower for k in (
        "offer of employment", "pleased to offer", "congratulations on your offer",
        "job offer", "formal offer", "offer letter", "we would like to offer",
    )):
        return "Offer"
    if any(k in text_lower for k in (
        "online test", "coding assessment", "hackerrank", "codility", "hirevue",
        "online assessment", "numerical reasoning", "logic test", "take-home",
        "experience platform", "complete your assessment", "assessment invitation",
    )):
        return "Online Assessment"
    if any(k in text_lower for k in (
        "interview", "schedule a call", "invitation to interview", "next step", "speaking with",
        "first round", "final round", "assessment centre", "assessment center", "video call",
    )):
        return "Interview"
    if any(k in text_lower for k in (
        "regret to inform", "unable to offer", "not moving forward", "other candidates",
        "unsuccessful", "high volume of applications", "after careful consideration",
        "decided not to proceed", "will not be proceeding",
    )):
        return "Rejected"
    if any(k in text_lower for k in (
        "thank you for applying", "application received", "received your application",
        "confirming your application", "application submitted", "successfully submitted",
    )):
        return "Applied"
    if any(k in text_lower for k in (
        "application status", "update regarding your", "regarding your application",
    )):
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


def handle_incoming_email_update(company_name, detected_stage, subject="", from_sender="", body_text=""):
    apps = _get_sheet_apps_with_retry()
    norm_company = normalize_company(company_name)
    matching_apps = [a for a in apps if normalize_company(a.get("company", "")) == norm_company]

    if not matching_apps and apps:
        ranked = _rank_apps_from_email(subject, body_text, apps)
        if ranked:
            best_score = ranked[0][0]
            second_score = ranked[1][0] if len(ranked) > 1 else -1
            if best_score >= 12 and best_score >= second_score + 3:
                matching_apps = [ranked[0][1]]
                company_name = ranked[0][1].get("company", company_name)
            else:
                matching_apps = [app for score, app in ranked if score >= 5][:10]

    if len(matching_apps) == 1:
        app = matching_apps[0]
        exact_role = app.get("role", "Software/Quant Role")
        exact_company = app.get("company", company_name)
        update_google_sheet_via_webhook(exact_company, detected_stage, role=exact_role, resolve_sequential=True)
        send_notification(
            title=f"Update Logged: {exact_company} ({detected_stage})",
            message=f"Automatically updated status to {detected_stage} for exact role: '{exact_role}'.",
            tags="check-mark", priority=3, sound="chime",
        )
        print(f"  ├── ✅ MATCHED APPLICATION: {exact_company} -> {exact_role} ({detected_stage})")
        return True

    options = _app_options(matching_apps if len(matching_apps) > 1 else apps)
    if detected_stage in {"Applied", "Offer"} and not matching_apps and company_name != "Application Company":
        update_google_sheet_via_webhook(company_name, detected_stage, role="Software/Quant Role", resolve_sequential=True)
        return True

    add_pending_email_update({
        "id": f"pending_{int(time.time()*1000)}",
        "company": company_name,
        "stage": detected_stage,
        "subject": subject[:120] if subject else f"{company_name} Email Update",
        "date_received": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "options": options,
    })
    send_notification(
        title=f"Action Required: {company_name} ({detected_stage})",
        message="Email update received. Select the matching logged application on the dashboard.",
        tags="warning,bell", priority=4, sound="chime",
    )
    print(f"  ├── ⚠️ Email update needs confirmation; {len(options)} role choice(s) available.")
    return False


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
    if msg.is_multipart():
        html_fallback = ""
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition", "")).lower():
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
            if part.get_content_type() == "text/plain":
                return text
            if part.get_content_type() == "text/html" and not html_fallback:
                html_fallback = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", html_fallback)
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")


def _process_message(label, seen_key, subject, from_sender, body_text, seen_emails):
    if seen_key in seen_emails:
        return 0
    seen_emails.add(seen_key)
    stage = classify_email_stage(f"{subject} {body_text}")
    if stage:
        company = extract_company_name(subject, from_sender, body_text)
        print(f"  │   ↳ {label}: detected {company} → {stage} | {subject[:70]}")
        handle_incoming_email_update(company, stage, subject, from_sender, body_text)
    return 1


def _check_one_imap_inbox(account, seen_emails):
    label = account["label"]
    source_name = f"{label} Inbox Listener"
    print(f"  ├── 📬 Checking {label} Inbox for application updates (Read & Unread)...")
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(account["host"], account["port"], timeout=12)
        mail.login(account["user"], account["password"])
        mail.select("inbox")
        since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        if status != "OK" or not messages[0]:
            status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages[0]:
            return 0

        processed = 0
        for e_id in messages[0].split()[-50:]:
            raw_id = e_id.decode()
            seen_key = f"{account['key']}:{raw_id}"
            if seen_key in seen_emails or (account["key"] == "gmail" and raw_id in seen_emails):
                continue
            status, msg_data = mail.fetch(e_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    processed += _process_message(
                        label, seen_key,
                        _decode_header_value(msg.get("Subject", "")),
                        _decode_header_value(msg.get("From", "")),
                        _extract_plain_text(msg), seen_emails,
                    )
                    break
        save_seen_emails(seen_emails)
        update_source_status(source_name, f"🟢 Active • {processed} new messages evaluated this check")
        print(f"  ├── ✅ {label} check complete ({processed} new messages evaluated).")
        return processed
    except Exception as exc:
        update_source_status(source_name, f"⚠️ Connection/check error ({str(exc)[:120]})")
        print(f"  ├── ⚠️ {label} listener error: {exc}")
        return 0
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


def check_email_inbox():
    inboxes = _configured_imap_inboxes()

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
    for account in inboxes:
        total += _check_one_imap_inbox(account, seen)

    # Try again after inbox processing in case the first Sheet fetch was transient.
    try:
        refresh_pending_role_choices()
    except Exception as exc:
        print(f"  ├── ⚠️ Could not repair pending role choices after polling: {exc}")

    save_seen_emails(seen)
    update_source_status("Email Inbox Listener", f"🟢 Active • {len(inboxes)} inbox(es) • {total} new messages this check")
    print(f"  └── ✅ Email listener cycle complete ({total} new messages evaluated across {len(inboxes)} inbox(es)).")
    return total
