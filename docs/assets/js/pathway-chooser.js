(() => {
  // ── Helper: populate bullet list from selected option data attributes ──
  const populateDetails = (select) => {
    const card = select.closest('.pathway-card');
    if (!card) {
      return;
    }
    const list = card.querySelector('.pathway-card-details');
    if (!list) {
      return;
    }
    const option = select.options[select.selectedIndex];
    if (!option) {
      list.innerHTML = '';
      return;
    }
    const notes = option.getAttribute('data-notes') || '';
    const applicability = option.getAttribute('data-applicability') || '';
    let html = '';
    // Split notes on newlines into individual bullets
    notes
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        html += `<li>${line}</li>`;
      });
    if (applicability) {
      html += `<li><strong>Applicability:</strong> ${applicability}</li>`;
    }
    list.innerHTML = html;
  };

  // ── Pathway chooser (guide cards) ──────────────────────────────
  const choosers = document.querySelectorAll('.pathway-chooser');

  choosers.forEach((chooser) => {
    const storageKey = chooser.getAttribute('data-storage-key') || 'staf-pathway';
    const tier = chooser.getAttribute('data-tier') || '';
    const cards = chooser.querySelectorAll('.pathway-card');

    // Populate bullet points from dropdown on page load
    chooser.querySelectorAll('.pathway-card-select').forEach((select) => {
      populateDetails(select);
      select.addEventListener('change', () => {
        populateDetails(select);
      });
    });

    // Restore saved open/closed state
    const saved = localStorage.getItem(storageKey);
    if (saved === 'collapsed') {
      chooser.removeAttribute('open');
    }

    // Persist open/closed state on toggle
    chooser.addEventListener('toggle', () => {
      localStorage.setItem(storageKey, chooser.open ? 'expanded' : 'collapsed');
    });

    // Handle card button clicks
    cards.forEach((card) => {
      const action = card.getAttribute('data-action');
      const button = card.querySelector('.pathway-card-action');
      if (!button || !action) {
        return;
      }

      button.addEventListener('click', () => {
        // Visual feedback — mark the chosen card
        cards.forEach((c) => c.classList.remove('is-selected'));
        card.classList.add('is-selected');

        // Expand the widget-collapse for this tier
        const widgetCollapse = chooser.parentElement.querySelector('.widget-collapse[data-tier="' + tier + '"]');
        if (widgetCollapse) {
          widgetCollapse.classList.remove('is-collapsed');
        }

        // Read the selected assessment from any dropdown in the card
        const select = card.querySelector('.pathway-card-select');
        const selectedAssessment = select ? select.value : null;

        // Dispatch custom event for the assessment widget to handle
        window.dispatchEvent(
          new CustomEvent('staf:pathway-chosen', {
            detail: { tier, action, selectedAssessment },
          })
        );


      });
    });
  });

  // ── Widget collapse headers (info icon only, no toggle) ──────
  // The widget-collapse is expanded only via pathway card "Get Started"
  // buttons. The header is no longer clickable for toggling.
})();
