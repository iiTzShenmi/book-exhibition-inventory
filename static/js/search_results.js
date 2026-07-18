  (() => {
    // Auto-focus + scroll: prioritize suggestions when no results, otherwise first result card
    window.requestAnimationFrame(() => {
      // Check if there are exact results
      const hasExactResults = document.querySelector('.results-grid:not(.suggestion-grid) .result-card');
      const suggestionSection = document.getElementById('suggestions-section');
      const firstSuggestionCard = document.querySelector('.suggestion-grid .result-card');

      // If no exact results but suggestions exist, focus on suggestions
      if (!hasExactResults && firstSuggestionCard) {
        // Scroll to suggestion section
        if (suggestionSection) {
          suggestionSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        // Focus first suggestion card
        firstSuggestionCard.setAttribute('tabindex', '-1');
        setTimeout(() => {
          firstSuggestionCard.focus({ preventScroll: true });
        }, 100);
      } else if (hasExactResults) {
        // Has exact results, focus first result card
        const firstCard = document.querySelector('.results-grid:not(.suggestion-grid) .result-card');
        if (firstCard) {
          firstCard.setAttribute('tabindex', '-1');
          firstCard.focus({ preventScroll: true });
          firstCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      } else {
        // No results and no suggestions, focus search input
        const input = document.querySelector('.customer-search .search-box__input');
        if (input) {
          input.focus({ preventScroll: true });
          input.select?.();
        }
      }
    });

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

    document.querySelectorAll('.result-card[data-title]').forEach(card => {
      card.addEventListener('click', (event) => {
        const isInteractive = event.target.closest('button, a, input, select, textarea');
        if (isInteractive) return;
        if (card.classList.contains('has-cover')) {
          card.classList.toggle('show-info');
        }
        trackView(card.dataset.title || '');
      });
    });
  })();
