(() => {
  // Render the tier selector questionnaire and compute a recommendation.
  const container = document.querySelector('.tier-selector');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const dataUrl = `${baseUrl}/assets/data/tier-questions.json`;
  const fallback = container.querySelector('.tier-selector-fallback');
  const ui = container.querySelector('.tier-selector-ui');

  const tierLabels = {
    screening: 'Screening',
    rapid: 'Rapid',
    detailed: 'Detailed'
  };

  const tierLinks = {
    screening: `${baseUrl}/tiers/screening/`,
    rapid: `${baseUrl}/tiers/rapid/`,
    detailed: `${baseUrl}/tiers/detailed/`
  };

  const buildForm = (questions) => {
    const form = document.createElement('form');
    form.className = 'tier-selector-form';

    questions.forEach((q, qIndex) => {
      const fieldset = document.createElement('fieldset');
      fieldset.className = 'tier-question';

      const legend = document.createElement('legend');
      legend.textContent = q.question;
      fieldset.appendChild(legend);

      q.answers.forEach((a, aIndex) => {
        const id = `ts-${qIndex}-${aIndex}`;
        const label = document.createElement('label');
        label.className = 'tier-answer';

        const input = document.createElement('input');
        input.type = 'radio';
        input.name = q.id;
        input.value = a.value;
        input.id = id;
        input.dataset.scoreScreening = a.score_screening;
        input.dataset.scoreRapid = a.score_rapid;
        input.dataset.scoreDetailed = a.score_detailed;
        input.dataset.rationale = a.rationale_snippet;
        if (aIndex === 0) {
          input.required = true;
        }

        const span = document.createElement('span');
        span.textContent = a.label;

        label.appendChild(input);
        label.appendChild(span);
        fieldset.appendChild(label);
      });

      form.appendChild(fieldset);
    });

    const actions = document.createElement('div');
    actions.className = 'tier-actions';

    const submitBtn = document.createElement('button');
    submitBtn.type = 'submit';
    submitBtn.className = 'btn';
    submitBtn.textContent = 'Get recommendation';

    actions.appendChild(submitBtn);
    form.appendChild(actions);

    return form;
  };

  const computeRecommendation = (questions, form) => {
    const totals = { screening: 0, rapid: 0, detailed: 0 };
    const reasons = [];
    let highRisk = false;

    for (const q of questions) {
      const selected = form.querySelector(`input[name="${q.id}"]:checked`);
      if (!selected) {
        return { ready: false };
      }

      totals.screening += parseFloat(selected.dataset.scoreScreening);
      totals.rapid += parseFloat(selected.dataset.scoreRapid);
      totals.detailed += parseFloat(selected.dataset.scoreDetailed);
      reasons.push(selected.dataset.rationale);

      if (q.id === 'decision-risk' && selected.value === 'high') {
        highRisk = true;
      }
    }

    const maxScore = Math.max(totals.screening, totals.rapid, totals.detailed);
    const tied = Object.keys(totals).filter((k) => totals[k] === maxScore);
    let recommended = tied[0];

    if (tied.length > 1) {
      if (tied.includes('detailed') && highRisk) {
        recommended = 'detailed';
      } else if (tied.includes('rapid')) {
        recommended = 'rapid';
      } else {
        recommended = 'screening';
      }
    }

    return {
      ready: true,
      recommended,
      reasons: reasons.filter(Boolean).slice(0, 3),
      totals
    };
  };

  const renderResults = (result, resultsEl) => {
    resultsEl.innerHTML = '';

    if (!result.ready) {
      resultsEl.textContent = 'Answer all questions to see a recommendation.';
      return;
    }

    const heading = document.createElement('h3');
    heading.textContent = `Recommended tier: ${tierLabels[result.recommended]}`;

    const reasonList = document.createElement('ul');
    result.reasons.forEach((reason) => {
      const li = document.createElement('li');
      li.textContent = reason;
      reasonList.appendChild(li);
    });

    const buttons = document.createElement('div');
    buttons.className = 'button-row';

    Object.keys(tierLinks).forEach((tier) => {
      const link = document.createElement('a');
      link.className = 'btn';
      link.href = tierLinks[tier];
      link.textContent = `${tierLabels[tier]} Tier`;
      if (tier === result.recommended) {
        link.classList.add('btn-primary');
      }
      buttons.appendChild(link);
    });

    resultsEl.appendChild(heading);
    resultsEl.appendChild(reasonList);
    resultsEl.appendChild(buttons);
  };

  const init = async () => {
    try {
      const response = await fetch(dataUrl);
      const questions = await response.json();

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

      const form = buildForm(questions);
      const results = document.createElement('div');
      results.className = 'tier-results';
      results.textContent = 'Answer all questions to see a recommendation.';

      form.addEventListener('submit', (event) => {
        event.preventDefault();
        const result = computeRecommendation(questions, form);
        renderResults(result, results);
      });

      form.addEventListener('change', () => {
        const result = computeRecommendation(questions, form);
        if (result.ready) {
          renderResults(result, results);
        }
      });

      ui.appendChild(form);
      ui.appendChild(results);
    } catch (error) {
      if (ui) {
        ui.textContent = 'Tier selector failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
