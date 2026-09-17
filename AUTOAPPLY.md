# Adaptive Application Agent

ApplicationTrackr has an opt-in browser agent at `/autoapply`. It is deliberately separate from job discovery so the discovery engine can remain useful even when browser automation is disabled.

## How the learner works

The application agent now combines three layers:

1. **Deterministic aliases** for obvious fields such as email, phone, CV, university and LinkedIn.
2. **Online multinomial Naive Bayes** trained from approved field mappings. The model uses the field label, nearby form text and application-site domain. Built-in aliases seed the classifier before any personal training data exists.
3. **Site behaviour memory** that records which Continue/Next/Submit wording successfully moved through each application site.

A nearest-neighbour fallback handles newly learned questions that differ only slightly from examples already approved.

The learner never trains on an unconfirmed guess. The training data lives in `/data/autoapply/field_learning.json`, not in Git.

## What it can do

- open an application page with Chromium/Selenium;
- click an initial Apply/Start button when it is only a landing page;
- identify visible inputs using label, ARIA, placeholder, name, id and autocomplete metadata;
- use nearby form text and select options as ML context;
- map standard fields to a private applicant profile;
- learn site-specific and general field mappings from corrections;
- learn reusable answers from approved examples;
- upload a CV from the persistent data volume;
- navigate safe multi-step Next/Continue flows;
- remember successful navigation labels per site;
- stop rather than invent an unknown answer;
- stop for CAPTCHA instead of bypassing it;
- flag sensitive/legal/attestation fields for review;
- optionally submit only when `AUTOAPPLY_AUTO_SUBMIT=true`, the individual run requests it, and there are zero review blockers;
- run an opt-in Autopilot queue over the strongest unattempted jobs, subject to a score threshold and daily cap.

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
AUTOAPPLY_AUTOPILOT_ENABLED=false
AUTOAPPLY_AUTOPILOT_MIN_SCORE=82
AUTOAPPLY_DAILY_LIMIT=3
AUTOAPPLY_HEADLESS=true
AUTOAPPLY_MAX_STEPS=8
AUTOAPPLY_PAGE_TIMEOUT=30
CHROMIUM_BINARY=/usr/bin/chromium
```

Recommended rollout:

1. Leave `AUTOAPPLY_AUTO_SUBMIT=false` and `AUTOAPPLY_AUTOPILOT_ENABLED=false`.
2. Run a few applications from `/autoapply` and teach unresolved labels/questions using the training forms.
3. Once the profile and field learner are reliable, enable `AUTOAPPLY_AUTOPILOT_ENABLED=true`. Autopilot will still stop the batch if an application needs human review.
4. Only enable `AUTOAPPLY_AUTO_SUBMIT=true` if you want final submission for applications that contain no unresolved or sensitive fields.

## Autopilot selection

Autopilot only queues jobs that:

- still pass the current eligibility-first relevance engine;
- meet `AUTOAPPLY_AUTOPILOT_MIN_SCORE` (default 82);
- have not already been attempted by the application agent;
- fit inside `AUTOAPPLY_DAILY_LIMIT`.

It runs sequentially rather than opening many applications at once. A CAPTCHA, sensitive/legal field, incomplete profile or unresolved question stops the batch so you can teach the agent before continuing.

## Training APIs

The `/autoapply` page exposes these through forms, but the HTTP endpoints are also available.

Field mapping:

```bash
curl -X POST http://localhost:5000/api/autoapply/learn-mapping \
  -d 'label=Preferred contact email' \
  -d 'profile_key=personal.email' \
  -d 'domain=jobs.example.com'
```

Reusable answer:

```bash
curl -X POST http://localhost:5000/api/autoapply/learn-answer \
  -d 'question=Why are you interested in this role?' \
  --data-urlencode 'answer=<your approved answer>' \
  -d 'domain=jobs.example.com'
```

Unknown screening questions remain unresolved until you approve an answer. Sensitive, legal and attestation fields remain review-gated even when an answer is known.
