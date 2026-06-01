(() => {
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  window.csrfToken = csrfToken;
  const allowedCoverHosts = (document.querySelector('meta[name="allowed-cover-hosts"]')?.content || '')
    .split(',')
    .map((host) => host.trim().toLowerCase())
    .filter(Boolean);

  function coverHostMatches(hostname, pattern) {
    const host = String(hostname || '').toLowerCase().replace(/\.$/, '');
    const cleanPattern = String(pattern || '').toLowerCase().replace(/\.$/, '');
    if (!host || !cleanPattern) return false;
    if (cleanPattern.startsWith('*.')) {
      const suffix = cleanPattern.slice(2);
      return host.endsWith(`.${suffix}`);
    }
    return host === cleanPattern;
  }

  function isAllowedCoverUrl(url) {
    if (!url) return false;
    try {
      const parsed = new URL(String(url), window.location.origin);
      if (parsed.origin === window.location.origin) return true;
      return parsed.protocol === 'https:'
        && allowedCoverHosts.some((host) => coverHostMatches(parsed.hostname, host));
    } catch (err) {
      return false;
    }
  }

  function createMuted(text) {
    const node = document.createElement('div');
    node.className = 'muted';
    node.textContent = text;
    return node;
  }

  function parseTrustedHtmlFragment(html) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(String(html || ''), 'text/html');
    return Array.from(doc.body.childNodes);
  }

  window.EXIS = window.EXIS || {};
  window.EXIS.isAllowedCoverUrl = isAllowedCoverUrl;
  window.EXIS.allowedCoverHosts = allowedCoverHosts;

  window.addEventListener('pageshow', (event) => {
    try {
      const nav = performance.getEntriesByType?.('navigation')?.[0];
      if (event.persisted || (nav && nav.type === 'reload')) {
        window.scrollTo(0, 0);
      }
    } catch (err) {
      window.scrollTo(0, 0);
    }
  });

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

  function bindDeclarativeActions() {
    if (window._declarativeActionsBound) return;
    document.addEventListener('click', (event) => {
      const trigger = event.target.closest('[data-ui-action]');
      if (!trigger) return;

      const action = trigger.dataset.uiAction;
      const title = trigger.dataset.title || '';
      const invoke = (fn, ...args) => {
        if (typeof fn === 'function') {
          event.preventDefault();
          fn(...args);
          return true;
        }
        return false;
      };

      if (action === 'toggle-advanced') {
        event.preventDefault();
        toggleAdvanced();
      } else if (action === 'close-notif') {
        invoke(closeNotif);
      } else if (action === 'handle-add-book') {
        invoke(handleAddBook);
      } else if (action === 'handle-cabinet-manager') {
        invoke(handleCabinetManager);
      } else if (action === 'navigate') {
        const href = trigger.dataset.href;
        if (href) {
          event.preventDefault();
          window.location.href = href;
        }
      } else if (action === 'open-book-modal') {
        invoke(window.openBookModal, title);
      } else if (action === 'open-cabinet-modal') {
        invoke(window.openCabinetModal, title);
      } else if (action === 'close-cabinet-modal') {
        invoke(window.closeCabinetModal);
      } else if (action === 'close-add-book-modal') {
        invoke(window.closeAddBookModal);
      } else if (action === 'close-cabinet-manager') {
        invoke(window.closeCabinetManager);
      } else if (action === 'close-cabinet-books-modal') {
        invoke(window.closeCabinetBooksModal);
      } else if (action === 'close-move-book-modal') {
        invoke(window.closeMoveBookModal);
      }
    });
    window._declarativeActionsBound = true;
  }

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

    list.textContent = '';
    if (!filtered.length) {
      const empty = document.createElement('div');
      empty.className = 'notif-empty';
      empty.textContent = '🎉 沒有相關通知';
      list.appendChild(empty);
      setNotifTitle(filterType);
      return;
    }

    const normalizeAlertType = (type) => {
      if (type === 'out-of-stock' || type === 'low-stock' || type === 'info') return type;
      return 'info';
    };

    const getStatusLabel = (type) => {
      if (type === 'out-of-stock') return '缺貨';
      if (type === 'low-stock') return '低庫存';
      return '資訊';
    };

    filtered.forEach(alert => {
      const safeType = normalizeAlertType(alert?.type);
      const item = document.createElement('div');
      item.className = `notif-item ${safeType}`;

      const icon = document.createElement('span');
      icon.className = 'notif-icon';
      icon.textContent = getStatusIcon(safeType);

      const body = document.createElement('div');
      body.className = 'notif-body';

      const type = document.createElement('span');
      type.className = 'notif-type';
      type.textContent = getStatusLabel(safeType);

      const message = document.createElement('span');
      message.className = 'notif-msg';
      message.textContent = String(alert?.message || '');

      body.append(type, message);
      item.append(icon, body);
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

  function openAnnouncement() {
    const overlay = document.getElementById('announcement-overlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function runSearchIntro() {
    const searchBox = document.querySelector('[data-search-intro]');
    if (!searchBox) return;
    searchBox.classList.remove('search-box--intro');
    void searchBox.offsetWidth;
    searchBox.classList.add('search-box--intro');
  }

  function closeAnnouncement() {
    const overlay = document.getElementById('announcement-overlay');
    if (overlay) overlay.style.display = 'none';
    runSearchIntro();
  }

  function openAboutModal() {
    const overlay = document.getElementById('about-overlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function closeAboutModal() {
    const overlay = document.getElementById('about-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  function openFooterModal(id) {
    const overlay = document.getElementById(id);
    if (overlay) overlay.style.display = 'flex';
  }

  function closeFooterModals() {
    document.querySelectorAll('.modal-overlay[data-footer-modal]').forEach((el) => {
      el.style.display = 'none';
    });
  }

  document.addEventListener('click', (event) => {
    const footerTrigger = event.target.closest('[data-modal-target]');
    if (footerTrigger) {
      event.preventDefault();
      openFooterModal(footerTrigger.dataset.modalTarget);
      return;
    }
    const footerClose = event.target.closest('[data-close-modal]');
    if (footerClose) {
      event.preventDefault();
      closeFooterModals();
      return;
    }
    const booksToggle = event.target.closest('.event-books-toggle');
    if (booksToggle) {
      const banner = booksToggle.closest('.event-banner');
      if (banner) {
        const isOpen = banner.classList.toggle('event-banner--books-open');
        booksToggle.textContent = isOpen ? '收合封面' : '顯示書籍封面';
      }
      return;
    }
    const closeBtn = event.target.closest('[data-close-announcement]');
    if (closeBtn) {
      closeAnnouncement();
      return;
    }
    const closeAbout = event.target.closest('[data-close-about]');
    if (closeAbout) {
      closeAboutModal();
      return;
    }
    const overlay = document.getElementById('announcement-overlay');
    if (overlay && event.target === overlay) {
      closeAnnouncement();
    }
    const aboutOverlay = document.getElementById('about-overlay');
    if (aboutOverlay && event.target === aboutOverlay) {
      closeAboutModal();
    }
    document.querySelectorAll('.modal-overlay[data-footer-modal]').forEach((el) => {
      if (event.target === el) {
        el.style.display = 'none';
      }
    });
  });

  // Home page hint actions
  document.addEventListener('DOMContentLoaded', () => {
    const focusBtn = document.getElementById('hint-focus-search');
    const input = document.querySelector('.customer-search input[name="q"]');
    const mapOverlay = document.getElementById('venue-map-modal');
    const quickGuideBtn = document.getElementById('hero-quick-guide');
    const aboutBtn = document.getElementById('about-btn');
    if (focusBtn) {
      focusBtn.addEventListener('click', () => {
        if (mapOverlay) {
          openVenueMap();
        } else if (input) {
          input.focus();
          input.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    }
    const realtimeBtn = document.getElementById('hint-realtime');
    if (realtimeBtn) {
      realtimeBtn.addEventListener('click', () => {
        openRealtimeModal();
      });
    }
    if (quickGuideBtn) {
      quickGuideBtn.addEventListener('click', () => {
        if (mapOverlay) {
          openVenueMap();
        } else if (input) {
          input.focus();
          input.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      });
    }
    loadEventsForHero();
    if (document.getElementById('announcement-overlay')) {
      openAnnouncement();
    } else {
      runSearchIntro();
    }
    if (aboutBtn) {
      aboutBtn.addEventListener('click', openAboutModal);
    }
  document.querySelectorAll('[data-issue-form]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitBtn = form.querySelector('button[type="submit"]');
      const status = form.querySelector('[data-issue-status]');
      const formData = new FormData(form);
      const payload = Object.fromEntries(formData.entries());
      if (submitBtn) submitBtn.disabled = true;
      if (status) status.textContent = '送出中...';
      try {
        const res = await fetch('/api/report_issue', {
          method: 'POST',
          headers: {
            ...headersWithCsrf(),
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
          const message = data.message || '回報送出失敗，請稍後再試。';
          if (status) status.textContent = message;
          showToast(message, false);
          return;
        }
        form.reset();
        if (status) status.textContent = data.message || '回報已送出。';
        showToast(data.message || '回報已送出。', true);
        setTimeout(closeFooterModals, 700);
      } catch (err) {
        console.error(err);
        const message = '目前無法送出回報，請確認網路連線後再試。';
        if (status) status.textContent = message;
        showToast(message, false);
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  });
  const footerStatusBtn = document.getElementById('footer-status-btn');
  if (footerStatusBtn) {
    footerStatusBtn.addEventListener('click', (event) => {
      event.preventDefault();
      const statusModal = document.getElementById('status-modal');
      if (statusModal) statusModal.style.display = 'flex';
    });
  }
});

  let venueMapReady = false;
  const zonePinPositions = {
    管理: { left: '46%', top: '26%' },
    動志: { left: '66%', top: '24%' },
    '親子+文學': { left: '66%', top: '30%' },
    新書: { left: '31%', top: '42%' },
    商業: { left: '47%', top: '48%' },
    科普: { left: '63%', top: '48%' },
    健康: { left: '63%', top: '58%' },
    工作: { left: '46%', top: '74%' },
    社文: { left: '63%', top: '74%' },
    暢銷: { left: '80%', top: '42%' },
  };

  function normalizeCabinetName(name) {
    return (name || '')
      .toString()
      .trim()
      .replace(/\s+/g, '')
      .replace(/＋/g, '+');
  }

  function getZoneKey(name) {
    const normalized = normalizeCabinetName(name);
    const match = normalized.match(/^([^\d]+)\d+/);
    if (match) return match[1];
    const alphaMatch = normalized.match(/^([A-Za-z]+)\d*/);
    if (alphaMatch) return alphaMatch[1];
    return normalized || '未分類';
  }

  function formatZoneTitle(zoneKey) {
    if (/^[A-Za-z]+$/.test(zoneKey)) {
      return `Zone ${zoneKey}`;
    }
    return zoneKey;
  }

  function buildVenueZones(cabinets) {
    const zones = new Map();
    cabinets.forEach((cab) => {
      const zoneKey = getZoneKey(cab.name);
      if (!zones.has(zoneKey)) {
        zones.set(zoneKey, []);
      }
      zones.get(zoneKey).push(cab.name);
    });
    return Array.from(zones.entries()).map(([key, names]) => ({
      key,
      title: formatZoneTitle(key),
      cabinets: names,
    }));
  }

  function renderVenueZones(zones) {
    const container = document.getElementById('venue-map-zones');
    if (!container) return;
    container.replaceChildren();
    if (!zones.length) {
      container.appendChild(createMuted('目前沒有可用櫃位。'));
      return;
    }
    zones.forEach((zone) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'venue-zone-card';
      card.dataset.zone = zone.key;
      const subtitle = zone.cabinets.join('、');
      const title = document.createElement('strong');
      title.textContent = zone.title;
      const details = document.createElement('span');
      details.textContent = `包含：${subtitle}`;
      card.append(title, details);
      container.appendChild(card);
    });
  }

  function renderVenuePins(zones) {
    const pins = document.getElementById('venue-map-pins');
    if (!pins) return;
    pins.replaceChildren();
    zones.forEach((zone, idx) => {
      const pin = document.createElement('div');
      pin.className = 'venue-map-pin';
      pin.dataset.zone = zone.key;
      const position = zonePinPositions[zone.key];
      if (position) {
        pin.style.left = position.left;
        pin.style.top = position.top;
      } else {
        const row = Math.floor(idx / 4);
        const col = idx % 4;
        pin.style.left = `${18 + col * 18}%`;
        pin.style.top = `${82 + row * 8}%`;
      }
      pin.textContent = zone.title;
      pins.appendChild(pin);
    });
  }

  function initVenueMap() {
    const modal = document.getElementById('venue-map-modal');
    let cabinets = [];
    try {
      cabinets = JSON.parse(modal?.dataset.activeCabinets || '[]');
    } catch (err) {
      cabinets = [];
    }
    cabinets = Array.isArray(cabinets) ? cabinets : [];
    const zones = buildVenueZones(cabinets);
    renderVenueZones(zones);
    renderVenuePins(zones);
  }

  function openVenueMap() {
    const overlay = document.getElementById('venue-map-modal');
    if (!overlay) return;
    overlay.style.display = 'flex';
    if (!venueMapReady) {
      initVenueMap();
      venueMapReady = true;
    }
  }

  function closeVenueMap() {
    const overlay = document.getElementById('venue-map-modal');
    if (overlay) overlay.style.display = 'none';
  }

  document.addEventListener('click', (event) => {
    const closeBtn = event.target.closest('#venue-map-close');
    if (closeBtn) {
      closeVenueMap();
      return;
    }
    const overlay = document.getElementById('venue-map-modal');
    if (overlay && event.target === overlay) {
      closeVenueMap();
    }
  });

  document.addEventListener('mouseover', (event) => {
    const card = event.target.closest('.venue-zone-card');
    if (!card) return;
    const zoneKey = card.dataset.zone;
    document.querySelectorAll('.venue-zone-card').forEach((el) => el.classList.remove('is-active'));
    document.querySelectorAll('.venue-map-pin').forEach((el) => el.classList.remove('highlight-zone'));
    card.classList.add('is-active');
    const pin = document.querySelector(`.venue-map-pin[data-zone="${zoneKey}"]`);
    if (pin) pin.classList.add('highlight-zone');
  });

  document.addEventListener('click', (event) => {
    const card = event.target.closest('.venue-zone-card');
    if (!card) return;
    const zoneKey = card.dataset.zone;
    document.querySelectorAll('.venue-zone-card').forEach((el) => el.classList.remove('is-active'));
    document.querySelectorAll('.venue-map-pin').forEach((el) => el.classList.remove('highlight-zone'));
    card.classList.add('is-active');
    const pin = document.querySelector(`.venue-map-pin[data-zone="${zoneKey}"]`);
    if (pin) pin.classList.add('highlight-zone');
  });

  document.addEventListener('mouseout', (event) => {
    const zonePanel = event.target.closest('#venue-map-zones');
    if (!zonePanel) return;
    if (zonePanel.contains(event.relatedTarget)) return;
    document.querySelectorAll('.venue-zone-card').forEach((el) => el.classList.remove('is-active'));
    document.querySelectorAll('.venue-map-pin').forEach((el) => el.classList.remove('highlight-zone'));
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
      const hasWork = typeof data?.has_work === 'boolean' ? data.has_work : messages.length > 0;
      listEl.replaceChildren();
      if (!messages.length) {
        statusEl.textContent = '目前沒有需要補貨的書籍。';
        setRealtimeWorker(hasWork);
        return;
      }
      statusEl.textContent = `目前有 ${messages.length} 筆補貨提醒`;
      messages.forEach((message) => {
        const li = document.createElement('li');
        li.textContent = message;
        listEl.appendChild(li);
      });
      setRealtimeWorker(hasWork);
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
        box.replaceChildren(...parseTrustedHtmlFragment(html));
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
    box.replaceChildren();
  }

  window.openBookModal = openBookModal;
  window.closeBookModal = closeBookModal;
  window.closeModal = closeBookModal;

  let eventRotationTimer = null;

  async function openEventBooksModal(evt) {
    const overlay = document.getElementById('event-books-overlay');
    const listEl = document.getElementById('event-books-list');
    const titleEl = document.getElementById('event-books-title');
    if (!overlay || !listEl) return;

    const books = Array.isArray(evt?.books) ? evt.books : [];
    const eventTitle = evt?.title || '';

    if (titleEl) {
      titleEl.textContent = eventTitle ? `「${eventTitle}」相關書籍` : '相關書籍';
    }

    listEl.replaceChildren();
    if (!books.length) {
      const empty = document.createElement('div');
      empty.className = 'event-books-empty muted';
      empty.textContent = '目前沒有相關書籍';
      listEl.appendChild(empty);
      overlay.style.display = 'flex';
      return;
    }

    const content = document.createElement('div');
    content.className = 'cover-modal-content event-books-content';

    const coverCol = document.createElement('div');
    coverCol.className = 'cover-modal-body';

    const detailCol = document.createElement('div');
    detailCol.className = 'event-books-panel';

    const meta = document.createElement('div');
    meta.className = 'cover-modal-meta';

    const cabinets = document.createElement('div');
    cabinets.className = 'cover-modal-cabinets';

    const selector = document.createElement('div');
    selector.className = 'event-book-selector';

    detailCol.appendChild(meta);
    detailCol.appendChild(cabinets);
    detailCol.appendChild(selector);

    content.appendChild(coverCol);
    content.appendChild(detailCol);
    listEl.appendChild(content);

    const renderSelector = (activeIndex) => {
      selector.replaceChildren();
      if (books.length <= 1) {
        selector.style.display = 'none';
        return;
      }
      selector.style.display = 'flex';
      books.forEach((book, idx) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = `event-book-thumb${idx === activeIndex ? ' is-active' : ''}`;
        btn.setAttribute('aria-label', book?.title || '書籍');
        if (isAllowedCoverUrl(book?.cover_url)) {
          const img = document.createElement('img');
          img.src = book.cover_url;
          img.alt = book.title || '';
          btn.appendChild(img);
        } else {
          btn.textContent = '📘';
        }
        btn.addEventListener('click', () => renderBook(book, idx));
        selector.appendChild(btn);
      });
    };

    const renderBookMeta = (book) => {
      const titleText = book?.title || '未命名書籍';
      const authorText = book?.author || '';
      const row = document.createElement('div');
      row.className = 'cover-meta-row';
      const titleNode = document.createElement('div');
      titleNode.className = 'cover-title';
      titleNode.textContent = titleText;
      row.appendChild(titleNode);
      if (authorText) {
        const authorNode = document.createElement('div');
        authorNode.className = 'cover-author';
        authorNode.textContent = `作者：${authorText}`;
        row.appendChild(authorNode);
      }
      meta.replaceChildren(row);
    };

    const renderBookCover = (book) => {
      coverCol.replaceChildren();
      if (isAllowedCoverUrl(book?.cover_url)) {
        const img = document.createElement('img');
        img.className = 'cover-modal-img';
        img.src = book.cover_url;
        img.alt = book.title || '';
        coverCol.appendChild(img);
      } else {
        const fallback = document.createElement('div');
        fallback.className = 'event-books-empty muted';
        fallback.textContent = '沒有封面';
        coverCol.appendChild(fallback);
      }
    };

    const renderBookCabinets = async (book) => {
      cabinets.replaceChildren(createMuted('載入櫃位中...'));
      if (!book?.title) {
        cabinets.replaceChildren(createMuted('找不到書名'));
        return;
      }
      try {
        const res = await fetch(`/book_details/${encodeURIComponent(book.title)}`, { cache: 'no-store' });
        const html = await res.text();
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const rows = Array.from(doc.querySelectorAll('.modal-row'));
        cabinets.replaceChildren();
        if (!rows.length) {
          cabinets.replaceChildren(createMuted('沒有可顯示的櫃位資訊'));
          return;
        }
        rows.forEach((row) => {
          const cabinetEl = row.querySelector('span');
          const statusEl = row.querySelector('.stat');
          const cabinet = cabinetEl ? cabinetEl.textContent.trim() : '';
          const status = statusEl ? statusEl.textContent.trim() : '';
          const statusClasses = statusEl ? Array.from(statusEl.classList) : [];
          const toneClass = statusClasses.find(cls => cls.includes('out') || cls.includes('in') || cls.startsWith('status--')) || '';
          const rowClass = toneClass.includes('out') ? 'out-stock' : toneClass.includes('in') ? 'in-stock' : '';

          const rowDiv = document.createElement('div');
          rowDiv.className = `cover-cab-row ${rowClass}`.trim();

          const cabSpan = document.createElement('span');
          cabSpan.className = 'cabinet';
          cabSpan.textContent = cabinet;

          const statSpan = document.createElement('span');
          statSpan.className = `status ${toneClass}`.trim();
          statSpan.textContent = status;

          rowDiv.appendChild(cabSpan);
          rowDiv.appendChild(statSpan);
          cabinets.appendChild(rowDiv);
        });
      } catch (err) {
        console.error(err);
        cabinets.replaceChildren(createMuted('載入櫃位資訊失敗'));
      }
    };

    const renderBook = (book, index) => {
      renderBookCover(book);
      renderBookMeta(book);
      renderBookCabinets(book);
      renderSelector(index);
    };

    renderBook(books[0], 0);
    overlay.style.display = 'flex';
  }

  function closeEventBooksModal() {
    const overlay = document.getElementById('event-books-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  const parseDateOnly = (value) => {
    if (!value) return null;
    const parts = String(value).split('-').map(Number);
    if (parts.length < 3) return null;
    const [year, month, day] = parts;
    if (!year || !month || !day) return null;
    return new Date(year, month - 1, day);
  };

  const formatDate = (date) => {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}/${m}/${d}`;
  };

  const formatDateRange = (start, end) => {
    if (!start && !end) return '';
    if (start && !end) return formatDate(start);
    if (!start && end) return formatDate(end);
    if (start.getTime() === end.getTime()) return formatDate(start);
    return `${formatDate(start)} - ${formatDate(end)}`;
  };

  const parseTimeRange = (text) => {
    if (!text) return null;
    const matches = String(text).match(/(\d{1,2}):(\d{2})/g);
    if (!matches || matches.length < 2) return null;
    const toMinutes = (val) => {
      const [h, m] = val.split(':').map(Number);
      return h * 60 + m;
    };
    return { start: toMinutes(matches[0]), end: toMinutes(matches[1]) };
  };

  const isEventActive = (eventData) => {
    const now = new Date();
    const dateOnly = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startDate = parseDateOnly(eventData?.date_start);
    const endDate = parseDateOnly(eventData?.date_end) || startDate;
    if (startDate || endDate) {
      if (startDate && dateOnly < startDate) return false;
      if (endDate && dateOnly > endDate) return false;
    } else {
      return false;
    }
    const range = parseTimeRange(eventData?.time_text);
    if (!range) return true;
    const nowMinutes = now.getHours() * 60 + now.getMinutes();
    if (range.end < range.start) {
      return nowMinutes >= range.start || nowMinutes <= range.end;
    }
    return nowMinutes >= range.start && nowMinutes <= range.end;
  };

  function buildEventSlide(evt) {
    const slide = document.createElement('div');
    slide.className = 'event-slide';
    const banner = document.createElement('section');
    banner.className = 'event-banner';

    const isActive = isEventActive(evt);
    if (isActive) {
      banner.classList.add('event-banner--active');
    }

    const content = document.createElement('div');
    content.className = 'event-banner__content';

    if (isActive) {
      const liveTag = document.createElement('span');
      liveTag.className = 'event-live';
      liveTag.textContent = '進行中';
      content.appendChild(liveTag);
    }

    const title = document.createElement('h2');
    title.className = 'event-title';
    title.textContent = evt?.title || '活動時段';
    content.appendChild(title);

    const time = document.createElement('div');
    time.className = 'event-time';
    time.replaceChildren();
    const timeLabel = document.createElement('span');
    timeLabel.className = 'event-label';
    timeLabel.textContent = '時間';
    time.appendChild(timeLabel);
    time.appendChild(document.createTextNode(evt?.time_text || ''));
    if (!time.textContent) {
      time.style.display = 'none';
    }
    const dateStart = parseDateOnly(evt?.date_start);
    const dateEnd = parseDateOnly(evt?.date_end);
    const dateText = formatDateRange(dateStart, dateEnd);
    if (dateText) {
      const dateRow = document.createElement('div');
      dateRow.className = 'event-date';
      const dateLabel = document.createElement('span');
      dateLabel.className = 'event-label';
      dateLabel.textContent = '日期';
      dateRow.appendChild(dateLabel);
      dateRow.appendChild(document.createTextNode(dateText));
      content.appendChild(dateRow);
    }
    content.appendChild(time);

    if (evt?.description) {
      const desc = document.createElement('p');
      desc.className = 'event-desc';
      const descLabel = document.createElement('span');
      descLabel.className = 'event-label';
      descLabel.textContent = '說明';
      desc.appendChild(descLabel);
      desc.appendChild(document.createTextNode(evt.description));
      content.appendChild(desc);
    }

    const meta = document.createElement('ul');
    meta.className = 'event-meta';
    const metaItems = [];
    if (evt?.location) metaItems.push({ label: '地點', value: evt.location });
    metaItems.forEach((item) => {
      const li = document.createElement('li');
      const label = document.createElement('span');
      label.className = 'event-label';
      label.textContent = item.label;
      li.appendChild(label);
      li.appendChild(document.createTextNode(item.value));
      meta.appendChild(li);
    });
    if (metaItems.length) {
      content.appendChild(meta);
    }

    const books = Array.isArray(evt?.books) ? evt.books : [];
    if (books.length) {
      const hint = document.createElement('p');
      hint.className = 'event-hint';
      hint.textContent = '點擊查看封面';
      content.appendChild(hint);

      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'btn btn--secondary btn--sm event-books-toggle';
      toggleBtn.textContent = '顯示書籍封面';
      content.appendChild(toggleBtn);
    }
    // No CTA button needed; book covers are clickable.

    banner.appendChild(content);

    const visuals = document.createElement('div');
    visuals.className = 'event-banner__visuals';
    const grid = document.createElement('div');
    grid.className = 'book-showcase-grid';

    const maxCovers = 3;
    const picks = books.slice(0, maxCovers);
    picks.forEach((book, idx) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = `showcase-book${idx === 0 ? ' is-primary' : ''}`;
      item.setAttribute('aria-label', book?.title ? `查看 ${book.title}` : '查看書籍');
      if (isAllowedCoverUrl(book?.cover_url)) {
        const img = document.createElement('img');
        img.src = book.cover_url;
        img.alt = book.title || '';
        item.appendChild(img);
      } else {
        item.textContent = '📘';
      }
      if (book?.title && typeof window.openBookModal === 'function') {
        item.addEventListener('click', () => window.openBookModal(book.title));
      }
      grid.appendChild(item);
    });

    if (books.length > maxCovers) {
      const more = document.createElement('a');
      more.className = 'showcase-more';
      more.href = '#';
      more.setAttribute('aria-label', '查看更多推薦書籍');
      more.addEventListener('click', (event) => {
        event.preventDefault();
        openEventBooksModal(evt);
      });

      const count = document.createElement('span');
      count.textContent = `+${books.length - maxCovers}`;
      const label = document.createElement('small');
      label.textContent = 'More';
      more.appendChild(count);
      more.appendChild(label);
      grid.appendChild(more);
    }

    visuals.appendChild(grid);
    banner.appendChild(visuals);

    if (!books.length) {
      banner.classList.add('event-banner--no-books');
    }

    slide.appendChild(banner);
    slide.dataset.active = isActive ? '1' : '0';
    return slide;
  }

  function renderEventDots(count, activeIdx) {
    const dots = document.getElementById('event-dots');
    if (!dots) return;
    dots.replaceChildren();
    if (count <= 1) return;
    for (let i = 0; i < count; i++) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `event-dot${i === activeIdx ? ' is-active' : ''}`;
      btn.dataset.index = String(i);
      dots.appendChild(btn);
    }
  }

  function setEventIndex(track, idx) {
    const width = track.parentElement?.clientWidth || 0;
    if (width) {
      track.style.transform = `translateX(${-idx * width}px)`;
    } else {
      track.style.transform = `translateX(-${idx * 100}%)`;
    }
    renderEventDots(track.children.length, idx);
  }

  async function loadEventsForHero() {
    const track = document.getElementById('event-track');
    const dots = document.getElementById('event-dots');
    if (!track || !dots) return;
    try {
      const res = await fetch('/api/events', { cache: 'no-store' });
      const data = await res.json();
      const events = Array.isArray(data?.events) ? data.events : [];
      if (!events.length) {
        const section = document.getElementById('events-section');
        if (section) section.remove();
        track.replaceChildren();
        dots.replaceChildren();
        return;
      }
      track.replaceChildren();
      const sortedEvents = [...events].sort((a, b) => {
        const aActive = isEventActive(a);
        const bActive = isEventActive(b);
        if (aActive === bActive) return 0;
        return aActive ? -1 : 1;
      });
      sortedEvents.forEach((evt) => {
        track.appendChild(buildEventSlide(evt));
      });

      let idx = 0;
      setEventIndex(track, idx);

      dots.addEventListener('click', (e) => {
        const btn = e.target.closest('.event-dot');
        if (!btn) return;
        idx = Number(btn.dataset.index) || 0;
        setEventIndex(track, idx);
        if (eventRotationTimer) {
          clearInterval(eventRotationTimer);
          eventRotationTimer = setInterval(() => {
            idx = (idx + 1) % events.length;
            setEventIndex(track, idx);
          }, 10000);
        }
      });

      if (events.length > 1) {
        eventRotationTimer = setInterval(() => {
          idx = (idx + 1) % events.length;
          setEventIndex(track, idx);
        }, 10000);
      }

      const carousel = track.parentElement;
      if (carousel) {
        let startX = 0;
        let startY = 0;
        let deltaX = 0;
        let dragging = false;
        let pointerDown = false;

        const isInteractiveTarget = (event) => {
          const target = event.target;
          if (!target || !target.closest) return false;
          return Boolean(target.closest('a, button, input, textarea, select'));
        };

        const onPointerDown = (event) => {
          if (isInteractiveTarget(event)) return;
          pointerDown = true;
          dragging = false;
          startX = event.clientX;
          startY = event.clientY;
          deltaX = 0;
        };

        const onPointerMove = (event) => {
          if (!pointerDown) return;
          deltaX = event.clientX - startX;
          const deltaY = event.clientY - startY;
          if (!dragging) {
            if (Math.abs(deltaX) < 6 && Math.abs(deltaY) < 6) return;
            if (Math.abs(deltaY) > Math.abs(deltaX)) {
              pointerDown = false;
              return;
            }
            dragging = true;
            carousel.classList.add('is-dragging');
            if (eventRotationTimer) {
              clearInterval(eventRotationTimer);
              eventRotationTimer = null;
            }
            carousel.setPointerCapture?.(event.pointerId);
          }
          const width = carousel.clientWidth || 1;
          const base = -idx * width;
          track.style.transform = `translateX(${base + deltaX}px)`;
        };

        const onPointerUp = () => {
          if (!pointerDown) return;
          dragging = false;
          pointerDown = false;
          carousel.classList.remove('is-dragging');
          const width = carousel.clientWidth || 1;
          if (Math.abs(deltaX) > width * 0.2) {
            if (deltaX < 0) {
              idx = idx + 1 >= events.length ? 0 : idx + 1;
            } else {
              idx = idx - 1 < 0 ? events.length - 1 : idx - 1;
            }
          }
          setEventIndex(track, idx);
          if (events.length > 1 && !eventRotationTimer) {
            eventRotationTimer = setInterval(() => {
              idx = (idx + 1) % events.length;
              setEventIndex(track, idx);
            }, 10000);
          }
        };

        carousel.addEventListener('pointerdown', onPointerDown);
        carousel.addEventListener('pointermove', onPointerMove);
        carousel.addEventListener('pointerup', onPointerUp);
        carousel.addEventListener('pointerleave', onPointerUp);
        carousel.addEventListener('wheel', (event) => {
          if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
          event.preventDefault();
          if (eventRotationTimer) {
            clearInterval(eventRotationTimer);
            eventRotationTimer = null;
          }
          if (event.deltaY > 0) {
            idx = idx + 1 >= events.length ? 0 : idx + 1;
          } else {
            idx = idx - 1 < 0 ? events.length - 1 : idx - 1;
          }
          setEventIndex(track, idx);
          if (events.length > 1 && !eventRotationTimer) {
            eventRotationTimer = setInterval(() => {
              idx = (idx + 1) % events.length;
              setEventIndex(track, idx);
            }, 10000);
          }
        }, { passive: false });
        window.addEventListener('resize', () => setEventIndex(track, idx));
      }
    } catch (err) {
      console.error(err);
    }
  }

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
        'event-books-overlay',
        'book-modal-overlay',
        'announcement-overlay',
        'about-overlay',
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

    const isInViewport = (el) => {
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      return rect.top >= 0 && rect.bottom <= (window.innerHeight || document.documentElement.clientHeight);
    };

    setInterval(() => {
      const active = document.activeElement;
      const activeTag = active && active.tagName;
      const isTyping =
        activeTag === 'INPUT' || activeTag === 'TEXTAREA' || (active && active.isContentEditable);
      if (document.visibilityState === 'visible' && !isTyping && active !== targetInput && isInViewport(targetInput)) {
        targetInput.focus({ preventScroll: true });
      }
    }, 5000);
  }

  function setupOfflineBanner() {
    const banner = document.createElement('div');
    banner.className = 'offline-banner';
    banner.textContent = '目前離線：系統只提供已快取的靜態資源，新增或修改不會排隊，請連線後再操作。';
    document.body.appendChild(banner);

    const toggle = (offline) => {
      banner.classList.toggle('is-visible', offline);
    };

    window.addEventListener('offline', () => toggle(true));
    window.addEventListener('online', () => toggle(false));
    toggle(!navigator.onLine);
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    const hadController = Boolean(navigator.serviceWorker.controller);
    let refreshedForNewWorker = false;

    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!hadController || refreshedForNewWorker) return;
      refreshedForNewWorker = true;
      window.location.reload();
    });

    navigator.serviceWorker.register('/sw.js', { updateViaCache: 'none' })
      .then((registration) => {
        const activateWaitingWorker = () => {
          if (registration.waiting) {
            registration.waiting.postMessage({ type: 'SKIP_WAITING' });
          }
        };

        registration.addEventListener('updatefound', () => {
          const worker = registration.installing;
          if (!worker) return;
          worker.addEventListener('statechange', () => {
            if (worker.state === 'installed' && navigator.serviceWorker.controller) {
              worker.postMessage({ type: 'SKIP_WAITING' });
            }
          });
        });

        return registration.update().then(activateWaitingWorker).catch(() => {});
      })
      .catch(err => {
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

  function setupAdminSidebar() {
    const toggleBtn = document.getElementById('admin-sidebar-toggle');
    const closeBtn = document.getElementById('admin-sidebar-close');
    const overlay = document.getElementById('admin-sidebar-overlay');

    if (toggleBtn && closeBtn && overlay) {
      toggleBtn.addEventListener('click', () => {
        document.body.classList.add('sidebar-is-open');
      });

      closeBtn.addEventListener('click', () => {
        document.body.classList.remove('sidebar-is-open');
      });

      overlay.addEventListener('click', () => {
        document.body.classList.remove('sidebar-is-open');
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindDeclarativeActions();
    resetAdminSearchForm();
    bindEscapeToCloseModals();
    document.addEventListener('click', handleNotificationTabs);
    document.addEventListener('click', (event) => {
      const card = event.target.closest('.book-card-mini[data-title]');
      if (!card) return;
      const title = card.dataset.title || '';
      if (!title || typeof window.openBookModal !== 'function') return;
      event.preventDefault();
      window.openBookModal(title);
    });
    setupScannerFocus();
    setupOfflineBanner();
    registerServiceWorker();
    bindInlineForms();
    bindReplenishHints();
    setupAdminSidebar();

    const notifBtn = document.getElementById('notif-btn');
    if (notifBtn) {
      notifBtn.addEventListener('click', openNotif);
      loadNotifications();
      setInterval(loadNotifications, 120000);
    }

    const eventOverlay = document.getElementById('event-books-overlay');
    if (eventOverlay) {
      eventOverlay.addEventListener('click', (event) => {
        if (event.target === eventOverlay || event.target.closest('[data-close-event-books]')) {
          closeEventBooksModal();
        }
      });
    }
  });
})();
