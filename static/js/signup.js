// ===== 비밀번호 표시/숨김 (이벤트 위임) =====
console.log('signup.js loaded');
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-toggle]');          // 클릭된 요소 근처에서 data-toggle 찾기
  if (!btn) return;                                       // 없으면 종료
  const input = document.querySelector(btn.getAttribute('data-toggle')); // 토글 대상 input
  if (!input) return;                                     // 대상 없으면 종료
  const show = input.type === 'password';                 // 지금 password냐?
  input.type = show ? 'text' : 'password';                // 타입 토글
  btn.textContent = show ? '숨김' : '표시';               // 버튼 텍스트 토글
});

// ✅ 셀렉트 값 → hidden(birth_date) 합치고 콘솔에 찍기
function syncBirthHidden(log = true) {
  const y = document.getElementById('dob_year');
  const m = document.getElementById('dob_month');
  const d = document.getElementById('dob_day');
  const h = document.getElementById('id_birth_date');
  if (!y || !m || !d || !h) return;                       // 요소 없으면 종료

  if (y.value && m.value && d.value) {                    // 세 값이 다 있으면
    const mm = String(m.value).padStart(2, '0');
    const dd = String(d.value).padStart(2, '0');
    const val = `${y.value}-${mm}-${dd}`;                 // YYYY-MM-DD
    h.value = val;                                        // hidden에 반영
    if (log) console.log('[DOB] 선택값', { year: y.value, month: m.value, day: d.value, hidden: val });
  } else {
    h.value = '';                                         // 불완전하면 비움
    if (log) console.log('[DOB] 값 미완성', { year: y.value, month: m.value, day: d.value });
  }
}

// ===== 생년월일 셀렉트 초기화 (한 번만) =====
function initDOB() {
  const y = document.getElementById('dob_year');
  const m = document.getElementById('dob_month');
  const d = document.getElementById('dob_day');
  if (!y || !m || !d) return;                             // 셀렉트 없으면 종료
  if (y.dataset.inited === '1') return;                   // 이미 초기화했다면 재실행 금지

  const now = new Date();
  const yearNow = now.getFullYear();

  // 년(최근 100년)
  for (let yy = yearNow; yy >= yearNow - 100; yy--) {
    y.appendChild(new Option(String(yy), String(yy)));
  }
  // 월
  for (let mm = 1; mm <= 12; mm++) {
    m.appendChild(new Option(String(mm).padStart(2, '0'), String(mm)));
  }

  // 서버가 넘긴 기본값 복구(data-selected 사용)
  const presetYear  = y.dataset.selected;
  const presetMonth = m.dataset.selected;
  let   presetDay   = d.dataset.selected;
  if (presetYear)  y.value = presetYear;  else y.value = String(yearNow);
  if (presetMonth) m.value = presetMonth; else m.value = '1';

  console.log('[DOB] preset from server', { presetYear, presetMonth, presetDay });

  function fillDays() {
    const yy = parseInt(y.value, 10);
    const mm = parseInt(m.value, 10);
    const last = new Date(yy, mm, 0).getDate();
           // 해당 월 마지막 일
    d.replaceChildren();
    for (let dd = 1; dd <= last; dd++) {
      d.appendChild(new Option(String(dd).padStart(2, '0'), String(dd)));
    }
    // day도 preset 없으면 1일로 기본 선택
    if (presetDay) { d.value = presetDay; presetDay = ''; }
    else d.value = '1';
  }

  fillDays();                                             // 초기 일자 채우기
  syncBirthHidden(true);                                   // 초기 hidden 반영 + 콘솔 로그

  // 🔧 오타 수정: yy → y
  y.addEventListener('change', () => { presetDay = d.value; fillDays(); syncBirthHidden(true); });
  m.addEventListener('change', () => { presetDay = d.value; fillDays(); syncBirthHidden(true); });
  d.addEventListener('change', () => syncBirthHidden(true));

  y.dataset.inited = '1';                                  // 재초기화 방지
}

// 최초 로드 + PJAX 후 재실행
document.addEventListener('DOMContentLoaded', initDOB);
document.addEventListener('auth:loaded', initDOB);

// 제출 직전에 한 번 더 보정 + 최종 값 로그
document.addEventListener('submit', (e) => {
  const form = e.target.closest('form');
  if (!form) return;
  if (form.querySelector('#id_birth_date')) {
    syncBirthHidden(true);
    console.log('[DOB] submit final:', document.getElementById('id_birth_date').value);
  }
}, true);
