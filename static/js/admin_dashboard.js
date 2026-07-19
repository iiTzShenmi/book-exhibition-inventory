(() => {
  const dashboard = document.getElementById('admin-dashboard');
  if (!dashboard) return;

  const originalToast = window.showToast;
  window.showToast = (msg, success, undo) => {
    document.querySelectorAll('.toast').forEach((toast) => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 200);
    });
    if (originalToast) originalToast(msg, success, undo);
  };

  const initialCabinets = (() => {
    try {
      return JSON.parse(dashboard.dataset.allCabinets || '[]');
    } catch (err) {
      console.error('Unable to parse cabinets payload', err);
      return [];
    }
  })();

  const ALL_CABINET_NAMES = initialCabinets.map(cab => cab.name).filter(Boolean);
  const isAllowedCoverUrl = window.EXIS?.isAllowedCoverUrl || (() => false);
  let currentTitle = null;
  let currentCabinetNames = [];
  let cabinetCache = [...initialCabinets];
  let currentCabinetBooksId = null;
  let pendingMove = null;

  function setPending(button, isPending, pendingLabel) {
    if (!button) return;
    if (!button.dataset.idleLabel) button.dataset.idleLabel = button.textContent.trim();
    button.disabled = isPending;
    button.textContent = isPending ? pendingLabel : button.dataset.idleLabel;
  }

  const modalReturnFocus = new Map();

  function openAdminModal(overlay, focusTarget = null) {
    if (!overlay) return;
    modalReturnFocus.set(overlay.id, document.activeElement);
    overlay.style.display = 'flex';
    overlay.setAttribute('aria-hidden', 'false');
    window.requestAnimationFrame(() => {
      const target = focusTarget || overlay.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      target?.focus();
    });
  }

  function closeAdminModal(overlay) {
    if (!overlay || overlay.style.display !== 'flex') return;
    overlay.style.display = 'none';
    overlay.setAttribute('aria-hidden', 'true');
    const returnFocus = modalReturnFocus.get(overlay.id);
    modalReturnFocus.delete(overlay.id);
    if (returnFocus instanceof HTMLElement && document.contains(returnFocus)) returnFocus.focus();
  }

  const refreshNotificationsIfAvailable = () => {
    if (typeof window.refreshNotifications === 'function') {
      window.refreshNotifications();
    }
  };

  const getFreshCsrfToken = (token = null) => (
    token
      || window.csrfToken
      || document.querySelector('meta[name="csrf-token"]')?.content
      || ''
  );

  const headersWithCsrf = (token = null) => ({
    'X-CSRF-Token': getFreshCsrfToken(token),
  });

  function parseTrustedHtmlFragment(html) {
    const parser = new DOMParser();
    const doc = parser.parseFromString(String(html || ''), 'text/html');
    return Array.from(doc.body.childNodes);
  }

  async function refreshModal(title) {
    const box = document.getElementById('book-modal-box');
    if (!box) return;
    const res = await fetch(`/book_details/${encodeURIComponent(title)}`, { cache: 'no-store' });
    if (res.ok) {
      const html = await res.text();
      box.replaceChildren(...parseTrustedHtmlFragment(html));
      // Re-bind inline forms after modal content is updated
      // The event listener should work via delegation, but ensure it's set up
    }
  }

  function collectCabinetNamesForTitle(title) {
    const card = document.getElementById(`card-${CSS.escape(title)}`);
    if (!card) return [];
    const seen = new Set();
    card.querySelectorAll('.status-row .cab').forEach(node => {
      const dataName = node.dataset.cabinet;
      const label = dataName || node.textContent.replace(/^📍\s*/, '').trim();
      if (label) seen.add(label);
    });
    return Array.from(seen);
  }

  function setCabinetAction(action) {
    const actionInput = document.getElementById('cabinet-action');
    if (!actionInput) return;
    const buttons = document.querySelectorAll('.action-btn');
    buttons.forEach(btn => btn.classList.remove('active'));
    let target = document.querySelector(`.action-btn.${action}`);
    if (!target || target.disabled) {
      action = 'add';
      target = document.querySelector('.action-btn.add');
    }
    if (target) target.classList.add('active');
    actionInput.value = action;
  }

  function updateCabinetSelect() {
    const select = document.getElementById('cabinet-select');
    const actionInput = document.getElementById('cabinet-action');
    const existsNotice = document.getElementById('cabinet-exists-notice');
    if (!select || !actionInput) return;

    let rawSource = actionInput.value === 'remove' ? currentCabinetNames : ALL_CABINET_NAMES;
    // When adding, exclude cabinets the title already has
    if (actionInput.value === 'add' && currentCabinetNames.length) {
      rawSource = rawSource.filter(name => !currentCabinetNames.includes(name));
    }
    const source = Array.from(new Set((rawSource || []).filter(Boolean))).sort();

    select.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '請選擇書櫃...';
    select.appendChild(placeholder);

    source.forEach(name => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      select.appendChild(option);
    });

    select.disabled = actionInput.value === 'remove' && source.length === 0;
    select.value = '';
    if (existsNotice) existsNotice.style.display = 'none';
  }

  function openCabinetModal(title) {
    currentTitle = title;
    const overlay = document.getElementById('cabinet-modal-overlay');
    const cabinetForm = document.getElementById('cabinet-form');
    if (cabinetForm) cabinetForm.action = `/modify_cabinet/${encodeURIComponent(title)}`;
    setCabinetAction('add');
    // Load cabinets from server to ensure accurate list
    fetch(`/api/title_cabinets/${encodeURIComponent(title)}`)
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          currentCabinetNames = (data.cabinets || []).map(c => c.cabinet).filter(Boolean);
        } else {
          currentCabinetNames = collectCabinetNamesForTitle(title);
        }
        updateCabinetSelect();
      })
      .catch(() => {
        currentCabinetNames = collectCabinetNamesForTitle(title);
        updateCabinetSelect();
      });
    openAdminModal(overlay, document.getElementById('cabinet-select'));
  }

  function closeCabinetModal() {
    const overlay = document.getElementById('cabinet-modal-overlay');
    closeAdminModal(overlay);
    currentTitle = null;
    currentCabinetNames = [];
  }

  function syncCabinetNames(cabinets) {
    ALL_CABINET_NAMES.length = 0;
    cabinets.forEach(cab => {
      if (cab?.name) ALL_CABINET_NAMES.push(cab.name);
    });
    updateCabinetSelect();
  }

  async function loadCabinets() {
    try {
      const res = await fetch('/cabinets');
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '櫃位載入失敗', false);
        return;
      }
      cabinetCache = Array.isArray(data.cabinets) ? data.cabinets : [];
      renderCabinetManager(cabinetCache);
    } catch (err) {
      console.error(err);
      showToast('櫃位載入失敗', false);
    }
  }

  function renderCabinetManager(cabinets) {
    const picker = document.getElementById('cabinet-picker');

    syncCabinetNames(cabinets);

    if (picker) {
      picker.replaceChildren();
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '請選擇書櫃...';
      picker.appendChild(placeholder);
      cabinets.forEach(cab => {
        const opt = document.createElement('option');
        opt.value = cab.id;
        opt.textContent = `${cab.name}（${cab.type === 'display' ? '展示櫃' : '備書櫃'}）`;
        picker.appendChild(opt);
      });
    }

  }

async function submitCabinetCreate(event) {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);
  const payload = {
    name: formData.get('name'),
    type: formData.get('type'),
  };

  if (!window.confirm(`新增櫃位「${payload.name || ''}」？`)) return;

  try {
    const res = await fetch('/cabinets', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...headersWithCsrf(),
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || '新增失敗', false);
      return;
    }
    const newCabinetId = data.cabinet?.id;
    const undoCreate = newCabinetId
      ? async () => {
          await fetch(`/cabinets/${newCabinetId}`, { method: 'DELETE', headers: headersWithCsrf() });
          await loadCabinets();
        }
      : null;
    showToast('已新增櫃位', true, undoCreate);
    form.reset();
    form.querySelector('select[name="type"]').value = payload.type || 'reserve';
    await loadCabinets();
  } catch (err) {
    console.error(err);
    showToast('新增失敗', false);
  }
}

  async function refreshBookCardsForTitles(titles) {
    if (!Array.isArray(titles) || !titles.length) return;
    for (const title of titles) {
      await refreshBookCard(title);
    }
  }

async function requestCabinetUpdate(id, payload, successMessage, undoHandler = null) {
  try {
    const res = await fetch(`/cabinets/${id}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
          ...headersWithCsrf(),
        },
        body: JSON.stringify(payload),
      });
    const data = await res.json();
    if (!res.ok || !data.success) {
      showToast(data.message || '更新失敗', false);
      return;
    }
    showToast(successMessage || '已更新', true, undoHandler);
    await refreshBookCardsForTitles(data.affected_titles);
    await loadCabinets();
  } catch (err) {
    console.error(err);
    showToast('更新失敗', false);
  }
}

async function promptRenameCabinet(id) {
  const cabinet = cabinetCache.find(cab => cab.id === id);
  if (!cabinet) return;
  const currentName = cabinet.name;
  const nextName = window.prompt('輸入新名稱', currentName);
  if (nextName === null) return;
  const trimmed = nextName.trim();
  if (!trimmed || trimmed === currentName) return;
  if (!window.confirm(`確定將櫃位「${currentName}」改名為「${trimmed}」？`)) return;
  const undo = () => requestCabinetUpdate(id, { name: currentName }, '名稱已還原', null);
  await requestCabinetUpdate(id, { name: trimmed }, '名稱已更新', undo);
}

async function toggleCabinetType(id) {
  const cabinet = cabinetCache.find(cab => cab.id === id);
  if (!cabinet) return;
  const nextType = cabinet.type === 'display' ? 'reserve' : 'display';
  if (!window.confirm(`確定將「${cabinet.name}」改為${nextType === 'display' ? '展示櫃' : '備書櫃'}？`)) return;
  const undo = () => requestCabinetUpdate(id, { type: cabinet.type }, '已還原類型', null);
  await requestCabinetUpdate(id, { type: nextType }, `已改為${nextType === 'display' ? '展示櫃' : '備書櫃'}`, undo);
}

  async function deleteCabinet(id) {
    const cabinet = cabinetCache.find(cab => cab.id === id);
    if (!cabinet) return;
    if (cabinet.book_count > 0) {
      showToast(`櫃位「${cabinet.name}」仍有書籍，請先移除所有書籍`, false);
      return;
    }
    if (!window.confirm(`確定要刪除「${cabinet.name}」？`)) return;

    try {
      const res = await fetch(`/cabinets/${id}`, {
        method: 'DELETE',
        headers: headersWithCsrf(),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '刪除失敗', false);
        return;
      }
      const deleted = data.deleted || {};
      const undo = deleted?.name
        ? async () => {
            await fetch('/cabinets', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                ...headersWithCsrf(),
              },
              body: JSON.stringify({ name: deleted.name, type: deleted.type || 'display' }),
            });
            await loadCabinets();
          }
        : null;
      showToast(`櫃位「${cabinet.name}」已刪除`, true, undo);
      await loadCabinets();
    } catch (err) {
      console.error(err);
      showToast('刪除失敗', false);
    }
  }

  function closeCabinetBooksModal() {
    const overlay = document.getElementById('cabinet-books-overlay');
    if (!overlay) return;
    closeAdminModal(overlay);
    overlay.removeAttribute('data-cabinet-id');
    currentCabinetBooksId = null;
  }

  async function loadCabinetBooks(cabinetId) {
    const overlay = document.getElementById('cabinet-books-overlay');
    const listEl = document.getElementById('cabinet-books-list');
    const titleEl = document.getElementById('cabinet-books-title');
    if (!overlay || !listEl || !titleEl) return;

    try {
      const res = await fetch(`/cabinets/${cabinetId}/books`);
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '讀取失敗', false);
        return;
      }
      const cabinetName = data.cabinet?.name || '';
      titleEl.textContent = cabinetName ? `櫃位：${cabinetName}` : '櫃位書籍';
      overlay.dataset.cabinetId = cabinetId;
      listEl.replaceChildren();

      if (!data.books.length) {
        const empty = document.createElement('div');
        empty.className = 'cabinet-empty';
        empty.textContent = '尚無書籍。可於管理面板新增或移動。';
        listEl.appendChild(empty);
        return;
      }

      data.books.forEach(book => {
        const row = document.createElement('div');
        row.className = 'cabinet-book-row';
        row.dataset.bookId = book.id;
        row.dataset.bookTitle = book.title;
        row.dataset.qty = book.in_stock ? 1 : 0;  // Quantity tracking removed - use in_stock boolean

        const titleSpan = document.createElement('span');
        titleSpan.className = 'cabinet-book-title';
        titleSpan.textContent = book.title;

        const statusSpan = document.createElement('span');
        statusSpan.className = `cabinet-book-status ${book.in_stock ? 'status--in' : 'status--out'}`;
        statusSpan.textContent = book.in_stock ? '在庫' : '缺貨';

        const actions = document.createElement('div');
        actions.className = 'cabinet-book-actions';

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'btn btn--secondary btn--sm';
        toggleBtn.dataset.bookAction = 'toggle';
        toggleBtn.textContent = book.in_stock ? '設為缺貨' : '設為在庫';

        const moveBtn = document.createElement('button');
        moveBtn.type = 'button';
        moveBtn.className = 'btn btn--primary btn--sm';
        moveBtn.dataset.bookAction = 'move';
        moveBtn.textContent = '移至櫃位';

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn btn--danger btn--sm';
        removeBtn.dataset.bookAction = 'remove';
        removeBtn.textContent = '封存紀錄';

        actions.appendChild(toggleBtn);
        actions.appendChild(moveBtn);
        actions.appendChild(removeBtn);

        row.appendChild(titleSpan);
        row.appendChild(statusSpan);
        row.appendChild(actions);
        listEl.appendChild(row);
      });
    } catch (err) {
      console.error(err);
      showToast('讀取失敗', false);
    }
  }

  function openCabinetBooks(id) {
    currentCabinetBooksId = id;
    const overlay = document.getElementById('cabinet-books-overlay');
    if (!overlay) return;
    openAdminModal(overlay);
    loadCabinetBooks(id);
  }

  async function toggleCabinetBook(cabinetId, bookId, title, button = null) {
    setPending(button, true, '更新中...');
    try {
      const res = await fetch(`/cabinets/${cabinetId}/books/${bookId}/toggle`, {
        method: 'PATCH',
        headers: headersWithCsrf(),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '更新失敗', false);
        return;
      }
      const label = title ? `「${title}」狀態已更新` : '書籍狀態已更新';
      showToast(label, true);
      refreshNotificationsIfAvailable();
      await loadCabinets();
      await loadCabinetBooks(cabinetId);
      await refreshBookCardsForTitles(data.affected_titles);
    } catch (err) {
      console.error(err);
      showToast('更新失敗', false);
    } finally {
      setPending(button, false);
    }
  }

  async function removeCabinetBook(cabinetId, bookId, title, button = null) {
    const bookName = title || '未命名';
    if (!window.confirm(`確定封存「${bookName}」在此櫃位的庫存紀錄？公開查詢將不再顯示這筆紀錄。`)) return;
    setPending(button, true, '封存中...');
    try {
      const res = await fetch(`/cabinets/${cabinetId}/books/${bookId}`, {
        method: 'DELETE',
        headers: headersWithCsrf(),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '移除失敗', false);
        return;
      }
      const qtyRemoved = Math.max(Number(data.qty_removed) || 1, 1);
      const undo = async () => {
        const fd = new FormData();
        fd.append('title', data.title || bookName);
        fd.append('cabinet_id', data.cabinet_id || cabinetId);
        fd.append('amount', qtyRemoved);
        fd.append('csrf_token', getFreshCsrfToken());
        await fetch('/add_book', {
          method: 'POST',
          body: fd,
          headers: headersWithCsrf(),
        });
        await loadCabinets();
        await loadCabinetBooks(cabinetId);
        await refreshBookCardsForTitles(data.affected_titles || [data.title]);
      };
      showToast('書籍已從此櫃位封存', true, undo);
      refreshNotificationsIfAvailable();
      await loadCabinets();
      await loadCabinetBooks(cabinetId);
      await refreshBookCardsForTitles(data.affected_titles);
    } catch (err) {
      console.error(err);
      showToast('封存失敗', false);
    } finally {
      setPending(button, false);
    }
  }

  // Removed quantity adjust (simplified inventory: present/absent)

  function populateMoveTargets(sourceCabinetId) {
    const select = document.getElementById('move-book-target');
    if (!select) return 0;

    select.replaceChildren();
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '請選擇櫃位';
    select.appendChild(placeholder);
    select.value = '';

    const options = cabinetCache.filter(cab => cab && cab.id !== sourceCabinetId);
    options.forEach(cab => {
      const option = document.createElement('option');
      option.value = String(cab.id);
      const typeLabel = cab.type === 'display' ? '展示櫃' : '備書櫃';
      option.textContent = `${cab.name}（${typeLabel}）`;
      select.appendChild(option);
    });

    const disabled = options.length === 0;
    select.disabled = disabled;
    const submitBtn = document.querySelector('#move-book-form button[type="submit"]');
    if (submitBtn) submitBtn.disabled = disabled;
    return options.length;
  }

  function openMoveBookModal(cabinetId, bookId, title) {
    const overlay = document.getElementById('move-book-overlay');
    const form = document.getElementById('move-book-form');
    const titleEl = document.getElementById('move-book-title');
    const bookIdInput = document.getElementById('move-book-id');
    const sourceInput = document.getElementById('move-source-cabinet-id');
    const sourceEl = document.getElementById('move-book-source');
    const statusEl = document.getElementById('move-book-status');
    if (!overlay || !form || !titleEl || !bookIdInput || !sourceInput || !sourceEl) return;

    if (!cabinetCache.length) loadCabinets();

    pendingMove = { cabinetId, bookId, title };
    titleEl.textContent = title;
    bookIdInput.value = String(bookId);
    sourceInput.value = String(cabinetId);
    const source = cabinetCache.find((cabinet) => cabinet.id === cabinetId);
    sourceEl.textContent = source
      ? `${source.name}（${source.type === 'display' ? '展示櫃' : '備書櫃'}）`
      : `櫃位 #${cabinetId}`;
    if (statusEl) statusEl.textContent = '';

    const availableCount = populateMoveTargets(cabinetId);
    const select = document.getElementById('move-book-target');
    openAdminModal(overlay, select && !select.disabled ? select : null);

    if (!availableCount) {
      showToast('尚未建立其他櫃位，可先於櫃位資訊新增。', false);
    }
  }

  function closeMoveBookModal() {
    const overlay = document.getElementById('move-book-overlay');
    closeAdminModal(overlay);
    const form = document.getElementById('move-book-form');
    if (form) form.reset();
    pendingMove = null;
  }

  async function submitMoveBook(event) {
    event.preventDefault();
    const targetSelect = document.getElementById('move-book-target');
    const bookInput = document.getElementById('move-book-id');
    const sourceInput = document.getElementById('move-source-cabinet-id');
    const statusEl = document.getElementById('move-book-status');
    const submitBtn = event.currentTarget.querySelector('button[type="submit"]');
    if (!targetSelect || !bookInput || !sourceInput) return;

    const targetId = Number(targetSelect.value);
    const bookId = Number(bookInput.value);
    const sourceId = Number(sourceInput.value);

  if (!targetId) {
    showToast('請選擇目標櫃位', false);
    return;
  }

    const movingTitle = pendingMove?.title || '';
    const sourceName = document.getElementById('move-book-source')?.textContent || `櫃位 #${sourceId}`;
    const targetName = targetSelect.options[targetSelect.selectedIndex]?.textContent || '所選櫃位';
    if (!window.confirm(`確認將「${movingTitle}」從「${sourceName}」移動到「${targetName}」？`)) return;

    if (statusEl) statusEl.textContent = '移動中...';
    setPending(submitBtn, true, '移動中...');

    try {
      const res = await fetch(`/cabinets/${sourceId}/books/${bookId}/move`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          ...headersWithCsrf(),
        },
        body: JSON.stringify({ target_cabinet_id: targetId }),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '移動失敗', false);
        if (statusEl) statusEl.textContent = data.message || '移動失敗，請重新確認來源與目標櫃位。';
        return;
      }

      const label = movingTitle ? `「${movingTitle}」已移至新櫃位` : '書籍已移動';
      const movedBookId = data.book?.id || bookId;
      const affectedTitles = data.affected_titles || (movingTitle ? [movingTitle] : []);
      const undo = movedBookId
        ? async () => {
            await fetch(`/cabinets/${targetId}/books/${movedBookId}/move`, {
              method: 'PATCH',
              headers: {
                'Content-Type': 'application/json',
                ...headersWithCsrf(),
              },
              body: JSON.stringify({ target_cabinet_id: sourceId }),
            });
            await loadCabinets();
            await loadCabinetBooks(sourceId);
            await refreshBookCardsForTitles(affectedTitles);
          }
        : null;
      showToast(label, true, undo);
      if (statusEl) statusEl.textContent = label;
      refreshNotificationsIfAvailable();

      closeMoveBookModal();
      await loadCabinets();
      await loadCabinetBooks(sourceId);
      await refreshBookCardsForTitles(data.affected_titles);
    } catch (err) {
      console.error(err);
      showToast('移動失敗', false);
      if (statusEl) statusEl.textContent = '移動失敗，請確認網路連線後再試。';
    } finally {
      setPending(submitBtn, false);
    }
  }

  function handleCabinetBooksClick(event) {
    const button = event.target.closest('[data-book-action]');
    if (!button) return;
    const row = button.closest('.cabinet-book-row');
    if (!row) return;
    const overlay = document.getElementById('cabinet-books-overlay');
    const cabinetId = Number(overlay?.dataset.cabinetId || currentCabinetBooksId || 0);
    if (!cabinetId) return;
    const bookId = Number(row.dataset.bookId);
    if (!bookId) return;
    const title = row.dataset.bookTitle || '未命名';
    const action = button.dataset.bookAction;
    if (action === 'toggle') {
      toggleCabinetBook(cabinetId, bookId, title, button);
    } else if (action === 'move') {
      openMoveBookModal(cabinetId, bookId, title);
    } else if (action === 'remove') {
      removeCabinetBook(cabinetId, bookId, title, button);
    }
  }

  function openCabinetManager() {
    const overlay = document.getElementById('cabinet-manager-overlay');
    if (!overlay) return;
    openAdminModal(overlay);
    loadCabinets();
  }

  function closeCabinetManager() {
    const overlay = document.getElementById('cabinet-manager-overlay');
    closeAdminModal(overlay);
  }

  function openCabinetActionModal(mode, cabinet) {
    const overlay = document.getElementById('cabinet-action-overlay');
    const box = document.getElementById('cabinet-action-box');
    if (!overlay || !box || !cabinet) return;

    const header = (titleText) => {
      const node = document.createElement('div');
      node.className = 'modal-header';
      const title = document.createElement('h4');
      title.textContent = titleText;
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn btn--secondary btn--sm';
      close.dataset.closeAction = '';
      close.textContent = '關閉';
      node.append(title, close);
      return node;
    };

    const actions = (...buttons) => {
      const node = document.createElement('div');
      node.className = 'actions btn-group';
      node.style.marginTop = '10px';
      node.append(...buttons);
      return node;
    };

    const cancelBtn = () => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn--secondary btn--sm btn--danger-outline';
      button.dataset.closeAction = '';
      button.textContent = '取消';
      return button;
    };

    box.replaceChildren();

    if (mode === 'rename') {
      const form = document.createElement('form');
      form.id = 'rename-cabinet-form';
      form.className = 'field';
      const label = document.createElement('label');
      label.textContent = '新名稱';
      const input = document.createElement('input');
      input.type = 'text';
      input.name = 'name';
      input.value = cabinet.name || '';
      input.required = true;
      const save = document.createElement('button');
      save.type = 'submit';
      save.className = 'btn btn--primary btn--sm';
      save.textContent = '儲存';
      form.append(label, input, actions(save, cancelBtn()));
      box.append(header('重新命名書櫃'), form);
    } else if (mode === 'toggle') {
      const form = document.createElement('form');
      form.id = 'type-cabinet-form';
      form.className = 'field';
      const label = document.createElement('label');
      label.textContent = '類型';
      const select = document.createElement('select');
      select.name = 'type';
      select.required = true;
      [
        ['display', '展示櫃'],
        ['reserve', '備書櫃'],
      ].forEach(([value, text]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        option.selected = cabinet.type === value;
        select.appendChild(option);
      });
      const save = document.createElement('button');
      save.type = 'submit';
      save.className = 'btn btn--primary btn--sm';
      save.textContent = '儲存';
      form.append(label, select, actions(save, cancelBtn()));
      box.append(header('更改書櫃類別'), form);
    } else if (mode === 'delete') {
      const message = document.createElement('p');
      message.textContent = `確定要刪除「${cabinet.name || ''}」嗎？此動作無法復原。`;
      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.className = 'btn btn--danger btn--sm';
      deleteButton.dataset.confirmDelete = String(cabinet.id);
      deleteButton.textContent = '刪除';
      box.append(header('刪除書櫃'), message, actions(deleteButton, cancelBtn()));
    } else {
      return;
    }

    openAdminModal(overlay);

    const closeButtons = box.querySelectorAll('[data-close-action]');
    closeButtons.forEach(btn => btn.addEventListener('click', closeCabinetActionModal));

    const renameForm = document.getElementById('rename-cabinet-form');
    if (renameForm) {
      renameForm.addEventListener('submit', async e => {
        e.preventDefault();
        const newName = renameForm.querySelector('input[name="name"]').value.trim();
        if (!newName || newName === cabinet.name) return;
        const undo = () => requestCabinetUpdate(cabinet.id, { name: cabinet.name }, '名稱已還原', null);
        await requestCabinetUpdate(cabinet.id, { name: newName }, '名稱已更新', undo);
        closeCabinetActionModal();
      });
    }

    const typeForm = document.getElementById('type-cabinet-form');
    if (typeForm) {
      typeForm.addEventListener('submit', async e => {
        e.preventDefault();
        const newType = typeForm.querySelector('select[name="type"]').value;
        if (!newType || newType === cabinet.type) return;
        const undo = () => requestCabinetUpdate(cabinet.id, { type: cabinet.type }, '已還原類型', null);
        await requestCabinetUpdate(cabinet.id, { type: newType }, `已改為${newType === 'display' ? '展示櫃' : '備書櫃'}`, undo);
        closeCabinetActionModal();
      });
    }

    const deleteBtn = box.querySelector('[data-confirm-delete]');
    if (deleteBtn) {
      deleteBtn.addEventListener('click', () => {
        deleteCabinet(cabinet.id);
        closeCabinetActionModal();
      });
    }
  }

  function closeCabinetActionModal() {
    const overlay = document.getElementById('cabinet-action-overlay');
    closeAdminModal(overlay);
  }

  function handleCabinetAction(action, cabinet) {
    if (!cabinet) return;
    if (action === 'view') {
      openCabinetBooks(cabinet.id);
    } else if (action === 'rename') {
      openCabinetActionModal('rename', cabinet);
    } else if (action === 'toggle-type') {
      openCabinetActionModal('toggle', cabinet);
    } else if (action === 'delete') {
      openCabinetActionModal('delete', cabinet);
    }
  }

  function handleCabinetListClick(event) {
    const button = event.target.closest('[data-cabinet-action]');
    if (!button) return;
    const row = button.closest('.cabinet-row');
    if (!row) return;
    const id = Number(row.dataset.id);
    if (!id) return;
    const action = button.dataset.cabinetAction;
    const cab = cabinetCache.find(c => c.id === id);
    if (!cab) return;
    handleCabinetAction(action, cab);
  }

  function openAddBookModal(prefillTitle) {
    const overlay = document.getElementById('add-book-overlay');
    const input = document.getElementById('add-book-title');
    if (!overlay || !input) return;
    input.value = prefillTitle || '';
    openAdminModal(overlay, input);
  }

  function closeAddBookModal() {
    const overlay = document.getElementById('add-book-overlay');
    const form = document.getElementById('add-book-form');
    closeAdminModal(overlay);
    if (form) form.reset();
  }

  async function refreshBookCard(title) {
    const card = document.getElementById(`card-${CSS.escape(title)}`);
    if (!card) return;

    const res = await fetch(`/book_card/${encodeURIComponent(title)}`);
    if (res.ok) {
      const html = await res.text();
      const newCard = parseTrustedHtmlFragment(html).find((node) => node.nodeType === Node.ELEMENT_NODE);
      if (!newCard) return;
      card.replaceWith(newCard);
      newCard.classList.add('updated');
    }
  }

  function bindCabinetForm() {
    const cabinetForm = document.getElementById('cabinet-form');
    if (!cabinetForm) return;
    cabinetForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      const title = currentTitle || '';
      const action = document.getElementById('cabinet-action')?.value || 'add';
      const cabSelect = document.getElementById('cabinet-select');
      const cabName = cabSelect?.value?.trim();
      const status = document.getElementById('cabinet-form-status');
      const actionLabel = action === 'remove' ? '封存此櫃位的庫存紀錄' : '新增至櫃位';
      if (!window.confirm(`確認要${actionLabel}《${title}》嗎？`)) return;
      if (action === 'add' && cabName && currentCabinetNames.includes(cabName)) {
        const notice = document.getElementById('cabinet-exists-notice');
        if (notice) notice.style.display = 'block';
        if (status) status.textContent = `《${title}》已存在於「${cabName}」。`;
        showToast(`《${title}》已存在於「${cabName}」`, false);
        return;
      }
      const data = new FormData(this);
      const submitBtn = this.querySelector('button[type="submit"]');
      if (status) status.textContent = action === 'remove' ? '封存中...' : '更新中...';
      setPending(submitBtn, true, action === 'remove' ? '封存中...' : '新增中...');
      try {
        const response = await fetch(this.action, {
          method: 'POST',
          body: data,
          headers: headersWithCsrf(),
        });
        const res = await response.json();
        let undo = null;
        if (res.success && res.action === 'add') {
          undo = async () => {
            const fd = new FormData();
            fd.append('csrf_token', getFreshCsrfToken());
            fd.append('add_or_remove', 'remove');
            fd.append('cabinet', res.cabinet_name);
            await fetch(`/modify_cabinet/${encodeURIComponent(res.title)}`, {
              method: 'POST',
              body: fd,
              headers: headersWithCsrf(),
            });
            await refreshBookCard(res.title);
            await loadCabinets();
          };
        } else if (res.success && res.action === 'remove') {
          const qty = Math.max(Number(res.qty_removed) || 1, 1);
          undo = async () => {
            const fd = new FormData();
            fd.append('csrf_token', getFreshCsrfToken());
            fd.append('title', res.title);
            fd.append('cabinet_id', res.cabinet_id);
            fd.append('amount', qty);
            await fetch('/add_book', {
              method: 'POST',
              body: fd,
              headers: headersWithCsrf(),
            });
            await refreshBookCard(res.title);
            await loadCabinets();
          };
        }
        if (status) status.textContent = res.message || (res.success ? '操作完成。' : '操作失敗，請重試。');
        showToast(res.message, res.success, undo);
        if (res.success) {
          if (currentTitle) await refreshBookCard(currentTitle);
          closeCabinetModal();
          await loadCabinets();
          await refreshBookCardsForTitles([title]);
          if (typeof refreshDashboardPage === 'function') {
            refreshDashboardPage();
          }
        }
      } catch (err) {
        console.warn('Cabinet update failed:', err);
        if (status) status.textContent = '操作失敗，請確認網路連線後再試。';
        showToast('操作失敗', false);
      } finally {
        setPending(submitBtn, false);
      }
    });
  }

  // Set up inline form handler once (event delegation works for dynamically added forms)
  // This must be set up immediately when script loads, not in a function
  if (!window._inlineFormsBound) {
    document.addEventListener('submit', function(e) {
      const form = e.target;
      if (!form || !form.matches || !form.matches('.inline-form')) return;
      
      console.log('[toggle] Form submit intercepted:', form.action);
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();

      if (!form.action || form.action === '[object HTMLSelectElement]') {
        console.warn('Invalid form action:', form.action);
        if (window.showToast) window.showToast('無效的表單路徑', false);
        return;
      }

      if (form.dataset.skipConfirm !== 'true') {
        const label = form.dataset.confirmLabel || '確認要送出嗎？';
        if (!window.confirm(label)) return;
      }

      const formData = new FormData(form);
      const formToken = formData.get('csrf_token') || '';
      fetch(form.action, {
        method: 'POST',
        headers: headersWithCsrf(formToken),
        body: formData,
      })
        .then(async r => {
          const contentType = r.headers.get('content-type');
          if (contentType && contentType.includes('application/json')) {
            return r.json();
          } else {
            const text = await r.text();
            console.error('Non-JSON response:', text);
            throw new Error('伺服器回應格式錯誤');
          }
        })
        .then(async data => {
          if (data.success) {
            if (window.showToast) window.showToast(data.message || '狀態已更新 ✅', true);
            refreshNotificationsIfAvailable();
            const title = data.title || document.querySelector('#book-modal-box h2')?.textContent?.trim();
            if (title) {
              // Use window functions that are exposed
              if (window.refreshBookCard) await window.refreshBookCard(title);
              if (window.refreshModal) await window.refreshModal(title);
              // If we're on admin search results, also refresh the matching card in the grid
              const searchCard = document.getElementById(`card-${CSS.escape(title)}`);
              if (searchCard && window.refreshBookCard) {
                await window.refreshBookCard(title);
              }
            }
          } else {
            if (window.showToast) window.showToast(data.message || '更新失敗', false);
          }
        })
        .catch(err => {
          console.error('Toggle error:', err);
          if (window.showToast) window.showToast(err.message || '網路錯誤', false);
        });
    }, true); // Use capture phase to catch early
    window._inlineFormsBound = true;
  }

  // Keep the function for backwards compatibility
  function bindInlineForms() {
    // Already bound via event delegation above
  }

  function bindActionToggleButtons() {
    document.querySelectorAll('.action-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        setCabinetAction(btn.dataset.value);
        updateCabinetSelect();
      });
    });
  }

  function bindOverlayClickClose() {
    const managerOverlay = document.getElementById('cabinet-manager-overlay');
    if (managerOverlay) {
      managerOverlay.addEventListener('click', event => {
        if (event.target === managerOverlay) closeCabinetManager();
      });
    }

    const booksOverlay = document.getElementById('cabinet-books-overlay');
    if (booksOverlay) {
      booksOverlay.addEventListener('click', event => {
        if (event.target === booksOverlay) closeCabinetBooksModal();
      });
    }

    const moveOverlay = document.getElementById('move-book-overlay');
    if (moveOverlay) {
      moveOverlay.addEventListener('click', event => {
        if (event.target === moveOverlay) closeMoveBookModal();
      });
    }

    const actionOverlay = document.getElementById('cabinet-action-overlay');
    if (actionOverlay) {
      actionOverlay.addEventListener('click', event => {
        if (event.target === actionOverlay) closeCabinetActionModal();
      });
    }
  }

  function bindKeyboardShortcuts() {
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        const previewOverlay = document.getElementById('add-book-preview-overlay');
        if (previewOverlay?.style.display === 'flex') {
          closeAdminModal(previewOverlay);
          return;
        }
        closeAddBookModal();
        closeCabinetBooksModal();
        closeCabinetManager();
        closeCabinetModal();
        closeMoveBookModal();
        closeCabinetActionModal();
      }
    });
  }

  function bindForms() {
    const addBookForm = document.getElementById('add-book-form');
    let pendingAddBookPayload = null;
    const previewOverlay = document.getElementById('add-book-preview-overlay');
    const previewBox = previewOverlay?.querySelector('.add-book-preview') || null;
    const previewTitle = document.getElementById('preview-title');
    const previewAuthor = document.getElementById('preview-author');
    const previewTopics = document.getElementById('preview-topics');
    const previewWarnings = document.getElementById('preview-warnings');
    const previewCover = document.getElementById('preview-cover-img');
    const previewLoading = document.getElementById('add-book-preview-loading');
    const previewClose = document.getElementById('add-book-preview-close');
    const previewCancel = document.getElementById('add-book-preview-cancel');
    const previewConfirm = document.getElementById('add-book-preview-confirm');
    const addBookStatus = document.getElementById('add-book-status');

    const closePreview = () => {
      closeAdminModal(previewOverlay);
    };

    const openPreview = (data, formData) => {
      if (!previewOverlay) return;
      pendingAddBookPayload = { data, formData };
      if (previewLoading) previewLoading.style.display = 'none';
      if (previewBox) previewBox.classList.remove('is-loading');
      if (previewTitle) previewTitle.textContent = data.title || '書名';
      if (previewAuthor) previewAuthor.textContent = data.author ? `作者：${data.author}` : '作者：未取得';
      if (previewCover) {
        previewCover.src = isAllowedCoverUrl(data.cover_url) ? data.cover_url : '';
        previewCover.alt = data.title || '封面';
      }
      if (previewTopics) {
        previewTopics.replaceChildren();
        const topics = Array.isArray(data.topics) ? data.topics : [];
        if (!topics.length) {
          const muted = document.createElement('span');
          muted.className = 'muted';
          muted.textContent = '未取得主題';
          previewTopics.appendChild(muted);
        } else {
          topics.forEach((topic) => {
            const chip = document.createElement('span');
            chip.className = 'topic-chip';
            chip.textContent = topic;
            previewTopics.appendChild(chip);
          });
        }
      }
      if (previewWarnings) {
        previewWarnings.replaceChildren();
        if (data.existing_in_cabinet) {
          const warn = document.createElement('div');
          warn.className = 'preview-warning preview-warning--danger';
          warn.textContent = '此書已存在於該櫃位，將視為補貨。';
          previewWarnings.appendChild(warn);
        }
        if (data.similar_titles && data.similar_titles.length) {
          const warn = document.createElement('div');
          warn.className = 'preview-warning';
          warn.textContent = `相似書名：${data.similar_titles.join('、')}`;
          previewWarnings.appendChild(warn);
        }
      }
      previewConfirm?.focus();
    };

    const submitAddBook = async () => {
      if (!pendingAddBookPayload) return;
      const { data, formData } = pendingAddBookPayload;
      const submitData = new FormData(formData);
      submitData.append('author', data.author || '');
      submitData.append('cover_url', isAllowedCoverUrl(data.cover_url) ? data.cover_url : '');
      submitData.append('topics', JSON.stringify(data.topics || []));
      setPending(previewConfirm, true, '新增中...');
      try {
        const res = await fetch(addBookForm.action || '/add_book', {
          method: 'POST',
          body: submitData,
          headers: headersWithCsrf(),
        });
        const result = await res.json();
        if (!res.ok || !result.success) {
          showToast(result.message || '新增失敗', false);
          return;
        }

        const undo = result.book_id
          ? async () => {
              await fetch(`/cabinets/${result.cabinet_id}/books/${result.book_id}`, {
                method: 'DELETE',
                headers: headersWithCsrf(),
              });
              await loadCabinets();
              if (result.title) await refreshBookCard(result.title);
            }
          : null;

        showToast(result.message || '新增完成', true, undo);
        refreshNotificationsIfAvailable();
        if (result.title && typeof refreshBookCard === 'function') {
          await refreshBookCard(result.title);
        }
        closePreview();
        closeAddBookModal();
        pendingAddBookPayload = null;
      } catch (err) {
        console.error(err);
        showToast('網路錯誤', false);
      } finally {
        setPending(previewConfirm, false);
      }
    };
    if (addBookForm) {
      addBookForm.addEventListener('submit', async e => {
        e.preventDefault();
        const fd = new FormData(addBookForm);
        const bookTitle = fd.get('title') || '';
        const cabId = fd.get('cabinet_id');
        if (!bookTitle || !cabId) {
          if (addBookStatus) addBookStatus.textContent = '請輸入書名並選擇櫃位。';
          showToast('請輸入書名並選擇櫃位', false);
          return;
        }
        const submitBtn = addBookForm.querySelector('button[type="submit"]');
        if (addBookStatus) addBookStatus.textContent = '正在取得確認資訊...';
        setPending(submitBtn, true, '讀取中...');
        try {
          openAdminModal(previewOverlay);
          if (previewLoading) previewLoading.style.display = 'flex';
          if (previewBox) previewBox.classList.add('is-loading');
          const res = await fetch('/admin/add_book_preview', {
            method: 'POST',
            body: fd,
            headers: headersWithCsrf(),
          });
          const data = await res.json();
          if (!res.ok || !data.success) {
            if (addBookStatus) addBookStatus.textContent = data.message || '取得確認資訊失敗，請重試。';
            showToast(data.message || '取得預覽失敗', false);
            closeAdminModal(previewOverlay);
            return;
          }
          if (addBookStatus) addBookStatus.textContent = '確認資訊已準備完成。';
          openPreview(data, addBookForm);
        } catch (err) {
          console.error(err);
          if (addBookStatus) addBookStatus.textContent = '網路錯誤，請確認連線後再試。';
          showToast('網路錯誤', false);
          closeAdminModal(previewOverlay);
        } finally {
          setPending(submitBtn, false);
        }
      });
    }

    if (previewClose) previewClose.addEventListener('click', closePreview);
    if (previewCancel) previewCancel.addEventListener('click', closePreview);
    if (previewConfirm) previewConfirm.addEventListener('click', submitAddBook);
    if (previewOverlay) {
      previewOverlay.addEventListener('click', (event) => {
        if (event.target === previewOverlay) closePreview();
      });
    }

    const createForm = document.getElementById('cabinet-create-form');
    if (createForm) createForm.addEventListener('submit', submitCabinetCreate);

    const cabinetList = document.getElementById('cabinet-list');
    if (cabinetList) cabinetList.addEventListener('click', handleCabinetListClick);

    const picker = document.getElementById('cabinet-picker');
    const actionButtons = [
      { id: 'cab-action-view', action: 'view' },
      { id: 'cab-action-rename', action: 'rename' },
      { id: 'cab-action-toggle', action: 'toggle-type' },
      { id: 'cab-action-delete', action: 'delete' },
    ];
    actionButtons.forEach(cfg => {
      const btn = document.getElementById(cfg.id);
      if (btn && picker) {
        btn.addEventListener('click', () => {
          const cabId = Number(picker.value);
          if (!cabId) {
            showToast('請先選擇書櫃', false);
            return;
          }
          const cab = cabinetCache.find(c => c.id === cabId);
          handleCabinetAction(cfg.action, cab);
        });
      }
    });

    const backupBtn = document.getElementById('backup-btn');
    if (backupBtn) {
      backupBtn.addEventListener('click', () => {
        runBackup(backupBtn).catch(() => {});
      });
    }

    const cabinetBooksList = document.getElementById('cabinet-books-list');
    if (cabinetBooksList) cabinetBooksList.addEventListener('click', handleCabinetBooksClick);

    const moveBookForm = document.getElementById('move-book-form');
    if (moveBookForm) moveBookForm.addEventListener('submit', submitMoveBook);

    bindCabinetForm();
    bindInlineForms();
  }

  async function runBackup(btn) {
    const button = btn || document.getElementById('backup-btn');
    if (button) button.disabled = true;
    try {
      const res = await safeFetch('/admin/backup', {
        method: 'POST',
        headers: headersWithCsrf(),
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        showToast(data.message || '備份失敗', false);
        return;
      }
      const list = document.getElementById('backup-list');
      if (list && data.backup) {
        const item = document.createElement('div');
        item.className = 'backup-item';
        const ts = (data.backup.created_at || '').replace('T', ' ').slice(0, 16);
        const time = document.createElement('strong');
        time.textContent = ts;
        const size = document.createElement('span');
        size.className = 'muted';
        size.textContent = `(${data.backup.size_kb} KB)`;
        item.append(time, document.createTextNode(' '), size);
        list.replaceChildren();
        list.appendChild(item);
      }
      showToast(data.message || '備份完成', true, null);
      console.info('[backup] created', data.backups || data);
    } catch (err) {
      console.error(err);
      showToast('備份失敗', false);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function init() {
    updateCabinetSelect();
    bindForms();
    bindActionToggleButtons();
    bindOverlayClickClose();
    bindKeyboardShortcuts();
    const hash = window.location.hash.replace('#', '');
    if (hash === 'add-book') {
      openAddBookModal();
    } else if (hash === 'cabinet-manager') {
      openCabinetManager();
    }
  }

  window.openAddBookModal = openAddBookModal;
  window.closeAddBookModal = closeAddBookModal;
  window.openCabinetModal = openCabinetModal;
  window.closeCabinetModal = closeCabinetModal;
  window.openCabinetManager = openCabinetManager;
  window.closeCabinetManager = closeCabinetManager;
  window.openCabinetBooks = openCabinetBooks;
  window.closeCabinetBooksModal = closeCabinetBooksModal;
  window.closeMoveBookModal = closeMoveBookModal;
  window.refreshBookCard = refreshBookCard;
  window.refreshBookCardsForTitles = refreshBookCardsForTitles;
  window.refreshNotificationsIfAvailable = refreshNotificationsIfAvailable;
  window.refreshModal = refreshModal;
  window.refreshDashboardPage = () => window.location.reload();
  window.runBackup = runBackup;
  const resetBtn = document.getElementById('admin-reset-btn');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      const form = document.getElementById('admin-search-form');
      if (form) form.reset();
      const advanced = document.getElementById('advanced-panel');
      if (advanced) {
        advanced.hidden = true;
        advanced.classList.add('u-hidden');
        advanced.classList.remove('is-open');
      }
      window.location.href = '/admin';
    });
  }

  document.addEventListener('DOMContentLoaded', init);
})();
