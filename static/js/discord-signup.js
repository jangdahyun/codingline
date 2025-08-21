// 비밀번호 표시/숨김 (디스코드와 비슷한 UX)
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-toggle]');
  if (!btn) return;
  const sel = btn.getAttribute('data-toggle');
  const input = document.querySelector(sel);
  if (!input) return;
  const isPwd = input.type === 'password';
  input.type = isPwd ? 'text' : 'password';
  btn.textContent = isPwd ? '숨김' : '표시';
});

// 생년월일 셀렉트 채우기 (년/월/일)
(function initDOB() {
  const ySel = document.getElementById('dob_year');
  const mSel = document.getElementById('dob_month');
  const dSel = document.getElementById('dob_day');
  if (!ySel || !mSel || !dSel) return;

  // 년: 현재 기준 100년치
  const now = new Date();
  const yearNow = now.getFullYear();
  for (let y = yearNow; y >= yearNow - 100; y--) {
    const opt = document.createElement('option');
    opt.value = y; opt.textContent = y;
    ySel.appendChild(opt);
  }
  // 월
  for (let m = 1; m <= 12; m++) {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m;
    mSel.appendChild(opt);
  }
  // 일(월/윤년 따라 변동)
  function daysInMonth(year, month) {
    return new Date(year, month, 0).getDate(); // month: 1~12
  }
  function fillDays() {
    dSel.innerHTML = '';
    const year = parseInt(ySel.value || yearNow, 10);
    const month = parseInt(mSel.value || 1, 10);
    const dim = daysInMonth(year, month);
    for (let d = 1; d <= dim; d++) {
      const opt = document.createElement('option');
      opt.value = d; opt.textContent = d;
      dSel.appendChild(opt);
    }
  }
  ySel.addEventListener('change', fillDays);
  mSel.addEventListener('change', fillDays);
  fillDays();
})();
