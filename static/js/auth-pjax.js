(function () {
  const containerSel = '#authView';
  const TRANSITION_MS = 160;

  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.auth-nav');
    if (!a) return;
    const url = a.getAttribute('href');
    if (!url) return;

    const sameOrigin = url.startsWith('/') || url.startsWith(location.origin);
    if (!sameOrigin) return;

    e.preventDefault();
    loadAuth(url, /* push */ true);
  });

  window.addEventListener('popstate', () => {
    loadAuth(location.href, /* push */ false);
  });

  async function loadAuth(url, push) {
    const container = document.querySelector(containerSel);
    if (!container) return;

    container.classList.add('is-leaving');
    await wait(TRANSITION_MS);

    try {
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const html = await res.text();

      const next = extractContainer(html, containerSel);
      if (!next) throw new Error('Container not found in response');

      container.innerHTML = next.innerHTML;

      container.classList.remove('is-leaving');
      container.classList.add('is-entering');
      await wait(4);
      container.classList.remove('is-entering');
      container.classList.add('is-entered');
      await wait(TRANSITION_MS);
      container.classList.remove('is-entered');

      if (push) history.pushState({}, '', url);
      const title = getTitle(html);
      if (title) document.title = title;

      // ✅ 교체 완료 신호
      document.dispatchEvent(new CustomEvent('auth:loaded', { detail: { url } }));
      // location.reload();
    } catch (err) {
      console.error(err);
      location.href = url;
    }
  }

  function extractContainer(html, sel) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    return doc.querySelector(sel);
  }
  function getTitle(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const t = doc.querySelector('title');
    return t ? t.textContent : '';
  }
  function wait(ms) { return new Promise(r => setTimeout(r, ms)); }
})();
