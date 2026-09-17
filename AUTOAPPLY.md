# Adaptive Application Agent

ApplicationTrackr now has an opt-in browser agent at `/autoapply`. It is separate from job discovery and is disabled by default.

## Design

The learner is deliberately lightweight: deterministic aliases plus an online nearest-neighbour model over form labels and questions. For structured application forms this is more useful than training a large model from a tiny personal dataset, and it improves as approved mappings/answers accumulate.

It can:

- open an application page with Chromium/Selenium;
- identify visible inputs using labels, ARIA, placeholder, name, id and autocomplete metadata;
- map standard fields to a private applicant profile;
- learn new field mappings and reusable answers from corrections;
- upload a CV from the persistent data volume;
- navigate safe Next/Continue steps;
- stop rather than invent an unknown answer;
- stop for CAPTCHA instead of bypassing it;
- flag sensitive/legal/attestation fields for review;
- optionally submit only when `AUTOAPPLY_AUTO_SUBMIT=true`, the run explicitly requests it, and there are zero review blockers.

## Private persistent data

None of this should be committed to Git. The Docker volume stores:

- `/data/autoapply/applicant_profile.json`
- `/data/autoapply/field_learning.json`
- `/data/autoapply/runs.json`
- `/data/autoapply/resume.pdf`
- `/data/autoapply/screenshots/`

The profile template is created on first use.

## Portainer environment

Start in training/fill-only mode:

```text
AUTOAPPLY_ENABLED=true
AUTOAPPLY_AUTO_SUBMIT=false
AUTOAPPLY_HEADLESS=true
AUTOAPPLY_MAX_STEPS=5
AUTOAPPLY_PAGE_TIMEOUT=30
CHROMIUM_BINARY=/usr/bin/chromium
```

## Teaching the learner

Field mapping:

```bash
curl -X POST http://localhost:5000/api/autoapply/learn-mapping \
  -d 'label=Preferred contact email' \
  -d 'profile_key=personal.email'
```

Reusable answer:

```bash
curl -X POST http://localhost:5000/api/autoapply/learn-answer \
  -d 'question=Why are you interested in this role?' \
  --data-urlencode 'answer=<your approved answer>'
```

Unknown screening questions remain unresolved until you approve an answer.
