(() => {
  const root = document.querySelector('[data-floor-plan-editor]');
  if (!root) return;

  const canvas = root.querySelector('[data-plan-canvas]');
  const selectedName = root.querySelector('[data-plan-selected-name]');
  const status = root.querySelector('[data-plan-status]');
  const saveButton = root.querySelector('[data-plan-save]');
  const resetButton = root.querySelector('[data-plan-reset]');
  const removeObjectButton = root.querySelector('[data-plan-remove-object]');
  const unplacedList = root.querySelector('[data-plan-unplaced]');
  const objectList = root.querySelector('[data-plan-object-list]');
  const snapStepSelect = root.querySelector('[data-plan-snap-step]');
  const addObjectKindSelect = root.querySelector('[data-plan-add-object-kind]');
  const addObjectButton = root.querySelector('[data-plan-add-object]');
  const objectLabelInput = root.querySelector('[data-plan-object-label]');
  const objectKindSelect = root.querySelector('[data-plan-object-kind]');
  const fields = new Map(
    Array.from(root.querySelectorAll('[data-plan-field]')).map((field) => [field.dataset.planField, field])
  );
  if (
    !canvas || !selectedName || !status || !saveButton || !resetButton || !removeObjectButton
    || !unplacedList || !objectList || !snapStepSelect || !addObjectKindSelect || !addObjectButton
    || !objectLabelInput || !objectKindSelect
  ) return;

  const POSITION_KEYS = ['left', 'top', 'width', 'height'];
  const OBJECT_KIND_LABELS = {
    walkway: '走道',
    entrance: '入口',
    checkout: '收銀區',
    service: '服務台',
    activity: '活動區',
    fixture: '設施',
  };
  const OBJECT_DEFAULT_SIZES = {
    walkway: { width: 42, height: 8 },
    entrance: { width: 12, height: 14 },
    checkout: { width: 18, height: 10 },
    service: { width: 16, height: 10 },
    activity: { width: 22, height: 14 },
    fixture: { width: 12, height: 10 },
  };
  const dirtyCabinetIds = new Set();
  let objectsDirty = false;
  let selected = null;
  let dragState = null;
  let layout = parseLayout(root.dataset.layout);
  let objects = parseObjects(root.dataset.objects);

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function finiteOr(value, fallback) {
    return Number.isFinite(value) ? value : fallback;
  }

  function snapStep() {
    const value = Number(snapStepSelect.value);
    return Number.isFinite(value) && value > 0 ? value : 2.5;
  }

  function applyGridStep() {
    canvas.style.setProperty('--plan-grid-size', `${snapStep()}%`);
  }

  function snap(value) {
    const step = snapStep();
    return Math.round(value / step) * step;
  }

  function normalizeGeometry(item, shouldSnap = false) {
    const width = finiteOr(item.width, 13);
    const height = finiteOr(item.height, 7);
    item.width = clamp(shouldSnap ? snap(width) : width, 1, 100);
    item.height = clamp(shouldSnap ? snap(height) : height, 1, 100);
    const left = finiteOr(item.left, 5);
    const top = finiteOr(item.top, 5);
    item.left = clamp(shouldSnap ? snap(left) : left, 0, 100 - item.width);
    item.top = clamp(shouldSnap ? snap(top) : top, 0, 100 - item.height);
    return item;
  }

  function parseLayout(rawLayout) {
    try {
      const parsed = JSON.parse(rawLayout || '[]');
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((item) => item && typeof item === 'object' && Number.isInteger(Number(item.cabinet_id)))
        .map((item) => normalizeGeometry({
          cabinet_id: Number(item.cabinet_id),
          cabinet_name: String(item.cabinet_name || ''),
          label: String(item.label || item.cabinet_name || ''),
          left: Number(item.left),
          top: Number(item.top),
          width: Number(item.width),
          height: Number(item.height),
          placed: item.placed === true,
          has_override: item.has_override === true,
          has_default: item.has_default === true,
        }));
    } catch (err) {
      return [];
    }
  }

  function parseObjects(rawObjects) {
    try {
      const parsed = JSON.parse(rawObjects || '[]');
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((item) => item && typeof item === 'object' && OBJECT_KIND_LABELS[item.kind])
        .map((item) => normalizeGeometry({
          object_key: String(item.object_key || ''),
          kind: item.kind,
          label: String(item.label || OBJECT_KIND_LABELS[item.kind]),
          left: Number(item.left),
          top: Number(item.top),
          width: Number(item.width),
          height: Number(item.height),
        }));
    } catch (err) {
      return [];
    }
  }

  function selectionEquals(type, id) {
    return selected && selected.type === type && selected.id === id;
  }

  function findCabinet(cabinetId) {
    return layout.find((item) => item.cabinet_id === cabinetId) || null;
  }

  function findObject(objectKey) {
    return objects.find((item) => item.object_key === objectKey) || null;
  }

  function selectedItem() {
    if (!selected) return null;
    return selected.type === 'cabinet' ? findCabinet(selected.id) : findObject(selected.id);
  }

  function selectedPlacedCabinet() {
    const item = selectedItem();
    return selected?.type === 'cabinet' && item?.placed ? item : null;
  }

  function formatNumber(value) {
    return String(Math.round(Number(value) * 10) / 10);
  }

  function canvasSelector(type, id) {
    return type === 'cabinet'
      ? `[data-plan-cabinet-id="${id}"]`
      : `[data-plan-object-key="${id}"]`;
  }

  function updateNodeGeometry(node, item) {
    node.style.setProperty('--plan-left', String(item.left));
    node.style.setProperty('--plan-top', String(item.top));
    node.style.setProperty('--plan-width', String(item.width));
    node.style.setProperty('--plan-height', String(item.height));
  }

  function updateStatus(message, isError = false) {
    status.textContent = message;
    status.classList.toggle('is-error', isError);
  }

  function markDirty(type, id) {
    if (type === 'cabinet') dirtyCabinetIds.add(id);
    else objectsDirty = true;
    updateStatus('尚未儲存');
    updateInspector();
  }

  function updateInspector() {
    const item = selectedItem();
    const isCabinet = selected?.type === 'cabinet';
    const isObject = selected?.type === 'object';
    const cabinet = selectedPlacedCabinet();
    selectedName.textContent = item
      ? `${isCabinet ? '展示櫃' : OBJECT_KIND_LABELS[item.kind]}：${isCabinet ? item.cabinet_name : item.label}`
      : '尚未選取';
    fields.forEach((field, key) => {
      field.disabled = !item || (isCabinet && !cabinet);
      field.value = item ? formatNumber(item[key]) : '';
    });
    objectLabelInput.disabled = !isObject;
    objectLabelInput.value = isObject ? item.label : '';
    objectKindSelect.disabled = !isObject;
    objectKindSelect.value = isObject ? item.kind : 'walkway';
    resetButton.hidden = !isCabinet;
    resetButton.disabled = !cabinet || !(cabinet.has_override || dirtyCabinetIds.has(cabinet.cabinet_id));
    removeObjectButton.hidden = !isObject;
    removeObjectButton.disabled = !isObject || objects.length <= 1;
  }

  function appendResizeHandle(node) {
    const handle = document.createElement('span');
    handle.className = 'floor-plan-editor__resize-handle';
    handle.dataset.planResize = '';
    handle.setAttribute('aria-hidden', 'true');
    node.appendChild(handle);
  }

  function renderObject(object) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = `floor-plan-editor__object floor-plan-editor__object--${object.kind}`;
    node.dataset.planObjectKey = object.object_key;
    node.textContent = object.label;
    node.setAttribute('aria-label', `選取${OBJECT_KIND_LABELS[object.kind]} ${object.label}`);
    node.setAttribute('aria-pressed', String(selectionEquals('object', object.object_key)));
    if (selectionEquals('object', object.object_key)) node.classList.add('is-selected');
    updateNodeGeometry(node, object);
    appendResizeHandle(node);
    node.addEventListener('click', () => selectItem('object', object.object_key));
    node.addEventListener('pointerdown', (event) => beginPointerInteraction(event, 'object', object.object_key, node));
    node.addEventListener('keydown', (event) => moveWithKeyboard(event, 'object', object.object_key, node));
    canvas.appendChild(node);
  }

  function renderCabinet(cabinet) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = 'floor-plan-editor__node';
    node.dataset.planCabinetId = String(cabinet.cabinet_id);
    node.textContent = cabinet.label || cabinet.cabinet_name;
    node.setAttribute('aria-label', `選取展示櫃 ${cabinet.cabinet_name}`);
    node.setAttribute('aria-pressed', String(selectionEquals('cabinet', cabinet.cabinet_id)));
    if (selectionEquals('cabinet', cabinet.cabinet_id)) node.classList.add('is-selected');
    updateNodeGeometry(node, cabinet);
    appendResizeHandle(node);
    node.addEventListener('click', () => selectItem('cabinet', cabinet.cabinet_id));
    node.addEventListener('pointerdown', (event) => beginPointerInteraction(event, 'cabinet', cabinet.cabinet_id, node));
    node.addEventListener('keydown', (event) => moveWithKeyboard(event, 'cabinet', cabinet.cabinet_id, node));
    canvas.appendChild(node);
  }

  function renderCanvas() {
    canvas.replaceChildren();
    objects
      .slice()
      .sort((left, right) => left.top - right.top || left.left - right.left)
      .forEach(renderObject);
    layout
      .filter((item) => item.placed)
      .slice()
      .sort((left, right) => left.top - right.top || left.left - right.left)
      .forEach(renderCabinet);
  }

  function renderUnplaced() {
    unplacedList.replaceChildren();
    const unplaced = layout.filter((item) => !item.placed);
    if (!unplaced.length) {
      const item = document.createElement('li');
      item.className = 'floor-plan-editor__empty';
      item.textContent = '所有展示櫃都已配置。';
      unplacedList.appendChild(item);
      return;
    }
    unplaced.forEach((cabinet) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'floor-plan-editor__add-cabinet';
      button.textContent = cabinet.cabinet_name;
      button.addEventListener('click', () => addCabinet(cabinet.cabinet_id));
      item.appendChild(button);
      unplacedList.appendChild(item);
    });
  }

  function renderObjectList() {
    objectList.replaceChildren();
    objects.forEach((object) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'floor-plan-editor__object-list-button';
      button.textContent = `${OBJECT_KIND_LABELS[object.kind]}：${object.label}`;
      if (selectionEquals('object', object.object_key)) button.classList.add('is-selected');
      button.addEventListener('click', () => selectItem('object', object.object_key));
      item.appendChild(button);
      objectList.appendChild(item);
    });
  }

  function render() {
    renderCanvas();
    renderUnplaced();
    renderObjectList();
    updateInspector();
  }

  function selectItem(type, id) {
    if (selectionEquals(type, id)) {
      updateInspector();
      return;
    }
    selected = { type, id };
    render();
  }

  function addCabinet(cabinetId) {
    const cabinet = findCabinet(cabinetId);
    if (!cabinet) return;
    cabinet.placed = true;
    normalizeGeometry(cabinet, true);
    selected = { type: 'cabinet', id: cabinetId };
    markDirty('cabinet', cabinetId);
    render();
  }

  function makeObjectKey() {
    const randomPart = window.crypto?.randomUUID?.().toLowerCase()
      || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
    return `object-${randomPart}`.slice(0, 64);
  }

  function addObject() {
    const kind = addObjectKindSelect.value;
    if (!OBJECT_KIND_LABELS[kind]) return;
    const size = OBJECT_DEFAULT_SIZES[kind];
    const offset = 8 + (objects.length % 6) * 6;
    const object = normalizeGeometry({
      object_key: makeObjectKey(),
      kind,
      label: OBJECT_KIND_LABELS[kind],
      left: offset,
      top: offset,
      width: size.width,
      height: size.height,
    }, true);
    objects.push(object);
    selected = { type: 'object', id: object.object_key };
    markDirty('object', object.object_key);
    render();
  }

  function beginPointerInteraction(event, type, id, node) {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    const mode = event.target.closest('[data-plan-resize]') ? 'resize' : 'move';
    if (!selectionEquals(type, id)) {
      selected = { type, id };
      render();
    }
    const activeNode = canvas.querySelector(canvasSelector(type, id)) || node;
    dragState = { type, id, mode, pointerId: event.pointerId, node: activeNode };
    activeNode.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function updateDrag(event) {
    if (!dragState || event.pointerId !== dragState.pointerId) return;
    const item = selectedItem();
    const rect = canvas.getBoundingClientRect();
    if (!item || !rect.width || !rect.height) return;
    if (dragState.mode === 'resize') {
      item.width = ((event.clientX - rect.left) / rect.width) * 100 - item.left;
      item.height = ((event.clientY - rect.top) / rect.height) * 100 - item.top;
    } else {
      item.left = ((event.clientX - rect.left) / rect.width) * 100 - item.width / 2;
      item.top = ((event.clientY - rect.top) / rect.height) * 100 - item.height / 2;
    }
    normalizeGeometry(item, true);
    updateNodeGeometry(dragState.node, item);
    markDirty(dragState.type, dragState.id);
  }

  function endDrag(event) {
    if (!dragState || (event && event.pointerId !== dragState.pointerId)) return;
    dragState = null;
    updateInspector();
  }

  function moveWithKeyboard(event, type, id, node) {
    const item = type === 'cabinet' ? findCabinet(id) : findObject(id);
    if (!item) return;
    const delta = event.shiftKey ? snapStep() * 2 : snapStep();
    if (event.key === 'ArrowLeft') item.left -= delta;
    else if (event.key === 'ArrowRight') item.left += delta;
    else if (event.key === 'ArrowUp') item.top -= delta;
    else if (event.key === 'ArrowDown') item.top += delta;
    else return;
    event.preventDefault();
    selected = { type, id };
    normalizeGeometry(item, true);
    updateNodeGeometry(node, item);
    markDirty(type, id);
  }

  function updateFromField(event) {
    const item = selectedItem();
    const key = event.currentTarget.dataset.planField;
    const value = Number(event.currentTarget.value);
    if (!item || !POSITION_KEYS.includes(key) || !Number.isFinite(value)) {
      updateInspector();
      return;
    }
    item[key] = value;
    normalizeGeometry(item, true);
    const node = canvas.querySelector(canvasSelector(selected.type, selected.id));
    if (node) updateNodeGeometry(node, item);
    markDirty(selected.type, selected.id);
    updateInspector();
  }

  function updateObjectLabel() {
    const object = selected?.type === 'object' ? selectedItem() : null;
    const label = objectLabelInput.value.trim();
    if (!object || !label || label.length > 80) {
      updateInspector();
      return;
    }
    object.label = label;
    markDirty('object', object.object_key);
    render();
  }

  function updateObjectKind() {
    const object = selected?.type === 'object' ? selectedItem() : null;
    const kind = objectKindSelect.value;
    if (!object || !OBJECT_KIND_LABELS[kind]) {
      updateInspector();
      return;
    }
    object.kind = kind;
    markDirty('object', object.object_key);
    render();
  }

  function snapSelectedItem() {
    const item = selectedItem();
    if (!item) return;
    normalizeGeometry(item, true);
    const node = canvas.querySelector(canvasSelector(selected.type, selected.id));
    if (node) updateNodeGeometry(node, item);
    markDirty(selected.type, selected.id);
    updateInspector();
  }

  function removeSelectedObject() {
    if (selected?.type !== 'object' || objects.length <= 1) return;
    const index = objects.findIndex((object) => object.object_key === selected.id);
    if (index < 0) return;
    objects.splice(index, 1);
    objectsDirty = true;
    selected = layout.find((cabinet) => cabinet.placed)
      ? { type: 'cabinet', id: layout.find((cabinet) => cabinet.placed).cabinet_id }
      : { type: 'object', id: objects[0].object_key };
    updateStatus('尚未儲存');
    render();
  }

  async function parseJsonResponse(response) {
    const payload = await response.json().catch(() => null);
    if (!response.ok || !payload || payload.success !== true) {
      throw new Error(payload?.message || '操作失敗，請稍後再試。');
    }
    return payload;
  }

  function serializedObjects() {
    return objects.map((object) => ({
      object_key: object.object_key,
      kind: object.kind,
      label: object.label,
      left: object.left,
      top: object.top,
      width: object.width,
      height: object.height,
    }));
  }

  async function saveLayout() {
    if (!dirtyCabinetIds.size && !objectsDirty) {
      updateStatus('配置已儲存');
      return;
    }
    const positions = layout
      .filter((item) => item.placed && dirtyCabinetIds.has(item.cabinet_id))
      .map((item) => ({
        cabinet_id: item.cabinet_id,
        left: item.left,
        top: item.top,
        width: item.width,
        height: item.height,
      }));
    const requestBody = { positions };
    if (objectsDirty) requestBody.objects = serializedObjects();
    saveButton.disabled = true;
    updateStatus('儲存中');
    try {
      const response = await window.safeFetch(root.dataset.saveUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(requestBody),
      });
      const payload = await parseJsonResponse(response);
      layout = parseLayout(JSON.stringify(payload.layout));
      if (Array.isArray(payload.objects)) objects = parseObjects(JSON.stringify(payload.objects));
      dirtyCabinetIds.clear();
      objectsDirty = false;
      updateStatus('已儲存');
      render();
    } catch (err) {
      updateStatus(err.message || '儲存失敗，請稍後再試。', true);
    } finally {
      saveButton.disabled = false;
    }
  }

  async function resetSelectedPosition() {
    const cabinet = selectedPlacedCabinet();
    if (!cabinet) return;
    resetButton.disabled = true;
    try {
      const url = root.dataset.resetUrl.replace(/0$/, String(cabinet.cabinet_id));
      const response = await window.safeFetch(url, { method: 'DELETE', headers: { Accept: 'application/json' } });
      const payload = await parseJsonResponse(response);
      const index = layout.findIndex((item) => item.cabinet_id === cabinet.cabinet_id);
      if (index >= 0) layout[index] = normalizeGeometry(payload.position);
      dirtyCabinetIds.delete(cabinet.cabinet_id);
      updateStatus('已恢復預設');
      render();
    } catch (err) {
      updateStatus(err.message || '重設失敗，請稍後再試。', true);
    } finally {
      updateInspector();
    }
  }

  fields.forEach((field) => field.addEventListener('change', updateFromField));
  objectLabelInput.addEventListener('change', updateObjectLabel);
  objectKindSelect.addEventListener('change', updateObjectKind);
  snapStepSelect.addEventListener('change', () => {
    applyGridStep();
    snapSelectedItem();
  });
  addObjectButton.addEventListener('click', addObject);
  removeObjectButton.addEventListener('click', removeSelectedObject);
  canvas.addEventListener('pointermove', updateDrag);
  window.addEventListener('pointerup', endDrag);
  window.addEventListener('pointercancel', endDrag);
  saveButton.addEventListener('click', saveLayout);
  resetButton.addEventListener('click', resetSelectedPosition);

  const firstCabinet = layout.find((item) => item.placed);
  selected = firstCabinet
    ? { type: 'cabinet', id: firstCabinet.cabinet_id }
    : (objects[0] ? { type: 'object', id: objects[0].object_key } : null);
  applyGridStep();
  render();
})();
