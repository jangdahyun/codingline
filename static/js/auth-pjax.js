(function () {                                            // 이 파일이 로드되자마자 즉시 실행되는 IIFE(즉시 실행 함수)입니다. 전역 변수 오염을 막습니다.
  const containerSel = '#authView';                      // 화면 전환(부분 교체)의 대상이 되는 컨테이너의 CSS 선택자입니다. 여기서는 #authView 내부만 교체합니다.
  const TRANSITION_MS = 160;                             // 전환 애니메이션(leave/enter)에 사용할 시간(ms)입니다. CSS 전환 시간과 맞추면 부드럽습니다.

  document.addEventListener('click', (e) => {            // 문서 전체에 클릭 이벤트를 한 번만 등록합니다(이벤트 위임).
    const a = e.target.closest('a.auth-nav');            // 클릭된 요소에서 가장 가까운 상위로 올라가며 a.auth-nav 링크를 찾습니다(로그인/회원가입 전환 링크).
    if (!a) return;                                      // auth-nav 링크가 아니라면 아무 것도 하지 않고 종료합니다.
    const url = a.getAttribute('href');                  // 해당 링크의 이동 대상 URL을 가져옵니다.
    if (!url) return;                                    // URL이 없으면 종료합니다(이상치 방어).

    const sameOrigin = url.startsWith('/') || url.startsWith(location.origin);
                                                         // 같은 오리진(동일 사이트)으로의 이동인지 확인합니다.
                                                         // '/'로 시작하면 상대경로 → 같은 오리진. 절대경로면 현재 origin으로 시작하는지 검사합니다.
    if (!sameOrigin) return;                             // 다른 오리진(외부 사이트)이면 부분 전환(PJAX)을 하지 않고 원래 동작에 맡깁니다.

    e.preventDefault();                                  // 기본 링크 내비게이션(전체 새로고침)을 막습니다.
    loadAuth(url, /* push */ true);                      // 부분 전환 함수 호출. push=true라서 히스토리에 주소를 푸시합니다.
  });

  window.addEventListener('popstate', () => {            // 브라우저 뒤로가기/앞으로가기(히스토리 이동) 이벤트를 처리합니다.
    loadAuth(location.href, /* push */ false);           // 현재 주소의 페이지를 부분 전환으로 로드합니다. push=false(이미 히스토리에 있음).
  });

  async function loadAuth(url, push) {                   // 부분 전환의 핵심 함수. url에서 HTML을 가져와 #authView 내부만 교체합니다.
    const container = document.querySelector(containerSel);
                                                         // 현재 문서에서 교체 대상 컨테이너(#authView)를 찾습니다.
    if (!container) return;                              // 컨테이너가 없으면 아무 것도 하지 않습니다(방어).

    container.classList.add('is-leaving');               // 나가기(leave) 애니메이션을 위한 CSS 클래스를 추가합니다.
    await wait(TRANSITION_MS);                           // 애니메이션 시간이 끝날 때까지 대기합니다. CSS 전환과 타이밍을 맞춥니다.

    try {                                                // 네트워크 요청 및 DOM 교체 과정에서의 오류를 잡기 위한 try/catch 블록입니다.
      const res = await fetch(url, { credentials: 'include' });
                                                         // fetch로 대상 URL의 HTML을 가져옵니다. credentials: 'include'로 쿠키/세션을 포함합니다(로그인 상태 유지).
      if (!res.ok) throw new Error('HTTP ' + res.status);// HTTP 상태 코드가 200대가 아니면 오류를 던집니다(예: 404, 500 등).
      const html = await res.text();                     // 응답 본문을 텍스트(HTML 문자열)로 읽어옵니다.

      const next = extractContainer(html, containerSel); // 응답 HTML에서 containerSel(#authView)만 골라낸 DOM 조각을 얻습니다.
      if (!next) throw new Error('Container not found in response');
                                                         // 응답 안에 #authView가 없으면 잘못된 페이지라고 보고 에러를 던집니다.

      container.innerHTML = next.innerHTML;              // 현재 문서의 #authView 내부 내용을 새로 받은 #authView 내부 내용으로 갈아끼웁니다.
                                                         // 주의: innerHTML로 교체하면 기존 자식 노드에 걸려있던 이벤트 핸들러는 사라지므로,
                                                         //       필요한 초기화는 아래의 커스텀 이벤트(auth:loaded)에서 다시 설정해야 합니다.

      container.classList.remove('is-leaving');          // leave 상태 클래스를 제거합니다(나가기 애니메이션 종료).
      container.classList.add('is-entering');            // 들어오기(enter) 애니메이션을 위한 클래스를 추가합니다.
      await wait(4);                                     // 아주 짧게 대기하여 브라우저가 DOM 변경을 인식하고 CSS 전환을 시작하게 합니다(reflow tick).
      container.classList.remove('is-entering');         // entering 클래스를 제거합니다(단계 전환).
      container.classList.add('is-entered');             // entered 클래스를 추가합니다(들어온 상태, CSS에서 마무리 애니메이션이 있을 수 있음).
      await wait(TRANSITION_MS);                         // 들어오기 애니메이션이 끝날 때까지 기다립니다.
      container.classList.remove('is-entered');          // 애니메이션이 끝났으니 상태 클래스를 정리합니다.

      if (push) history.pushState({}, '', url);          // 사용자 클릭으로 온 경우 pushState로 주소창을 업데이트하며 히스토리에 기록합니다.
      const title = getTitle(html);                      // 응답 HTML에서 <title> 텍스트를 추출합니다.
      if (title) document.title = title;                 // 문서 제목을 응답 페이지의 제목으로 동기화합니다.

      // ✅ 교체 완료 신호
      document.dispatchEvent(new CustomEvent('auth:loaded', { detail: { url } }));
                                                         // 커스텀 이벤트를 전파하여 "새 DOM이 주입 완료되었다"는 신호를 보냅니다.
                                                         // 다른 스크립트(예: 날짜 셀렉트 채우기 initDOB)가 이 이벤트를 듣고 다시 초기화할 수 있습니다.
      // location.reload();                               // (주석) 전체 새로고침을 강제로 하던 디버그 코드로 보입니다. PJAX 의미가 없어져서 주석 처리한 상태가 정상입니다.
    } catch (err) {                                      // 위 try 블록에서 발생한 모든 에러를 처리합니다.
      console.error(err);                                // 콘솔에 에러를 출력하여 디버깅에 도움을 줍니다.
      location.href = url;                               // 부분 전환에 실패하면 폴백으로 전체 페이지 이동을 시도합니다(사용자 경험 보장).
    }
  }

  function extractContainer(html, sel) {                 // 응답받은 HTML 문자열에서 특정 선택자(sel)에 해당하는 노드를 뽑아내는 도우미 함수입니다.
    const doc = new DOMParser().parseFromString(html, 'text/html');
                                                         // DOMParser로 문자열을 실제 Document로 파싱합니다(스크립트는 실행되지 않음).
    return doc.querySelector(sel);                       // 파싱된 문서에서 sel(#authView)을 찾아서 반환합니다(없으면 null).
  }
  function getTitle(html) {                              // 응답 HTML의 <title> 텍스트만 뽑아오는 도우미 함수입니다.
    const doc = new DOMParser().parseFromString(html, 'text/html');
                                                         // 마찬가지로 문자열을 Document로 파싱합니다.
    const t = doc.querySelector('title');                // <title> 요소를 찾습니다.
    return t ? t.textContent : '';                       // 있으면 그 텍스트를, 없으면 빈 문자열을 반환합니다.
  }
  function wait(ms) { return new Promise(r => setTimeout(r, ms)); }
                                                         // 지정한 ms 후에 resolve되는 Promise를 리턴하는 유틸 함수입니다.
                                                         // async/await와 함께 써서 타이밍을 제어합니다(애니메이션 대기 등).
})();                                                    // IIFE를 즉시 호출하여 위 설정들이 한 번만 등록되게 합니다.
