/* Responsive browsing and an honest fallback when a browser helper is unavailable. */
let jobPage = 1;
const jobsPerPage = 12;
let toastTimer;
function renderJobPage(reset = true) {
    if (reset) jobPage = 1;
    const cards = [...document.querySelectorAll('#jobs-container .job-card')];
    const matches = cards.filter(card => card.dataset.matches === 'true');
    const pages = Math.max(1, Math.ceil(matches.length / jobsPerPage));
    jobPage = Math.max(1, Math.min(jobPage, pages));
    cards.forEach(card => card.style.display = 'none');
    const start = (jobPage - 1) * jobsPerPage;
    matches.slice(start, start + jobsPerPage).forEach(card => card.style.display = 'flex');
    document.getElementById('results-summary').textContent = matches.length
        ? `${start + 1}–${Math.min(start + jobsPerPage, matches.length)} of ${matches.length} matching jobs`
        : 'No matching jobs';
    document.getElementById('jobs-page-label').textContent = `Page ${jobPage} of ${pages}`;
    document.getElementById('jobs-prev').disabled = jobPage === 1;
    document.getElementById('jobs-next').disabled = jobPage === pages;
    document.getElementById('jobs-empty').hidden = matches.length !== 0;
}
function changeJobPage(delta) {
    jobPage += delta;
    renderJobPage(false);
    document.getElementById('view-jobs').scrollIntoView({block: 'start'});
}
function resetJobFilters() {
    document.getElementById('job-search').value = '';
    document.getElementById('job-status').value = 'not_applied';
    filterProgram('all', document.querySelector('[data-prog-chip="all"]'));
    filterDomain('all', document.querySelector('[data-dom-chip="all"]'));
}
function showNotice(message) {
    let toast = document.getElementById('app-notice');
    if (!toast) {
        toast = document.createElement('div');
        toast.id = 'app-notice'; toast.className = 'toast';
        toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite');
        document.body.append(toast);
    }
    const host = document.querySelector('dialog[open]') || document.body;
    host.append(toast);
    toast.textContent = message; toast.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.hidden = true, 6000);
}
function canFillApplication(url) {
    if (document.documentElement.dataset.applicationtrackrBrowserHelper !== 'ready') return false;
    try {
        const target = new URL(url);
        return target.protocol === 'https:' && (target.hostname.endsWith('.greenhouse.io') || ['jobs.lever.co', 'jobs.ashbyhq.com', 'careers.smartrecruiters.com'].includes(target.hostname));
    } catch (_) { return false; }
}
function updateFillActions() {
    document.querySelectorAll('.browser-fill-action').forEach(button => {
        const ready = canFillApplication(button.closest('.job-card').querySelector('.card-primary-action')?.href);
        button.textContent = ready ? 'Open & fill ↗' : 'Saved details';
        button.title = ready ? 'Open the application and fill known answers. You submit it.' : 'Copy saved answers and download your CV. Automatic filling needs a browser helper.';
    });
}
function openFilledApplication(jobId, url) {
    if (canFillApplication(url)) {
        window.postMessage({type: 'applicationtrackr:open-job', jobId}, location.origin);
        showNotice('Opening the application with your saved details…');
    } else {
        openApplicationDetails(jobId, url);
    }
}
window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.type !== 'applicationtrackr:job-opened') return;
    if (event.data.error || event.data.notice) showNotice(event.data.error || event.data.notice);
});
function assistField(label, value) {
    const row = document.createElement('div'); row.className = 'assist-field';
    const wrap = document.createElement('div');
    const caption = document.createElement('label'); caption.textContent = label;
    const input = document.createElement(String(value).length > 100 ? 'textarea' : 'input');
    input.readOnly = true; input.value = String(value);
    input.id = `assist-field-${document.querySelectorAll('.assist-field').length}`;
    caption.htmlFor = input.id; wrap.append(caption, input);
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'btn btn-tinted'; button.textContent = 'Copy';
    button.setAttribute('aria-label', `Copy ${label}`);
    button.onclick = async () => {
        try {
            if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(input.value);
            else {
                // The home-server HTTP address is not a secure Clipboard API context.
                input.focus(); input.select(); input.setSelectionRange(0, input.value.length);
                if (!document.execCommand('copy')) throw new Error('Copy unavailable');
            }
            showNotice(`${label} copied`);
        } catch (_) { input.focus(); input.select(); showNotice('Press and hold the selected answer, then choose Copy.'); }
    };
    row.append(wrap, button); return row;
}
let assistRequest = 0;
async function openApplicationDetails(jobId, url) {
    const requestId = ++assistRequest;
    let dialog = document.getElementById('application-assist');
    if (!dialog) {
        dialog = document.createElement('dialog'); dialog.id = 'application-assist'; dialog.className = 'assist-dialog';
        dialog.setAttribute('aria-labelledby', 'assist-title');
        dialog.addEventListener('close', () => { document.body.style.overflow = ''; window.scrollTo(0, dialog.returnScroll || 0); });
        document.body.append(dialog);
    }
    dialog.replaceChildren();
    const head = document.createElement('div'); head.className = 'assist-head';
    const title = document.createElement('h2'); title.id = 'assist-title'; title.textContent = 'Your application details';
    const close = document.createElement('button'); close.className = 'btn btn-ghost'; close.textContent = '✕'; close.setAttribute('aria-label', 'Close saved details'); close.onclick = () => dialog.close();
    head.append(title, close); dialog.append(head);
    const intro = document.createElement('p'); intro.className = 'assist-intro';
    intro.textContent = (document.documentElement.dataset.applicationtrackrBrowserHelper === 'ready' ? 'This job site is outside your helper’s supported sites. ' : 'This browser has no filling helper. ') + 'Open the listing, copy saved answers and upload your CV. You complete unknown answers and submit the application.';
    const footer = document.createElement('div'); footer.className = 'assist-footer';
    const application = document.createElement('a'); application.className = 'btn btn-filled'; application.textContent = 'Open application ↗'; application.target = '_blank'; application.rel = 'noopener noreferrer';
    // Only links from the trusted job feed, never profile data in a URL.
    const target = new URL(url, location.origin);
    if (['http:', 'https:'].includes(target.protocol)) application.href = target.href;
    const edit = document.createElement('a'); edit.className = 'btn btn-ghost'; edit.textContent = 'Edit saved details'; edit.href = '/profile';
    footer.append(application, edit);
    const fields = document.createElement('div'); fields.textContent = 'Loading saved answers…';
    dialog.returnScroll = window.scrollY;
    document.body.style.overflow = 'hidden';
    dialog.append(intro, footer, fields); dialog.showModal();
    try {
        const response = await fetch('/api/autoapply/browser-bundle?include_documents=false&job_id=' + encodeURIComponent(jobId), {cache: 'no-store'});
        const bundle = await response.json();
        if (!response.ok) throw new Error(bundle.message || 'Could not load saved details.');
        if (requestId !== assistRequest || !dialog.open) return;
        title.textContent = bundle.job.company;
        const role = document.createElement('p'); role.className = 'assist-intro'; role.textContent = bundle.job.title; head.after(role);
        fields.replaceChildren();
        const labels = {'personal.first_name': 'First name', 'personal.middle_names': 'Middle names', 'personal.last_name': 'Last name', 'personal.preferred_name': 'Preferred name', 'personal.email': 'Email', 'personal.phone': 'Phone', 'personal.city': 'City', 'personal.country': 'Country', 'personal.address_line1': 'Address', 'personal.address_line2': 'Address line 2', 'personal.postcode': 'Postcode', 'education.university': 'University', 'education.degree': 'Degree', 'education.course': 'Course', 'education.graduation_date': 'Graduation date', 'education.graduation_year': 'Graduation year', 'education.grade': 'Grade', 'links.linkedin': 'LinkedIn', 'links.github': 'GitHub', 'links.portfolio': 'Portfolio', 'eligibility.right_to_work_uk': 'UK work permission', 'eligibility.requires_sponsorship': 'Sponsorship requirement', 'employment.previous_employers': 'Previous employers'};
        Object.entries(bundle.profile).forEach(([key, value]) => { if (labels[key] && String(value || '').trim()) fields.append(assistField(labels[key], value)); });
        Object.entries(bundle.answers || {}).forEach(([label, value]) => fields.append(assistField(label, value)));
        if (!fields.children.length) fields.textContent = 'No saved answers yet. Add your details using “Edit saved details”.';
        const docs = document.createElement('div'); docs.className = 'assist-documents';
        (bundle.documents || []).forEach(doc => {
            const link = document.createElement('a'); link.className = 'btn btn-tinted'; link.textContent = `Download ${doc.name}`;
            link.href = '/api/autoapply/document?kind=' + encodeURIComponent(doc.key.split('.').pop()); link.setAttribute('download', doc.name); docs.append(link);
        });
        fields.prepend(docs);
    } catch (error) { if (requestId === assistRequest) fields.textContent = error.message; }
}
document.addEventListener('DOMContentLoaded', () => {
    updateFillActions();
    new MutationObserver(updateFillActions).observe(document.documentElement, {attributes: true, attributeFilter: ['data-applicationtrackr-browser-helper']});
    document.querySelector('.header-menu').addEventListener('click', event => { if (event.target.closest('button, a')) event.currentTarget.open = false; });
    document.addEventListener('click', event => { const menu = document.querySelector('.header-menu'); if (!menu.contains(event.target)) menu.open = false; });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') { document.querySelector('.header-menu').open = false; document.querySelectorAll('.modal-backdrop').forEach(modal => modal.style.display = 'none'); }
    });
    // Legacy edit dialogs need the same keyboard containment as the native details dialog.
    document.querySelectorAll('.form-group').forEach((group, index) => {
        const label = group.querySelector('label'), field = group.querySelector('input, select, textarea');
        if (label && field && !label.htmlFor) { field.id ||= `form-field-${index}`; label.htmlFor = field.id; }
    });
    document.querySelectorAll('.modal-backdrop').forEach(modal => {
        modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); modal.setAttribute('aria-label', 'Edit application details');
        let returnFocus;
        new MutationObserver(() => {
            if (modal.style.display !== 'none') { returnFocus = document.activeElement; document.body.style.overflow = 'hidden'; modal.querySelector('input, button, textarea')?.focus(); }
            else { document.body.style.overflow = ''; (returnFocus?.getClientRects().length ? returnFocus : document.querySelector('.header-menu summary'))?.focus(); }
        }).observe(modal, {attributes: true, attributeFilter: ['style']});
        modal.addEventListener('click', event => { if (event.target === modal) modal.style.display = 'none'; });
        modal.addEventListener('keydown', event => {
            if (event.key !== 'Tab') return;
            const focusable = [...modal.querySelectorAll('input, select, textarea, button, a[href]')].filter(el => !el.disabled && el.getClientRects().length);
            const first = focusable[0], last = focusable.at(-1);
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        });
    });
});
function filterApplications() {
    const search = document.getElementById('application-search');
    const status = document.getElementById('application-status');
    if (!search || !status) return;
    const query = search.value.trim().toLowerCase();
    let count = 0;
    document.querySelectorAll('#applications-grid .application-card').forEach(card => {
        const visible = (status.value === 'all' || card.dataset.status === status.value) && card.dataset.search.includes(query);
        card.hidden = !visible; count += visible;
    });
    document.getElementById('application-results').textContent = `${count} matching application${count === 1 ? '' : 's'}`;
    localStorage.setItem('applicationtrackr:application-filter', JSON.stringify({search: search.value, status: status.value}));
}
document.addEventListener('DOMContentLoaded', () => {
    try {
        const saved = JSON.parse(localStorage.getItem('applicationtrackr:application-filter') || '{}');
        const search = document.getElementById('application-search'), status = document.getElementById('application-status');
        if (search) search.value = saved.search || '';
        if (status && [...status.options].some(o => o.value === saved.status)) status.value = saved.status;
    } catch (_) {}
    filterApplications();
});
// Refresh cards and totals after background email/Sheet updates without losing filters.
let dashboardRefreshRunning = false;
async function refreshDashboard() {
    if (dashboardRefreshRunning || document.hidden || document.querySelector('dialog[open]') || [...document.querySelectorAll('.modal-backdrop')].some(m => m.style.display !== 'none') || ['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName) || document.querySelector('#jobs-container details[open]')) return;
    dashboardRefreshRunning = true;
    try {
        const response = await fetch(location.pathname, {cache:'no-store'});
        if (!response.ok) return;
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        for (const selector of ['#applications-grid', '#jobs-container', '.hero-stats-grid', '.status-pill']) {
            const current = document.querySelector(selector), updated = page.querySelector(selector);
            if (current && updated && current.innerHTML !== updated.innerHTML) {
                if (selector === '.status-pill') current.title = updated.title;
                current.innerHTML = updated.innerHTML;
            }
        }
        filterApplications();
        if (typeof sortJobs === 'function') sortJobs();
        if (typeof filterJobs === 'function') filterJobs(false);
        updateFillActions();
    } catch (_) { /* Keep the displayed data during a temporary connection failure. */ }
    finally { dashboardRefreshRunning = false; }
}
setInterval(refreshDashboard, 90000);
