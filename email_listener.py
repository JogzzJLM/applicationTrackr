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
    OUTLOOK_USER, MICROSOFT_CLIENT_ID, MICROSOFT_TENANT,
    EMAIL_USER, EMAIL_APP_PASS, EMAIL_IMAP_HOST, EMAIL_IMAP_PORT,
    SEEN_EMAILS_FILE, update_source_status,
)
from notifications import send_notification
from sheets import update_google_sheet_via_webhook, get_detailed_applications, normalize_company
from core.storage import add_pending_email_update
from outlook_graph import fetch_recent_inbox_messages


GENERIC_DOMAINS = {
    "gmail", "yahoo", "hotmail", "outlook", "icloud", "proton", "mail",
    "googlemail", "live", "msn", "me", "comcast", "aol"
}


def load_seen_emails():
    if os.path.exists(SEEN_EMAILS_FILE):
        try:
            with open(SEEN_EMAILS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen_emails(seen):
    try:
        with open(SEEN_EMAILS_FILE, "w") as f:
            json.dump(list(seen), f)
    except Exception:
        pass


def _decode_header_value(value):
    chunks = decode_header(value or "")
    out = []
    for chunk, encoding in chunks:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(encoding or "utf-8", errors="ignore"))
        else:
            out.append(str(chunk))
    return "".join(out)


def extract_company_name(subject, from_sender, body_text=""):
    """Extract a likely company from subject, sender domain, or recognisable body signature."""
    subject = subject or ""
    body_text = body_text or ""

    sub_match = (
        re.search(r"\b(?:at|with|for|to)\s+([A-Z][a-zA-Z0-9\s\&]+?)(?=\s+[\-\–\|]|[\.\,\!\?]|$)", subject, re.IGNORECASE) or
        re.search(r"([A-Z][a-zA-Z0-9\s\&]+?)\s+Application\b", subject)
    )
    if sub_match:
        c_name = sub_match.group(1).strip()
        if len(c_name) > 2 and c_name.lower() not in ["your", "the", "a", "an", "our", "us"]:
            return c_name.title()

    body_patterns = (
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Talent Acquisition Team\b",
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Recruitment Team\b",
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Early Careers Team\b",
        r"\b([A-Z][A-Za-z0-9&.'\- ]{2,50}?)\s+Email Classification\b",
    )
    for pattern in body_patterns:
        match = re.search(pattern, body_text)
        if match:
            candidate = re.sub(r"\s+", " ", match.group(1)).strip(" -–|,.")
            if 2 < len(candidate) <= 50:
                return candidate

    domain_match = re.search(r"@([a-zA-Z0-9\-]+)\.", from_sender or "")
    if domain_match:
        dom = domain_match.group(1).lower()
        if dom not in GENERIC_DOMAINS and len(dom) > 2:
            if dom in {"marshallwace", "mwc"}:
                return "Marshall Wace"
            if dom == "the-trackr":
                return "Trackr"
            return dom.capitalize()

    return "Application Company"


def classify_email_stage(text):
    """Determine application status from email content."""
    text_lower = (text or "").lower()

    offer_keywords = [
        "offer of employment", "pleased to offer", "congratulations on your offer",
        "job offer", "formal offer", "offer letter", "we would like to offer"
    ]
    if any(k in text_lower for k in offer_keywords):
        return "Offer"

    oa_keywords = [
        "online test", "coding assessment", "hackerrank", "codility", "hirevue",
        "online assessment", "numerical reasoning", "logic test", "take-home",
        "experience platform", "complete your assessment", "assessment invitation"
    ]
    if any(k in text_lower for k in oa_keywords):
        return "Online Assessment"

    interview_keywords = [
        "interview", "schedule a call", "invitation to interview", "next step", "speaking with",
        "first round", "final round", "assessment centre", "assessment center", "video call"
    ]
    if any(k in text_lower for k in interview_keywords):
        return "Interview"

    rejection_keywords = [
        "regret to inform", "unable to offer", "not moving forward", "other candidates",
        "unsuccessful", "high volume of applications", "after careful consideration",
        "decided not to proceed", "will not be proceeding"
    ]
    if any(k in text_lower for k in rejection_keywords):
        return "Rejected"

    applied_keywords = [
        "thank you for applying", "application received", "received your application",
        "confirming your application", "application submitted", "successfully submitted"
    ]
    if any(k in text_lower for k in applied_keywords):
        return "Applied"

    update_keywords = [
        "application status", "update regarding your", "regarding your application"
    ]
    if any(k in text_lower for k in update_keywords):
        return "Application Update"

    return None


def handle_incoming_email_update(company_name, detected_stage, subject="", from_sender="", body_text=""):
    """Map an incoming status update onto an existing Google Sheet application."""
    apps = get_detailed_applications(force_refresh=True)
    norm_c = normalize_company(company_name)
    matching_apps = [a for a in apps if normalize_company(a.get("company", "")) == norm_c]

    if len(matching_apps) == 1:
        exact_role = matching_apps[0].get("role", "Software/Quant Role")
        exact_company = matching_apps[0].get("company", company_name)
        update_google_sheet_via_webhook(exact_company, detected_stage, role=exact_role, resolve_sequential=True)
        send_notification(
            title=f"Update Logged: {exact_company} ({detected_stage})",
            message=f"Automatically updated status to {detected_stage} for exact role: '{exact_role}'.",
            tags="check-mark", priority=3, sound="chime"
        )
        print(f"  ├── ✅ MATCHED 1-EXACT APPLICATION: {exact_company} -> {exact_role} ({detected_stage})")
        return True

    if len(matching_apps) > 1:
        update_id = f"pending_{int(time.time()*1000)}"
        add_pending_email_update({
            "id": update_id, "company": company_name, "stage": detected_stage,
            "subject": subject[:80] if subject else f"{company_name} Email Update",
            "date_received": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "options": [{"company": a.get("company", company_name), "role": a.get("role")} for a in matching_apps]
        })
        send_notification(
            title=f"⚠️ Action Required: {company_name} ({detected_stage})",
            message=f"Email update received from {company_name}. You have {len(matching_apps)} active applications for {company_name}. Click to select which role this update belongs to.",
            tags="warning,bell", priority=5, sound="fanfare"
        )
        print(f"  ├── ⚠️ AMBIGUOUS UPDATE ({len(matching_apps)} apps found for {company_name}). Saved to Pending Actions.")
        return False

    if detected_stage in ["Applied", "Offer"]:
        update_google_sheet_via_webhook(company_name, detected_stage, role="Software/Quant Role", resolve_sequential=True)
        send_notification(
            title=f"New Application Logged: {company_name}",
            message=f"Logged new application for {company_name} ({detected_stage}).",
            tags="check-mark", priority=2, sound="subtle"
        )
        print(f"  ├── ✅ LOGGED NEW APPLICATION: {company_name} ({detected_stage})")
        return True

    update_id = f"pending_{int(time.time()*1000)}"
    add_pending_email_update({
        "id": update_id, "company": company_name, "stage": detected_stage,
        "subject": subject[:80] if subject else f"{company_name} Email Update",
        "date_received": datetime.now().strftime("%Y-%m-%d %H:%M"), "options": []
    })
    send_notification(
        title=f"Notice: {company_name} ({detected_stage})",
        message=f"Email update ({detected_stage}) received from {company_name}, but no active application was logged. Click to resolve on dashboard.",
        tags="information_source", priority=3, sound="subtle"
    )
    print(f"  ├── ℹ️ Email update for unlogged company {company_name}. Saved to Pending Items.")
    return False


def _configured_imap_inboxes():
    inboxes = []
    if GMAIL_USER and GMAIL_APP_PASS:
        inboxes.append({"key": "gmail", "label": "Gmail", "host": "imap.gmail.com", "port": 993, "user": GMAIL_USER, "password": GMAIL_APP_PASS})
    if EMAIL_USER and EMAIL_APP_PASS and EMAIL_IMAP_HOST:
        inboxes.append({"key": "imap", "label": "IMAP", "host": EMAIL_IMAP_HOST, "port": EMAIL_IMAP_PORT, "user": EMAIL_USER, "password": EMAIL_APP_PASS})
    return inboxes


def _extract_plain_text(msg):
    if msg.is_multipart():
        fallback_html = ""
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in disposition:
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="ignore")
            if ctype == "text/plain":
                return text
            if ctype == "text/html" and not fallback_html:
                fallback_html = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", fallback_html)
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")


def _process_message(label, seen_key, subject, from_sender, body_text, seen_emails):
    if seen_key in seen_emails:
        return 0
    seen_emails.add(seen_key)
    company_name = extract_company_name(subject, from_sender, body_text)
    detected_stage = classify_email_stage(f"{subject} {body_text}")
    if detected_stage:
        print(f"  │   ↳ {label}: detected {company_name} → {detected_stage} | {subject[:70]}")
        handle_incoming_email_update(
            company_name=company_name,
            detected_stage=detected_stage,
            subject=subject,
            from_sender=from_sender,
            body_text=body_text,
        )
    return 1


def _check_one_imap_inbox(account, seen_emails):
    label = account["label"]
    source_name = f"{label} Inbox Listener"
    print(f"  ├── 📬 Checking {label} Inbox for application updates (Read & Unread)...")

    mail = None
    for attempt in range(2):
        try:
            mail = imaplib.IMAP4_SSL(account["host"], account["port"], timeout=12)
            mail.login(account["user"], account["password"])
            mail.select("inbox")
            break
        except Exception as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            message = str(exc)
            update_source_status(source_name, f"⚠️ Connection failed ({message[:120]})")
            print(f"  ├── ⚠️ {label} listener connection failed: {message}")
            return 0

    processed_new = 0
    try:
        since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        if status != "OK" or not messages[0]:
            status, messages = mail.search(None, "ALL")
        if status != "OK" or not messages[0]:
            update_source_status(source_name, f"🟢 Active • {label} inbox up to date")
            return 0

        email_ids = messages[0].split()
        for e_id in email_ids[-40:]:
            raw_id = e_id.decode()
            seen_key = f"{account['key']}:{raw_id}"
            if seen_key in seen_emails or (account["key"] == "gmail" and raw_id in seen_emails):
                continue
            status, msg_data = mail.fetch(e_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for response_part in msg_data:
                if not isinstance(response_part, tuple):
                    continue
                msg = email.message_from_bytes(response_part[1])
                processed_new += _process_message(
                    label,
                    seen_key,
                    _decode_header_value(msg.get("Subject", "")),
                    _decode_header_value(msg.get("From", "")),
                    _extract_plain_text(msg),
                    seen_emails,
                )
                break

        save_seen_emails(seen_emails)
        update_source_status(source_name, f"🟢 Active • {processed_new} new messages evaluated this check")
        print(f"  ├── ✅ {label} check complete ({processed_new} new messages evaluated).")
        return processed_new
    except Exception as exc:
        update_source_status(source_name, f"⚠️ Check error ({str(exc)[:120]})")
        print(f"  ├── ⚠️ {label} listener error: {exc}")
        return 0
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


def _check_outlook_graph(seen_emails):
    source_name = "Outlook Inbox Listener"
    print("  ├── 📬 Checking Outlook Inbox through Microsoft Graph OAuth...")

    result = fetch_recent_inbox_messages(
        client_id=MICROSOFT_CLIENT_ID,
        tenant=MICROSOFT_TENANT,
        login_hint=OUTLOOK_USER,
        days=3,
        limit=50,
    )
    if not result.get("ok"):
        auth = result.get("auth") or {}
        error = result.get("error") or auth.get("error_description") or auth.get("error") or "unknown Microsoft authentication error"
        update_source_status(source_name, f"⚠️ OAuth/Graph error ({str(error)[:120]})")
        print(f"  ├── ⚠️ Outlook OAuth/Graph listener error: {error}")
        return 0

    processed_new = 0
    # Graph returns newest first; process oldest first so stage progression is chronological.
    for item in reversed(result.get("messages", [])):
        raw_id = item.get("internet_message_id") or item.get("id") or ""
        if not raw_id:
            continue
        seen_key = f"outlook:{raw_id}"
        processed_new += _process_message(
            "Outlook",
            seen_key,
            item.get("subject", ""),
            item.get("from", ""),
            item.get("body", ""),
            seen_emails,
        )

    save_seen_emails(seen_emails)
    auth_mode = result.get("auth_mode", "silent")
    update_source_status(source_name, f"🟢 OAuth active • {processed_new} new messages • auth={auth_mode}")
    print(f"  ├── ✅ Outlook Graph check complete ({processed_new} new messages evaluated; auth={auth_mode}).")
    return processed_new


def check_email_inbox():
    imap_inboxes = _configured_imap_inboxes()
    outlook_enabled = bool(MICROSOFT_CLIENT_ID)
    source_count = len(imap_inboxes) + (1 if outlook_enabled else 0)
    if not source_count:
        update_source_status("Email Inbox Listener", "⚪ Offline (No inbox credentials configured)")
        return 0

    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 📧 EMAIL INBOX AUTOMATION & STATUS LISTENER                            │
└────────────────────────────────────────────────────────────────────────┘""")

    seen_emails = load_seen_emails()
    total = 0
    for account in imap_inboxes:
        total += _check_one_imap_inbox(account, seen_emails)
    if outlook_enabled:
        total += _check_outlook_graph(seen_emails)

    save_seen_emails(seen_emails)
    update_source_status("Email Inbox Listener", f"🟢 Active • {source_count} inbox(es) • {total} new messages this check")
    print(f"  └── ✅ Email listener cycle complete ({total} new messages evaluated across {source_count} inbox(es)).")
    return total