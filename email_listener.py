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
from sheets import update_google_sheet_via_webhook


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

                    if detected_stage == "Offer":
                        update_google_sheet_via_webhook(company_name, "Offer")
                        send_notification(
                            title=f"🎉 JOB OFFER: {company_name}!",
                            message=f"Congratulations! Offer email received from {company_name}.",
                            tags="tada,trophy",
                            priority=5,
                            sound="fanfare"
                        )
                        print(f"  ├── 🥳 OFFER DETECTED for {company_name}!")

                    elif detected_stage == "Interview":
                        update_google_sheet_via_webhook(company_name, "Interview")
                        send_notification(
                            title=f"Interview Invite: {company_name}",
                            message=f"Next round/interview email received from {company_name}.",
                            tags="calendar,fire",
                            priority=5,
                            sound="fanfare"
                        )
                        print(f"  ├── 🗓 INTERVIEW INVITE DETECTED for {company_name}!")

                    elif detected_stage == "Online Assessment":
                        update_google_sheet_via_webhook(company_name, "Online Assessment")
                        send_notification(
                            title=f"Assessment Invite: {company_name}",
                            message=f"Coding test / online assessment email received from {company_name}.",
                            tags="computer,fire",
                            priority=5,
                            sound="fanfare"
                        )
                        print(f"  ├── 💻 ASSESSMENT INVITE DETECTED for {company_name}!")

                    elif detected_stage == "Rejected":
                        update_google_sheet_via_webhook(company_name, "Rejected")
                        send_notification(
                            title=f"Update: {company_name}",
                            message=f"Application status updated to Rejected for {company_name}.",
                            tags="x",
                            priority=2,
                            sound="minion"
                        )
                        print(f"  ├── ❌ REJECTION DETECTED for {company_name}.")

                    elif detected_stage == "Applied":
                        update_google_sheet_via_webhook(company_name, "Applied")
                        send_notification(
                            title=f"Application Confirmed: {company_name}",
                            message=f"Logged 'Applied' status for {company_name} in Google Sheets.",
                            tags="check-mark",
                            priority=2,
                            sound="subtle"
                        )
                        print(f"  ├── ✅ APPLICATION CONFIRMED for {company_name}.")

        mail.logout()
        save_seen_emails(seen_emails)
        update_source_status("Gmail Inbox Listener", f"🟢 Active • {len(seen_emails)} emails tracked")
        print(f"  └── ✅ Gmail check complete ({processed_new} new messages evaluated).")

    except Exception as e:
        update_source_status("Gmail Inbox Listener", f"⚠️ Notice ({e})")
        print(f"  └── ⚠️ Email Listener Error: {e}")