(() => {
  const initPageHeaderShrink = () => {
    const hero = document.querySelector('.tier-selector-page-hero');
    if (!hero) {
      return;
    }

    let ticking = false;
    const applyState = () => {
      ticking = false;
      hero.classList.toggle('is-condensed', window.scrollY > 0);
    };

    applyState();

    window.addEventListener(
      'scroll',
      () => {
        if (ticking) {
          return;
        }
        ticking = true;
        window.requestAnimationFrame(applyState);
      },
      { passive: true }
    );
  };

  initPageHeaderShrink();

  const containers = Array.from(document.querySelectorAll('.tier-selector'));
  if (!containers.length) {
    return;
  }

  const TIERS = ['screening', 'rapid', 'detailed'];
  const tierLabels = {
    screening: 'Screening',
    rapid: 'Rapid',
    detailed: 'Detailed'
  };

  const scoreFromInput = (input, key) => {
    if (!input) {
      return 0;
    }
    const value = Number.parseFloat(input.dataset[key] || '0');
    return Number.isFinite(value) ? value : 0;
  };

  const getSelectedInput = (form, questionId) =>
    form.querySelector(`input[name="${questionId}"]:checked`);

  const getAnsweredCount = (questions, form) =>
    questions.reduce((count, question) => {
      return getSelectedInput(form, question.id) ? count + 1 : count;
    }, 0);

  const computeTierMaxTotals = (questions) => {
    const maxTotals = { screening: 0, rapid: 0, detailed: 0 };

    questions.forEach((question) => {
      let screeningMax = 0;
      let rapidMax = 0;
      let detailedMax = 0;

      question.answers.forEach((answer) => {
        screeningMax = Math.max(screeningMax, Number(answer.score_screening) || 0);
        rapidMax = Math.max(rapidMax, Number(answer.score_rapid) || 0);
        detailedMax = Math.max(detailedMax, Number(answer.score_detailed) || 0);
      });

      maxTotals.screening += screeningMax;
      maxTotals.rapid += rapidMax;
      maxTotals.detailed += detailedMax;
    });

    return maxTotals;
  };

  const uniqueReasons = (items, limit) => {
    const seen = new Set();
    const reasons = [];

    items.forEach((item) => {
      const reason = (item.reason || '').trim();
      if (!reason || seen.has(reason)) {
        return;
      }
      seen.add(reason);
      reasons.push(reason);
    });

    return reasons.slice(0, limit);
  };

  const computeRecommendation = (questions, form) => {
    const totals = { screening: 0, rapid: 0, detailed: 0 };
    const selectedAnswers = [];
    let highRisk = false;

    questions.forEach((question) => {
      const selected = getSelectedInput(form, question.id);
      if (!selected) {
        return;
      }

      const scores = {
        screening: scoreFromInput(selected, 'scoreScreening'),
        rapid: scoreFromInput(selected, 'scoreRapid'),
        detailed: scoreFromInput(selected, 'scoreDetailed')
      };

      totals.screening += scores.screening;
      totals.rapid += scores.rapid;
      totals.detailed += scores.detailed;

      selectedAnswers.push({
        questionId: question.id,
        reason: selected.dataset.rationale || '',
        scores
      });

      if (question.id === 'decision-risk' && selected.value === 'high') {
        highRisk = true;
      }
    });

    const answeredCount = selectedAnswers.length;
    const totalQuestions = questions.length;

    if (!answeredCount) {
      return {
        ready: false,
        answeredCount,
        totalQuestions,
        totals,
        selectedAnswers,
        recommended: null,
        keyReasons: [],
        previewReason: ''
      };
    }

    const maxScore = Math.max(totals.screening, totals.rapid, totals.detailed);
    const tied = TIERS.filter((tier) => totals[tier] === maxScore);
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

    const rankedReasons = selectedAnswers
      .filter((entry) => entry.reason)
      .map((entry) => ({
        reason: entry.reason,
        weight: entry.scores[recommended] || 0
      }))
      .sort((a, b) => b.weight - a.weight);

    const keyReasons = uniqueReasons(rankedReasons, 4);
    const previewReason = keyReasons[0] || `Current answers lean toward the ${tierLabels[recommended]} tier.`;

    return {
      ready: answeredCount === totalQuestions,
      answeredCount,
      totalQuestions,
      totals,
      recommended,
      selectedAnswers,
      keyReasons,
      previewReason
    };
  };

  const updateStepHint = (question, form) => {
    const hint = form.querySelector(`[data-hint-for="${question.id}"]`);
    const selected = getSelectedInput(form, question.id);
    if (!hint) {
      return;
    }

    if (!selected || !selected.dataset.rationale) {
      hint.textContent = '';
      hint.classList.remove('is-visible');
      hint.setAttribute('aria-hidden', 'true');
      return;
    }

    hint.textContent = selected.dataset.rationale;
    hint.classList.add('is-visible');
    hint.setAttribute('aria-hidden', 'false');
  };

  const updateHints = (questions, form) => {
    questions.forEach((question) => {
      updateStepHint(question, form);
      const card = form.querySelector(`[data-question-card="${question.id}"]`);
      if (!card) {
        return;
      }
      card.classList.toggle('is-answered', Boolean(getSelectedInput(form, question.id)));
    });
  };

  const buildTierSelectorUI = (questions, maxTotals) => {
    const form = document.createElement('form');
    form.className = 'tier-selector-form';

    const header = document.createElement('div');
    header.className = 'tier-form-header';

    const headerCopy = document.createElement('div');
    headerCopy.className = 'tier-form-header-copy';

    const kicker = document.createElement('p');
    kicker.className = 'tier-form-kicker';
    kicker.textContent = 'Tier fit questions';

    const hint = document.createElement('p');
    hint.className = 'tier-form-hint';
    hint.textContent = 'Answer each card. Your recommendation updates as you go.';

    headerCopy.appendChild(kicker);
    headerCopy.appendChild(hint);

    const progressPanel = document.createElement('div');
    progressPanel.className = 'tier-progress-panel';

    const progressCount = document.createElement('p');
    progressCount.className = 'tier-progress-count';
    progressCount.setAttribute('aria-live', 'polite');
    progressCount.textContent = `Questions answered: 0 of ${questions.length}`;

    const progressBar = document.createElement('div');
    progressBar.className = 'tier-progress-bar';
    progressBar.setAttribute('role', 'progressbar');
    progressBar.setAttribute('aria-label', 'Questions answered');
    progressBar.setAttribute('aria-valuemin', '0');
    progressBar.setAttribute('aria-valuemax', String(questions.length));
    progressBar.setAttribute('aria-valuenow', '0');

    const progressFill = document.createElement('span');
    progressFill.className = 'tier-progress-fill';
    progressFill.style.width = '0%';
    progressBar.appendChild(progressFill);

    progressPanel.appendChild(progressCount);
    progressPanel.appendChild(progressBar);

    header.appendChild(headerCopy);
    header.appendChild(progressPanel);
    form.appendChild(header);

    const questionGrid = document.createElement('div');
    questionGrid.className = 'tier-question-grid';

    questions.forEach((question, qIndex) => {
      const fieldset = document.createElement('section');
      fieldset.className = 'tier-question tier-question-card';
      fieldset.dataset.questionCard = question.id;

      const meta = document.createElement('p');
      meta.className = 'tier-question-meta';
      meta.textContent = `Question ${qIndex + 1} of ${questions.length}`;
      fieldset.appendChild(meta);

      const titleId = `ts-${question.id}-title`;
      fieldset.setAttribute('role', 'radiogroup');
      fieldset.setAttribute('aria-labelledby', titleId);

      const title = document.createElement('h3');
      title.className = 'tier-question-title';
      title.id = titleId;
      title.textContent = question.question;
      fieldset.appendChild(title);

      const answerList = document.createElement('div');
      answerList.className = 'tier-answer-list';

      question.answers.forEach((answer, aIndex) => {
        const inputId = `ts-${question.id}-${aIndex}`;

        const label = document.createElement('label');
        label.className = 'tier-answer';

        const input = document.createElement('input');
        input.type = 'radio';
        input.name = question.id;
        input.value = answer.value;
        input.id = inputId;
        input.dataset.scoreScreening = String(answer.score_screening);
        input.dataset.scoreRapid = String(answer.score_rapid);
        input.dataset.scoreDetailed = String(answer.score_detailed);
        input.dataset.rationale = answer.rationale_snippet || '';

        const text = document.createElement('span');
        text.className = 'tier-answer-label';
        text.textContent = answer.label;

        label.appendChild(input);
        label.appendChild(text);
        answerList.appendChild(label);
      });

      const stepHint = document.createElement('p');
      stepHint.className = 'tier-answer-hint';
      stepHint.dataset.hintFor = question.id;
      stepHint.setAttribute('aria-live', 'polite');
      stepHint.setAttribute('aria-hidden', 'true');

      fieldset.appendChild(answerList);
      fieldset.appendChild(stepHint);
      questionGrid.appendChild(fieldset);
    });

    form.appendChild(questionGrid);

    const actions = document.createElement('div');
    actions.className = 'tier-actions';

    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'btn tier-reset-btn';
    resetBtn.textContent = 'Clear answers';

    actions.appendChild(resetBtn);
    form.appendChild(actions);

    const preview = document.createElement('aside');
    preview.className = 'tier-preview';

    const previewKicker = document.createElement('p');
    previewKicker.className = 'tier-preview-kicker';
    previewKicker.textContent = 'Recommended Tier (so far)';

    const previewTier = document.createElement('h3');
    previewTier.className = 'tier-preview-tier';
    previewTier.textContent = 'Not enough information yet';

    const previewWhy = document.createElement('p');
    previewWhy.className = 'tier-preview-why';
    previewWhy.textContent = 'Choose an answer to start the recommendation.';

    const previewMeta = document.createElement('p');
    previewMeta.className = 'tier-preview-meta';
    previewMeta.textContent = `Based on 0 of ${questions.length} answers`;

    const previewReasonsLabel = document.createElement('p');
    previewReasonsLabel.className = 'tier-preview-reasons-label';
    previewReasonsLabel.textContent = 'Key reasons';
    previewReasonsLabel.hidden = true;

    const previewReasons = document.createElement('ul');
    previewReasons.className = 'tier-preview-reasons';
    previewReasons.hidden = true;

    const chartTitle = document.createElement('p');
    chartTitle.className = 'tier-score-chart-title';
    chartTitle.textContent = 'Score tracking';

    const chart = document.createElement('div');
    chart.className = 'tier-score-chart';
    chart.setAttribute('role', 'img');
    chart.setAttribute('aria-label', 'Tier scores as vertical bars');

    const scoreBars = {};

    TIERS.forEach((tier) => {
      const col = document.createElement('div');
      col.className = 'tier-score-col';
      col.dataset.tier = tier;

      const value = document.createElement('p');
      value.className = 'tier-score-value';
      value.textContent = '0';

      const track = document.createElement('div');
      track.className = 'tier-score-track';

      const fill = document.createElement('span');
      fill.className = 'tier-score-fill';
      fill.style.height = '0%';
      track.appendChild(fill);

      const label = document.createElement('p');
      label.className = 'tier-score-label';
      label.textContent = tierLabels[tier];

      const max = document.createElement('p');
      max.className = 'tier-score-max';
      max.textContent = `max ${maxTotals[tier]}`;

      col.appendChild(value);
      col.appendChild(track);
      col.appendChild(label);
      col.appendChild(max);
      chart.appendChild(col);

      scoreBars[tier] = { col, value, fill };
    });

    const previewNote = document.createElement('p');
    previewNote.className = 'tier-preview-note';
    previewNote.textContent = 'Scores update as you change answers.';

    preview.appendChild(previewKicker);
    preview.appendChild(previewTier);
    preview.appendChild(previewWhy);
    preview.appendChild(previewMeta);
    preview.appendChild(previewReasonsLabel);
    preview.appendChild(previewReasons);
    preview.appendChild(chartTitle);
    preview.appendChild(chart);
    preview.appendChild(previewNote);

    return {
      form,
      preview,
      progressCount,
      progressBar,
      progressFill,
      resetBtn,
      previewTier,
      previewWhy,
      previewMeta,
      previewReasonsLabel,
      previewReasons,
      scoreBars
    };
  };

  const updateTierComparisonFocus = (recommendedTier) => {
    const tables = document.querySelectorAll('[data-tier-comparison="true"]');
    if (!tables.length) {
      return;
    }

    tables.forEach((table) => {
      table.classList.remove('tier-focus-screening', 'tier-focus-rapid', 'tier-focus-detailed');

      if (recommendedTier && TIERS.includes(recommendedTier)) {
        table.classList.add(`tier-focus-${recommendedTier}`);
        table.dataset.tierFocus = recommendedTier;
      } else {
        delete table.dataset.tierFocus;
      }
    });
  };

  const updateProgress = (questions, form, refs) => {
    const total = questions.length;
    const answered = getAnsweredCount(questions, form);
    const percent = Math.round((answered / total) * 100);

    refs.progressCount.textContent = `Questions answered: ${answered} of ${total}`;
    refs.progressBar.setAttribute('aria-valuenow', String(answered));
    refs.progressBar.setAttribute('aria-valuetext', `${answered} of ${total} questions answered`);
    refs.progressFill.style.width = `${percent}%`;
  };

  const updateScoreBars = (result, maxTotals, refs) => {
    TIERS.forEach((tier) => {
      const bar = refs.scoreBars[tier];
      if (!bar) {
        return;
      }

      const value = result.totals[tier] || 0;
      const max = maxTotals[tier] || 1;
      const percent = Math.max(0, Math.min(100, Math.round((value / max) * 100)));

      bar.value.textContent = String(value);
      bar.fill.style.height = `${percent}%`;
      bar.col.classList.toggle('is-leading', Boolean(result.recommended) && result.recommended === tier);
    });
  };

  const updatePreview = (result, maxTotals, refs, animate) => {
    if (!result.recommended) {
      refs.previewTier.textContent = 'Not enough information yet';
      refs.previewWhy.textContent = 'Choose an answer to start the recommendation.';
      refs.previewMeta.textContent = `Based on 0 of ${result.totalQuestions} answers`;
    } else {
      refs.previewTier.textContent = tierLabels[result.recommended];
      refs.previewWhy.textContent = result.previewReason;
      refs.previewMeta.textContent = `Based on ${result.answeredCount} of ${result.totalQuestions} answers`;
    }

    if (result.ready && result.keyReasons.length) {
      refs.previewReasonsLabel.hidden = false;
      refs.previewReasons.hidden = false;
      refs.previewReasons.innerHTML = '';

      result.keyReasons.slice(0, 4).forEach((reason) => {
        const item = document.createElement('li');
        item.textContent = reason;
        refs.previewReasons.appendChild(item);
      });
    } else {
      refs.previewReasonsLabel.hidden = true;
      refs.previewReasons.hidden = true;
      refs.previewReasons.innerHTML = '';
    }

    updateScoreBars(result, maxTotals, refs);

    if (animate) {
      refs.preview.classList.add('is-refreshing');
      window.setTimeout(() => {
        refs.preview.classList.remove('is-refreshing');
      }, 180);
    }
  };

  const initContainer = async (container) => {
    const baseUrl = container.dataset.baseurl || '';
    const dataUrl = `${baseUrl}/assets/data/tier-questions.json`;

    const fallback = container.querySelector('.tier-selector-fallback');
    const ui = container.querySelector('.tier-selector-ui');
    if (!ui) {
      return;
    }

    try {
      const response = await fetch(dataUrl);
      if (!response.ok) {
        throw new Error(`Unable to load tier questions (${response.status})`);
      }

      const questions = await response.json();
      if (!Array.isArray(questions) || !questions.length) {
        throw new Error('Tier question data is empty or invalid.');
      }

      const maxTotals = computeTierMaxTotals(questions);
      const refs = buildTierSelectorUI(questions, maxTotals);

      const syncUI = (animatePreview = true) => {
        updateHints(questions, refs.form);
        updateProgress(questions, refs.form, refs);
        const result = computeRecommendation(questions, refs.form);
        updatePreview(result, maxTotals, refs, animatePreview);
        updateTierComparisonFocus(result.ready ? result.recommended : '');
      };

      refs.form.addEventListener('submit', (event) => {
        event.preventDefault();
      });

      refs.form.addEventListener('change', (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement) || target.type !== 'radio') {
          return;
        }
        syncUI(true);
      });

      refs.resetBtn.addEventListener('click', () => {
        refs.form.reset();
        syncUI(false);
      });

      syncUI(false);

      if (fallback) {
        fallback.hidden = true;
      }
      ui.hidden = false;
      ui.innerHTML = '';
      ui.appendChild(refs.form);
      ui.appendChild(refs.preview);
    } catch (error) {
      ui.textContent = 'Tier selector failed to load.';
      ui.hidden = false;
      console.error('Tier selector failed to load.', error);
    }
  };

  containers.forEach((container) => {
    initContainer(container);
  });
})();
