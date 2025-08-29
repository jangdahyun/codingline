// static/js/home.js
(() => {
  // ---------- 유틸: CSRF 토큰 가져오기 ----------
  function getCsrfToken() {
    // 템플릿 폼에 있는 csrfmiddlewaretoken input에서 꺼내도 되고,
    // 쿠키에서 가져와도 됨. 여기선 폼에서 꺼냄.
    const el = document.querySelector('#pwForm input[name=csrfmiddlewaretoken]');
    return el ? el.value : '';
  }

  // ---------- 모달 제어 ----------
  const modal = document.getElementById('pwModal');         // 모달 루트
  const overlay = document.getElementById('pwOverlay');     // 배경
  const form = document.getElementById('pwForm');           // 폼 엘리먼트
  const input = document.getElementById('pwInput');         // 비번 입력창
  const errorEl = document.getElementById('pwError');       // 에러 문구
  const cancelBtn = document.getElementById('pwCancel');    // 취소 버튼
  const titleEl = document.getElementById('pwRoomTitle');   // 방 제목(옵션)

  let currentSlug = null;                                    // 현재 검증할 방 slug

  function openModal(slug, roomTitleText) {
    currentSlug = slug;                                      // 어떤 방에 대해 검증할지 기억
    titleEl.textContent = roomTitleText ? `방: ${roomTitleText}` : ''; // 제목 표시(옵션)
    errorEl.classList.add('hidden');                         // 이전 에러 숨김
    input.value = '';                                        // 입력값 초기화
    modal.classList.remove('hidden');                        // 표시
    modal.classList.add('flex');                             // 센터 정렬(flex 컨테이너)
    setTimeout(() => input.focus(), 0);                      // 포커스
  }

  function closeModal() {
    modal.classList.add('hidden');                           // 숨김
    modal.classList.remove('flex');
    currentSlug = null;                                      // 상태 초기화
  }

  overlay.addEventListener('click', closeModal);             // 배경 클릭 시 닫기
  cancelBtn.addEventListener('click', closeModal);           // 취소 클릭 시 닫기
  document.addEventListener('keydown', (e) => {              // ESC로 닫기
    if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal();
  });

  // ---------- 입장 버튼 가로채기 ----------
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('a.enter-btn');             // 리스트의 "입장" 버튼만 대상
    if (!btn) return;

    const slug = btn.getAttribute('data-slug');              // 어떤 방?
    const locked = btn.hasAttribute('data-locked');          // 비번 필요?

    if (!locked) return;                                     // 비번 없으면 그대로 링크 내비게이션 진행

    e.preventDefault();                                      // 기본 이동 막고
    const roomTitle = btn.closest('li')?.querySelector('.font-medium')?.textContent?.trim() || '';
    openModal(slug, roomTitle);                              // 모달 오픈
  });

  // ---------- 비번 제출(fetch POST) ----------
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!currentSlug) return;                                // 안전장치
    const pw = input.value.trim();
    const csrf = getCsrfToken();

    try {
      const res = await fetch(`/rooms/${currentSlug}/enter/`, {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf,                               // Django CSRF
          // 아래 둘 중 하나만 사용. 여기선 폼-POST로 보낼게요:
          // 'Content-Type': 'application/json',
        },
        // JSON으로 보낼 수도 있지만, 서버에서 request.POST로 받기 쉽도록 FormData 사용:
        body: new URLSearchParams({ password: pw }),
        // JSON으로 보내려면: body: JSON.stringify({ password: pw })
      });

      const data = await res.json();                         // JSON 파싱
      if (res.ok && data.ok) {                               // 성공
        closeModal();                                        // 모달 닫고
        window.location.href = data.next || `/rooms/${currentSlug}/`; // 상세로 이동
      } else {                                               // 실패(400 등)
        errorEl.textContent = (data && data.error) ? data.error : '비밀번호가 올바르지 않습니다.';
        errorEl.classList.remove('hidden');                  // 에러 표시
        input.focus();
        input.select();
      }
    } catch (err) {
      console.error(err);
      errorEl.textContent = '네트워크 오류가 발생했습니다. 잠시 후 다시 시도하세요.';
      errorEl.classList.remove('hidden');
    }
  });
})();