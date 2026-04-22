(function() {
  const applyTheme = t => {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('sora-theme', t); } catch(e){}
  };
  applyTheme(localStorage.getItem('sora-theme') || 'dark');
})();

window.SoraSplash = {
  inject() {
    if (document.getElementById('sora-splash')) return;
    const s = document.createElement('div');
    s.id = 'sora-splash';
    s.className = 'sora-splash';
    s.innerHTML = '<video autoplay loop muted playsinline><source src="/static/video/earth-loop.mp4" type="video/mp4"></video><div class="sora-splash-content"><h1 class="sora-splash-logo">SORA<span>.earth</span></h1><p class="sora-splash-tagline">ESG Intelligence Platform</p></div><div class="sora-splash-hint">Click anywhere or press any key to continue</div><div class="sora-splash-version">v2.0 LIVE</div>';
    document.body.appendChild(s);
  },
  hide() {
    const el = document.getElementById('sora-splash');
    if (!el) return;
    el.classList.add('hidden');
    setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 900);
  }
};

document.addEventListener('DOMContentLoaded', () => {
  window.SoraSplash.inject();
  let hidden = false;
  const hideOnce = () => {
    if (hidden) return;
    hidden = true;
    window.SoraSplash.hide();
  };
  window.addEventListener('click', hideOnce, true);
  window.addEventListener('keydown', hideOnce, true);
  window.addEventListener('touchstart', hideOnce, true);
  window.addEventListener('wheel', hideOnce, true);
  setTimeout(hideOnce, 10000);
});
