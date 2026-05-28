'use strict';

// ── CSRF token ──────────────────────────────────────
function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[2]) : null;
}

// ── Generic API request helper ──────────────────────
async function apiRequest(url, method, data) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') || '' },
  };
  if (data && method !== 'DELETE') opts.body = JSON.stringify(data);
  const res = await fetch(url, opts);
  if (res.redirected || res.url.includes('/login/')) {
    window.location.href = '/login/';
    return;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `Request failed: ${res.status}`);
  }
  return res.json().catch(() => ({}));
}

// ── Modal helpers ───────────────────────────────────
function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('hidden');
  // Force inner .modal box visible — Bootstrap's global `.modal { display:none }`
  // can win via stylesheet even with our !important override depending on load order.
  const box = el.querySelector('.modal');
  if (box) {
    box.style.display = 'flex';
    box.style.flexDirection = 'column';
    box.style.position = 'relative';
    box.style.inset = 'auto';
    box.style.zIndex = 'auto';
    box.style.width = '100%';
    box.style.height = 'auto';
  }
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('hidden');
  const box = el.querySelector('.modal');
  if (box) box.removeAttribute('style');
}

// Close modal on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay') && !e.target.classList.contains('hidden')) {
    closeModal(e.target.id);
  }
});

// Close modal on Escape key
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay:not(.hidden)').forEach(m => closeModal(m.id));
  }
});

// ── Sidebar toggle ──────────────────────────────────
function toggleSidebar() {
  document.body.classList.toggle('sidebar-collapsed');
  localStorage.setItem('sidebarCollapsed', document.body.classList.contains('sidebar-collapsed'));
}

// Restore sidebar state
(function () {
  if (localStorage.getItem('sidebarCollapsed') === 'true') {
    document.body.classList.add('sidebar-collapsed');
  }
})();

// ── Dark mode toggle ────────────────────────────────
function toggleDarkMode(checkbox) {
  document.body.classList.toggle('dark-mode', checkbox.checked);
  localStorage.setItem('darkMode', checkbox.checked);
}

(function () {
  const dark = localStorage.getItem('darkMode') === 'true';
  if (dark) { document.body.classList.add('dark-mode'); }
  const toggle = document.getElementById('darkModeToggle');
  if (toggle) toggle.checked = dark;
})();

// ── Media Channels submenu ──────────────────────────
function toggleMediaMenu(e) {
  e.preventDefault();
  const sub = document.getElementById('mediaSubMenu');
  const chevron = e.currentTarget.querySelector('.nav-chevron');
  if (sub) {
    sub.classList.toggle('open');
    if (chevron) chevron.style.transform = sub.classList.contains('open') ? 'rotate(180deg)' : '';
  }
}

// Auto-open media submenu if on a media page
(function () {
  const sub = document.getElementById('mediaSubMenu');
  if (sub && (location.pathname.includes('/media/'))) {
    sub.classList.add('open');
    const chevron = document.querySelector('.nav-item-parent .nav-chevron');
    if (chevron) chevron.style.transform = 'rotate(180deg)';
  }
})();

// ── Org switcher dropdown ───────────────────────────
function toggleOrgDropdown(e) {
  e.stopPropagation();
  const dropdown = document.getElementById('orgDropdown');
  if (dropdown) dropdown.classList.toggle('hidden');
}

document.addEventListener('click', e => {
  const wrap = document.getElementById('orgSwitcherWrap');
  if (wrap && !wrap.contains(e.target)) {
    const dropdown = document.getElementById('orgDropdown');
    if (dropdown) dropdown.classList.add('hidden');
  }
});

// ── Row menu (three-dot) ────────────────────────────
function toggleRowMenu(btn) {
  const menu = btn.nextElementSibling;
  const isOpen = !menu.classList.contains('hidden');
  // Close all open menus first
  document.querySelectorAll('.row-menu:not(.hidden)').forEach(m => m.classList.add('hidden'));
  if (!isOpen) menu.classList.remove('hidden');
}

function closeMeAndRun(btn, fn) {
  const menu = btn.closest('.row-menu');
  if (menu) menu.classList.add('hidden');
  fn();
}

// Close row menus on outside click
document.addEventListener('click', e => {
  if (!e.target.closest('.row-menu-wrap')) {
    document.querySelectorAll('.row-menu:not(.hidden)').forEach(m => m.classList.add('hidden'));
  }
});

// ── Table sort ──────────────────────────────────────
document.querySelectorAll('.sort-icon').forEach(icon => {
  icon.style.cursor = 'pointer';
  icon.addEventListener('click', () => {
    const th = icon.closest('th');
    const table = th.closest('table');
    const tbody = table.querySelector('tbody');
    const idx = Array.from(th.parentNode.children).indexOf(th);
    const asc = th.dataset.sortAsc !== 'true';
    th.dataset.sortAsc = asc;
    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {
      const av = a.cells[idx]?.textContent.trim() || '';
      const bv = b.cells[idx]?.textContent.trim() || '';
      const an = parseFloat(av.replace(/,/g, ''));
      const bn = parseFloat(bv.replace(/,/g, ''));
      if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    rows.forEach(r => tbody.appendChild(r));
  });
});
