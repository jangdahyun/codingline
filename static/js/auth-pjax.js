(function () {
  const containerSel = '#authView';

  // auth-nav 링크 클릭을 가로채서 부분 교체
  document.addEventListener('click', (e) => {
    const a = e.target.closest('a.auth-nav');
    if (!a) return;

    const url = a.getAttribute('href');
    if (!url) return;

    // 외부 링크는 무시
    const sameOrigin = url.startsWith('/') || url.startsWith(location.origin);
    if (!sameOrigin) return;

    e.preventDefault();
    loadAuth(url, /* push */ true);
  });

  // 뒤로가기/앞으로가기 대응
  window.addEventListener('popstate', () => {
    loadAuth(location.href, /* push */ false);
  });

  async function loadAuth(url, push) {
    const container = document.querySelector(containerSel);
    if (!container) return;

    // exit 애니메이션
    container.classList.add('is-leaving');
    await wait(160);

    try {
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const html = await res.text();

      // 응답 문서에서 같은 컨테이너만 추출
      const next = extractContainer(html, containerSel);
      if (!next) throw new Error('Container not found in response');

      // 새 내용 주입 + enter 애니메이션
      container.innerHTML = next.innerHTML;
      container.classList.remove('is-leaving');
      container.classList.add('is-entering');
      await wait(4); // reflow tick
      container.classList.remove('is-entering');
      container.classList.add('is-entered');
      await wait(160);
      container.classList.remove('is-entered');

      // 주소/타이틀 갱신
      if (push) history.pushState({}, '', url);
      const title = getTitle(html);
      if (title) document.title = title;
    } catch (err) {
      console.error(err);
      // 실패 시 일반 네비게이션으로 폴백
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
