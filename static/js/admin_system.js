  (() => {
    const toggle = document.getElementById('audit-toggle');
    const body = document.getElementById('audit-body');
    if (!toggle || !body) return;

    const card = toggle.closest('.admin-audit-card');
    const setExpanded = (expanded) => {
      body.classList.toggle('is-collapsed', !expanded);
      body.setAttribute('aria-hidden', String(!expanded));
      if (card) card.classList.toggle('is-collapsed', !expanded);
      toggle.dataset.state = expanded ? 'expanded' : 'collapsed';
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.textContent = expanded ? '收合' : '展開';
    };

    setExpanded(toggle.getAttribute('aria-expanded') === 'true');

    toggle.addEventListener('click', () => {
      setExpanded(body.classList.contains('is-collapsed'));
    });
  })();
