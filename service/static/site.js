'use strict';
// Progressive enhancement only. No analytics, cookies or camera access.
const menuButton = document.querySelector('[data-menu-toggle]');
const menu = document.getElementById('primary-nav');
if (menuButton && menu) {
  menuButton.addEventListener('click', () => {
    const expanded = menuButton.getAttribute('aria-expanded') === 'true';
    menuButton.setAttribute('aria-expanded', String(!expanded));
    menu.classList.toggle('is-open', !expanded);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menuButton.getAttribute('aria-expanded') === 'true') {
      menuButton.setAttribute('aria-expanded', 'false');
      menu.classList.remove('is-open');
      menuButton.focus();
    }
  });
}
// The demonstration uses fixed fictitious observations; never reads student data.
const demo = document.querySelector('[data-synthetic-demo]');
if (demo) {
  const days = {
    saturday: {present: 842, late: 12, absent: 46, time: ['07:34', '07:38', '07:52', '08:09', '—', '07:45']},
    sunday: {present: 856, late: 9, absent: 35, time: ['07:32', '07:36', '07:48', '08:04', '—', '07:40']},
    monday: {present: 831, late: 17, absent: 52, time: ['07:40', '07:44', '07:58', '08:12', '—', '07:50']}
  };
  const daySelect = demo.querySelector('[data-demo-day]');
  const filter = demo.querySelector('[data-demo-search]');
  const rows = [...demo.querySelectorAll('[data-demo-row]')];
  function update() {
    const data = days[daySelect.value] || days.saturday;
    for (const key of ['present', 'late', 'absent']) {
      demo.querySelector('[data-demo-' + key + ']').textContent = new Intl.NumberFormat(document.documentElement.lang === 'fa' ? 'fa-IR' : 'en').format(data[key]);
    }
    const query = filter.value.trim();
    let visible = 0;
    rows.forEach((row, index) => {
      row.querySelector('[data-demo-time]').textContent = data.time[index];
      row.hidden = !row.textContent.includes(query);
      if (!row.hidden) visible += 1;
    });
    demo.querySelector('[data-demo-empty]').hidden = visible !== 0;
    demo.querySelector('[data-demo-announcement]').textContent = (document.documentElement.lang === 'fa' ? 'داده‌های ساختگی به‌روز شد. ' : 'Synthetic data updated. ') + new Intl.NumberFormat(document.documentElement.lang === 'fa' ? 'fa-IR' : 'en').format(visible) + (document.documentElement.lang === 'fa' ? ' ردیف نمونه نمایش داده می‌شود.' : ' sample rows displayed.');
  }
  daySelect.addEventListener('change', update);
  filter.addEventListener('input', update);
  update();
}
// Native forms remain functional without JS. Prevent accidental duplicate submits.
for (const form of document.querySelectorAll('form[data-submit-once]')) {
  form.addEventListener('submit', () => {
    for (const button of form.querySelectorAll('button[type="submit"]:not([name])')) {
      button.disabled = true;
      button.setAttribute('aria-busy', 'true');
      button.textContent = document.documentElement.lang === 'fa' ? 'در حال ارسال…' : 'Submitting…';
    }
  });
}
for (const button of document.querySelectorAll('[data-print]')) {
  button.addEventListener('click', () => window.print());
}

// Keep language submission CSP-compatible; preserve form fields/navigation via next.
for (const select of document.querySelectorAll('[data-language-select]')) {
  select.addEventListener('change', () => select.form.requestSubmit());
}
