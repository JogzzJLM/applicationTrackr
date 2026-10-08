"""Private, destination-bound data for the user's browser extension."""
import base64
import mimetypes
import re
import time
import requests
from pathlib import Path
from urllib.parse import urlparse, quote

from autoapply.learning import BUILTIN_ALIASES, _load, normalize_label
from autoapply.profile import AUTOAPPLY_DIR, ensure_profile, flatten_profile
from autoapply.service import _find_job


def owned_document(kind):
    """Only named profile documents inside the private upload directory are downloadable."""
    if kind not in {'resume_path', 'cover_letter_path'}:
        return None
    path = Path(str(ensure_profile().get('documents', {}).get(kind, '')))
    if not path.is_file() or not path.resolve().is_relative_to(AUTOAPPLY_DIR.resolve()):
        return None
    return path if path.stat().st_size <= 10 * 1024 * 1024 else None


_SMART_FORMS = {}


def application_form_url(url):
    """Resolve a published SmartRecruiters posting to its exact form UUID."""
    target = urlparse(url)
    match = re.fullmatch(r'/([^/]+)/(\d+)(?:-[^/]*)?/?', target.path)
    if target.scheme != 'https' or target.hostname != 'jobs.smartrecruiters.com' or not match:
        return url
    cached = _SMART_FORMS.get(url)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    company, posting = match.groups()
    try:
        response = requests.get(f'https://api.smartrecruiters.com/v1/companies/{quote(company, safe="")}/postings/{posting}', timeout=8)
        response.raise_for_status()
        data = response.json()
        uuid = str(data.get('uuid', ''))
        identifier = str((data.get('company') or {}).get('identifier', ''))
        if str(data.get('id')) != posting or identifier.casefold() != company.casefold() or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uuid):
            return url
        form = f'https://jobs.smartrecruiters.com/oneclick-ui/company/{quote(identifier, safe="")}/publication/{uuid}'
        _SMART_FORMS[url] = (time.time(), form)
        return form
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        return url


def browser_bundle(job_id, include_documents=True):
    job = _find_job(job_id)
    if not job:
        raise ValueError('Job not found.')
    url = job.get('link', '')
    target = urlparse(url)
    if target.scheme != 'https' or not target.hostname or target.username or target.password:
        raise ValueError('A valid HTTPS application link is required.')
    profile = ensure_profile()
    flat = flatten_profile(profile)
    # Answers stay bound to this listing. Do not disclose other employers' answers.
    answers = {item['question']: item['answer'] for item in _load().get('question_answers', [])
               if not item.get('domain') or normalize_label(item['domain']) == normalize_label(url)}
    documents = []
    for key in ('resume_path', 'cover_letter_path'):
        path = owned_document(key)
        if path is None:
            continue
        document = {'key': 'documents.' + key, 'name': path.name,
                    'mime': mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}
        if include_documents:
            document['base64'] = base64.b64encode(path.read_bytes()).decode('ascii')
        documents.append(document)
    return {'job': {'id': job_id, 'company': job.get('company', ''), 'title': job.get('title', ''), 'url': application_form_url(url)},
            'profile': {key: value for key, value in flat.items() if not key.startswith(('documents.', 'answers.'))},
            'aliases': BUILTIN_ALIASES, 'answers': answers, 'documents': documents}
