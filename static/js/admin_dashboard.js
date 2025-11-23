(() => {
  const dashboard = document.getElementById('admin-dashboard');
  if (!dashboard) return;

  const initialCabinets = (() => {
    try {
      return JSON.parse(dashboard.dataset.allCabinets || '[]');
    } catch (err) {
      console.error('Unable to parse cabinets payload', err);
      return [];
    }
  })();

  const ALL_CABINET_NAMES = initialCabinets.map(cab => cab.name).filter(Boolean);
  let currentTitle = null;
  let currentCabinetNames = [];
  let cabinetCache = [...initialCabinets];
  let currentCabinetBooksId = null;
  let pendingMove = null;

  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || window.csrfToken || '';

  const headersWithCsrf = (extra = {}) => ({
    ...extra,
    'X-CSRF-Token': csrfToken,
  });

  async function refreshModal(title) {
    const box = document.getElementById('book-modal-box');
    if (!box) return;
    const res = await fetch(`/book_details/${encodeURIComponent(title)}`);
    if (res.ok) {
      const html = await res.text();
      box.innerHTML = html;
    }
  }

  function collectCabinetNamesForTitle(title) {
    const card = document.getElementById(`card-${CSS.escape(title)}`);
    if (!card) return [];
    const seen = new Set();
    card.querySelectorAll('.status-row .cab').forEach(node => {
      const label = node.textContent.trim();
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
    const removeBtn = document.querySelector('.action-btn.remove');
    if (!select || !actionInput) return;

    const hasCurrent = currentCabinetNames.length > 0;

    if (removeBtn) {
      if (!hasCurrent) {
        removeBtn.disabled = true;
        removeBtn.classList.add('disabled');
        if (actionInput.value === 'remove') setCabinetAction('add');
      } else {
        removeBtn.disabled = false;
        removeBtn.classList.remove('disabled');
      }
    }

    const source = actionInput.value === 'remove' ? currentCabinetNames : ALL_CABINET_NAMES;

    select.innerHTML = '';
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

    const disableSelect = actionInput.value === 'remove' && !hasCurrent;
    select.disabled = disableSelect;
    select.value = '';
  }

  function openCabinetModal(title) {
    currentTitle = title;
    currentCabinetNames = collectCabinetNamesForTitle(title);
    const overlay = document.getElementById('cabinet-modal-overlay');
    const cabinetForm = document.getElementById('cabinet-form');
    if (cabinetForm) cabinetForm.action = `/modify_cabinet/${encodeURIComponent(title)}`;
    setCabinetAction('add');
    updateCabinetSelect();
    if (overlay) overlay.style.display = 'flex';
  }

  function closeCabinetModal() {
    const overlay = document.getElementById('cabinet-modal-overlay');
    if (overlay) overlay.style.display = 'none';
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
    const listEl = document.getElementById('cabinet-list');
    if (!listEl) return;

    syncCabinetNames(cabinets);

    listEl.innerHTML = '';
    if (!cabinets.length) {
      const empty = document.createElement('div');
      empty.className = 'cabinet-empty';
      empty.textContent = '尚未建立任何櫃位';
      listEl.appendChild(empty);
      return;
    }

    const groups = [
      { type: 'display', title: '展示櫃', empty: '尚無展示櫃' },
      { type: 'reserve', title: '備書櫃', empty: '尚無備書櫃' },
    ];

    groups.forEach(group => {
      const section = document.createElement('div');
      section.className = 'cabinet-group';
      const heading = document.createElement('h5');
      heading.textContent = group.title;
      section.appendChild(heading);

      const items = cabinets.filter(cab => cab.type === group.type);
      if (!items.length) {
        const empty = document.createElement('div');
        empty.className = 'cabinet-empty';
        empty.textContent = group.empty;
        section.appendChild(empty);
      } else {
        items.forEach(cab => {
          const row = document.createElement('div');
          row.className = 'cabinet-row';
          row.dataset.id = cab.id;
          row.dataset.type = cab.type;

          const manageBtn = document.createElement('button');
          manageBtn.type = 'button';
          manageBtn.className = 'btn btn--outline btn--sm';
          manageBtn.dataset.cabinetAction = 'manage-books';
          manageBtn.textContent = '查看書籍';

          const renameBtn = document.createElement('button');
          renameBtn.type = 'button';
          renameBtn.className = 'btn btn--secondary btn--sm';
          renameBtn.dataset.cabinetAction = 'rename';
          renameBtn.textContent = '重新命名';

          const toggleBtn = document.createElement('button');
          toggleBtn.type = 'button';
          toggleBtn.className = 'btn btn--primary btn--sm';
          toggleBtn.dataset.cabinetAction = 'toggle-type';
          toggleBtn.textContent = cab.type === 'display' ? '改為備書' : '改為展示';

          const deleteBtn = document.createElement('button');
          deleteBtn.type = 'button';
          deleteBtn.className = 'btn btn--danger btn--sm';
          deleteBtn.dataset.cabinetAction = 'delete';
          deleteBtn.textContent = '刪除';
          deleteBtn.disabled = cab.book_count > 0;
          if (deleteBtn.disabled) deleteBtn.title = '櫃位尚有書籍，無法刪除';

          const nameRow = document.createElement('div');
          nameRow.className = 'cabinet-info-row';
          const nameText = document.createElement('span');
          nameText.className = 'cabinet-info-text cabinet-name';
          nameText.textContent = `名稱：${cab.name}`;
          nameRow.appendChild(nameText);
          nameRow.appendChild(renameBtn);

          const typeRow = document.createElement('div');
          typeRow.className = 'cabinet-info-row';
          const typeText = document.createElement('span');
          typeText.className = 'cabinet-info-text cabinet-type';
          typeText.textContent = `類型：${cab.type === 'display' ? '展示櫃' : '備書櫃'}`;
          typeRow.appendChild(typeText);
          typeRow.appendChild(toggleBtn);

          const countRow = document.createElement('div');
          countRow.className = 'cabinet-info-row';
          const countText = document.createElement('span');
          countText.className = 'cabinet-info-text cabinet-count';
          countText.textContent = `書籍數：${cab.book_count} 本`;
          countRow.appendChild(countText);
          countRow.appendChild(manageBtn);

          const footerRow = document.createElement('div');
          footerRow.className = 'cabinet-row__footer';
          footerRow.appendChild(deleteBtn);

          row.appendChild(nameRow);
          row.appendChild(typeRow);
          row.appendChild(countRow);
          row.appendChild(footerRow);
          section.appendChild(row);
        });
      }

      listEl.appendChild(section);
    });
  }

  async function submitCabinetCreate(event) {
    event.preventDefault();
    const form = event.target;
    const formData = new FormData(form);
    const payload = {
      name: formData.get('name'),
      type: formData.get('type'),
    };

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
      showToast('已新增櫃位', true);
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

  async function requestCabinetUpdate(id, payload, successMessage) {
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
      showToast(successMessage || '已更新', true);
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
    await requestCabinetUpdate(id, { name: trimmed }, '名稱已更新');
  }

  async function toggleCabinetType(id) {
    const cabinet = cabinetCache.find(cab => cab.id === id);
    if (!cabinet) return;
    const nextType = cabinet.type === 'display' ? 'reserve' : 'display';
    await requestCabinetUpdate(id, { type: nextType }, `已改為${nextType === 'display' ? '展示櫃' : '備書櫃'}`);
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
      showToast(`櫃位「${cabinet.name}」已刪除`, true);
      await loadCabinets();
    } catch (err) {
      console.error(err);
      showToast('刪除失敗', false);
    }
  }

  function closeCabinetBooksModal() {
    const overlay = document.getElementById('cabinet-books-overlay');
    if (!overlay) return;
    overlay.style.display = 'none';
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
      listEl.innerHTML = '';

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
        removeBtn.textContent = '移出本櫃';

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
    overlay.style.display = 'flex';
    loadCabinetBooks(id);
  }

  async function toggleCabinetBook(cabinetId, bookId, title) {
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
      await loadCabinets();
      await loadCabinetBooks(cabinetId);
      await refreshBookCardsForTitles(data.affected_titles);
    } catch (err) {
      console.error(err);
      showToast('更新失敗', false);
    }
  }

  async function removeCabinetBook(cabinetId, bookId, title) {
    const bookName = title || '未命名';
    if (!window.confirm(`確定將「${bookName}」移出此櫃位？`)) return;
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
      showToast('書籍已移出本櫃', true);
      await loadCabinets();
      await loadCabinetBooks(cabinetId);
      await refreshBookCardsForTitles(data.affected_titles);
    } catch (err) {
      console.error(err);
      showToast('移除失敗', false);
    }
  }

  function populateMoveTargets(sourceCabinetId) {
    const select = document.getElementById('move-book-target');
    if (!select) return 0;

    select.innerHTML = '';
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
    if (!overlay || !form || !titleEl || !bookIdInput || !sourceInput) return;

    if (!cabinetCache.length) loadCabinets();

    pendingMove = { cabinetId, bookId, title };
    titleEl.textContent = title;
    bookIdInput.value = String(bookId);
    sourceInput.value = String(cabinetId);

    const availableCount = populateMoveTargets(cabinetId);
    overlay.style.display = 'flex';

    const select = document.getElementById('move-book-target');
    if (select && !select.disabled) select.focus();

    if (!availableCount) {
      showToast('尚未建立其他櫃位，可先於櫃位資訊新增。', false);
    }
  }

  function closeMoveBookModal() {
    const overlay = document.getElementById('move-book-overlay');
    if (overlay) overlay.style.display = 'none';
    const form = document.getElementById('move-book-form');
    if (form) form.reset();
    pendingMove = null;
  }

  async function submitMoveBook(event) {
    event.preventDefault();
    const targetSelect = document.getElementById('move-book-target');
    const bookInput = document.getElementById('move-book-id');
    const sourceInput = document.getElementById('move-source-cabinet-id');
    if (!targetSelect || !bookInput || !sourceInput) return;

    const targetId = Number(targetSelect.value);
    const bookId = Number(bookInput.value);
    const sourceId = Number(sourceInput.value);

    if (!targetId) {
      showToast('請選擇目標櫃位', false);
      return;
    }

    const movingTitle = pendingMove?.title || '';

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
        return;
      }

      const label = movingTitle ? `「${movingTitle}」已移至新櫃位` : '書籍已移動';
      showToast(label, true);

      closeMoveBookModal();
      await loadCabinets();
      await loadCabinetBooks(sourceId);
      await refreshBookCardsForTitles(data.affected_titles);
    } catch (err) {
      console.error(err);
      showToast('移動失敗', false);
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
      toggleCabinetBook(cabinetId, bookId, title);
    } else if (action === 'move') {
      openMoveBookModal(cabinetId, bookId, title);
    } else if (action === 'remove') {
      removeCabinetBook(cabinetId, bookId, title);
    }
  }

  function openCabinetManager() {
    const overlay = document.getElementById('cabinet-manager-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    loadCabinets();
  }

  function closeCabinetManager() {
    const overlay = document.getElementById('cabinet-manager-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  function handleCabinetListClick(event) {
    const button = event.target.closest('[data-cabinet-action]');
    if (!button) return;
    const row = button.closest('.cabinet-row');
    if (!row) return;
    const id = Number(row.dataset.id);
    if (!id) return;
    const action = button.dataset.cabinetAction;
    if (action === 'manage-books') {
      openCabinetBooks(id);
    } else if (action === 'rename') {
      promptRenameCabinet(id);
    } else if (action === 'toggle-type') {
      toggleCabinetType(id);
    } else if (action === 'delete') {
      deleteCabinet(id);
    }
  }

  function openAddBookModal(prefillTitle) {
    const overlay = document.getElementById('add-book-overlay');
    const input = document.getElementById('add-book-title');
    if (!overlay || !input) return;
    overlay.style.display = 'flex';
    input.value = prefillTitle || '';
    input.focus();
  }

  function closeAddBookModal() {
    const overlay = document.getElementById('add-book-overlay');
    const form = document.getElementById('add-book-form');
    if (overlay) overlay.style.display = 'none';
    if (form) form.reset();
  }

  async function refreshBookCard(title) {
    const card = document.getElementById(`card-${CSS.escape(title)}`);
    if (!card) return;

    const res = await fetch(`/book_card/${encodeURIComponent(title)}`);
    if (res.ok) {
      const html = await res.text();
      const wrapper = document.createElement('div');
      wrapper.innerHTML = html;
      const newCard = wrapper.firstElementChild;
      card.replaceWith(newCard);
      newCard.classList.add('updated');
    }
  }

  function bindCabinetForm() {
    const cabinetForm = document.getElementById('cabinet-form');
    if (!cabinetForm) return;
    cabinetForm.addEventListener('submit', function (e) {
      e.preventDefault();
      const data = new FormData(this);
      fetch(this.action, {
        method: 'POST',
        body: data,
        headers: headersWithCsrf(),
      })
        .then(async r => {
          try {
            const res = await r.json();
            showToast(res.message, res.success);
            if (res.success) {
              if (currentTitle) await refreshBookCard(currentTitle);
              closeCabinetModal();
              loadCabinets();
            }
          } catch (err) {
            console.warn('Unexpected response:', r);
            showToast('無法解析伺服器回應', false);
          }
        })
        .catch(() => showToast('操作失敗', false));
    });
  }

  function bindInlineForms() {
    document.addEventListener('submit', e => {
      if (!e.target.matches('.inline-form')) return;
      e.preventDefault();
      const form = e.target;

      if (!form.action || form.action === '[object HTMLSelectElement]') {
        console.warn('Invalid form action:', form.action);
        showToast('無效的表單路徑', false);
        return;
      }

      const formData = new FormData(form);
      fetch(form.action, {
        method: 'POST',
        headers: headersWithCsrf(),
        body: formData,
      })
        .then(r => r.json())
        .then(async data => {
          if (data.success) {
            showToast('狀態已更新 ✅', true);
            const title = data.title || document.querySelector('#book-modal-box h2')?.textContent?.trim();
            if (title) {
              await refreshBookCard(title);
              await refreshModal(title);
            }
          } else {
            showToast(data.message || '更新失敗', false);
          }
        })
        .catch(() => showToast('網路錯誤', false));
    });
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
  }

  function bindKeyboardShortcuts() {
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        closeAddBookModal();
        closeCabinetBooksModal();
        closeCabinetManager();
        closeCabinetModal();
        closeMoveBookModal();
      }
    });
  }

  function bindForms() {
    const addBookForm = document.getElementById('add-book-form');
    if (addBookForm) {
      addBookForm.addEventListener('submit', async e => {
        e.preventDefault();
        const fd = new FormData(addBookForm);

        try {
          const res = await fetch(addBookForm.action || '/add_book', {
            method: 'POST',
            body: fd,
            headers: headersWithCsrf(),
          });
          const data = await res.json();
          if (!res.ok || !data.success) {
            showToast(data.message || '新增失敗', false);
            return;
          }

          showToast(data.message || '新增完成', true);

          const title = fd.get('title');
          if (title && typeof refreshBookCard === 'function') {
            await refreshBookCard(title);
          }

          closeAddBookModal();
        } catch (err) {
          console.error(err);
          showToast('網路錯誤', false);
        }
      });
    }

    const createForm = document.getElementById('cabinet-create-form');
    if (createForm) createForm.addEventListener('submit', submitCabinetCreate);

    const cabinetList = document.getElementById('cabinet-list');
    if (cabinetList) cabinetList.addEventListener('click', handleCabinetListClick);

    const cabinetBooksList = document.getElementById('cabinet-books-list');
    if (cabinetBooksList) cabinetBooksList.addEventListener('click', handleCabinetBooksClick);

    const moveBookForm = document.getElementById('move-book-form');
    if (moveBookForm) moveBookForm.addEventListener('submit', submitMoveBook);

    bindCabinetForm();
    bindInlineForms();
  }

  function init() {
    updateCabinetSelect();
    bindForms();
    bindActionToggleButtons();
    bindOverlayClickClose();
    bindKeyboardShortcuts();
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

  document.addEventListener('DOMContentLoaded', init);
})();
