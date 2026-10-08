/* Responsive browsing and direct Mac browser-assisted filling. */
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
        button.hidden = !ready;
        button.textContent = 'Open & fill ↗';
        button.title = 'Open the employer form and fill known answers. You finish and submit it.';
    });
}
function openFilledApplication(jobId, url) {
    if (canFillApplication(url)) {
        window.postMessage({type: 'applicationtrackr:open-job', jobId}, location.origin);
        showNotice('Opening the application with your saved details…');
    } else {
        showNotice('The filling helper is unavailable for this page. Use Open listing to apply.');
    }
}
window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.type !== 'applicationtrackr:job-opened') return;
    if (event.data.error || event.data.notice) showNotice(event.data.error || event.data.notice);
});
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
        let applicationsChanged = false;
        for (const selector of ['#applications-grid', '#pending-email-updates', '#application-count', '#application-distribution', '#jobs-container', '.hero-stats-grid', '.status-pill']) {
            const current = document.querySelector(selector), updated = page.querySelector(selector);
            if (current && updated && current.innerHTML !== updated.innerHTML) {
                if (selector === '#applications-grid') applicationsChanged = true;
                if (selector === '.status-pill') current.title = updated.title;
                current.innerHTML = updated.innerHTML;
            }
        }
        filterApplications();
        if (typeof sortJobs === 'function') sortJobs(false);
        if (typeof filterJobs === 'function') filterJobs(false);
        updateFillActions();
        if (applicationsChanged && typeof reloadSankeyIframe === 'function') reloadSankeyIframe();
    } catch (_) { /* Keep the displayed data during a temporary connection failure. */ }
    finally { dashboardRefreshRunning = false; }
}
setInterval(refreshDashboard, 90000);
function assignPendingSelection(id, selectId, stage) {
    const value = document.getElementById(selectId)?.value;
    if (!value) { showNotice('Choose the company and role before confirming.'); return; }
    const choice = JSON.parse(value);
    resolvePendingUpdate(id, choice.company, choice.role, stage);
}
