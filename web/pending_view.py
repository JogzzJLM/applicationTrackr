"""Escape email content and keep uncertain updates compact and reviewable."""
from html import escape
import json
from web.components import _js_literal


def render_pending_updates(updates):
    if not updates:
        return ''
    items = []
    for update in updates:
        uid = _js_literal(update.get('id', ''))
        company = update.get('company', 'Company')
        stage = update.get('stage', 'Application Update')
        select_id = 'pending-role-' + str(update.get('id', ''))
        options = ['<option value="">Choose company and role…</option>']
        seen = set()
        for option in update.get('options', []):
            employer, role = option.get('company', company), option.get('role', '')
            key = (employer, role)
            if key in seen: continue
            seen.add(key)
            value = escape(json.dumps({'company':employer,'role':role}), quote=True)
            options.append(f'<option value="{value}">{escape(employer)} — {escape(role)}</option>')
        items.append(f'''<div class="pending-update-card">
<strong>{escape(company)}</strong> · {escape(stage)} <span>{escape(update.get('date_received', ''))}</span>
<p>Email: {escape(update.get('subject', ''))}</p><p>{escape(update.get('reason', 'Select the matching application.'))}</p>
<label for="{escape(select_id, quote=True)}">Match application</label>
<select class="form-input" id="{escape(select_id, quote=True)}">{''.join(options)}</select>
<div class="card-extra-actions">
<button class="btn btn-filled" onclick="assignPendingSelection({uid}, {_js_literal(select_id)}, {_js_literal(stage)})">Confirm update</button>
<button class="btn btn-tinted" onclick="openLogModalForPending({uid}, {_js_literal(company)}, {_js_literal(stage)})">Another role</button>
<button class="btn btn-ghost" onclick="dismissPendingUpdate({uid})">Dismiss</button>
</div></div>''')
    return f'<div class="section-card"><div class="section-title">Email updates to review ({len(updates)})</div>{"".join(items)}</div>'
