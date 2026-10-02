const api = globalThis.browser || globalThis.chrome;
const tracker = 'http://192.168.0.136:5000';
const supported = url => {
  const u = new URL(url);
  return u.protocol === 'https:' && (u.hostname.endsWith('.greenhouse.io') || ['jobs.lever.co','jobs.ashbyhq.com','careers.smartrecruiters.com'].includes(u.hostname));
};
function sameApplication(expected, actual) {
  try {
    const a = new URL(expected), b = new URL(actual);
    const aGreen = a.hostname.endsWith('.greenhouse.io'), bGreen = b.hostname.endsWith('.greenhouse.io');
    const identity = ['gh_jid','token','for','jobId'];
    return b.protocol === 'https:' && a.pathname === b.pathname && identity.every(key=>!a.searchParams.has(key)||a.searchParams.get(key)===b.searchParams.get(key)||(key==='gh_jid'&&a.pathname.endsWith('/'+a.searchParams.get(key)))) && (a.hostname === b.hostname || (aGreen && bGreen));
  } catch { return false; }
}
async function openJob(jobId, sender) {
  if (!sender.tab || new URL(sender.url).origin !== tracker || typeof jobId !== 'string') throw new Error('Open jobs from ApplicationTrackr.');
  const response = await fetch(tracker + '/api/autoapply/browser-bundle?job_id=' + encodeURIComponent(jobId), {cache:'no-store'});
  if (!response.ok) throw new Error('This job could not be loaded.');
  const bundle = await response.json();
  const tab = await api.tabs.create({url:bundle.job.url, active:true});
  if (!supported(bundle.job.url)) return {opened:true, notice:'This website is outside the helper’s permitted job sites. Fill this form manually.'};
  await api.storage.local.set({['job-tab-' + tab.id]: {jobId, url:bundle.job.url, expires:Date.now() + 20*60*1000}});
  // Account for a fast page completing before storage was written.
  const current = await api.tabs.get(tab.id);
  if (current.status === 'complete') await fillTab(tab.id, current.url);
  return {opened:true};
}
const filling = new Set();
async function fillTab(tabId, url) {
  if (filling.has(tabId)) return;
  const key = 'job-tab-' + tabId;
  const saved = (await api.storage.local.get(key))[key];
  if (!saved) return;
  if (saved.expires < Date.now() || !sameApplication(saved.url, url)) { await api.storage.local.remove(key); return; }
  filling.add(tabId);
  try {
    const response = await fetch(tracker + '/api/autoapply/browser-bundle?job_id=' + encodeURIComponent(saved.jobId), {cache:'no-store'});
    if (!response.ok) throw new Error('Saved details unavailable.');
    const bundle = await response.json();
    if (!sameApplication(bundle.job.url, url)) throw new Error('Application destination changed.');
    await api.scripting.executeScript({target:{tabId}, files:['fill-form.js']});
    await api.scripting.executeScript({target:{tabId}, func: data => globalThis.applicationTrackrFill(data), args:[bundle]});
    await api.storage.local.remove(key);
  } catch (error) {
    console.warn('ApplicationTrackr: form was left for manual completion.');
  } finally { filling.delete(tabId); }
}
api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== 'open-job') return false;
  openJob(message.jobId, sender).then(sendResponse, () => sendResponse({error:'Could not open and fill this job. Check the helper permissions.'}));
  return true;
});
api.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.status === 'complete' && tab.url) fillTab(tabId, tab.url);
});
api.tabs.onRemoved.addListener(tabId => api.storage.local.remove('job-tab-' + tabId));
