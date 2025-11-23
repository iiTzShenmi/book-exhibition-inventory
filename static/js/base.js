(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  window.csrfToken = csrfToken;

  function showToast(message, ok = true) {
    const toast = document.createElement('div');
    toast.className = `toast ${ok ? 'success' : 'error'}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 300);
    }, 2500);
  }

  window.showToast = showToast;

  function toggleAdvanced() {
    const panel = document.getElementById('advanced-panel');
    if (!panel) return;
    const hidden = panel.style.display === 'none' || panel.style.display === '';
    panel.style.display = hidden ? 'flex' : 'none';
  }

  window.toggleAdvanced = toggleAdvanced;

  function resetAdminSearchForm() {
    const form = document.getElementById('admin-search-form');
    if (!form) return;
    form.addEventListener('submit', () => {
      setTimeout(() => {
        form.reset();
        const panel = document.getElementById('advanced-panel');
        if (panel) panel.style.display = 'none';
      }, 200);
    });
  }

  function getStatusIcon(type) {
    if (type === 'out-of-stock') return '🚫';
    if (type === 'low-stock') return '📦';
    return 'ℹ️';
  }

  let allAlerts = [];

  function setNotifTitle(filterType = 'all') {
    const titleEl = document.getElementById('notif-title');
    if (!titleEl) return;
    const labels = {
      'all': '📢 系統通知',
      'out-of-stock': '缺貨',
      'low-stock': '補書',
      'info': '資訊'
    };
    titleEl.textContent = labels[filterType] || '📢 系統通知';
  }

  function renderNotifications(filterType = 'all') {
    const list = document.getElementById('notif-list');
    if (!list) return;

    const filtered = filterType === 'all'
      ? allAlerts
      : allAlerts.filter(a => a.type === filterType);

    list.innerHTML = '';
    if (!filtered.length) {
      list.innerHTML = "<div class='notif-empty'>🎉 沒有相關通知</div>";
      return;
    }

    const getStatusLabel = (type) => {
      if (type === 'out-of-stock') return '缺貨';
      if (type === 'low-stock') return '低庫存';
      return '資訊';
    };

    filtered.forEach(alert => {
      const item = document.createElement('div');
      item.className = `notif-item ${alert.type || 'info'}`;
      item.innerHTML = `
        <span class="notif-icon">${getStatusIcon(alert.type)}</span>
        <div class="notif-body">
          <span class="notif-type">${getStatusLabel(alert.type)}</span>
          <span class="notif-msg">${alert.message}</span>
        </div>
      `;
      list.appendChild(item);
    });

    setNotifTitle(filterType);
  }

  async function loadNotifications() {
    const res = await fetch('/api/notifications');
    if (!res.ok) return;
    allAlerts = await res.json();

    const activeTab = document.querySelector('.notif-tab.active');
    const activeType = activeTab?.dataset.type || 'all';
    renderNotifications(activeType);

    const count = document.getElementById('notif-count');
    const notifBtn = document.getElementById('notif-btn');
    if (count && notifBtn) {
      if (allAlerts.length > 0) {
        count.textContent = allAlerts.length;
        count.style.display = 'inline-block';
        notifBtn.classList.add('has-alerts');
      } else {
        count.style.display = 'none';
        notifBtn.classList.remove('has-alerts');
      }
    }
  }

  function openNotif() {
    const overlay = document.getElementById('notif-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    loadNotifications();
  }

  function closeNotif() {
    const overlay = document.getElementById('notif-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  window.openNotif = openNotif;
  window.closeNotif = closeNotif;

  function openBookModal(title) {
    const overlay = document.getElementById('book-modal-overlay');
    const box = document.getElementById('book-modal-box');
    if (!overlay || !box) return;

    fetch(`/book_details/${encodeURIComponent(title)}`)
      .then(res => res.text())
      .then(html => {
        box.innerHTML = html;
        overlay.style.display = 'flex';
      })
      .catch(err => {
        console.error(err);
        showToast('載入書籍內容失敗', false);
      });
  }

  function closeBookModal() {
    const overlay = document.getElementById('book-modal-overlay');
    const box = document.getElementById('book-modal-box');
    if (!overlay || !box) return;
    overlay.style.display = 'none';
    box.innerHTML = '';
  }

  window.openBookModal = openBookModal;
  window.closeBookModal = closeBookModal;

  function handleNotificationTabs(event) {
    const tab = event.target.closest('.notif-tab');
    if (!tab) return;
    document.querySelectorAll('.notif-tab').forEach(btn => btn.classList.remove('active'));
    tab.classList.add('active');
    const type = tab.dataset.type || 'all';
    renderNotifications(type);
  }

  function bindEscapeToCloseModals() {
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      const modals = [
        'modal-overlay',
        'book-modal-overlay',
        'cabinet-modal-overlay',
        'cabinet-manager-overlay',
        'cabinet-books-overlay',
        'move-book-overlay',
        'notif-overlay',
        'add-book-overlay'
      ];

      modals.forEach(id => {
        const el = document.getElementById(id);
        if (el && el.style.display === 'flex') {
          el.style.display = 'none';
        }
      });
    });
  }

  function safeFetch(url, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (!('X-CSRF-Token' in headers)) {
      headers['X-CSRF-Token'] = window.csrfToken || '';
    }
    return fetch(url, { credentials: 'same-origin', ...opts, headers });
  }

  window.safeFetch = safeFetch;

  document.addEventListener('DOMContentLoaded', () => {
    resetAdminSearchForm();
    bindEscapeToCloseModals();
    document.addEventListener('click', handleNotificationTabs);

    const notifBtn = document.getElementById('notif-btn');
    if (notifBtn) {
      notifBtn.addEventListener('click', openNotif);
      loadNotifications();
      setInterval(loadNotifications, 120000);
    }
  });
})();
