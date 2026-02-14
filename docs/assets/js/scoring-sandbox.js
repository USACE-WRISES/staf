(() => {
  // Compute outcome indices and overall condition from function scores.
  const container = document.querySelector('.scoring-sandbox');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const functionsUrl = `${baseUrl}/assets/data/functions.json`;
  const mappingUrl = `${baseUrl}/assets/data/cwa-mapping.json`;
  const exampleUrl = `${baseUrl}/assets/data/scoring-example.json`;
  const fallback = container.querySelector('.scoring-sandbox-fallback');
  const ui = container.querySelector('.scoring-sandbox-ui');

  const weightFromCode = (code) => {
    if (code === 'D') {
      return 1.0;
    }
    if (code === 'i') {
      return 0.1;
    }
    return 0.0;
  };

  const weightLabelFromCode = (code) => {
    if (code === 'D') {
      return 'D';
    }
    if (code === 'i') {
      return 'i';
    }
    return '-';
  };

  const summaryColorForValue = (value) => {
    if (value <= 0.39) {
      return '#f5b5b5';
    }
    if (value <= 0.69) {
      return '#f5e7a6';
    }
    return '#c8d9f2';
  };

  const functionScoreColorForValue = (value) => {
    if (value <= 5) {
      return '#f5b5b5';
    }
    if (value <= 10) {
      return '#f5e7a6';
    }
    return '#c8d9f2';
  };

  const getScreeningSliderPalette = (score) => {
    if (score >= 11) {
      return { base: '#a7c7f2', active: '#7faee9' };
    }
    if (score >= 6) {
      return { base: '#f7e088', active: '#f0cd52' };
    }
    return { base: '#ef9a9a', active: '#e07070' };
  };

  const updateScreeningSliderVisual = (rangeInput, scoreValue) => {
    if (!rangeInput) {
      return;
    }
    const min = Number(rangeInput.min) || 0;
    const max = Number(rangeInput.max) || 15;
    const safeValue = Number.isFinite(scoreValue) ? scoreValue : min;
    const clamped = Math.min(max, Math.max(min, safeValue));
    const percent = max > min ? ((clamped - min) / (max - min)) * 100 : 0;
    const palette = getScreeningSliderPalette(clamped);
    rangeInput.style.setProperty('--screening-score-pct', `${percent}%`);
    rangeInput.style.setProperty('--screening-score-color', palette.base);
    rangeInput.style.setProperty('--screening-score-color-active', palette.active);
  };

  const slugCategory = (category) =>
    `category-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
  const collapsedGlyph = '&#9656;';
  const expandedGlyph = '&#9662;';

  const init = async () => {
    try {
      const [functionsList, mappingList, exampleList] = await Promise.all([
        fetch(functionsUrl).then((r) => r.json()),
        fetch(mappingUrl).then((r) => r.json()),
        fetch(exampleUrl).then((r) => r.json())
      ]);

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

      const mappingById = mappingList.reduce((acc, item) => {
        acc[item.id] = item;
        return acc;
      }, {});

      const sampleScores = exampleList.reduce((acc, item) => {
        acc[item.function_id] = item.score;
        return acc;
      }, {});

      const table = document.createElement('table');
      table.className = 'scoring-table screening-table show-condensed-view show-function-mappings';
      const thead = document.createElement('thead');
      thead.innerHTML =
        '<tr>' +
        '<th>Discipline</th>' +
        '<th>Function</th>' +
        '<th class="col-function-score">Function<br>Score</th>' +
        '<th class="col-physical">Physical</th>' +
        '<th class="col-chemical">Chemical</th>' +
        '<th class="col-biological">Biological</th>' +
        '</tr>';
      const tbody = document.createElement('tbody');
      const tfoot = document.createElement('tfoot');
      table.appendChild(thead);
      table.appendChild(tbody);
      table.appendChild(tfoot);

      const widgetState = {
        showFunctionMappings: true,
        showRollupComputations: false,
        showFunctionScoreCueLabels: false
      };

      const inputs = new Map();
      const summaryRows = {
        physical: [],
        chemical: [],
        biological: []
      };

      const spans = new Array(functionsList.length).fill(0);
      let i = 0;
      while (i < functionsList.length) {
        const category = functionsList[i].category;
        let j = i + 1;
        while (j < functionsList.length && functionsList[j].category === category) {
          j += 1;
        }
        spans[i] = j - i;
        i = j;
      }

      functionsList.forEach((fn, index) => {
        const mapping = mappingById[fn.id] || { physical: '-', chemical: '-', biological: '-' };
        const row = document.createElement('tr');
        row.classList.add(slugCategory(fn.category));

        if (spans[index] > 0) {
          const disciplineCell = document.createElement('td');
          disciplineCell.textContent = fn.category;
          disciplineCell.rowSpan = spans[index];
          disciplineCell.className = 'discipline-cell';
          row.appendChild(disciplineCell);
        }

        const nameCell = document.createElement('td');
        nameCell.className = 'function-cell';
        const nameLine = document.createElement('div');
        nameLine.className = 'function-title';
        const nameText = document.createElement('span');
        nameText.textContent = fn.name;
        nameLine.appendChild(nameText);
        const functionToggle = document.createElement('button');
        functionToggle.type = 'button';
        functionToggle.className = 'criteria-toggle function-toggle';
        functionToggle.innerHTML = collapsedGlyph;
        functionToggle.setAttribute('aria-expanded', 'false');
        functionToggle.setAttribute('aria-label', 'Toggle function statement');
        functionToggle.addEventListener('mousedown', (event) => {
          if (event.detail > 0) {
            event.preventDefault();
          }
        });
        nameLine.appendChild(functionToggle);
        nameCell.appendChild(nameLine);

        const statementLine = document.createElement('div');
        statementLine.className = 'function-statement';
        statementLine.textContent = fn.function_statement || fn.functionStatement || '';
        statementLine.hidden = true;
        nameCell.appendChild(statementLine);

        const functionScoreCell = document.createElement('td');
        functionScoreCell.className = 'col-function-score function-score-cell';
        const scoreWrap = document.createElement('div');
        scoreWrap.className = 'score-input function-score-inline';
        const sliderWrap = document.createElement('div');
        sliderWrap.className = 'function-score-slider';
        const cueLabels = document.createElement('div');
        cueLabels.className = 'function-score-cue-labels';
        [
          { text: 'NF', left: '16.67%' },
          { text: 'AR', left: '50%' },
          { text: 'F', left: '83.33%' }
        ].forEach(({ text, left }) => {
          const label = document.createElement('span');
          label.className = 'function-score-cue-label';
          label.textContent = text;
          label.style.left = left;
          cueLabels.appendChild(label);
        });
        const cueBar = document.createElement('button');
        cueBar.type = 'button';
        cueBar.className = 'function-score-cue-bar';
        cueBar.disabled = false;
        cueBar.setAttribute('aria-label', 'Set function score by range');
        cueBar.setAttribute('aria-disabled', 'false');
        [0, 33.33, 66.67, 100].forEach((percent) => {
          const tick = document.createElement('span');
          tick.className = 'function-score-cue-tick';
          tick.style.left = `${percent}%`;
          cueBar.appendChild(tick);
        });
        const input = document.createElement('input');
        input.type = 'range';
        input.min = '0';
        input.max = '15';
        input.step = '1';
        const currentScore = sampleScores[fn.id] !== undefined ? sampleScores[fn.id] : 0;
        input.value = String(currentScore);
        input.setAttribute('value', String(currentScore));
        input.disabled = false;
        input.setAttribute('aria-label', `${fn.name} score`);
        updateScreeningSliderVisual(input, currentScore);
        const value = document.createElement('span');
        value.className = 'score-value';
        value.textContent = String(currentScore);
        sliderWrap.appendChild(cueLabels);
        sliderWrap.appendChild(cueBar);
        sliderWrap.appendChild(input);
        scoreWrap.appendChild(sliderWrap);
        scoreWrap.appendChild(value);
        functionScoreCell.appendChild(scoreWrap);
        const setInteractionState = (isActive) => {
          sliderWrap.classList.toggle('is-dragging', Boolean(isActive));
        };
        let cueHighlightTimer = null;
        const clearCueHighlightTimer = () => {
          if (!cueHighlightTimer) {
            return;
          }
          clearTimeout(cueHighlightTimer);
          cueHighlightTimer = null;
        };
        const endSliderInteraction = () => {
          clearCueHighlightTimer();
          setInteractionState(false);
        };
        const setScoreFromCueBar = (event) => {
          if (cueBar.disabled || input.disabled) {
            return;
          }
          const rect = cueBar.getBoundingClientRect();
          if (!rect.width) {
            return;
          }
          const min = Number(input.min) || 0;
          const max = Number(input.max) || 15;
          const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
          const nextValue = Math.round(min + (relativeX / rect.width) * (max - min));
          setInteractionState(true);
          input.value = String(nextValue);
          input.dispatchEvent(new Event('input', { bubbles: true }));
          clearCueHighlightTimer();
          cueHighlightTimer = setTimeout(() => {
            setInteractionState(false);
            cueHighlightTimer = null;
          }, 180);
        };
        input.addEventListener('pointerdown', () => {
          clearCueHighlightTimer();
          setInteractionState(true);
        });
        input.addEventListener('pointerup', () => {
          endSliderInteraction();
        });
        input.addEventListener('pointercancel', () => {
          endSliderInteraction();
        });
        input.addEventListener('blur', () => {
          endSliderInteraction();
        });
        cueBar.addEventListener('pointerdown', (event) => {
          event.preventDefault();
          setScoreFromCueBar(event);
        });
        functionToggle.addEventListener('click', (event) => {
          if (!statementLine.textContent) {
            return;
          }
          const isOpen = !statementLine.hidden;
          statementLine.hidden = isOpen;
          functionToggle.setAttribute('aria-expanded', String(!isOpen));
          functionToggle.innerHTML = statementLine.hidden ? collapsedGlyph : expandedGlyph;
          if (event.detail > 0) {
            setTimeout(() => functionToggle.blur(), 0);
          }
        });

        const physicalCell = document.createElement('td');
        const chemicalCell = document.createElement('td');
        const biologicalCell = document.createElement('td');
        physicalCell.className = 'weight-cell col-physical';
        chemicalCell.className = 'weight-cell col-chemical';
        biologicalCell.className = 'weight-cell col-biological';

        const pWeight = weightFromCode(mapping.physical);
        const cWeight = weightFromCode(mapping.chemical);
        const bWeight = weightFromCode(mapping.biological);

        physicalCell.textContent = weightLabelFromCode(mapping.physical);
        chemicalCell.textContent = weightLabelFromCode(mapping.chemical);
        biologicalCell.textContent = weightLabelFromCode(mapping.biological);

        row.appendChild(nameCell);
        row.appendChild(functionScoreCell);
        row.appendChild(physicalCell);
        row.appendChild(chemicalCell);
        row.appendChild(biologicalCell);

        tbody.appendChild(row);
        inputs.set(fn.id, {
          input,
          value,
          cueBar,
          sliderWrap,
          weights: { p: pWeight, c: cWeight, b: bWeight }
        });
      });

      const labelItems = [
        { label: 'Direct Effect', rollup: true },
        { label: 'Indirect Effect', rollup: true },
        { label: 'Weighted Score Total', rollup: true },
        { label: 'Max Weighted Score Total', rollup: true },
        { label: 'Condition Sub-Index', rollup: false },
        { label: 'Ecosystem Condition Index', rollup: false }
      ];

      const buildSummaryStack = (store, count) => {
        const stack = document.createElement('div');
        stack.className = 'summary-stack';
        for (let i = 0; i < count; i += 1) {
          const div = document.createElement('div');
          div.textContent = '-';
          store.push(div);
          stack.appendChild(div);
        }
        return stack;
      };

      for (let i = 0; i < labelItems.length; i += 1) {
        const item = labelItems[i];
        const row = document.createElement('tr');
        if (item.rollup) {
          row.dataset.rollupRow = 'true';
        }
        const labelCell = document.createElement('td');
        labelCell.colSpan = 3;
        labelCell.className = 'summary-labels';
        labelCell.textContent = item.label;
        row.appendChild(labelCell);

        if (item.label === 'Ecosystem Condition Index') {
          const mergedCell = document.createElement('td');
          mergedCell.colSpan = 3;
          mergedCell.className = 'summary-values summary-merged';
          const stack = buildSummaryStack(summaryRows.chemical, 1);
          mergedCell.appendChild(stack);
          row.appendChild(mergedCell);
        } else {
          const physicalCell = document.createElement('td');
          physicalCell.className = 'summary-values';
          const chemicalCell = document.createElement('td');
          chemicalCell.className = 'summary-values';
          const biologicalCell = document.createElement('td');
          biologicalCell.className = 'summary-values';

          const physicalStack = buildSummaryStack(summaryRows.physical, 1);
          const chemicalStack = buildSummaryStack(summaryRows.chemical, 1);
          const biologicalStack = buildSummaryStack(summaryRows.biological, 1);

          physicalCell.appendChild(physicalStack);
          chemicalCell.appendChild(chemicalStack);
          biologicalCell.appendChild(biologicalStack);

          row.appendChild(physicalCell);
          row.appendChild(chemicalCell);
          row.appendChild(biologicalCell);
        }

        tfoot.appendChild(row);
      }

      const summary = document.createElement('div');
      summary.className = 'outcome-summary';

      summary.textContent = 'Direct mapping (1.0), Indirect Mapping (0.1).';

      const chartsShell = document.createElement('div');
      chartsShell.className = 'screening-settings-panel screening-charts-panel';
      const chartsHeader = document.createElement('div');
      chartsHeader.className = 'settings-header screening-charts-header';
      const chartsTitle = document.createElement('h3');
      chartsTitle.textContent = 'Summary Plots';
      chartsHeader.appendChild(chartsTitle);
      const chartsLegend = document.createElement('div');
      chartsLegend.className = 'screening-chart-legend';
      chartsLegend.innerHTML =
        '<div class="legend-group" aria-label="Function score ranges">' +
        '<div class="legend-labels legend-labels-left">' +
        '<div>Functioning</div>' +
        '<div>At-Risk</div>' +
        '<div>Non-Functioning</div>' +
        '</div>' +
        '<div class="legend-bar" aria-hidden="true"></div>' +
        '<div class="legend-labels legend-labels-right">' +
        '<div>11 - 15</div>' +
        '<div>6 - 10</div>' +
        '<div>0 - 5</div>' +
        '</div>' +
        '</div>' +
        '<div class="legend-spacer" aria-hidden="true"></div>' +
        '<div class="legend-group" aria-label="Condition index ranges">' +
        '<div class="legend-labels legend-labels-left">' +
        '<div>Functioning</div>' +
        '<div>At-Risk</div>' +
        '<div>Non-Functioning</div>' +
        '</div>' +
        '<div class="legend-bar" aria-hidden="true"></div>' +
        '<div class="legend-labels legend-labels-right">' +
        '<div>0.70 - 1.00</div>' +
        '<div>0.40 - 0.69</div>' +
        '<div>0.00 - 0.39</div>' +
        '</div>' +
        '</div>';
      chartsHeader.appendChild(chartsLegend);
      const chartsWrap = document.createElement('div');
      chartsWrap.className = 'screening-charts';
      const functionChartSection = document.createElement('div');
      functionChartSection.className = 'screening-chart-section';
      const functionChartTitle = document.createElement('div');
      functionChartTitle.className = 'screening-chart-title';
      functionChartTitle.textContent = 'Function Scores';
      const functionChartBody = document.createElement('div');
      functionChartBody.className = 'screening-chart-body';
      functionChartSection.appendChild(functionChartTitle);
      functionChartSection.appendChild(functionChartBody);

      const summaryChartSection = document.createElement('div');
      summaryChartSection.className = 'screening-chart-section';
      const summaryChartTitle = document.createElement('div');
      summaryChartTitle.className = 'screening-chart-title';
      summaryChartTitle.textContent = 'Condition Indices';
      const summaryChartBody = document.createElement('div');
      summaryChartBody.className = 'screening-chart-body';
      summaryChartSection.appendChild(summaryChartTitle);
      summaryChartSection.appendChild(summaryChartBody);

      chartsWrap.appendChild(functionChartSection);
      const chartsSpacer = document.createElement('div');
      chartsSpacer.className = 'screening-chart-spacer';
      chartsWrap.appendChild(chartsSpacer);
      chartsWrap.appendChild(summaryChartSection);
      chartsShell.appendChild(chartsHeader);
      chartsShell.appendChild(chartsWrap);

      const buildBarRow = ({ label, value, maxValue, color, valueText }) => {
        const row = document.createElement('div');
        row.className = 'screening-bar-row';
        const labelCell = document.createElement('div');
        labelCell.className = 'screening-bar-label';
        labelCell.textContent = label;
        const barCell = document.createElement('div');
        barCell.className = 'screening-bar-track';
        const fill = document.createElement('div');
        fill.className = 'screening-bar-fill';
        const width = maxValue > 0 ? Math.max(0, Math.min(1, value / maxValue)) : 0;
        fill.style.width = `${(width * 100).toFixed(1)}%`;
        fill.style.background = color;
        barCell.appendChild(fill);
        const valueCell = document.createElement('div');
        valueCell.className = 'screening-bar-value';
        valueCell.textContent = valueText ?? value.toFixed(2);
        row.appendChild(labelCell);
        row.appendChild(barCell);
        row.appendChild(valueCell);
        return row;
      };

      const functionOrder = functionsList.map((fn) => ({
        functionId: fn.id,
        name: fn.name,
        discipline: fn.category || ''
      }));

      const renderCharts = (functionScores, summaryValues) => {
        functionChartBody.innerHTML = '';
        summaryChartBody.innerHTML = '';

        if (!functionOrder.length) {
          const empty = document.createElement('div');
          empty.className = 'screening-chart-empty';
          empty.textContent = 'No function scores available.';
          functionChartBody.appendChild(empty);
        } else {
          let lastDiscipline = null;
          functionOrder.forEach((fn, index) => {
            const disciplineKey = (fn.discipline || '').toLowerCase();
            if (index > 0 && lastDiscipline !== null && disciplineKey !== lastDiscipline) {
              const divider = document.createElement('div');
              divider.className = 'screening-bar-divider';
              functionChartBody.appendChild(divider);
            }
            const score = functionScores.get(fn.functionId);
            if (score === undefined || score === null) {
              return;
            }
            const rounded = Math.round(score);
            const barColor = functionScoreColorForValue(rounded);
            functionChartBody.appendChild(
              buildBarRow({
                label: fn.name,
                value: rounded,
                maxValue: 15,
                color: barColor,
                valueText: String(rounded)
              })
            );
            lastDiscipline = disciplineKey;
          });
        }

        const summaryItems = [
          { label: 'Physical', value: summaryValues.physical },
          { label: 'Chemical', value: summaryValues.chemical },
          { label: 'Biological', value: summaryValues.biological },
          { label: 'Overall Ecosystem', value: summaryValues.ecosystem }
        ];
        summaryItems.forEach((item) => {
          if (item.value === null || item.value === undefined) {
            return;
          }
          summaryChartBody.appendChild(
            buildBarRow({
              label: item.label,
              value: item.value,
              maxValue: 1,
              color: summaryColorForValue(item.value),
              valueText: item.value.toFixed(2)
            })
          );
        });
      };

      const scoringControls = document.createElement('div');
      scoringControls.className = 'screening-scoring-controls';

      const mappingToggleLabel = document.createElement('label');
      mappingToggleLabel.className = 'screening-advanced-toggle';
      const mappingToggle = document.createElement('input');
      mappingToggle.type = 'checkbox';
      mappingToggle.className = 'screening-mapping-toggle-input';
      const mappingToggleText = document.createElement('span');
      mappingToggleText.textContent = 'Show Function Mappings';
      mappingToggleLabel.appendChild(mappingToggle);
      mappingToggleLabel.appendChild(mappingToggleText);

      const rollupToggleLabel = document.createElement('label');
      rollupToggleLabel.className = 'screening-advanced-toggle';
      const rollupToggle = document.createElement('input');
      rollupToggle.type = 'checkbox';
      rollupToggle.className = 'screening-rollup-toggle-input';
      const rollupToggleText = document.createElement('span');
      rollupToggleText.textContent = 'Show roll-up at bottom';
      rollupToggleLabel.appendChild(rollupToggle);
      rollupToggleLabel.appendChild(rollupToggleText);

      const sliderLabelsToggleLabel = document.createElement('label');
      sliderLabelsToggleLabel.className = 'screening-advanced-toggle';
      const sliderLabelsToggle = document.createElement('input');
      sliderLabelsToggle.type = 'checkbox';
      sliderLabelsToggle.className = 'screening-slider-labels-toggle-input';
      const sliderLabelsToggleText = document.createElement('span');
      sliderLabelsToggleText.textContent = 'Show F/AR/NF labels';
      sliderLabelsToggleLabel.appendChild(sliderLabelsToggle);
      sliderLabelsToggleLabel.appendChild(sliderLabelsToggleText);

      const randomizeBtn = document.createElement('button');
      randomizeBtn.type = 'button';
      randomizeBtn.className = 'btn btn-small scoring-randomize-btn';
      randomizeBtn.textContent = 'Randomize Function Scores';

      scoringControls.appendChild(mappingToggleLabel);
      scoringControls.appendChild(rollupToggleLabel);
      scoringControls.appendChild(sliderLabelsToggleLabel);
      scoringControls.appendChild(randomizeBtn);

      const applyRollupRowsVisibility = () => {
        const showRollup = Boolean(widgetState.showRollupComputations);
        table.querySelectorAll('tr[data-rollup-row="true"]').forEach((row) => {
          row.hidden = !showRollup;
        });
      };

      const syncViewState = () => {
        mappingToggle.checked = Boolean(widgetState.showFunctionMappings);
        rollupToggle.checked = Boolean(widgetState.showRollupComputations);
        sliderLabelsToggle.checked = Boolean(widgetState.showFunctionScoreCueLabels);
        table.classList.toggle('show-function-mappings', widgetState.showFunctionMappings);
        table.classList.toggle(
          'show-function-score-cue-labels',
          widgetState.showFunctionScoreCueLabels
        );
        chartsShell.classList.toggle(
          'show-function-score-cue-labels',
          widgetState.showFunctionScoreCueLabels
        );
        applyRollupRowsVisibility();
      };

      const updateScores = () => {
        let pSum = 0;
        let pWeighted = 0;
        let cSum = 0;
        let cWeighted = 0;
        let bSum = 0;
        let bWeighted = 0;
        let pDirect = 0;
        let pIndirect = 0;
        let cDirect = 0;
        let cIndirect = 0;
        let bDirect = 0;
        let bIndirect = 0;
        const functionScores = new Map();

        inputs.forEach(({ input, weights }, functionId) => {
          const score = parseFloat(input.value) || 0;
          functionScores.set(functionId, score);
          pSum += weights.p;
          pWeighted += score * weights.p;
          cSum += weights.c;
          cWeighted += score * weights.c;
          bSum += weights.b;
          bWeighted += score * weights.b;
          if (weights.p === 1.0) {
            pDirect += 1;
          } else if (weights.p === 0.1) {
            pIndirect += 1;
          }
          if (weights.c === 1.0) {
            cDirect += 1;
          } else if (weights.c === 0.1) {
            cIndirect += 1;
          }
          if (weights.b === 1.0) {
            bDirect += 1;
          } else if (weights.b === 0.1) {
            bIndirect += 1;
          }
        });

        const physicalScore = pSum > 0 ? pWeighted / pSum : 0;
        const chemicalScore = cSum > 0 ? cWeighted / cSum : 0;
        const biologicalScore = bSum > 0 ? bWeighted / bSum : 0;

        const toIndex = (value) => Math.min(1, Math.max(0, value / 15));
        const physicalIndex = toIndex(physicalScore);
        const chemicalIndex = toIndex(chemicalScore);
        const biologicalIndex = toIndex(biologicalScore);
        const ecosystemCondition = (physicalIndex + chemicalIndex + biologicalIndex) / 3;

        const formatNumber = (value) => value.toFixed(2);
        const formatCount = (value) => String(value);

        summaryRows.physical[0].textContent = formatCount(pDirect);
        summaryRows.physical[1].textContent = formatCount(pIndirect);
        const pMaxWeighted = pSum * 15;
        const cMaxWeighted = cSum * 15;
        const bMaxWeighted = bSum * 15;

        summaryRows.physical[2].textContent = formatNumber(pWeighted);
        summaryRows.physical[3].textContent = formatNumber(pMaxWeighted);
        summaryRows.physical[4].textContent = formatNumber(physicalIndex);

        summaryRows.chemical[0].textContent = formatCount(cDirect);
        summaryRows.chemical[1].textContent = formatCount(cIndirect);
        summaryRows.chemical[2].textContent = formatNumber(cWeighted);
        summaryRows.chemical[3].textContent = formatNumber(cMaxWeighted);
        summaryRows.chemical[4].textContent = formatNumber(chemicalIndex);
        summaryRows.chemical[5].textContent = formatNumber(ecosystemCondition);

        summaryRows.biological[0].textContent = formatCount(bDirect);
        summaryRows.biological[1].textContent = formatCount(bIndirect);
        summaryRows.biological[2].textContent = formatNumber(bWeighted);
        summaryRows.biological[3].textContent = formatNumber(bMaxWeighted);
        summaryRows.biological[4].textContent = formatNumber(biologicalIndex);

        renderCharts(functionScores, {
          physical: physicalIndex,
          chemical: chemicalIndex,
          biological: biologicalIndex,
          ecosystem: ecosystemCondition
        });
      };

      inputs.forEach(({ input, value }) => {
        input.addEventListener('input', () => {
          value.textContent = input.value;
          input.setAttribute('value', input.value);
          updateScreeningSliderVisual(input, Number(input.value));
          updateScores();
        });
      });

      mappingToggle.addEventListener('change', () => {
        widgetState.showFunctionMappings = mappingToggle.checked;
        syncViewState();
      });

      rollupToggle.addEventListener('change', () => {
        widgetState.showRollupComputations = rollupToggle.checked;
        syncViewState();
      });

      sliderLabelsToggle.addEventListener('change', () => {
        widgetState.showFunctionScoreCueLabels = sliderLabelsToggle.checked;
        syncViewState();
      });

      randomizeBtn.addEventListener('click', () => {
        inputs.forEach(({ input, value }) => {
          const randomScore = Math.floor(Math.random() * 16);
          input.value = String(randomScore);
          value.textContent = input.value;
          input.setAttribute('value', input.value);
          updateScreeningSliderVisual(input, Number(input.value));
        });
        updateScores();
      });

      syncViewState();
      ui.appendChild(scoringControls);
      ui.appendChild(table);
      ui.appendChild(summary);
      ui.appendChild(chartsShell);

      updateScores();
    } catch (error) {
      if (ui) {
        ui.textContent = 'Scoring sandbox failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
