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
    """Extract a likely employer, including from forwarded-email body signatures."""
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

    sub_match = (
        re.search(r"\b(?:at|with|for|to)\s+([A-Z][a-zA-Z0-9\s&]+?)(?=\s+[-–|]|[.,!?]|$)", subject, re.I)
        or re.search(r"([A-Z][a-zA-Z0-9\s&]+?)\s+Application\b", subject)
    )
    if sub_match:
        candidate = sub_match.group(1).strip()
        if len(candidate) > 2 and candidate.lower() not in {"your", "the", "a", "an", "our", "us"}:
            return candidate.title()

    domain_match = re.search(r"@([a-zA-Z0-9\-]+)\.", from_sender or "")
    if domain_match:
        domain = domain_match.group(1).lower()
        if domain not in GENERIC_DOMAINS and len(domain) > 2:
            if domain in {"marshallwace", "mwc"}:
                return "Marshall Wace"
            if domain == "the-trackr":
                return "Trackr"
            return domain.capitalize()

    return "Application Company"


def classify_email_stage(text):
    text_lower = (text or "").lower()

    if any(k in text_lower for k in (
        "offer of employment", "pleased to offer", "congratulations on your offer",
        "job offer", "formal offer", "offer letter", "we would like to offer",
    )):
        return "Offer"

    # Assessment must be checked before generic "next step" interview wording.
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
    """Rank logged applications using employer and role evidence from the full email."""
    haystack = f"{subject or ''} {body_text or ''}".lower()
    hay_tokens = _tokens(haystack)
    ranked = []
    for app in apps:
        company = str(app.get("company") or "")
        role = str(app.get("role") or "")
        score = 0
        company_norm = normalize_company(company)
        if company_norm and company_norm in re.sub(r"[^a-z0-9]", "", haystack):
            score += 8
        elif company.lower() and company.lower() in haystack:
            score += 8

        role_tokens = _tokens(role)
        overlap = role_tokens & hay_tokens
        score += min(8, len(overlap))
        role_norm = re.sub(r"[^a-z0-9]+", " ", role.lower()).strip()
        if role_norm and role_norm in re.sub(r"[^a-z0-9]+", " ", haystack):
            score += 10
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


def _refresh_empty_pending_options():
    """Backfill old pending cards so they always let the user choose an existing application."""
    pending = load_pending_email_updates()
    if not pending:
        return
    apps = get_detailed_applications(force_refresh=True)
    fallback = _app_options(apps)
    changed = False
    for item in pending:
        if not item.get("options") and fallback:
            item["options"] = fallback
            changed = True
    if changed:
        save_pending_email_updates(pending)
        print(f"  ├── 🔧 Backfilled role choices for {sum(1 for x in pending if x.get('options'))} pending email update(s).")


def handle_incoming_email_update(company_name, detected_stage, subject="", from_sender="", body_text=""):
    apps = get_detailed_applications(force_refresh=True)
    norm_company = normalize_company(company_name)
    matching_apps = [a for a in apps if normalize_company(a.get("company", "")) == norm_company]

    # Forwarded mail can hide the original sender. If normal extraction failed,
    # use employer + role evidence from the complete message against the Sheet.
    if not matching_apps:
        ranked = _rank_apps_from_email(subject, body_text, apps)
        if ranked:
            best_score = ranked[0][0]
            second_score = ranked[1][0] if len(ranked) > 1 else -1
            if best_score >= 10 and best_score >= second_score + 3:
                matching_apps = [ranked[0][1]]
                company_name = ranked[0][1].get("company", company_name)
            else:
                plausible = [app for score, app in ranked if score >= 4][:10]
                if plausible:
                    matching_apps = plausible
                    company_name = plausible[0].get("company", company_name)

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

    if len(matching_apps) > 1:
        options = _app_options(matching_apps)
    else:
        # Never strand the dashboard with only "new role". Give the user all
        # currently logged applications as a manual fallback.
        options = _app_options(apps)

    if detected_stage in {"Applied", "Offer"} and not matching_apps and company_name != "Application Company":
        update_google_sheet_via_webhook(company_name, detected_stage, role="Software/Quant Role", resolve_sequential=True)
        return True

    update_id = f"pending_{int(time.time()*1000)}"
    add_pending_email_update({
        "id": update_id,
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
    if not inboxes:
        update_source_status("Email Inbox Listener", "⚪ Offline (No inbox credentials configured)")
        return 0

    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 📧 EMAIL INBOX AUTOMATION & STATUS LISTENER                            │
└────────────────────────────────────────────────────────────────────────┘""")

    # This also repairs old pending cards created before role fallback existed.
    try:
        _refresh_empty_pending_options()
    except Exception as exc:
        print(f"  ├── ⚠️ Could not refresh pending role choices: {exc}")

    seen = load_seen_emails()
    total = 0
    for account in inboxes:
        total += _check_one_imap_inbox(account, seen)
    save_seen_emails(seen)
    update_source_status("Email Inbox Listener", f"🟢 Active • {len(inboxes)} inbox(es) • {total} new messages this check")
    print(f"  └── ✅ Email listener cycle complete ({total} new messages evaluated across {len(inboxes)} inbox(es)).")
    return total
