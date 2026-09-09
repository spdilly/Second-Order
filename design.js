(() => {
  const nav = document.querySelector('.nav');
  const toggle = document.querySelector('.menu-toggle');
  const menu = document.getElementById('site-menu');
  if (!nav || !toggle || !menu) return;
  nav.classList.add('menu-ready');
  const close = () => { toggle.setAttribute('aria-expanded', 'false'); nav.classList.remove('menu-open'); };
  toggle.addEventListener('click', () => {
    const open = toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded', String(open));
    nav.classList.toggle('menu-open', open);
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && nav.classList.contains('menu-open')) { close(); toggle.focus(); } });
  document.addEventListener('click', event => { if (!nav.contains(event.target)) close(); });
  menu.addEventListener('click', event => { if (event.target.closest('a')) close(); });
  matchMedia('(min-width: 1081px)').addEventListener('change', close);
})();
