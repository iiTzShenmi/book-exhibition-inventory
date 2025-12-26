(() => {
  const btn = document.getElementById('backup-btn');
  if (!btn) return;
  const list = document.getElementById('backup-list');
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const toast = window.showToast || ((msg, ok) => {
    if (!msg) return;
    window.alert(msg);
  });

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
        item.innerHTML = `<strong>${ts}</strong> <span class="muted">(${data.backup.size_kb} KB)</span>`;
        list.innerHTML = '';
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
})();
