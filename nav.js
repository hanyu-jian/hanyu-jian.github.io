/**
 * nav.js — Shared navigation for European Electricity Dashboard
 * 
 * Usage: Add ONE line inside <head> of every page:
 *   <script src="nav.js"></script>
 *
 * The script auto-detects the current page and marks the active tab.
 * It also injects the Disclaimer modal (triggered from the header).
 *
 * To add a new nav item in future, edit NAV_ITEMS below — one place, all pages.
 */

(function () {
  'use strict';

  /* ── 1. Nav definition ──────────────────────────────────────────────────
     Add / remove / reorder items here. href is matched against location.pathname
     to auto-highlight the active tab.
  ─────────────────────────────────────────────────────────────────────── */
  const NAV_ITEMS = [
    { label: 'Projects',           href: 'project.html' },
    { label: 'Price',              href: 'price-overview.html' },
    { label: 'Load',               href: 'load.html' },
    { label: 'Generation',         href: 'generation-overview.html' },
    { label: 'Installed Capacity', href: 'capacity.html' },
  ];

  /* ── 2. Disclaimer text ─────────────────────────────────────────────── */
  const DISCLAIMER_HTML = `
    <p style="margin:0 0 10px 0;">By accessing this dashboard you acknowledge the following:</p>
    <ol style="margin:0;padding-left:18px;display:flex;flex-direction:column;gap:8px;">
      <li>All information on this site is based on public knowledge, from various sources.</li>
      <li>One with access to this information should still keep all the information on this
          site <strong>confidential</strong> and must not disclose, divulge or provide it to
          anyone except as permitted by Shanghai Electric Power; information here should be
          used only as permitted by Shanghai Electric Power.</li>
      <li>Most information here is based on TSO submissions to ENTSO-E. The lack of DSO-level
          and residential/industrial-side data may impact further analysis.</li>
      <li>Anyone shall make decisions on their own judgement. No liabilities or
          responsibilities shall be assumed by Shanghai Electric Power or the designer of
          this site.</li>
    </ol>`;

  /* ── 3. Shared styles injected once ─────────────────────────────────── */
  const CSS = `
    .nav-header{background:#2c3e50;color:#fff;padding:20px 30px 0}
    .nav-header-top{padding-bottom:16px;display:flex;align-items:center;gap:14px}
    .nav-header-top h1{font-size:24px;font-weight:500;letter-spacing:.2px}
    .nav-disclaimer-btn{
      display:inline-flex;align-items:center;gap:5px;
      padding:4px 11px;border:1px solid rgba(255,255,255,.35);border-radius:5px;
      font-size:11px;font-weight:600;color:rgba(255,255,255,.75);
      background:rgba(255,255,255,.08);cursor:pointer;white-space:nowrap;
      letter-spacing:.2px;transition:background .2s,color .2s;font-family:inherit;
    }
    .nav-disclaimer-btn:hover{background:rgba(255,255,255,.18);color:#fff}
    .main-nav{display:flex;gap:2px;margin-top:4px}
    .main-nav-link{
      display:inline-flex;align-items:center;gap:7px;
      padding:10px 24px;font-size:14px;font-weight:600;
      color:rgba(255,255,255,.65);text-decoration:none;
      border-radius:6px 6px 0 0;background:rgba(255,255,255,.06);
      border:1px solid transparent;border-bottom:none;
      letter-spacing:.2px;transition:background .2s,color .2s;
      white-space:nowrap;position:relative;top:1px;
    }
    .main-nav-link:hover{color:rgba(255,255,255,.95);background:rgba(255,255,255,.14)}
    .main-nav-link.active{color:#2c3e50;background:#f8f9fa;border-color:#e6e9ed;border-bottom-color:#f8f9fa}

    /* Disclaimer modal */
    .nav-modal-overlay{
      display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);
      z-index:10000;align-items:center;justify-content:center;
    }
    .nav-modal-overlay.open{display:flex}
    .nav-modal{
      background:#fff;border-radius:10px;max-width:560px;width:calc(100% - 40px);
      box-shadow:0 8px 32px rgba(0,0,0,.25);overflow:hidden;
    }
    .nav-modal-head{
      background:#2c3e50;color:#fff;padding:16px 20px;
      display:flex;align-items:center;justify-content:space-between;
    }
    .nav-modal-head h2{font-size:15px;font-weight:600;margin:0}
    .nav-modal-close{
      background:none;border:none;color:rgba(255,255,255,.7);font-size:20px;
      cursor:pointer;line-height:1;padding:0 2px;transition:color .2s;font-family:inherit;
    }
    .nav-modal-close:hover{color:#fff}
    .nav-modal-body{
      padding:20px;font-size:13px;line-height:1.65;color:#2c3e50;
    }
    .nav-modal-footer{
      padding:12px 20px;border-top:1px solid #e6e9ed;
      display:flex;justify-content:flex-end;background:#fafbfc;
    }
    .nav-modal-ok{
      padding:8px 22px;background:#2c3e50;color:#fff;border:none;
      border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;
      transition:background .2s;font-family:inherit;
    }
    .nav-modal-ok:hover{background:#1a252f}
    @media(max-width:900px){
      .nav-header{padding:14px 14px 0}
      .nav-header-top h1{font-size:18px}
      .main-nav-link{padding:8px 14px;font-size:13px}
    }`;

  /* ── 4. Detect active page ──────────────────────────────────────────── */
  function isActive(href) {
    const path = window.location.pathname;
    return path.endsWith(href) || path.endsWith('/' + href);
  }

  /* ── 5. Build nav HTML ─────────────────────────────────────────────── */
  function buildNavHTML(titleText) {
    const links = NAV_ITEMS.map(item => {
      const active = isActive(item.href) ? ' active' : '';
      return `<a href="${item.href}" class="main-nav-link${active}">${item.label}</a>`;
    }).join('\n          ');

    return `
      <div class="nav-header" id="navHeader">
        <div class="nav-header-top">
          <h1>${titleText}</h1>
          <button class="nav-disclaimer-btn" id="navDisclaimerBtn">
            &#9432; Disclaimer
          </button>
        </div>
        <div class="main-nav">
          ${links}
        </div>
      </div>`;
  }

  /* ── 6. Build modal HTML ───────────────────────────────────────────── */
  function buildModalHTML() {
    return `
      <div class="nav-modal-overlay" id="navModalOverlay">
        <div class="nav-modal" role="dialog" aria-modal="true" aria-labelledby="navModalTitle">
          <div class="nav-modal-head">
            <h2 id="navModalTitle">Disclaimer</h2>
            <button class="nav-modal-close" id="navModalClose" aria-label="Close">&#x2715;</button>
          </div>
          <div class="nav-modal-body">${DISCLAIMER_HTML}</div>
          <div class="nav-modal-footer">
            <button class="nav-modal-ok" id="navModalOk">I understand</button>
          </div>
        </div>
      </div>`;
  }

  /* ── 7. Inject everything ─────────────────────────────────────────── */
  function inject() {
    /* Style */
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    /* Find or create a mount point.
       Convention: pages should have <div id="navMount"></div> as the very first
       child of .container, or we prepend to .container automatically. */
    const container = document.querySelector('.container');
    if (!container) return; // page not ready yet — see DOMContentLoaded below

    const titleEl = document.querySelector('.header-top h1');
    const titleText = titleEl ? titleEl.textContent.trim() : 'European Electricity Dashboard';

    /* Remove existing static header if present (for pages already having one) */
    const existingHeader = container.querySelector('.header');
    if (existingHeader) existingHeader.remove();

    /* Inject nav at top of container */
    const navDiv = document.createElement('div');
    navDiv.innerHTML = buildNavHTML(titleText);
    container.insertAdjacentElement('afterbegin', navDiv.firstElementChild);

    /* Inject modal at end of body */
    const modalDiv = document.createElement('div');
    modalDiv.innerHTML = buildModalHTML();
    document.body.appendChild(modalDiv.firstElementChild);

    /* Wire up modal */
    const overlay = document.getElementById('navModalOverlay');
    document.getElementById('navDisclaimerBtn').addEventListener('click', () => {
      overlay.classList.add('open');
    });
    document.getElementById('navModalClose').addEventListener('click', () => {
      overlay.classList.remove('open');
    });
    document.getElementById('navModalOk').addEventListener('click', () => {
      overlay.classList.remove('open');
    });
    overlay.addEventListener('click', e => {
      if (e.target === overlay) overlay.classList.remove('open');
    });
  }

  /* Run after DOM is ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }

})();
