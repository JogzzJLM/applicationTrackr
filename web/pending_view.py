"""Escape email content and generate reliable assignment actions."""
from html import escape
from web.components import _js_literal


def render_pending_updates(updates):
    if not updates:
        return ''
    items = []
    for update in updates:
        uid = _js_literal(update.get('id', ''))
        company = update.get('company', 'Company')
        stage = update.get('stage', 'Application Update')
        buttons = []
        for option in update.get('options', []):
            role = option.get('role', '')
            buttons.append(f'<button class="btn btn-filled" style="margin:4px" onclick="resolvePendingUpdate({uid}, {_js_literal(option.get("company", company))}, {_js_literal(role)}, {_js_literal(stage)})">Assign to: {escape(role)}</button>')
        buttons.append(f'<button class="btn btn-tinted" style="margin:4px" onclick="openLogModalForPending({uid}, {_js_literal(company)}, {_js_literal(stage)})">Assign another role</button>')
        buttons.append(f'<button class="btn btn-ghost" onclick="dismissPendingUpdate({uid})">Dismiss</button>')
        items.append(f'''<div style="background:#fff;border:1px solid var(--papaya);border-radius:12px;padding:16px;margin-top:12px">
<strong>{escape(company)}</strong> · {escape(stage)} <span>{escape(update.get('date_received', ''))}</span>
<p>Email: {escape(update.get('subject', ''))}</p><p>{escape(update.get('reason', 'Select the matching application.'))}</p>
<div>{''.join(buttons)}</div></div>''')
    return f'<div class="section-card"><div class="section-title">Email updates to assign ({len(updates)})</div>{"".join(items)}</div>'
