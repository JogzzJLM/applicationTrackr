"""Persistent job objects and evidence-based email allocation."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from hashlib import sha256
import re
from urllib.parse import urlparse
from email.utils import parseaddr
from core.normalization import normalize_company, normalize_role, extract_ats_post_id, normalize_url
from core.storage import DATA_DIR, load_json_safe, atomic_write_json, _FILE_LOCK

JOBS_FILE = str(DATA_DIR / 'job_objects.json')
def role_identity(title):
    # Keep years: the same employer's 2026 and 2027 vacancies are different jobs.
    return ' '.join(re.findall(r'[a-z0-9]+', (title or '').lower()))

MANUAL_JOBS_FILE = str(DATA_DIR / 'manual_jobs.json')

@dataclass
class Job:
    id: str
    company: str
    title: str
    link: str = ''
    location: str = ''
    notes: str = ''
    custom_fields: dict = field(default_factory=dict)
    stages: list = field(default_factory=list)
    email_events: list = field(default_factory=list)

    @classmethod
    def from_listing(cls, row):
        company = row.get('company', '').strip()
        title = (row.get('title') or row.get('role') or '').strip()
        identity = f'{normalize_company(company)}:{role_identity(title)}'
        job_id = row.get('id') or 'job_' + sha256(identity.encode()).hexdigest()[:20]
        return cls(job_id, company, title, row.get('link', ''), row.get('location', ''),
                   row.get('notes', ''), row.get('custom_fields', {}), row.get('stages', []))

class JobRepository:
    def sync(self, listings, applications):
        with _FILE_LOCK:
            saved = load_json_safe(JOBS_FILE, {})
            for row in list(listings) + list(applications):
                job = Job.from_listing(row)
                existing = saved.get(job.id) or next((j for j in saved.values() if
                    normalize_company(j['company']) == normalize_company(job.company) and
                    role_identity(j['title']) == role_identity(job.title)), None)
                if existing:
                    if existing['id'] == job.id:
                        existing.update(company=job.company, title=job.title)
                        if job.location:
                            existing['location'] = job.location
                    if row.get('stages'):
                        existing['stages'] = row['stages']
                    if job.link:
                        existing['link'] = job.link
                else:
                    saved[job.id] = asdict(job)
            atomic_write_json(JOBS_FILE, saved)
            return [Job(**j) for j in saved.values()]

    def record_email(self, job, event_id, stage, subject, synced=False):
        with _FILE_LOCK:
            saved = load_json_safe(JOBS_FILE, {})
            row = saved.setdefault(job.id, asdict(job))
            event = next((e for e in row['email_events'] if e['id'] == event_id), None)
            if event:
                event['sheet_synced'] = event.get('sheet_synced', False) or synced
            else:
                row['email_events'].append({'id': event_id, 'stage': stage, 'subject': subject,
                    'sheet_synced': synced, 'received_at': datetime.now(timezone.utc).isoformat()})
            if synced and stage not in row['stages']:
                row['stages'].append(stage)
            atomic_write_json(JOBS_FILE, saved)

    def invalidate_cinema_newsletter_events(self):
        """Retain audit evidence for the known cinema-newsletter false positive."""
        with _FILE_LOCK:
            saved = load_json_safe(JOBS_FILE, {})
            changed = 0
            for row in saved.values():
                for event in row.get('email_events', []):
                    if ('@e-mail.odeon.co.uk>' in event.get('id', '').lower()
                            and event.get('subject', '').lower() == "what's new at odeon"
                            and event.get('stage') == 'Interview' and not event.get('invalidated')):
                        event.update(invalidated=True, invalidated_reason='Cinema newsletter, not a recruitment invitation',
                                     invalidated_at=datetime.now(timezone.utc).isoformat())
                        changed += 1
            if changed:
                atomic_write_json(JOBS_FILE, saved)
            return changed

    def email_synced(self, job, event_id):
        return any(e['id'] == event_id and (e.get('sheet_synced') or e.get('invalidated')) for e in job.email_events)


def match_email_to_job(jobs, company, subject, sender, body):
    """Require a unique role or job URL; never guess among same-company roles."""
    text = f'{subject} {body}'
    urls = re.findall(r'https?://[^\s<>"\)]+', text)
    exact = [j for j in jobs if j.link and any(
        normalize_url(u.rstrip('.,')) == normalize_url(j.link) or
        (extract_ats_post_id(u) and extract_ats_post_id(u) == extract_ats_post_id(j.link)) for u in urls)]
    if len(exact) == 1:
        return exact[0], exact, 'job URL'
    norm_text = ' ' + normalize_role(text) + ' '
    sender_host = parseaddr(sender)[1].lower().partition('@')[2]
    # App-store and social/footer brands are not employer evidence.
    employer_text = re.sub(r'\b(?:google play|google maps|apple store|app store|facebook|instagram|twitter)\b', '', text, flags=re.I)
    candidates = []
    for job in jobs:
        name = normalize_company(job.company)
        mentioned = bool(name and re.search(r'\b' + re.escape(job.company.lower()) + r'\b', employer_text.lower()))
        own_domain = bool(name and name in sender_host.split('.'))
        if mentioned or own_domain or (normalize_company(company) == name and company != 'Application Company'):
            candidates.append(job)
    roles = [j for j in candidates if normalize_role(j.title) and
             ' ' + normalize_role(j.title) + ' ' in norm_text]
    if len(roles) == 1:
        return roles[0], candidates, 'role title'
    # Only a previously logged application can be allocated by company alone.
    applied = [j for j in candidates if j.stages]
    if len(applied) == 1 and len(candidates) == 1:
        return applied[0], candidates, 'single logged application'
    return None, candidates, 'ambiguous or insufficient evidence'


def add_manual_job(company, title, link='', location='', notes='', custom_fields=None):
    if not company.strip() or not title.strip():
        raise ValueError('Company and role are required.')
    if link and (urlparse(link).scheme not in ('http', 'https') or not urlparse(link).netloc):
        raise ValueError('Job link must be an HTTP or HTTPS URL.')
    if not isinstance(custom_fields or {}, dict):
        raise ValueError('Extra fields must be a JSON object.')
    row = asdict(Job.from_listing({'company': company, 'title': title, 'link': link,
        'location': location, 'notes': notes, 'custom_fields': custom_fields or {}}))
    row.update(source='Manual', sources=['Manual'], date_found=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'))
    with _FILE_LOCK:
        rows = load_json_safe(MANUAL_JOBS_FILE, [])
        rows = [j for j in rows if j['id'] != row['id']] + [row]
        atomic_write_json(MANUAL_JOBS_FILE, rows)
    return row


def save_job_details(job_id, notes, custom_fields):
    if not isinstance(custom_fields, dict):
        raise ValueError('Extra fields must be a JSON object.')
    with _FILE_LOCK:
        saved = load_json_safe(JOBS_FILE, {})
        if job_id not in saved:
            raise ValueError('Job not found; refresh the dashboard first.')
        saved[job_id]['notes'] = notes
        saved[job_id]['custom_fields'] = custom_fields
        atomic_write_json(JOBS_FILE, saved)
        return saved[job_id]
