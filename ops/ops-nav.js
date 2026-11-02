/**
 * ops-nav.js — shared header + sub-navigation for the Operations pages.
 * Add one line at the top of <body> in every ops page:  <script src="/ops/ops-nav.js"></script>
 * To add a page: add it to OPS_NAV below AND to OPS_PAGE_NAMES in gate/worker.js,
 * then create ops/ops-<name>.html.
 */
(function () {
  'use strict';
  const OPS_NAV = [
    { label: 'Overview', href: '/ops/overview' },
    // { label: 'Capture Price', href: '/ops/capture-price' },
  ];
  const CSS = `
    .ops-header{background:#2c3e50;color:#fff;padding:16px 30px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
    .ops-header h1{font-size:22px;font-weight:500;letter-spacing:.2px;margin-right:auto}
    .ops-header a{color:rgba(255,255,255,.75);text-decoration:none;font-size:12px;font-weight:600;border:1px solid rgba(255,255,255,.35);padding:4px 11px;border-radius:5px;background:rgba(255,255,255,.08)}
    .ops-header a:hover{color:#fff;background:rgba(255,255,255,.18)}
    .sub-nav{background:#f8f9fa;border-bottom:2px solid #e6e9ed;padding:0 30px;display:flex;overflow-x:auto}
    .sub-nav-link{display:inline-block;padding:14px 22px;font-size:14px;font-weight:600;color:#6c757d;text-decoration:none;border-bottom:3px solid transparent;margin-bottom:-2px;white-space:nowrap;transition:color .2s,border-color .2s}
    .sub-nav-link:hover{color:#2c3e50}
    .sub-nav-link.active{color:#2c3e50;border-bottom-color:#2c3e50}
    @media(max-width:900px){.ops-header{padding:14px}.ops-header h1{font-size:18px}.sub-nav{padding:0 14px}.sub-nav-link{padding:12px 14px;font-size:13px}}`;
  const here = location.pathname.replace(/\/+$/, '').toLowerCase();
  function inject() {
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <div class="ops-header">
        <h1>Hungary Operations · TOKAJ</h1>
        <a href="/project">&larr; Projects</a>
        <a href="/ops/logout">Log out</a>
      </div>
      <div class="sub-nav">${OPS_NAV.map(n =>
        `<a class="sub-nav-link${here === n.href ? ' active' : ''}" href="${n.href}">${n.label}</a>`).join('')}</div>`;
    const first = document.body.firstChild;
    while (wrap.firstElementChild) document.body.insertBefore(wrap.firstElementChild, first);
  }
  if (document.body) inject(); else document.addEventListener('DOMContentLoaded', inject);
})();
