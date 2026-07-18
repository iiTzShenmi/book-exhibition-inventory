  (() => {
    const root = document.querySelector('.admin-events');
    if (!root) return;
    const list = document.getElementById('event-list');
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

    let dragEl = null;

    if (list) {
      list.addEventListener('dragstart', (event) => {
        const row = event.target.closest('.event-row');
        if (!row) return;
        dragEl = row;
        row.classList.add('event-row--dragging');
        event.dataTransfer.effectAllowed = 'move';
      });

      list.addEventListener('dragend', () => {
        if (dragEl) dragEl.classList.remove('event-row--dragging');
        dragEl = null;
      });

      list.addEventListener('dragover', (event) => {
        event.preventDefault();
        const target = event.target.closest('.event-row');
        if (!target || target === dragEl) return;
        const rect = target.getBoundingClientRect();
        const shouldInsertAfter = event.clientY > rect.top + rect.height / 2;
        if (shouldInsertAfter) {
          target.after(dragEl);
        } else {
          target.before(dragEl);
        }
      });

      list.addEventListener('drop', async (event) => {
        event.preventDefault();
        if (!dragEl) return;
        const ids = Array.from(list.querySelectorAll('.event-row'))
          .map((row) => Number(row.dataset.eventId))
          .filter((id) => Number.isFinite(id));
        try {
          await fetch(root.dataset.reorderUrl || '', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRF-Token': csrf,
            },
            body: JSON.stringify({ ids }),
          });
        } catch (err) {
          console.error(err);
        }
      });
    }

    const serializeForm = (form) => {
      const data = {};
      form.querySelectorAll('input[name], textarea[name], select[name]').forEach((field) => {
        if (field.type === 'checkbox') {
          data[field.name] = field.checked;
        } else {
          data[field.name] = field.value;
        }
      });
      return data;
    };

    const setInitialState = (form) => {
      form.dataset.initial = JSON.stringify(serializeForm(form));
      const picker = form.querySelector('.event-book-picker');
      if (picker && picker._getSelected) {
        form.dataset.initialBooks = JSON.stringify(picker._getSelected());
      }
    };

    const isDirty = (form) => {
      const initial = form.dataset.initial ? JSON.parse(form.dataset.initial) : {};
      const current = serializeForm(form);
      const keys = new Set([...Object.keys(initial), ...Object.keys(current)]);
      for (const key of keys) {
        if (String(initial[key] ?? '') !== String(current[key] ?? '')) {
          return true;
        }
      }
      return false;
    };

    const updateDirtyState = (form) => {
      const dirty = isDirty(form);
      form.classList.toggle('is-dirty', dirty);
      const label = form.querySelector('.event-dirty-label');
      if (label) label.classList.toggle('is-hidden', !dirty);
      const discard = form.querySelector('.event-discard');
      if (discard) discard.classList.toggle('is-hidden', !dirty);
    };

    const restoreForm = (form) => {
      const initial = form.dataset.initial ? JSON.parse(form.dataset.initial) : {};
      form.querySelectorAll('input[name], textarea[name], select[name]').forEach((field) => {
        if (!(field.name in initial)) return;
        if (field.type === 'checkbox') {
          field.checked = Boolean(initial[field.name]);
        } else {
          field.value = initial[field.name];
        }
      });
      const picker = form.querySelector('.event-book-picker');
      if (picker && picker._setSelected) {
        const initialBooks = form.dataset.initialBooks ? JSON.parse(form.dataset.initialBooks) : [];
        picker._setSelected(initialBooks);
      }
      updateDirtyState(form);
    };

    const initBookPicker = (picker) => {
      const input = picker.querySelector('.book-picker-input');
      const dropdown = picker.querySelector('.picker-dropdown');
      const tags = picker.querySelector('.selected-tags-container');
      const hidden = picker.querySelector('.book-ids-input');
      if (!input || !dropdown || !tags || !hidden) return;

      const canUseCoverUrl = (url) => Boolean(window.EXIS?.isAllowedCoverUrl?.(url));

      const makeRemoveButton = () => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'tag-remove';
        button.setAttribute('aria-label', '移除');
        button.textContent = '×';
        return button;
      };

      const makeBookTag = (book) => {
        const tag = document.createElement('span');
        tag.className = 'book-tag';
        tag.dataset.id = String(book.id);
        tag.append(document.createTextNode(book.title || ''), makeRemoveButton());
        return tag;
      };

      const selected = new Map();
      tags.querySelectorAll('.book-tag').forEach((tag) => {
        const id = Number(tag.dataset.id);
        const title = tag.firstChild?.textContent?.trim() || '';
        if (Number.isFinite(id)) selected.set(id, { id, title });
      });

      const syncHidden = () => {
        hidden.value = Array.from(selected.keys()).join(',');
      };

      const addTag = (book) => {
        if (!book || selected.has(book.id)) return;
        selected.set(book.id, book);
        tags.appendChild(makeBookTag(book));
        syncHidden();
      };

      tags.addEventListener('click', (event) => {
        const btn = event.target.closest('.tag-remove');
        if (!btn) return;
        const tag = btn.closest('.book-tag');
        const id = Number(tag?.dataset.id);
        if (tag) tag.remove();
        if (Number.isFinite(id)) selected.delete(id);
        syncHidden();
      });

      input.addEventListener('input', async (event) => {
        const query = event.target.value.trim();
        if (query.length < 2) {
          dropdown.style.display = 'none';
          dropdown.replaceChildren();
          return;
        }
        try {
          const res = await fetch(`/api/book_titles?q=${encodeURIComponent(query)}`);
          const data = await res.json();
          const results = Array.isArray(data?.results) ? data.results : [];
          dropdown.replaceChildren();
          if (!results.length) {
            dropdown.style.display = 'none';
            return;
          }
          results.forEach((book) => {
            const item = document.createElement('div');
            item.className = 'picker-item';
            item.dataset.id = String(book.id);
            item.dataset.title = book.title || '';
            if (canUseCoverUrl(book.cover_url)) {
              const cover = document.createElement('img');
              cover.src = book.cover_url;
              cover.alt = book.title || '';
              item.appendChild(cover);
            } else {
              const placeholder = document.createElement('span');
              placeholder.className = 'picker-cover-placeholder';
              placeholder.setAttribute('aria-hidden', 'true');
              item.appendChild(placeholder);
            }
            const title = document.createElement('span');
            title.textContent = book.title || '';
            item.appendChild(title);
            dropdown.appendChild(item);
          });
          dropdown.style.display = 'block';
        } catch (err) {
          console.error(err);
          dropdown.style.display = 'none';
        }
      });

      dropdown.addEventListener('click', (event) => {
        const item = event.target.closest('.picker-item');
        if (!item) return;
        const id = Number(item.dataset.id);
        const title = item.dataset.title || '';
        addTag({ id, title });
        dropdown.style.display = 'none';
        dropdown.replaceChildren();
        input.value = '';
      });

      document.addEventListener('click', (event) => {
        if (picker.contains(event.target)) return;
        dropdown.style.display = 'none';
      });

      syncHidden();
      picker._getSelected = () => Array.from(selected.values()).map((item) => ({
        id: item.id,
        title: item.title,
      }));
      picker._setSelected = (books = []) => {
        selected.clear();
        tags.replaceChildren();
        books.forEach((book) => {
          if (!book || !Number.isFinite(Number(book.id))) return;
          selected.set(Number(book.id), { id: Number(book.id), title: book.title || '' });
          tags.appendChild(makeBookTag(book));
        });
        syncHidden();
      };
    };

    document.querySelectorAll('.event-book-picker').forEach(initBookPicker);

    const buildTimeOptions = () => {
      const options = [];
      for (let h = 0; h < 24; h++) {
        for (let m = 0; m < 60; m += 15) {
          const hh = String(h).padStart(2, '0');
          const mm = String(m).padStart(2, '0');
          options.push(`${hh}:${mm}`);
        }
      }
      return options;
    };

    const timeOptions = buildTimeOptions();
    document.querySelectorAll('.time-select').forEach((select) => {
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = '--:--';
      const options = timeOptions.map((time) => {
        const option = document.createElement('option');
        option.value = time;
        option.textContent = time;
        return option;
      });
      select.replaceChildren(placeholder, ...options);
    });

    const syncTimeText = (form) => {
      const start = form.querySelector('select[name="time_start"]');
      const end = form.querySelector('select[name="time_end"]');
      const out = form.querySelector('input[data-time-text]');
      if (!start || !end || !out) return;
      if (start.value && end.value) {
        out.value = `${start.value} / ${end.value}`;
      } else {
        out.value = '';
      }
    };

    document.querySelectorAll('.admin-event-form, .event-row').forEach((form) => {
      const start = form.querySelector('select[name="time_start"]');
      const end = form.querySelector('select[name="time_end"]');
      const out = form.querySelector('input[data-time-text]');
      if (!start || !end) return;
      if (out && out.value) {
        const matches = out.value.match(/\d{1,2}:\d{2}/g) || [];
        if (matches[0]) start.value = matches[0];
        if (matches[1]) end.value = matches[1];
      }
      syncTimeText(form);
      start.addEventListener('input', () => syncTimeText(form));
      end.addEventListener('input', () => syncTimeText(form));
    });

    const forms = Array.from(document.querySelectorAll('.admin-event-form, .event-row'));
    forms.forEach((form) => {
      setInitialState(form);
      updateDirtyState(form);
      form.addEventListener('input', () => updateDirtyState(form));
      form.addEventListener('change', () => updateDirtyState(form));
    });

    document.addEventListener('click', (event) => {
      const discardBtn = event.target.closest('[data-discard]');
      if (discardBtn) {
        const form = discardBtn.closest('.event-row');
        if (form) restoreForm(form);
        return;
      }
      const discardNew = event.target.closest('[data-discard-new]');
      if (discardNew) {
        const form = discardNew.closest('.admin-event-form');
        if (form) restoreForm(form);
      }
    });

    const hasDirtyForms = () => forms.some((form) => isDirty(form));

    window.addEventListener('beforeunload', (event) => {
      if (!hasDirtyForms()) return;
      event.preventDefault();
      event.returnValue = '';
    });

    document.addEventListener('click', (event) => {
      const link = event.target.closest('a[href]');
      if (!link) return;
      const href = link.getAttribute('href') || '';
      if (!href || href.startsWith('#') || link.target === '_blank') return;
      if (hasDirtyForms() && !window.confirm('尚有未儲存的活動變更，確定要離開嗎？')) {
        event.preventDefault();
      }
    });
  })();
