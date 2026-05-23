  (() => {
    const form = document.getElementById('import-commit-form');
    const root = document.querySelector('.admin-import-preview');
    const canCommit = root?.dataset.canCommit === '1';
    const autoFetch = root?.dataset.autoFetch === '1';
    const warningCount = Number(root?.dataset.warningCount || 0);
    const totalRows = Number(root?.dataset.totalRows || 0);
    const ackBox = document.getElementById('import-ack');
    const excludedInput = document.getElementById('excluded-warnings');
    const warningKeepCountEl = document.getElementById('import-warning-keep-count');
    const warningAckCountEl = document.getElementById('import-warning-ack-count');
    const warningChecks = Array.from(document.querySelectorAll('.warning-keep'));
    const titleOverridesInput = document.getElementById('title-overrides');
    const cabinetOverridesInput = document.getElementById('cabinet-overrides');
    let keptWarnings = warningChecks.filter((chk) => chk.checked).length;
    let excludedCount = 0;
    const importRows = Array.from(document.querySelectorAll('.import-table__row'));
    const importRowMap = new Map();
    importRows.forEach((row) => {
      if (row.dataset.key) {
        importRowMap.set(row.dataset.key, row);
      }
    });
    if (!form) return;
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const toast = window.showToast || ((msg) => msg && window.alert(msg));

    const commitProgress = document.getElementById('import-commit-progress');
    const commitProgressText = document.getElementById('import-commit-progress-text');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = form.querySelector('button[type="submit"]');
      if (btn) btn.disabled = true;
      const importTotal = Math.max(0, (totalRows || importRows.length) - excludedCount);
      if (commitProgress) commitProgress.classList.add('is-running');
      if (commitProgressText) {
        commitProgressText.textContent = `匯入中...（剩餘 ${importTotal} 本）`;
      }
      if (commitProgress) {
        const bar = commitProgress.querySelector('.import-progress__bar');
        if (bar) bar.style.width = '12%';
      }
      try {
        const formData = new FormData(form);
        const res = await fetch(form.action, {
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
        toast(data.message || '匯入完成', true);
        if (commitProgress) {
          commitProgress.classList.remove('is-running');
          const bar = commitProgress.querySelector('.import-progress__bar');
          if (bar) bar.style.width = '100%';
        }
        if (commitProgressText) {
          commitProgressText.textContent = `匯入完成 (${importTotal} / ${importTotal})`;
        }
        window.location.href = root?.dataset.systemUrl || '/admin/system';
      } catch (err) {
        console.error(err);
        toast('匯入失敗', false);
        if (commitProgressText) commitProgressText.textContent = '匯入失敗';
      } finally {
        if (commitProgress) commitProgress.classList.remove('is-running');
        if (btn) btn.disabled = false;
      }
    });

    const metaBtn = document.getElementById('import-meta-btn');
    const metaStatus = document.getElementById('import-meta-status');
    const metaProgress = document.getElementById('import-meta-progress');
    const metaProgressText = document.getElementById('import-meta-progress-text');
    const safeFetchCount = Number(root?.dataset.safeFetchCount || 0);
    let metaRunning = false;
    const updateConfirmState = () => {
      if (!form) return;
      const confirmBtn = form.querySelector('button[type="submit"]');
      if (!confirmBtn) return;
      const ackRequired = keptWarnings > 0;
      const ackOk = !ackRequired || (ackBox && ackBox.checked);
      confirmBtn.disabled = !canCommit || metaRunning || !ackOk;
    };

    const runMetaFetch = async () => {
      if (!metaBtn || metaBtn.disabled) return;
      metaBtn.disabled = true;
      metaRunning = true;
      updateConfirmState();

      const batchSize = 5;
      const total = safeFetchCount || 0;
      if (metaProgress) metaProgress.classList.add('is-running');
      if (metaStatus) metaStatus.textContent = '準備抓取...';
      if (metaProgressText) metaProgressText.textContent = `待處理 ${total} 本`;
      let processed = 0;
      let successCount = 0;
      let guard = 0;
      const maxLoops = Math.max(1, Math.ceil(total / batchSize) + 5);

      try {
        while (processed < total) {
          guard += 1;
          if (guard > maxLoops) {
            toast('抓取時間過久，請稍後再試', false);
            break;
          }
          if (metaProgressText) {
            const remaining = Math.max(0, total - processed);
            metaProgressText.textContent = `處理中... (${processed} / ${total})，剩餘 ${remaining} 本`;
          }

          const res = await fetch(root?.dataset.metadataUrl || '', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-CSRF-Token': csrf,
            },
            body: JSON.stringify({
              token: metaBtn.dataset.token,
              only_safe: true,
              limit: batchSize,
            }),
          });
          const data = await res.json();
          if (!res.ok || !data.success) {
            toast(data.message || '部分抓取失敗', false);
            break;
          }

          const rows = new Map();
          document.querySelectorAll('.import-table__row').forEach((row) => {
            if (row.dataset.normalized) rows.set(row.dataset.normalized, row);
          });

          (data.items || []).forEach((item) => {
            const row = rows.get(item.normalized);
            if (!row) return;
            const authorCell = row.querySelector('.import-author');
            if (authorCell && item.author) {
              authorCell.textContent = item.author;
              authorCell.classList.add('text-success');
            }
            const coverCell = row.querySelector('.import-cover');
            if (coverCell && item.cover_url) {
              coverCell.innerHTML = `<img src="${item.cover_url}" alt="" class="fade-in">`;
            }
          });

          const batchCount = (data.updated || 0) + (data.failed || 0);
          if (batchCount === 0) break;

          processed += batchCount;
          successCount += data.updated || 0;

          if (metaProgress) {
            const bar = metaProgress.querySelector('.import-progress__bar');
            const percent = total ? Math.min(100, Math.round((processed / total) * 100)) : 100;
            if (bar) bar.style.width = `${percent}%`;
          }
        }

        toast(`抓取完成！更新了 ${successCount} 本書籍`, true);
      } catch (err) {
        console.error(err);
        toast('抓取過程發生錯誤', false);
      } finally {
        metaBtn.disabled = false;
        if (metaProgress) metaProgress.classList.remove('is-running');
        if (metaStatus) metaStatus.textContent = '抓取結束';
        if (metaProgressText) {
          const remaining = Math.max(0, total - processed);
          metaProgressText.textContent = `完成 ${successCount} / ${total}，剩餘 ${remaining} 本`;
        }
        metaRunning = false;
        updateConfirmState();
      }
    };

    if (metaBtn) {
      metaBtn.addEventListener('click', runMetaFetch);
      if (autoFetch && !metaBtn.disabled) {
        runMetaFetch();
      }
    }

    const updateWarningState = () => {
      if (!warningChecks.length) return;
      const excluded = [];
      const titleOverrides = {};
      const cabinetOverrides = {};
      keptWarnings = 0;
      warningChecks.forEach((chk) => {
        const row = chk.dataset.key ? importRowMap.get(chk.dataset.key) : null;
        if (chk.checked) {
          keptWarnings += 1;
          if (row) row.classList.remove('is-excluded');
        } else {
          excluded.push({
            cabinet: chk.dataset.cabinet || '',
            title: chk.dataset.title || '',
            normalized: chk.dataset.normalized || '',
          });
          if (row) row.classList.add('is-excluded');
        }
      });
      excludedCount = excluded.length;
      document.querySelectorAll('.title-override').forEach((select) => {
        const key = select.dataset.key;
        if (key && select.value) {
          titleOverrides[key] = select.value;
        }
      });
      document.querySelectorAll('.similar-choice').forEach((group) => {
        const key = group.dataset.key;
        const chosen = group.querySelector('input:checked');
        if (key && chosen && chosen.value) {
          titleOverrides[key] = chosen.value;
        }
      });
      document.querySelectorAll('.cabinet-override').forEach((select) => {
        const key = select.dataset.key;
        if (key && select.value && select.value !== '__new__') {
          cabinetOverrides[key] = select.value;
        }
      });
      if (warningKeepCountEl) warningKeepCountEl.textContent = String(keptWarnings);
      if (warningAckCountEl) warningAckCountEl.textContent = String(keptWarnings);
      if (excludedInput) excludedInput.value = JSON.stringify(excluded);
      if (titleOverridesInput) titleOverridesInput.value = JSON.stringify(titleOverrides);
      if (cabinetOverridesInput) cabinetOverridesInput.value = JSON.stringify(cabinetOverrides);
      if (ackBox) {
        ackBox.disabled = keptWarnings === 0;
        if (keptWarnings === 0) ackBox.checked = false;
      }
      const importTotal = Math.max(0, (totalRows || importRows.length) - excludedCount);
      if (commitProgressText && !commitProgress?.classList.contains('is-running')) {
        commitProgressText.textContent = `待匯入 ${importTotal} 本`;
      }
      updateConfirmState();
    };

    if (warningChecks.length) {
      warningChecks.forEach((chk) => chk.addEventListener('change', updateWarningState));
      document.querySelectorAll('.title-override').forEach((select) => {
        select.addEventListener('change', updateWarningState);
      });
      document.querySelectorAll('.cabinet-override').forEach((select) => {
        select.addEventListener('change', updateWarningState);
      });
      const escapeValue = (value) => {
        if (window.CSS && typeof window.CSS.escape === 'function') {
          return window.CSS.escape(value);
        }
        return String(value).replace(/\"/g, '\\\\\"');
      };
      document.querySelectorAll('.apply-cabinet-all').forEach((btn) => {
        btn.addEventListener('click', () => {
          const cab = btn.dataset.cabinet || '';
          const wrapper = btn.closest('.warning-action');
          const select = wrapper?.querySelector('.cabinet-override');
          if (!select) return;
          const value = select.value;
          const selector = `.cabinet-override[data-cabinet=\"${escapeValue(cab)}\"]`;
          document.querySelectorAll(selector).forEach((other) => {
            other.value = value;
          });
          updateWarningState();
        });
      });
      document.querySelectorAll('.similar-choice').forEach((group) => {
        group.querySelectorAll('input').forEach((radio) => {
          radio.addEventListener('change', updateWarningState);
        });
      });
      document.getElementById('warning-exclude-all')?.addEventListener('click', () => {
        warningChecks.forEach((chk) => { chk.checked = false; });
        updateWarningState();
      });
      document.getElementById('warning-keep-all')?.addEventListener('click', () => {
        warningChecks.forEach((chk) => { chk.checked = true; });
        updateWarningState();
      });
      updateWarningState();
    }

    if (ackBox) {
      ackBox.addEventListener('change', updateConfirmState);
    }
    updateConfirmState();
  })();
