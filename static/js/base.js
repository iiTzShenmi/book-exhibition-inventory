(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  window.csrfToken = csrfToken;

  function showToast(message, ok = true, undoHandler = null) {
    const toast = document.createElement('div');
    toast.className = `toast ${ok ? 'success' : 'error'}`;

    const body = document.createElement('div');
    body.className = 'toast__body';
    body.textContent = message;
    toast.appendChild(body);

    if (ok && typeof undoHandler === 'function') {
      const actions = document.createElement('div');
      actions.className = 'toast__actions';
      const undoBtn = document.createElement('button');
      undoBtn.type = 'button';
      undoBtn.className = 'toast__undo';
      undoBtn.textContent = 'Undo';
      undoBtn.addEventListener('click', async () => {
        try {
          const result = await undoHandler();
          toast.remove();
          showToast(typeof result === 'string' ? result : '已還原上一筆操作', true);
          
        } catch (err) {
          console.error('Undo failed', err);
          toast.remove();
          showToast('無法還原，請重試', false);
        }
      });
      actions.appendChild(undoBtn);
      toast.appendChild(actions);
    }

    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    const duration = ok ? 6000 : 3500;
    setTimeout(() => {
      toast.classList.remove('show');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  window.showToast = showToast;

  function handleAddBook() {
    const overlay = document.getElementById('add-book-overlay');
    if (overlay) {
      openAddBookModal();
    } else {
      window.location.href = '/admin#add-book';
    }
  }

  function handleCabinetManager() {
    const overlay = document.getElementById('cabinet-manager-overlay');
    if (overlay) {
      openCabinetManager();
    } else {
      window.location.href = '/admin#cabinet-manager';
    }
  }

  window.handleAddBook = handleAddBook;
  window.handleCabinetManager = handleCabinetManager;

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

  window.refreshNotifications = loadNotifications;

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

  // Home page hint actions
  document.addEventListener('DOMContentLoaded', () => {
    const focusBtn = document.getElementById('hint-focus-search');
    const input = document.querySelector('.customer-search input[name="q"]');
    const mapOverlay = document.getElementById('booth-map-overlay');
    const realtimeBtn = document.getElementById('hint-realtime');
    if (focusBtn) {
      focusBtn.addEventListener('click', () => {
        if (mapOverlay) {
          openBoothMap();
        } else if (input) {
          input.focus();
          input.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    }
    if (realtimeBtn) {
      realtimeBtn.addEventListener('click', () => {
        openRealtimeModal();
      });
    }
  });

  let boothMapLoaded = false;
  let boothMapCabinets = [];

  function buildBoothMapSvg(cabinets) {
    const cols = 3;
    const cellW = 120;
    const cellH = 70;
    const gap = 16;
    const rows = Math.max(1, Math.ceil(cabinets.length / cols));
    const width = cols * cellW + (cols + 1) * gap;
    const height = rows * cellH + (rows + 1) * gap;

    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('class', 'booth-map-svg');

    const source = cabinets.length ? cabinets : [{ id: 0, name: 'Cabinet A' }, { id: 1, name: 'Cabinet B' }];
    source.forEach((cab, idx) => {
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      const x = gap + col * (cellW + gap);
      const y = gap + row * (cellH + gap);

      const rect = document.createElementNS(ns, 'rect');
      rect.setAttribute('x', x);
      rect.setAttribute('y', y);
      rect.setAttribute('rx', 12);
      rect.setAttribute('ry', 12);
      rect.setAttribute('width', cellW);
      rect.setAttribute('height', cellH);
      rect.setAttribute('fill', '#eef3ff');
      rect.setAttribute('stroke', '#cbd8ff');
      rect.setAttribute('class', 'booth-map-seat');
      rect.dataset.cabinetId = cab.id;

      const text = document.createElementNS(ns, 'text');
      text.setAttribute('x', x + cellW / 2);
      text.setAttribute('y', y + cellH / 2 + 5);
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('fill', '#2b3a55');
      text.setAttribute('font-size', '14');
      text.setAttribute('font-weight', '700');
      text.textContent = cab.name;

      svg.appendChild(rect);
      svg.appendChild(text);
    });

    return svg;
  }

  async function loadBoothMap() {
    const canvas = document.getElementById('booth-map-canvas');
    if (!canvas) return;
    canvas.innerHTML = '<div class="muted">載入櫃位中...</div>';
    try {
      const res = await fetch('/api/cabinets', { cache: 'no-store' });
      const data = await res.json();
      boothMapCabinets = data.cabinets || [];
      canvas.innerHTML = '';
      canvas.appendChild(buildBoothMapSvg(boothMapCabinets));
      boothMapLoaded = true;
    } catch (err) {
      console.error(err);
      canvas.innerHTML = '<div class="muted">無法載入櫃位資料</div>';
    }
  }

  async function showCabinetDetails(cabinetId) {
    const titleEl = document.getElementById('booth-map-title');
    const metaEl = document.getElementById('booth-map-meta');
    const listEl = document.getElementById('booth-map-list');
    if (!titleEl || !metaEl || !listEl) return;
    titleEl.textContent = '載入中...';
    metaEl.textContent = '';
    listEl.innerHTML = '';

    try {
      const res = await fetch(`/api/cabinets/${cabinetId}/featured`, { cache: 'no-store' });
      const data = await res.json();
      if (!data.success) throw new Error('failed');
      titleEl.textContent = data.cabinet?.name || '櫃位';
      metaEl.textContent = data.cabinet?.type === 'reserve' ? '備書櫃' : '展示櫃';
      const titles = data.titles || [];
      if (!titles.length) {
        listEl.innerHTML = '<li>目前沒有書籍</li>';
        return;
      }
      titles.forEach((name) => {
        const li = document.createElement('li');
        li.textContent = name;
        listEl.appendChild(li);
      });
    } catch (err) {
      console.error(err);
      titleEl.textContent = '載入失敗';
      metaEl.textContent = '請稍後再試';
    }
  }

  function openBoothMap() {
    const overlay = document.getElementById('booth-map-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    if (!boothMapLoaded) {
      loadBoothMap();
    }
  }

  function closeBoothMap() {
    const overlay = document.getElementById('booth-map-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  document.addEventListener('click', (event) => {
    const closeBtn = event.target.closest('#booth-map-close');
    if (closeBtn) {
      closeBoothMap();
      return;
    }
    const seat = event.target.closest('.booth-map-seat');
    if (seat) {
      document.querySelectorAll('.booth-map-seat').forEach((node) => node.classList.remove('booth-map-seat--active'));
      seat.classList.add('booth-map-seat--active');
      const cabinetId = seat.dataset.cabinetId;
      if (cabinetId) showCabinetDetails(cabinetId);
    }
    const overlay = document.getElementById('booth-map-overlay');
    if (overlay && event.target === overlay) {
      closeBoothMap();
    }
  });

  let realtimeTimer = null;

  function setRealtimeWorker(hasWork) {
    const worker = document.getElementById('worker');
    if (!worker) return;
    if (hasWork) {
      worker.classList.remove('sleeping');
      worker.classList.add('working');
      const bubble = worker.querySelector('.bubble');
      if (bubble) bubble.style.display = 'none';
    } else {
      worker.classList.remove('working');
      worker.classList.add('sleeping');
      const bubble = worker.querySelector('.bubble');
      if (bubble) bubble.style.display = 'block';
    }
  }

  async function refreshRealtimeStatus() {
    const statusEl = document.getElementById('realtime-status');
    const listEl = document.getElementById('realtime-alerts');
    if (!statusEl || !listEl) return;
    try {
      const res = await fetch('/api/realtime_status', { cache: 'no-store' });
      const data = await res.json();
      const messages = Array.isArray(data?.messages) ? data.messages : [];
      listEl.innerHTML = '';
      if (!messages.length) {
        statusEl.textContent = '目前沒有需要補貨的書籍。';
        setRealtimeWorker(false);
        return;
      }
      statusEl.textContent = `目前有 ${messages.length} 筆補貨提醒`;
      messages.forEach((message) => {
        const li = document.createElement('li');
        li.textContent = message;
        listEl.appendChild(li);
      });
      setRealtimeWorker(true);
    } catch (err) {
      console.error(err);
      statusEl.textContent = '無法載入補貨資訊，請稍後再試。';
    }
  }

  function openRealtimeModal() {
    const overlay = document.getElementById('realtime-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    refreshRealtimeStatus();
    if (!realtimeTimer) {
      realtimeTimer = setInterval(refreshRealtimeStatus, 30000);
    }
  }

  function closeRealtimeModal() {
    const overlay = document.getElementById('realtime-overlay');
    if (overlay) overlay.style.display = 'none';
    if (realtimeTimer) {
      clearInterval(realtimeTimer);
      realtimeTimer = null;
    }
  }

  document.addEventListener('click', (event) => {
    const closeBtn = event.target.closest('#realtime-close');
    if (closeBtn) {
      closeRealtimeModal();
      return;
    }
    const overlay = document.getElementById('realtime-overlay');
    if (overlay && event.target === overlay) {
      closeRealtimeModal();
    }
  });

  function openBookModal(title) {
    const overlay = document.getElementById('book-modal-overlay');
    const box = document.getElementById('book-modal-box');
    if (!overlay || !box) return;

    fetch(`/book_details/${encodeURIComponent(title)}`, { cache: 'no-store' })
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
  window.closeModal = closeBookModal;

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

  function setupScannerFocus() {
    const customerInput = document.querySelector('.customer-search input[name="q"]');
    const adminInput = document.querySelector('#admin-search-form input[name="filter"]');
    const targetInput = customerInput || adminInput;
    if (!targetInput) return;

    let scanBuffer = '';
    let resetTimer = null;

    const resetBuffer = () => {
      scanBuffer = '';
    };

    document.addEventListener('keydown', (evt) => {
      const target = evt.target;
      const isInput = target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      if (isInput) return;

      if (evt.key.length === 1 && !evt.metaKey && !evt.ctrlKey && !evt.altKey) {
        targetInput.focus();
        scanBuffer += evt.key;
        targetInput.value = scanBuffer;
        clearTimeout(resetTimer);
        resetTimer = setTimeout(resetBuffer, 400);
      } else if (evt.key === 'Enter' && scanBuffer) {
        evt.preventDefault();
        targetInput.form?.requestSubmit?.();
        const firstTarget = document.querySelector('[data-title]');
        if (firstTarget && typeof window.openBookModal === 'function') {
          const title = firstTarget.getAttribute('data-title');
          if (title) {
            window.openBookModal(title);
          }
        }
        resetBuffer();
      }
    });

    setInterval(() => {
      const active = document.activeElement;
      const activeTag = active && active.tagName;
      const isTyping =
        activeTag === 'INPUT' || activeTag === 'TEXTAREA' || (active && active.isContentEditable);
      if (document.visibilityState === 'visible' && !isTyping && active !== targetInput) {
        targetInput.focus();
      }
    }, 5000);
  }

  function setupOfflineBanner() {
    const banner = document.createElement('div');
    banner.className = 'offline-banner';
    banner.textContent = '⚡ Offline - changes queued until connection returns';
    banner.style.position = 'fixed';
    banner.style.bottom = '12px';
    banner.style.left = '12px';
    banner.style.right = '12px';
    banner.style.zIndex = '9999';
    banner.style.background = '#1f2937';
    banner.style.color = '#f9fafb';
    banner.style.padding = '12px 16px';
    banner.style.borderRadius = '8px';
    banner.style.display = 'none';
    banner.style.boxShadow = '0 10px 30px rgba(0,0,0,0.2)';
    document.body.appendChild(banner);

    const toggle = (offline) => {
      banner.style.display = offline ? 'flex' : 'none';
    };

    window.addEventListener('offline', () => toggle(true));
    window.addEventListener('online', () => toggle(false));
    toggle(!navigator.onLine);
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js').catch(err => {
      console.warn('Service worker registration failed', err);
    });
  }

  function headersWithCsrf(token) {
    const freshToken = token
      || window.csrfToken
      || document.querySelector('meta[name="csrf-token"]')?.content
      || '';
    return { 'X-CSRF-Token': freshToken };
  }

  function bindInlineForms() {
    if (window._inlineFormsBound) return;
    document.addEventListener('submit', async (e) => {
      const form = e.target;
      if (!form || !form.matches || !form.matches('.inline-form')) return;
      e.preventDefault();
      e.stopPropagation();

      const formData = new FormData(form);
      const formToken = formData.get('csrf_token') || '';
      try {
        const res = await fetch(form.action, {
          method: form.method || 'POST',
          headers: headersWithCsrf(formToken),
          body: formData,
        });
        const contentType = res.headers.get('content-type') || '';
        const data = contentType.includes('application/json') ? await res.json() : null;
        if (data && data.success) {
          showToast(data.message || '狀態已更新', true);
          const title = data.title;
          if (title && typeof window.openBookModal === 'function') {
            window.openBookModal(title);
          }
        } else if (data && !data.success) {
          showToast(data.message || '操作失敗', false);
        } else if (!res.ok) {
          showToast('操作失敗', false);
        }
      } catch (err) {
        console.error('Inline form submit failed', err);
        showToast('操作失敗', false);
      }
    });
    window._inlineFormsBound = true;
  }

  function bindReplenishHints() {
    if (window._replenishHintsBound) return;
    document.addEventListener('click', async (e) => {
      const hint = e.target.closest('.replenish-hint');
      if (!hint) return;
      e.preventDefault();

      const title = hint.dataset.title;
      const displayCabinetId = Number(hint.dataset.displayCabinetId);
      const reserveCabinetId = Number(hint.dataset.reserveCabinetId);
      const reserveBookId = Number(hint.dataset.reserveBookId);
      const reserveCabinetName = hint.dataset.reserveCabinetName || '備書櫃';

      if (!title || !displayCabinetId || !reserveCabinetId || !reserveBookId) {
        showToast('補貨資訊不完整', false);
        return;
      }

      if (!window.confirm(`確定從「${reserveCabinetName}」補貨至展示櫃？`)) return;

      hint.style.opacity = '0.6';
      hint.style.pointerEvents = 'none';
      const originalText = hint.textContent;
      hint.textContent = '補貨中...';

      try {
        const res = await fetch(`/replenish/${encodeURIComponent(title)}`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...headersWithCsrf(),
          },
          body: JSON.stringify({
            display_cabinet_id: displayCabinetId,
            reserve_cabinet_id: reserveCabinetId,
            reserve_book_id: reserveBookId,
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          showToast(data.message || '補貨失敗', false);
          hint.style.opacity = '1';
          hint.style.pointerEvents = 'auto';
          hint.textContent = originalText;
          return;
        }

        showToast(data.message || '補貨成功 ✅', true);
        if (typeof window.openBookModal === 'function') {
          window.openBookModal(title);
        }
      } catch (err) {
        console.error(err);
        showToast('補貨失敗', false);
        hint.style.opacity = '1';
        hint.style.pointerEvents = 'auto';
        hint.textContent = originalText;
      }
    });
    window._replenishHintsBound = true;
  }

  document.addEventListener('DOMContentLoaded', () => {
    resetAdminSearchForm();
    bindEscapeToCloseModals();
    document.addEventListener('click', handleNotificationTabs);
    setupScannerFocus();
    setupOfflineBanner();
    registerServiceWorker();
    bindInlineForms();
    bindReplenishHints();

    const notifBtn = document.getElementById('notif-btn');
    if (notifBtn) {
      notifBtn.addEventListener('click', openNotif);
      loadNotifications();
      setInterval(loadNotifications, 120000);
    }
  });
})();
