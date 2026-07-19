(() => {
    const trackView = (title) => {
      if (!title) return;
      if (typeof window.EXIS?.trackBookView === 'function') {
        window.EXIS.trackBookView(title);
        return;
      }
      const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
      fetch('/api/track_view', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrf,
        },
        body: JSON.stringify({ title }),
      }).catch(() => {});
    };

    document.querySelectorAll('.result-card[data-title]').forEach((card) => {
      card.addEventListener('click', (event) => {
        const isInteractive = event.target.closest('button, a, input, select, textarea');
        if (isInteractive) return;
        trackView(card.dataset.title || '');
      });
    });
})();
