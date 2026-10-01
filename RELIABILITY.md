ApplicationTrackr now keeps job objects, custom details, manually added jobs, and
per-job email history in the existing persistent `/data` volume. Use **Add Job**
for listings from elsewhere and **Details / Notes** for additional information.
Additional information uses editable field names and values, such as Salary and Contact.

Set `NTFY_TOPIC=jog_applicationtrackr_alerts` in the Portainer stack, with
`NTFY_BASE_URL=https://ntfy.sh` (or your own server). Set `NTFY_TOKEN` if the topic
requires authentication. Subscribe the phone/client to the same server and topic.
**Test ntfy** reports whether the server accepted a message; this is separate from
whether a subscribed device displayed it. Diagnostics retain the last receipt or
error. Notification sounds are configured in the client.

`GOOGLE_SHEET_EDIT_URL` controls **Open Sheet**. The stack defaults to the user's
ApplicationTrackr sheet. Published `/d/e/` CSV links cannot identify the editor URL.
Sheet write failures are shown to the user and do not discard pending emails.

Email polling uses read-only IMAP UID/UIDVALIDITY and Message-ID deduplication.
During migration, recent mail is evaluated once again because old sequence
numbers cannot be safely mapped to UIDs. Ambiguous messages and failed Sheet writes
remain in Pending Actions. Exact job URLs and role titles are preferred over
company-only allocation. Email ingestion does not invent interview round numbers.

The Application Agent is opt-in and requires a populated applicant profile and a
real CV file in `/data/autoapply/`. **Check form only** works while the agent is
disabled and never fills fields, uploads files or submits applications. Inspection
only reports the page it reaches; no discovered form is not proof a job is closed.
A submit click without a success acknowledgement is `submission_unconfirmed` and
is excluded from autopilot retries. Do not retry it before checking the employer.

The stack has a local HTTP healthcheck and rebuilds its image on Git redeployment.
To deploy, push the checked changes and use **Pull and redeploy** in Portainer.
The named data volume remains in place. `/api/health` reports server liveness,
integration configuration and the last ntfy result; it does not assert every
external service is healthy.

Validation: `uv pip install --python .venv/bin/python pytest -r requirements.txt`
and `.venv/bin/python -m pytest -q`.

Guided applications can save chat-supplied profile details with `POST /api/autoapply/profile` (`values` is a JSON object of dotted profile fields). Updates merge with existing details. Upload a CV or cover letter from `/autoapply` using the document upload control; documents are retained in the persistent volume with private filesystem permissions. PDF/DOC/DOCX uploads are bounded to 10 MB and use server-controlled filenames. Answers learned with an application URL as their domain stay scoped to that application, including fuzzy lookup, so another employer cannot inherit its answers. Form inspection now includes required flags, context and native select choices. Low-confidence field guesses are left unanswered.

The Application Agent includes a manual answer panel with the rendered listing context, detected questions, dropdown choices and saved profile answers. Each nonblank manual answer is stored against the exact application URL. Saved answers override profile guesses on that form. The previous-employer rule uses only user-supplied employer history and only direct questions about employment at the named company. Optional unanswered fields are left blank. Searchable dropdowns must select an actual matching option; typed search text alone does not count as a completed field.
