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
function supportsApplication(url) {
    try {
        const target = new URL(url);
        return target.protocol === 'https:' && (target.hostname.endsWith('.greenhouse.io') || ['jobs.lever.co', 'jobs.ashbyhq.com', 'careers.smartrecruiters.com', 'jobs.smartrecruiters.com'].includes(target.hostname));
    } catch (_) { return false; }
}
function canFillApplication(url) {
    return document.documentElement.dataset.applicationtrackrBrowserHelper === 'ready' && supportsApplication(url);
}
function updateFillActions() {
    const status = document.getElementById('autofill-status');
    if (status) {
        const ready = document.documentElement.dataset.applicationtrackrBrowserHelper === 'ready';
        const mac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform || navigator.userAgent || '');
        status.hidden = !ready && !mac;
        status.textContent = ready ? 'Mac autofill connected · Open & fill appears on compatible forms.' : 'Mac autofill disconnected · Enable the Safari helper, then reload.';
    }
    document.querySelectorAll('.browser-fill-action').forEach(button => {
        const ready = canFillApplication(button.closest('.job-card').querySelector('.card-primary-action')?.href);
        const mac = typeof navigator !== 'undefined' && /Mac/.test(navigator.platform || navigator.userAgent || '');
        button.hidden = !supportsApplication(button.closest('.job-card').querySelector('.card-primary-action')?.href) || (!ready && !mac);
        button.disabled = !ready;
        button.textContent = 'Open & fill ↗';
        button.title = ready ? 'Open the employer form and fill known answers. You finish and submit it.' : 'Safari helper disconnected. Enable it in Safari Settings → Extensions, then reload.';
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
    const visibilityAtStart = visibilityRevision;
    try {
        const response = await fetch(location.pathname, {cache:'no-store'});
        if (!response.ok) return;
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (visibilityAtStart !== visibilityRevision) return;
        let applicationsChanged = false;
        for (const selector of ['#applications-grid', '#pending-email-updates', '#application-count', '#application-distribution', '#jobs-container', '#closing-soon-section', '.hero-stats-grid', '.status-pill']) {
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
        updateHiddenCount();
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

// Descriptions are fetched only when needed; background refresh preserves open cards.
document.addEventListener('toggle', async event => {
    if (!event.target.matches('details') || !event.target.open) return;
    const text = event.target.querySelector('.listing-description[data-description-id]');
    if (!text || text.dataset.loaded) return;
    text.textContent = 'Loading job description…';
    try {
        const response = await fetch('/api/listing-details?id=' + encodeURIComponent(text.dataset.descriptionId));
        const data = await response.json();
        if (!response.ok) throw new Error('Could not load description.');
        text.textContent = data.description || 'Open the employer page for the full job description.';
        text.dataset.loaded = 'true';
    } catch (_) { text.textContent = 'Could not load details. Close and reopen this section to retry, or open the employer listing.'; }
}, true);

// A manual visibility choice only affects discovered listings, never Sheet history.
let visibilityRevision = 0;
async function changeListingsVisibility(ids, hidden) {
    const body = new URLSearchParams();
    ids.forEach(id => body.append('id', id));
    const response = await fetch(hidden ? '/api/hide-job' : '/api/unhide-job', {method:'POST', body});
    const result = await response.json();
    if (!response.ok || result.status !== 'ok') throw new Error(result.message || 'Could not save your choice.');
    visibilityRevision++;
    const changed = new Set(result.ids);
    document.querySelectorAll('#jobs-container .job-card').forEach(card => {
        if (!changed.has(card.dataset.id)) return;
        card.dataset.status = hidden ? 'hidden' : card.dataset.originalStatus;
        if (hidden) card.querySelectorAll('details').forEach(details => details.open = false);
        const button = card.querySelector('.hide-listing-button');
        if (button) {
            button.textContent = hidden ? 'Restore listing' : 'Hide listing';
            button.onclick = () => setListingHidden(card.dataset.id, !hidden, button);
        }
    });
    document.querySelectorAll('[data-closing-id]').forEach(card => { if (hidden && changed.has(card.dataset.closingId)) card.remove(); });
    const closing = document.querySelector('#closing-soon-section');
    if (closing && hidden && !closing.querySelector('[data-closing-id]')) closing.querySelector('.closing-caption').textContent = 'No remaining deadlines in this panel. Hidden listings are excluded; refresh to check the next deadlines.';
    filterJobs(false);
    updateHiddenCount();
    showNotice(`${result.ids.length} listing${result.ids.length === 1 ? '' : 's'} ${hidden ? 'hidden. Restore any time using the Hidden listings filter.' : 'restored.'}`);
    const undo = document.createElement('button');
    undo.className = 'btn btn-tinted'; undo.textContent = 'Undo'; undo.type = 'button';
    undo.onclick = async () => { undo.disabled = true; try { await changeListingsVisibility(result.ids, !hidden); } catch (error) { showNotice(error.message); } };
    document.getElementById('app-notice').append(' ', undo);
    void refreshDashboard();
}
async function setListingHidden(id, hidden, button) {
    if (button) button.disabled = true;
    try { await changeListingsVisibility([id], hidden); }
    catch (error) { showNotice(error.message); }
    finally { if (button) button.disabled = false; }
}
function hideCompanyListings(company) {
    const cards = [...document.querySelectorAll('#jobs-container .job-card')].filter(card => card.dataset.company === company && card.dataset.status === 'not_applied');
    if (!cards.length) { showNotice('No visible, unapplied listings for this company.'); return; }
    if (!window.confirm(`Hide all ${cards.length} currently unapplied listings for ${cards[0].querySelector('.card-company').textContent}? You can restore them using the Hidden listings filter. Future listings are not automatically hidden.`)) return;
    changeListingsVisibility(cards.map(card => card.dataset.id), true).catch(error => showNotice(error.message));
}
function updateHiddenCount() {
    const option = document.querySelector('#job-status option[value="hidden"]');
    if (option) option.textContent = `Hidden listings (${document.querySelectorAll('#jobs-container .job-card[data-status="hidden"]').length})`;
}
document.addEventListener('DOMContentLoaded', updateHiddenCount);
