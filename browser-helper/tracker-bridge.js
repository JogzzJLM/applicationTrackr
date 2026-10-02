(() => {
  // Safari match patterns do not support ports; restrict the bridge explicitly.
  if (location.origin !== 'http://192.168.0.136:5000') return;
  const api = globalThis.browser || globalThis.chrome;
  document.documentElement.dataset.applicationtrackrBrowserHelper = 'ready';
  window.addEventListener('message', async event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.type !== 'applicationtrackr:open-job') return;
    try {
      const response = await api.runtime.sendMessage({type: 'open-job', jobId: event.data.jobId});
      window.postMessage({type: 'applicationtrackr:job-opened', ...response}, location.origin);
    } catch (error) {
      window.postMessage({type: 'applicationtrackr:job-opened', error: 'Browser helper could not open this job. Check the extension permissions.'}, location.origin);
    }
  });
})();
