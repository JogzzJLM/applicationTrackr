from __future__ import annotations

import html

from autoapply.service import autopilot_candidates, status
import json
from autoapply.service import _find_job
from core.storage import DISCOVERED_JOBS_FILE, load_json_safe


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

    candidates = autopilot_candidates(10)
    candidate_rows = "".join(
        f"<tr><td>{html.escape(str(c.get('company','')))}</td><td>{html.escape(str(c.get('title','')))}</td><td><strong>{int(c.get('score',0))}%</strong></td><td>{html.escape(str(c.get('location','')))}</td></tr>"
        for c in candidates
    ) or '<tr><td colspan="4">No unattempted strong-fit jobs currently qualify.</td></tr>'

    latest_run = next((r["id"] for r in info.get("recent_runs", []) if r.get("job_id") == job_id and r.get("status") not in {"disabled", "needs_profile"}), "") if job_id else ""
    enabled = "Enabled" if info["enabled"] else "Disabled"
    submit = "Enabled" if info["auto_submit_enabled"] else "Off"
    autopilot = "Enabled" if info.get("autopilot_enabled") else "Disabled"
    learning = info.get("learning", {})

    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#ffffff"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="ApplicationTrackr"><link rel="manifest" href="/manifest.webmanifest"><title>ApplicationTrackr Apply Agent</title>
<style>
body{{font-family:Inter,system-ui,sans-serif;background:#f5f7fa;color:#111827;margin:0}}.wrap{{max-width:1100px;margin:auto;padding:28px}}.card{{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:22px;margin-bottom:18px}}h1,h2,h3{{margin-top:0}}code{{background:#f3f4f6;padding:2px 5px;border-radius:5px}}input,select,textarea{{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:8px;margin:6px 0 12px;box-sizing:border-box}}button,a.btn{{display:inline-block;background:#ff8000;color:white;border:0;border-radius:8px;padding:10px 15px;font-weight:700;text-decoration:none;cursor:pointer}}.badge{{display:inline-block;padding:4px 8px;border-radius:6px;background:#fff3e8;color:#d96c00;font-weight:700;margin:2px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #eee;text-align:left;font-size:13px}}.muted{{color:#6b7280}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}pre{{white-space:pre-wrap;background:#111827;color:#d1fae5;padding:14px;border-radius:9px;min-height:70px;overflow:auto}}.danger{{background:#991b1b}}.ok{{background:#065f46}}
</style></head><body><div class="wrap">
<a href="/jobs" class="btn" style="background:#374151">← Jobs</a><h1 style="margin-top:18px">🤖 Application Agent</h1>
<div class="card"><h2>{html.escape(title)}</h2><p>The agent now uses an online text classifier plus site-specific memory. It learns only from answers/mappings you explicitly approve, never invents unknown information, and stops on CAPTCHA or sensitive/legal questions.</p><p><span class="badge">Agent: {enabled}</span><span class="badge">Auto-submit: {submit}</span><span class="badge">Autopilot: {autopilot}</span><span class="badge">Profile: {info['profile_completeness']}%</span></p><p class="muted">ML examples: {learning.get('ml_training_examples',0)} · learned mappings: {learning.get('field_mappings',0)} · reusable answers: {learning.get('question_answers',0)} · sites learned: {learning.get('sites_learned',0)}</p></div>

<div class="grid"><div class="card"><h2>Run one application</h2><form id="run-form"><label>Job ID</label><input id="job-id" value="{html.escape(job_id)}"><label>Application URL</label><input id="url" value="{html.escape(target_url)}"><label><input id="auto-submit" type="checkbox" style="width:auto"> Request final submission</label><br><br><button type="submit">Start agent</button><button type="button" id="inspect-form" style="margin-left:8px">Check form only</button></form><pre id="result">Idle.</pre>{('<p class="muted">Why this job ranked: '+html.escape(' · '.join(reasons[:4]))+'</p>') if reasons else ''}</div>
<div class="card"><h2>Autopilot queue</h2><p>Autopilot only considers unattempted jobs scoring at least <strong>{info.get('autopilot_min_score',82)}%</strong>. It obeys a hard daily cap of <strong>{info.get('daily_limit',3)}</strong> and stops the batch when a page needs human review.</p><p><strong>{info.get('candidate_count',0)}</strong> candidates · {info.get('attempts_today',0)} attempts today.</p><form id="batch-form"><label>Jobs to try this batch</label><input id="batch-limit" type="number" min="1" max="20" value="{min(3, info.get('daily_limit',3))}"><label><input id="batch-submit" type="checkbox" style="width:auto"> Request final submission where allowed</label><br><br><button type="submit" class="ok">Start autopilot batch</button></form><pre id="batch-result">Idle.</pre></div></div>

<div class="card" id="manual-panel"><h2>Job context and your answers</h2><p>Choose “Check form only” to read this listing and its questions. Write your own answers below, save them, then start the agent. Saved application answers are reused for this listing.</p><a id="listing-link" target="_blank" rel="noopener" hidden>Open original listing</a><details><summary>Read the full listing and application context</summary><pre id="listing-context" style="background:#f9fafb;color:#111827;max-height:600px">No form checked yet.</pre></details><form id="manual-answer-form"><div id="manual-question-list"></div><button type="submit" id="save-manual-answers" hidden>Save my answers</button></form><p id="manual-answer-status" aria-live="polite"></p></div>
<div class="card"><h2>Next strong-fit jobs</h2><table><thead><tr><th>Company</th><th>Role</th><th>Fit</th><th>Location</th></tr></thead><tbody>{candidate_rows}</tbody></table></div>

<div class="card"><h2>Teach the learner</h2><div class="grid"><form id="map-form"><h3>Field mapping</h3><p class="muted">Example: “Candidate email address” → <code>personal.email</code>. Optional domain/context makes site-specific fields easier to classify.</p><label>Field label</label><input id="map-label" required><label>Profile key</label><input id="map-key" placeholder="personal.email" required><label>Domain (optional)</label><input id="map-domain" placeholder="boards.greenhouse.io"><label>Nearby context (optional)</label><textarea id="map-context"></textarea><button type="submit">Learn mapping</button><pre id="map-result">Ready.</pre></form>
<form id="answer-form"><h3>Reusable answer</h3><p class="muted">Use this for questions you are happy to reuse. Sensitive/attestation fields will still stop for review even if an answer exists.</p><label>Question</label><input id="answer-question" required><label>Answer</label><textarea id="answer-value" required></textarea><label>Domain (optional)</label><input id="answer-domain" placeholder="jobs.ashbyhq.com"><button type="submit">Learn answer</button><pre id="answer-result">Ready.</pre></form></div></div>

<div class="card"><h2>Recent runs</h2><table><thead><tr><th>ID</th><th>Status</th><th>URL</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="card"><h2>Upload application documents</h2><p>Upload your CV here. It is saved in ApplicationTrackr for future applications. PDF, DOC or DOCX, up to 10 MB.</p><form id="document-form"><label>Document</label><select id="document-kind"><option value="resume">CV / résumé</option><option value="cover_letter">Cover letter</option></select><input id="document-file" type="file" accept=".pdf,.doc,.docx" required><button type="submit">Upload and save</button></form><pre id="document-result">No upload selected.</pre></div>
<div class="card"><h2>Private data</h2><p>Profile: <code>{html.escape(info['profile_path'])}</code>. CV default: <code>/data/autoapply/resume.pdf</code>. These remain in the persistent Docker volume and are not committed to GitHub.</p></div></div>
<script>
async function post(url, values){{const body=new URLSearchParams(values);const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});return await r.json();}}
document.getElementById('document-form').addEventListener('submit',async e=>{{e.preventDefault();const data=new FormData();data.append('kind',document.getElementById('document-kind').value);data.append('file',document.getElementById('document-file').files[0]);const r=await fetch('/api/autoapply/document',{{method:'POST',body:data}});const j=await r.json();document.getElementById('document-result').textContent=j.status==='ok'?'Saved. Your document is ready for application forms.':j.message;}});
let questionRun='', questionFields=[];
async function showQuestions(runId){{
 const r=await fetch('/api/autoapply/questions?id='+encodeURIComponent(runId));const plan=await r.json();
 if(!r.ok || ['queued','running'].includes(plan.status))return;
 questionRun=runId;questionFields=plan.questions||[];
 document.getElementById('listing-context').textContent=plan.context||'Open the original listing to read its details.';
 const link=document.getElementById('listing-link');link.href=plan.url;link.hidden=!plan.url;link.textContent='Open '+(plan.company||'original')+' listing';
 const container=document.getElementById('manual-question-list');container.replaceChildren();
 questionFields.forEach((q,index)=>{{const block=document.createElement('div');const label=document.createElement('label');label.textContent=q.label+(q.required?' (required)':'');block.append(label);
 if(q.type==='file'){{const help=document.createElement('p');help.textContent='Use Upload application documents below. The agent will use the saved document.';block.append(help);}}
 else{{let input;if((q.options||[]).length){{input=document.createElement('select');const empty=document.createElement('option');empty.value='';empty.textContent='Choose an answer';input.append(empty);q.options.forEach(v=>{{const o=document.createElement('option');o.value=v;o.textContent=v;input.append(o)}});}}else{{input=document.createElement('textarea');input.rows=q.type==='textarea'?5:2;}}
 input.id='manual-answer-'+index;input.value=q.answer||'';label.htmlFor=input.id;block.append(input);}}
 container.append(block);}});
 document.getElementById('save-manual-answers').hidden=!questionFields.length;
 document.getElementById('manual-answer-status').textContent=questionFields.length?'Write or edit answers, then save. Blank answers remain unanswered.':'No questions found on this page.';
}}
document.getElementById('manual-answer-form').addEventListener('submit',async e=>{{
 e.preventDefault();const message=document.getElementById('manual-answer-status');message.textContent='Saving…';
 try{{const r=await fetch('/api/autoapply/run?id='+encodeURIComponent(questionRun));const run=await r.json();let saved=0;
 for(let i=0;i<questionFields.length;i++){{const input=document.getElementById('manual-answer-'+i);if(!input||!input.value.trim())continue;const q=questionFields[i];const answer=input.value.trim();
 const result=await post('/api/autoapply/learn-answer',{{question:q.label,answer,domain:run.url}});if(result.status!=='ok')throw new Error(result.message||'Could not save answer');saved++;}}
 message.textContent='Saved '+saved+' answers for this application. You can now start the agent.';
 }}catch(err){{message.textContent=err.message;}}
}});
const out=document.getElementById('result');
let shownRun='';new MutationObserver(()=>{{try{{const run=JSON.parse(out.textContent);if(run.status&&!['queued','running'].includes(run.status)&&run.created_at&&shownRun!==String(run.created_at)){{shownRun=String(run.created_at);const match=out.dataset.runId;if(match)showQuestions(match);}}}}catch(err){{}}}}).observe(out,{{childList:true}});
const initialQuestionRun={json.dumps(latest_run)};if(initialQuestionRun)showQuestions(initialQuestionRun);
document.getElementById('inspect-form').addEventListener('click',async()=>{{const j=await post('/api/autoapply/inspect',{{job_id:document.getElementById('job-id').value,url:document.getElementById('url').value}});out.textContent=JSON.stringify(j,null,2);if(j.run_id){{out.dataset.runId=j.run_id;const p=setInterval(async()=>{{const r=await fetch('/api/autoapply/run?id='+encodeURIComponent(j.run_id));const run=await r.json();out.textContent=JSON.stringify(run,null,2);if(!['queued','running'].includes(run.status))clearInterval(p)}},1500)}}}});document.getElementById('run-form').addEventListener('submit',async e=>{{e.preventDefault();const j=await post('/api/autoapply/run',{{job_id:document.getElementById('job-id').value,url:document.getElementById('url').value,auto_submit:document.getElementById('auto-submit').checked?'true':'false'}});out.textContent=JSON.stringify(j,null,2);if(j.run_id){{out.dataset.runId=j.run_id;const p=setInterval(async()=>{{const rr=await fetch('/api/autoapply/run?id='+encodeURIComponent(j.run_id));const jj=await rr.json();out.textContent=JSON.stringify(jj,null,2);if(!['queued','running'].includes(jj.status))clearInterval(p)}},1500)}}}});
const bout=document.getElementById('batch-result');document.getElementById('batch-form').addEventListener('submit',async e=>{{e.preventDefault();const j=await post('/api/autoapply/autopilot',{{limit:document.getElementById('batch-limit').value,auto_submit:document.getElementById('batch-submit').checked?'true':'false'}});bout.textContent=JSON.stringify(j,null,2);if(j.batch_id){{const p=setInterval(async()=>{{const rr=await fetch('/api/autoapply/batch?id='+encodeURIComponent(j.batch_id));const jj=await rr.json();bout.textContent=JSON.stringify(jj,null,2);if(!['queued','running'].includes(jj.status))clearInterval(p)}},1800)}}}});
document.getElementById('map-form').addEventListener('submit',async e=>{{e.preventDefault();const j=await post('/api/autoapply/learn-mapping',{{label:document.getElementById('map-label').value,profile_key:document.getElementById('map-key').value,domain:document.getElementById('map-domain').value,context:document.getElementById('map-context').value}});document.getElementById('map-result').textContent=JSON.stringify(j,null,2);}});
document.getElementById('answer-form').addEventListener('submit',async e=>{{e.preventDefault();const j=await post('/api/autoapply/learn-answer',{{question:document.getElementById('answer-question').value,answer:document.getElementById('answer-value').value,domain:document.getElementById('answer-domain').value}});document.getElementById('answer-result').textContent=JSON.stringify(j,null,2);}});
</script></body></html>'''
