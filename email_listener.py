import os
import json
import re
import imaplib
import email
from email.header import decode_header
from config import GMAIL_USER, GMAIL_APP_PASS, SEEN_EMAILS_FILE
from notifications import send_notification
from sheets import update_google_sheet_via_webhook

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

def check_email_inbox():
    if not GMAIL_USER or not GMAIL_APP_PASS or "your_email" in GMAIL_USER:
        return

    print("📧 Checking Gmail Inbox for application updates...")
    seen_emails = load_seen_emails()

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASS)
        mail.select("inbox")

        status, messages = mail.search(None, '(UNSEEN)')
        if status != "OK":
            return

        email_ids = messages[0].split()
        for e_id in email_ids[-15:]:
            if e_id.decode() in seen_emails:
                continue

            status, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg["Subject"])[0]
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

                    company_match = re.search(r"at ([A-Z][a-zA-Z0-9]+)", subject) or re.search(r"@([a-zA-Z0-9]+)\.", from_sender)
                    company_name = company_match.group(1).capitalize() if company_match else "Company"

                    if any(k in combined_text for k in ["online assessment", "coding test", "hackerrank", "codility", "hirevue", "invitation to interview", "schedule your interview"]):
                        seen_emails.add(e_id.decode())
                        stage = "Online Assessment" if "assessment" in combined_text or "hackerrank" in combined_text else "Interview"
                        update_google_sheet_via_webhook(company_name, stage)
                        send_notification(
                            title=f"Assessment Invite: {company_name}",
                            message=f"New interview/assessment email received from {company_name}.\nCheck your inbox!",
                            tags="tada,fire",
                            priority=5,
                            sound="fanfare"
                        )

                    elif any(k in combined_text for k in ["thank you for applying", "application received", "thanks for your interest", "received your application"]):
                        seen_emails.add(e_id.decode())
                        update_google_sheet_via_webhook(company_name, "Applied")
                        send_notification(
                            title=f"Application Confirmed: {company_name}",
                            message=f"Logged 'Applied' status for {company_name} in Google Sheets.",
                            tags="check-mark",
                            priority=2,
                            sound="subtle"
                        )

                    elif any(k in combined_text for k in ["we regret to inform you", "unfortunately", "will not be moving forward", "pursue other candidates"]):
                        seen_emails.add(e_id.decode())
                        update_google_sheet_via_webhook(company_name, "Rejected")
                        send_notification(
                            title=f"Update: {company_name}",
                            message=f"Application status updated to Rejected for {company_name}.",
                            tags="x",
                            priority=2,
                            sound="minion"
                        )

        mail.logout()
        save_seen_emails(seen_emails)

    except Exception as e:
        print(f"⚠️ Email Listener Error: {e}")