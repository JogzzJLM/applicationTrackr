"""A compact editor for the applicant's own saved details and documents."""
from html import escape
from datetime import datetime
from autoapply.profile import ensure_profile, flatten_profile


def render_profile_html():
    saved = flatten_profile(ensure_profile())
    sections = [
        ('Contact details', [('personal.first_name', 'First name', 'given-name'), ('personal.middle_names', 'Middle names', 'additional-name'), ('personal.last_name', 'Last name', 'family-name'), ('personal.preferred_name', 'Preferred name', ''), ('personal.email', 'Email', 'email'), ('personal.phone', 'Phone including country code', 'tel'), ('personal.city', 'City', 'address-level2'), ('personal.country', 'Country', 'country-name'), ('personal.postcode', 'Postcode', 'postal-code'), ('personal.address_line1', 'Address line 1', 'address-line1'), ('personal.address_line2', 'Address line 2', 'address-line2')]),
        ('Education', [('education.university', 'University', ''), ('education.degree', 'Degree', ''), ('education.course', 'Course', ''), ('education.graduation_date', 'Expected graduation date', ''), ('education.graduation_year', 'Graduation year', ''), ('education.grade', 'Grade, if known', '')]),
        ('Links', [('links.linkedin', 'LinkedIn', 'url'), ('links.github', 'GitHub', 'url'), ('links.portfolio', 'Portfolio', 'url')]),
        ('Employment and work permission', [('employment.previous_employers', 'Previous employers', ''), ('eligibility.right_to_work_uk', 'Permission to work in the UK', ''), ('eligibility.requires_sponsorship', 'Do you require sponsorship?', '')]),
    ]
    groups = []
    for index, (title, fields) in enumerate(sections):
        inputs = []
        for key, label, autocomplete in fields:
            value = escape(str(saved.get(key) or ''), quote=True)
            if key.startswith('eligibility.'):
                answers = ['', 'Yes', 'No']
                current = str(saved.get(key) or '')
                if current and current.lower() not in ('yes', 'no'):
                    answers.append(current)
                options = ''.join(f'<option value="{escape(answer, quote=True)}" {"selected" if str(saved.get(key) or "").lower() == answer.lower() else ""}>{escape(answer or "Not confirmed")}</option>' for answer in answers)
                control = f'<select id="{key}" name="{key}">{options}</select>'
            else:
                kind = 'email' if key == 'personal.email' else 'tel' if key == 'personal.phone' else 'date' if key == 'education.graduation_date' else 'text'
                if kind == 'date' and value:
                    for date_format in ('%Y-%m-%d', '%d/%m/%Y'):
                        try:
                            value = datetime.strptime(str(saved[key]), date_format).date().isoformat()
                            break
                        except ValueError:
                            pass
                    else:
                        kind = 'text'
                control = f'<input id="{key}" name="{key}" type="{kind}" value="{value}" autocomplete="{autocomplete or "off"}">'
            inputs.append(f'<div class="profile-field"><label for="{key}">{label}</label>{control}</div>')
        groups.append(f'<details class="card profile-section" {"open" if index == 0 else ""}><summary>{title}</summary><div class="profile-grid">{"".join(inputs)}</div></details>')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Your application details · ApplicationTrackr</title><link rel="stylesheet" href="/assets/pages.css"></head><body><main class="wrap">
<a class="btn secondary" href="/jobs">← Back to jobs</a><h1>Your application details</h1><p>Save what you know. Leave uncertain answers blank. These details help you fill applications; you review and submit each one yourself.</p>
<form id="profile-form">''' + ''.join(groups) + '''<button type="submit">Save details</button><p id="profile-status" role="status" aria-live="polite"></p></form>
<section class="card"><h2>Your CV and cover letter</h2><p>Upload a PDF, DOC or DOCX file up to 10 MB. Saved documents are available from a job’s “Saved details” button.</p><form id="document-form"><label for="document-kind">Document type</label><select id="document-kind" name="kind"><option value="resume">CV / résumé</option><option value="cover_letter">Cover letter</option></select><label for="document-file">Choose a file</label><input id="document-file" name="file" type="file" accept=".pdf,.doc,.docx" required><button type="submit">Upload and save</button></form><p id="document-status" role="status" aria-live="polite"></p></section>
</main><script>
async function saveForm(event, url, data, statusId) {
    event.preventDefault(); const button = event.target.querySelector('button[type="submit"]'); const output = document.getElementById(statusId);
    button.disabled = true; output.textContent = 'Saving…';
    try { const response = await fetch(url, {method:'POST',body:data}); const result = await response.json(); if (!response.ok || result.status !== 'ok') throw new Error(result.message || 'Could not save.'); output.textContent = 'Saved successfully.'; }
    catch(error) { output.textContent = error.message; } finally { button.disabled = false; }
}
document.getElementById('profile-form').addEventListener('submit', event => {
    const values = Object.fromEntries(new FormData(event.target)); const data = new URLSearchParams({values:JSON.stringify(values)});
    saveForm(event, '/api/autoapply/profile', data, 'profile-status');
});
document.getElementById('document-form').addEventListener('submit', event => saveForm(event, '/api/autoapply/document', new FormData(event.target), 'document-status'));
</script></body></html>'''
