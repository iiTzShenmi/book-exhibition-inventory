(() => {
  const btn = document.getElementById('backup-btn');
  const list = document.getElementById('backup-list');
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const toast = window.showToast || ((msg, ok) => {
    if (!msg) return;
    window.alert(msg);
  });

  if (btn) {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const res = await fetch('/admin/backup', {
          method: 'POST',
          headers: {
            'X-CSRF-Token': csrf,
          },
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          toast(data.message || '備份失敗', false);
          return;
        }
        if (list && data.backup) {
          const item = document.createElement('div');
          item.className = 'backup-item';
          const ts = (data.backup.created_at || '').replace('T', ' ').slice(0, 16);
          const timestamp = document.createElement('strong');
          timestamp.textContent = ts;
          const size = document.createElement('span');
          size.className = 'muted';
          size.textContent = ` (${data.backup.size_kb} KB)`;
          item.append(timestamp, size);
          list.replaceChildren();
          list.appendChild(item);
        }
        toast(data.message || '備份完成', true);
      } catch (err) {
        console.error(err);
        toast('備份失敗', false);
      } finally {
        btn.disabled = false;
      }
    });
  }

  const uploadForm = document.getElementById('csv-upload-form');
  if (uploadForm && uploadForm.dataset.ajax === 'true') {
    uploadForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submitBtn = uploadForm.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      try {
        const formData = new FormData(uploadForm);
        const res = await fetch('/admin/import', {
          method: 'POST',
          headers: {
            'X-CSRF-Token': csrf,
          },
          body: formData,
        });
        const data = await res.json();
        if (!res.ok || !data.success) {
          toast(data.message || '匯入失敗', false);
          return;
        }
        uploadForm.reset();
        const summary = data.summary || {};
        const hint = [
          `rows=${summary.rows || 0}`,
          `pairs=${summary.pairs || 0}`,
          `新增=${summary.created_inventory || 0}`,
          `封存=${summary.archived_inventory || 0}`,
        ].join(' ');
        toast(`${data.message || '匯入完成'} (${hint})`, true);
      } catch (err) {
        console.error(err);
        toast('匯入失敗', false);
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }
})();
