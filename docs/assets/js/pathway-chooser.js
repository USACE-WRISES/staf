(() => {
  const fetchJson = async (url) => {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Failed to load ${url} (${response.status})`);
    }
    return response.json();
  };

  const populateDetailedAdaptedOptions = async (chooser) => {
    const select = chooser.querySelector(
      '.pathway-card[data-action="use-predefined"] .pathway-card-select'
    );
    if (!select) {
      return;
    }
    const sourceUrl = select.getAttribute('data-adapted-assessments-url');
    if (!sourceUrl) {
      return;
    }
    const previousValue = select.value;
    const payload = await fetchJson(sourceUrl);
    const assessments = Array.isArray(payload?.assessments) ? payload.assessments : [];
    if (!assessments.length) {
      return;
    }

    select.innerHTML = '';
    assessments.forEach((assessment) => {
      const option = document.createElement('option');
      option.value = assessment.assessmentId || '';
      option.textContent = assessment.assessmentName || 'Detailed Assessment';
      const missingFunctions = Array.isArray(assessment.missingFunctionNames)
        ? assessment.missingFunctionNames
        : [];
      const notes = [
        `Adapted from ${assessment.sourceCitation || 'state SQT'}.`,
        `${assessment.metricCount || 0} metrics mapped across ${
          assessment.functionCount || 0
        } of ${assessment.totalFunctionCount || 0} functions.`,
      ];
      if (missingFunctions.length) {
        notes.push(
          `Missing function coverage (${missingFunctions.length}): ${missingFunctions.join(
            '; '
          )}.`
        );
      } else {
        notes.push('Includes at least one metric for every function.');
      }
      option.setAttribute('data-notes', notes.join('\n'));
      option.setAttribute(
        'data-applicability',
        assessment.applicability || `${assessment.stateName || ''} streams`.trim()
      );
      select.appendChild(option);
    });

    if (previousValue && Array.from(select.options).some((option) => option.value === previousValue)) {
      select.value = previousValue;
    } else if (select.options.length) {
      select.value = select.options[0].value;
    }
  };

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
    list.innerHTML = '';
    // Split notes on newlines into individual bullets
    notes
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
      .forEach((line) => {
        const li = document.createElement('li');
        li.textContent = line;
        list.appendChild(li);
      });
    if (applicability) {
      const li = document.createElement('li');
      const strong = document.createElement('strong');
      strong.textContent = 'Applicability:';
      li.appendChild(strong);
      li.appendChild(document.createTextNode(` ${applicability}`));
      list.appendChild(li);
    }
  };

  const initChooser = async (chooser) => {
    const storageKey = chooser.getAttribute('data-storage-key') || 'staf-pathway';
    const tier = chooser.getAttribute('data-tier') || '';
    const cards = chooser.querySelectorAll('.pathway-card');

    if (tier === 'detailed') {
      try {
        await populateDetailedAdaptedOptions(chooser);
      } catch (error) {
        // Keep any static fallback options if adapted list fails to load.
      }
    }

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
      if (action === 'launch-app') {
        return; // Launch cards are plain external links — nothing to wire up.
      }
      const button = card.querySelector('.pathway-card-action');
      if (!button || !action) {
        return;
      }

      button.addEventListener('click', () => {
        // Visual feedback — mark the chosen card
        cards.forEach((c) => c.classList.remove('is-selected'));
        card.classList.add('is-selected');

        // Expand the widget-collapse for this tier
        const widgetCollapse = chooser.parentElement.querySelector(
          `.widget-collapse[data-tier="${tier}"]`
        );
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
  };

  // ── Pathway chooser (guide cards) ──────────────────────────────
  const choosers = document.querySelectorAll('.pathway-chooser');
  choosers.forEach((chooser) => {
    void initChooser(chooser);
  });

  // ── Widget collapse headers (info icon only, no toggle) ──────
  // The widget-collapse is expanded only via pathway card "Get Started"
  // buttons. The header is no longer clickable for toggling.
})();
