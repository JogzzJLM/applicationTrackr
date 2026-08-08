import os
import json
import re
import imaplib
import email
from email.header import decode_header
import time
from datetime import datetime, timedelta
from config import GMAIL_USER, GMAIL_APP_PASS, SEEN_EMAILS_FILE
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
    # 1. Direct Subject Patterns: "at Company", "to Company", "Company Application"
    sub_match = (
        re.search(r"\b(?:at|with|for|to)\s+([A-Z][a-zA-Z0-9\s\&]+?)(?=\s+[\-\–\|]|[\.\,\!\?]|$)", subject, re.IGNORECASE) or
        re.search(r"([A-Z][a-zA-Z0-9\s\&]+?)\s+Application\b", subject)
    )
    if sub_match:
        c_name = sub_match.group(1).strip()
        if len(c_name) > 2 and c_name.lower() not in ["your", "the", "a", "an", "our", "us"]:
            return c_name.title()

    # 2. Sender Domain Extraction (@company.com)
    domain_match = re.search(r"@([a-zA-Z0-9\-]+)\.", from_sender)
    if domain_match:
        dom = domain_match.group(1).lower()
        if dom not in GENERIC_DOMAINS and len(dom) > 2:
            # Format domain nicely e.g. "marshallwace" -> "Marshall Wace" if known, else Title Case
            if dom == "marshallwace" or dom == "mwc":
                return "Marshall Wace"
            elif dom == "the-trackr":
                return "Trackr"
            return dom.capitalize()

    # 3. Body Text Match
    body_match = re.search(r"team at\s+([A-Z][a-zA-Z0-9\s]+)", body_text) or re.search(r"applying to\s+([A-Z][a-zA-Z0-9\s]+)", body_text)
    if body_match:
        c_name = body_match.group(1).strip().split('\n')[0].split('.')[0]
        if len(c_name) > 2:
            return c_name.strip().title()

    return "Target Company"

def check_email_inbox():
    if not GMAIL_USER or not GMAIL_APP_PASS or "your_email" in GMAIL_USER:
        return

    print("📧 Checking Gmail Inbox for application updates (Read & Unread)...")
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
                print(f"⚠️ Email Listener Notice: IMAP connection offline/retry skipped ({e})")
                return

    try:
        # Search recent emails (past 3 days) so even if marked as READ on phone, we process them!
        since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        if status != "OK" or not messages[0]:
            status, messages = mail.search(None, 'ALL')

        if status != "OK" or not messages[0]:
            mail.logout()
            return

        email_ids = messages[0].split()

        # First Run Protection: If seen_emails is empty, seed with current inbox UIDs so old history is never retroactively processed
        if is_first_run:
            print(f"📧 [Email Listener] Initialized email tracker with {len(email_ids)} existing inbox messages.")
            for e_id in email_ids:
                seen_emails.add(e_id.decode())
            save_seen_emails(seen_emails)
            mail.logout()
            return

        # Check latest 30 messages
        for e_id in email_ids[-30:]:
            str_id = e_id.decode()
            if str_id in seen_emails:
                continue

            # Mark email ID as evaluated immediately to avoid duplicate processing
            seen_emails.add(str_id)

            status, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg["Subject"])[0] if msg["Subject"] else ("No Subject", None)
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding or "utf-8", errors="ignore")
                    
                    from_sender = msg.get("From", "")
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

                    # 1. Offer Stage
                    if any(k in combined_text for k in [
                        "offer of employment", "job offer", "pleased to offer", "congratulations!",
                        "offer letter", "formal offer", "delighted to offer"
                    ]):
                        update_google_sheet_via_webhook(company_name, "Offer")
                        send_notification(
                            title=f"🎉 JOB OFFER: {company_name}!",
                            message=f"Congratulations! Offer email received from {company_name}.\nCheck your inbox for details!",
                            tags="tada,trophy",
                            priority=5,
                            sound="fanfare"
                        )
                        print(f"📧 [Email Listener] 🥳 OFFER DETECTED for {company_name}!")

                    # 2. Interview / Next Round Stage
                    elif any(k in combined_text for k in [
                        "invitation to interview", "interview invitation", "schedule your interview",
                        "schedule a chat", "speak with our team", "technical interview", "behavioral interview",
                        "final round", "next round", "next steps", "next stage", "move forward with your application",
                        "pleased to invite you", "progress your application", "advanced to the next stage",
                        "shortlisted", "book your time slot", "interview slot"
                    ]):
                        update_google_sheet_via_webhook(company_name, "Interview")
                        send_notification(
                            title=f"Interview Invite: {company_name}",
                            message=f"Next round/interview email received from {company_name}.\nCheck your inbox to schedule!",
                            tags="calendar,fire",
                            priority=5,
                            sound="fanfare"
                        )
                        print(f"📧 [Email Listener] 🗓 INTERVIEW INVITE DETECTED for {company_name}!")

                    # 3. Online Assessment / Coding Test Stage
                    elif any(k in combined_text for k in [
                        "online assessment", "coding test", "technical assessment", "hackerrank",
                        "codility", "hirevue", "codesignal", "pymetrics", "shl", "testgorilla",
                        "workday test", "greenhouse assessment", "assessment centre", "assessment center",
                        "superday", "take-home test", "online test"
                    ]):
                        update_google_sheet_via_webhook(company_name, "Online Assessment")
                        send_notification(
                            title=f"Assessment Invite: {company_name}",
                            message=f"Coding test / online assessment email received from {company_name}.\nCheck your inbox!",
                            tags="computer,fire",
                            priority=5,
                            sound="fanfare"
                        )
                        print(f"📧 [Email Listener] 💻 ASSESSMENT INVITE DETECTED for {company_name}!")

                    # 4. Rejection / Unsuccessful Stage
                    elif any(k in combined_text for k in [
                        "we regret to inform", "regret to inform", "unfortunately", "will not be moving forward",
                        "pursue other candidates", "decided not to proceed", "other candidates whose skills",
                        "unsuccessful", "not shortlisted", "not selected", "filled the role"
                    ]):
                        update_google_sheet_via_webhook(company_name, "Rejected")
                        send_notification(
                            title=f"Update: {company_name}",
                            message=f"Application status updated to Rejected for {company_name}.",
                            tags="x",
                            priority=2,
                            sound="minion"
                        )
                        print(f"📧 [Email Listener] ❌ REJECTION DETECTED for {company_name}.")

                    # 5. Application Confirmed Stage
                    elif any(k in combined_text for k in [
                        "thank you for applying", "thanks for applying", "application received",
                        "thanks for your interest", "received your application", "application submitted",
                        "successfully submitted", "received your resume", "received your cv", "application confirmation"
                    ]):
                        update_google_sheet_via_webhook(company_name, "Applied")
                        send_notification(
                            title=f"Application Confirmed: {company_name}",
                            message=f"Logged 'Applied' status for {company_name} in Google Sheets.",
                            tags="check-mark",
                            priority=2,
                            sound="subtle"
                        )
                        print(f"📧 [Email Listener] ✅ APPLICATION CONFIRMED for {company_name}.")

                    # 6. Fallback Catch-All for Unclassified Application Updates
                    elif any(k in combined_text for k in [
                        "application update", "update on your application", "status update",
                        "regarding your application", "application status", "position at", "role at"
                    ]):
                        update_google_sheet_via_webhook(company_name, "Application Update")
                        send_notification(
                            title=f"Application Update: {company_name}",
                            message=f"New application update email received from {company_name}.\nCheck your inbox!",
                            tags="envelope,bell",
                            priority=4,
                            sound="fanfare"
                        )
                        print(f"📧 [Email Listener] 🔔 APPLICATION UPDATE DETECTED for {company_name}!")

        mail.logout()
        save_seen_emails(seen_emails)

    except Exception as e:
        print(f"⚠️ Email Listener Error: {e}")