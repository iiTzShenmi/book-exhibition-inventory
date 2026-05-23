  (() => {
    const toggle = document.getElementById('audit-toggle');
    const body = document.getElementById('audit-body');
    if (!toggle || !body) return;
    toggle.addEventListener('click', () => {
      const card = toggle.closest('.admin-audit-card');
      const collapsed = body.classList.toggle('is-collapsed');
      if (card) card.classList.toggle('is-collapsed', collapsed);
      toggle.dataset.state = collapsed ? 'collapsed' : 'expanded';
      toggle.textContent = collapsed ? '展開' : '收合';
    });
  })();
