import os
import json
import re
import imaplib
import email
from email.header import decode_header
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from config import GMAIL_USER, GMAIL_APP_PASS, SEEN_EMAILS_FILE, HP_STREAM_TAILSCALE_IP, update_source_status
from notifications import send_notification
from sheets import update_google_sheet_via_webhook, get_detailed_applications, normalize_company
from core.storage import add_pending_email_update


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

def extract_company_name(subject, from_sender, body_text=""):
    """Intelligently extracts the company name from email subject, sender domain, or body."""
    sub_match = (
        re.search(r"\b(?:at|with|for|to)\s+([A-Z][a-zA-Z0-9\s\&]+?)(?=\s+[\-\–\|]|[\.\,\!\?]|$)", subject, re.IGNORECASE) or
        re.search(r"([A-Z][a-zA-Z0-9\s\&]+?)\s+Application\b", subject)
    )
    if sub_match:
        c_name = sub_match.group(1).strip()
        if len(c_name) > 2 and c_name.lower() not in ["your", "the", "a", "an", "our", "us"]:
            return c_name.title()

    domain_match = re.search(r"@([a-zA-Z0-9\-]+)\.", from_sender)
    if domain_match:
        dom = domain_match.group(1).lower()
        if dom not in GENERIC_DOMAINS and len(dom) > 2:
            if dom == "marshallwace" or dom == "mwc":
                return "Marshall Wace"
            elif dom == "the-trackr":
                return "Trackr"
            return dom.capitalize()

    return "Application Company"

def classify_email_stage(text):
    """Determines application status from email content."""
    text_lower = text.lower()

    offer_keywords = [
        "offer of employment", "pleased to offer", "congratulations on your offer",
        "job offer", "formal offer", "offer letter", "we would like to offer"
    ]
    if any(k in text_lower for k in offer_keywords):
        return "Offer"

    interview_keywords = [
        "interview", "schedule a call", "invitation to interview", "next step", "speaking with",
        "first round", "final round", "assessment centre", "assessment center", "video call"
    ]
    if any(k in text_lower for k in interview_keywords):
        return "Interview"

    oa_keywords = [
        "online test", "coding assessment", "hackerrank", "codility", "hirevue",
        "online assessment", "numerical reasoning", "logic test", "take-home"
    ]
    if any(k in text_lower for k in oa_keywords):
        return "Online Assessment"

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
    """
    Intelligently maps incoming email status updates (Rejected, Interview, OA, Offer) to existing applications in Google Sheets.
    - If 1 application exists for company_name: updates that exact application's role on Google Sheets.
    - If >1 application exists for company_name: saves a pending update so the user can select which role it belongs to.
    - If 0 applications exist: logs a new entry or creates a pending update.
    """
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
            tags="check-mark",
            priority=3,
            sound="chime"
        )
        print(f"  ├── ✅ MATCHED 1-EXACT APPLICATION: {exact_company} -> {exact_role} ({detected_stage})")
        return True

    elif len(matching_apps) > 1:
        update_id = f"pending_{int(time.time()*1000)}"
        pending_obj = {
            "id": update_id,
            "company": company_name,
            "stage": detected_stage,
            "subject": subject[:80] if subject else f"{company_name} Email Update",
            "date_received": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "options": [
                {"company": a.get("company", company_name), "role": a.get("role")}
                for a in matching_apps
            ]
        }
        add_pending_email_update(pending_obj)
        send_notification(
            title=f"⚠️ Action Required: {company_name} ({detected_stage})",
            message=f"Email update received from {company_name}. You have {len(matching_apps)} active applications for {company_name}. Click to select which role this update belongs to.",
            tags="warning,bell",
            priority=5,
            sound="fanfare"
        )
        print(f"  ├── ⚠️ AMBIGUOUS UPDATE ({len(matching_apps)} apps found for {company_name}). Saved to Pending Actions.")
        return False

    else:
        if detected_stage in ["Applied", "Offer"]:
            update_google_sheet_via_webhook(company_name, detected_stage, role="Software/Quant Role", resolve_sequential=True)
            send_notification(
                title=f"New Application Logged: {company_name}",
                message=f"Logged new application for {company_name} ({detected_stage}).",
                tags="check-mark",
                priority=2,
                sound="subtle"
            )
            print(f"  ├── ✅ LOGGED NEW APPLICATION: {company_name} ({detected_stage})")
            return True
        else:
            update_id = f"pending_{int(time.time()*1000)}"
            pending_obj = {
                "id": update_id,
                "company": company_name,
                "stage": detected_stage,
                "subject": subject[:80] if subject else f"{company_name} Email Update",
                "date_received": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "options": []
            }
            add_pending_email_update(pending_obj)
            send_notification(
                title=f"Notice: {company_name} ({detected_stage})",
                message=f"Email update ({detected_stage}) received from {company_name}, but no active application was logged. Click to resolve on dashboard.",
                tags="information_source",
                priority=3,
                sound="subtle"
            )
            print(f"  ├── ℹ️ Email update for unlogged company {company_name}. Saved to Pending Items.")
            return False

def check_email_inbox():
    if not GMAIL_USER or not GMAIL_APP_PASS:
        update_source_status("Gmail Inbox Listener", "⚪ Offline (No Credentials Set)")
        return

    print("""
┌────────────────────────────────────────────────────────────────────────┐
│ 📧 GMAIL INBOX AUTOMATION & STATUS LISTENER                            │
└────────────────────────────────────────────────────────────────────────┘""")
    print("  ├── 📬 Checking Gmail Inbox for application updates (Read & Unread)...")
    seen_emails = load_seen_emails()
    is_first_run = len(seen_emails) == 0

    mail = None
    for attempt in range(2):
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=10)
            mail.login(GMAIL_USER, GMAIL_APP_PASS)
            mail.select("inbox")
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
            else:
                update_source_status("Gmail Inbox Listener", f"⚠️ Connection Skipped ({e})")
                print(f"  └── ⚠️ Email Listener Notice: IMAP connection offline/retry skipped ({e})")
                return

    try:
        since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        if status != "OK" or not messages[0]:
            status, messages = mail.search(None, 'ALL')

        if status != "OK" or not messages[0]:
            update_source_status("Gmail Inbox Listener", f"🟢 Active • {len(seen_emails)} emails tracked")
            print("  └── ℹ️ Inbox up to date (0 new application emails).")
            mail.logout()
            return

        email_ids = messages[0].split()

        if is_first_run:
            print(f"  └── 📦 Initialized email tracker with {len(email_ids)} existing inbox messages.")
            for e_id in email_ids:
                seen_emails.add(e_id.decode())
            save_seen_emails(seen_emails)
            update_source_status("Gmail Inbox Listener", f"🟢 Active • {len(seen_emails)} emails tracked")
            mail.logout()
            return

        processed_new = 0
        for e_id in email_ids[-30:]:
            str_id = e_id.decode()
            if str_id in seen_emails:
                continue

            seen_emails.add(str_id)
            processed_new += 1

            status, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject, encoding = decode_header(msg.get("Subject", ""))[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8", errors="ignore")

                    from_sender, encoding = decode_header(msg.get("From", ""))[0]
                    if isinstance(from_sender, bytes):
                        from_sender = from_sender.decode(encoding or "utf-8", errors="ignore")

                    body_text = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body_text = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        body_text = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

                    combined_text = f"{subject} {body_text}".lower()
                    company_name = extract_company_name(subject, from_sender, body_text)
                    detected_stage = classify_email_stage(combined_text)

                    if detected_stage:
                        handle_incoming_email_update(
                            company_name=company_name,
                            detected_stage=detected_stage,
                            subject=subject,
                            from_sender=from_sender,
                            body_text=body_text
                        )

        mail.logout()
        save_seen_emails(seen_emails)
        update_source_status("Gmail Inbox Listener", f"🟢 Active • {len(seen_emails)} emails tracked")
        print(f"  └── ✅ Gmail check complete ({processed_new} new messages evaluated).")

    except Exception as e:
        update_source_status("Gmail Inbox Listener", f"⚠️ Check Notice ({e})")
        print(f"  └── ⚠️ Email Listener Notice: Error reading inbox messages ({e})")
        if mail:
            try:
                mail.logout()
            except Exception:
                pass