from __future__ import annotations

import html

from autoapply.service import status
from core.storage import DISCOVERED_JOBS_FILE, load_json_safe


def _find_job(job_id):
    jobs = load_json_safe(DISCOVERED_JOBS_FILE, [])
    for job in jobs if isinstance(jobs, list) else []:
        if str(job.get("id")) == str(job_id):
            return job
    return None


def render_autoapply_html(job_id=""):
    info = status()
    job = _find_job(job_id) if job_id else None
    title = f"{job.get('company')} — {job.get('title')}" if job else "Application Agent"
    target_url = (job or {}).get("link", "")
    reasons = (job or {}).get("match_reasons", [])
    rows = "".join(
        f"<tr><td><code>{html.escape(r.get('id',''))}</code></td><td>{html.escape(str(r.get('status','')))}</td><td>{html.escape(str(r.get('url',''))[:80])}</td></tr>"
        for r in info.get("recent_runs", [])
    ) or '<tr><td colspan="3">No runs yet.</td></tr>'
    enabled = "Enabled" if info["enabled"] else "Disabled"
    submit = "Enabled" if info["auto_submit_enabled"] else "Off (recommended while training)"
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ApplicationTrackr Apply Agent</title>
<style>body{{font-family:Inter,system-ui,sans-serif;background:#f5f7fa;color:#111827;margin:0}}.wrap{{max-width:1050px;margin:auto;padding:28px}}.card{{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:22px;margin-bottom:18px}}h1,h2{{margin-top:0}}code{{background:#f3f4f6;padding:2px 5px;border-radius:5px}}input{{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:8px;margin:6px 0 12px}}button,a.btn{{display:inline-block;background:#ff8000;color:white;border:0;border-radius:8px;padding:10px 15px;font-weight:700;text-decoration:none;cursor:pointer}}.badge{{display:inline-block;padding:4px 8px;border-radius:6px;background:#fff3e8;color:#d96c00;font-weight:700}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:13px}}.muted{{color:#6b7280}}</style></head><body><div class="wrap">
<a href="/jobs" class="btn" style="background:#374151">← Jobs</a><h1 style="margin-top:18px">🤖 Application Agent</h1>
<div class="card"><h2>{html.escape(title)}</h2><p>This adaptive browser agent learns field mappings and reusable answers from explicit corrections. It never invents unknown answers or bypasses CAPTCHA.</p><p><span class="badge">Agent: {enabled}</span> <span class="badge">Auto-submit: {submit}</span> <span class="badge">Profile: {info['profile_completeness']}%</span></p><p class="muted">Private profile: <code>{html.escape(info['profile_path'])}</code> · learned mappings: {info['learning']['field_mappings']} · answers: {info['learning']['question_answers']}</p></div>
<div class="card"><h2>Run one application</h2><form id="run-form"><label>Job ID</label><input id="job-id" value="{html.escape(job_id)}"><label>Application URL</label><input id="url" value="{html.escape(target_url)}"><label><input id="auto-submit" type="checkbox" style="width:auto"> Request final submission (requires server opt-in and zero review blockers)</label><br><br><button type="submit">Start agent</button></form><pre id="result" style="white-space:pre-wrap;background:#111827;color:#d1fae5;padding:14px;border-radius:9px;min-height:80px;margin-top:14px">Idle.</pre>{('<p class="muted">Why this job ranked: '+html.escape(' · '.join(reasons[:4]))+'</p>') if reasons else ''}</div>
<div class="card"><h2>Recent runs</h2><table><thead><tr><th>ID</th><th>Status</th><th>URL</th></tr></thead><tbody>{rows}</tbody></table></div><div class="card"><h2>Training</h2><p>Fill <code>/data/autoapply/applicant_profile.json</code> and place your CV at <code>/data/autoapply/resume.pdf</code>. Unknown or sensitive questions stop for review; approved answers can then be learned for next time.</p></div></div>
<script>const out=document.getElementById('result');document.getElementById('run-form').addEventListener('submit',async e=>{{e.preventDefault();const body=new URLSearchParams({{job_id:document.getElementById('job-id').value,url:document.getElementById('url').value,auto_submit:document.getElementById('auto-submit').checked?'true':'false'}});const r=await fetch('/api/autoapply/run',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});const j=await r.json();out.textContent=JSON.stringify(j,null,2);if(j.run_id){{const p=setInterval(async()=>{{const rr=await fetch('/api/autoapply/run?id='+encodeURIComponent(j.run_id));const jj=await rr.json();out.textContent=JSON.stringify(jj,null,2);if(!['queued','running'].includes(jj.status))clearInterval(p)}},1500)}}}});</script></body></html>'''
