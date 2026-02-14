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

  const normalizeMappingCode = (code) => {
    if (code === 'D') {
      return 'D';
    }
    if (code === 'i') {
      return 'i';
    }
    return '-';
  };

  const clampIndirectMappingWeight = (value, fallback = 0.1) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return fallback;
    }
    return Math.max(0, Math.min(1, numeric));
  };

  const formatMappingWeight = (value) => {
    if (!Number.isFinite(value)) {
      return '0';
    }
    const rounded = Math.round(value * 1000) / 1000;
    return String(rounded);
  };

  const weightFromCode = (code, indirectWeight = 0.1) => {
    const resolvedCode = normalizeMappingCode(code);
    if (resolvedCode === 'D') {
      return 1.0;
    }
    if (resolvedCode === 'i') {
      return clampIndirectMappingWeight(indirectWeight, 0.1);
    }
    return 0.0;
  };

  const weightLabelFromCode = (code) => normalizeMappingCode(code);

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
      const tbody = document.createElement('tbody');
      const tfoot = document.createElement('tfoot');
      table.appendChild(thead);
      table.appendChild(tbody);
      table.appendChild(tfoot);

      const summaryTable = document.createElement('table');
      summaryTable.className = 'screening-table screening-summary-table';
      const summaryColGroup = document.createElement('colgroup');
      summaryTable.appendChild(summaryColGroup);
      const summaryBody = document.createElement('tbody');
      summaryTable.appendChild(summaryBody);

      const widgetState = {
        showFunctionMappings: true,
        showRollupComputations: false,
        showFunctionScoreCueLabels: false,
        enableMappingEditing: false,
        indirectMappingWeight: 0.1
      };

      const inputs = new Map();
      const summaryRows = {
        physical: [],
        chemical: [],
        biological: [],
        ecosystem: null
      };

      const renderHeader = (showMappings) => {
        const mappingHeaders = showMappings
          ? '<th class="col-physical">Physical</th>' +
            '<th class="col-chemical">Chemical</th>' +
            '<th class="col-biological">Biological</th>'
          : '';
        thead.innerHTML =
          '<tr>' +
          '<th>Discipline</th>' +
          '<th>Function</th>' +
          '<th class="col-function-score">Function<br>Score</th>' +
          mappingHeaders +
          '</tr>';
      };

      const mappingDimensions = [
        { key: 'physical', className: 'col-physical', label: 'Physical' },
        { key: 'chemical', className: 'col-chemical', label: 'Chemical' },
        { key: 'biological', className: 'col-biological', label: 'Biological' }
      ];

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

        const mappingEditors = {};
        mappingDimensions.forEach(({ key, className, label }) => {
          const initialCode = normalizeMappingCode(mapping[key]);
          const cell = document.createElement('td');
          cell.className = `weight-cell ${className}`;
          const valueEl = document.createElement('span');
          valueEl.className = 'mapping-display-value';
          valueEl.textContent = weightLabelFromCode(initialCode);
          const select = document.createElement('select');
          select.className = 'mapping-edit-select';
          select.setAttribute(
            'aria-label',
            `${label} mapping for ${fn.name}`
          );
          ['D', 'i', '-'].forEach((optionValue) => {
            const option = document.createElement('option');
            option.value = optionValue;
            option.textContent = optionValue;
            select.appendChild(option);
          });
          select.value = initialCode;
          select.hidden = true;
          select.disabled = true;
          select.addEventListener('change', () => {
            const nextCode = normalizeMappingCode(select.value);
            mappingEditors[key].code = nextCode;
            select.value = nextCode;
            valueEl.textContent = weightLabelFromCode(nextCode);
            updateScores();
          });
          cell.appendChild(valueEl);
          cell.appendChild(select);
          mappingEditors[key] = { code: initialCode, valueEl, select, cell };
        });

        row.appendChild(nameCell);
        row.appendChild(functionScoreCell);
        mappingDimensions.forEach(({ key }) => {
          row.appendChild(mappingEditors[key].cell);
        });

        tbody.appendChild(row);
        inputs.set(fn.id, {
          input,
          value,
          cueBar,
          sliderWrap,
          mappings: mappingEditors
        });
      });

      const buildSummary = (showMappings) => {
        const labelItems = [
          { label: 'Direct Effect', rollup: true },
          { label: 'Indirect Effect', rollup: true },
          { label: 'Weighted Score Total', rollup: true },
          { label: 'Max Weighted Score Total', rollup: true },
          { label: 'Condition Sub-Index', rollup: false },
          { label: 'Ecosystem Condition Index', rollup: false }
        ];
        const baseLabelSpan = 3;
        const labelSpan = showMappings ? baseLabelSpan : Math.max(1, baseLabelSpan - 3);
        const totalSpan = labelSpan + 3;

        summaryColGroup.innerHTML = '';
        const labelCol = document.createElement('col');
        labelCol.span = labelSpan;
        labelCol.className = 'summary-label-col';
        summaryColGroup.appendChild(labelCol);
        ['physical', 'chemical', 'biological'].forEach((key) => {
          const col = document.createElement('col');
          col.className = `col-${key}`;
          summaryColGroup.appendChild(col);
        });

        tfoot.innerHTML = '';
        summaryBody.innerHTML = '';
        summaryRows.physical = [];
        summaryRows.chemical = [];
        summaryRows.biological = [];
        summaryRows.ecosystem = null;
        const summaryTarget = showMappings ? tfoot : summaryBody;

        if (!showMappings) {
          const gapRow = document.createElement('tr');
          gapRow.className = 'summary-gap-row';
          const gapCell = document.createElement('td');
          gapCell.colSpan = totalSpan;
          gapRow.appendChild(gapCell);
          summaryTarget.appendChild(gapRow);

          const headerRow = document.createElement('tr');
          const spacer = document.createElement('td');
          spacer.colSpan = labelSpan;
          spacer.className = 'summary-labels summary-spacer';
          headerRow.appendChild(spacer);
          [
            { label: 'Physical', className: 'col-physical' },
            { label: 'Chemical', className: 'col-chemical' },
            { label: 'Biological', className: 'col-biological' }
          ].forEach(({ label, className }) => {
            const cell = document.createElement('td');
            cell.className = `summary-mapping-header ${className}`;
            cell.textContent = label;
            headerRow.appendChild(cell);
          });
          summaryTarget.appendChild(headerRow);
        }

        labelItems.forEach((item) => {
          const row = document.createElement('tr');
          if (item.rollup) {
            row.dataset.rollupRow = 'true';
            if (!widgetState.showRollupComputations) {
              row.hidden = true;
            }
          }
          const labelCell = document.createElement('td');
          labelCell.colSpan = labelSpan;
          labelCell.className = 'summary-labels';
          labelCell.textContent = item.label;
          row.appendChild(labelCell);

          if (item.label === 'Ecosystem Condition Index') {
            const mergedCell = document.createElement('td');
            mergedCell.colSpan = 3;
            mergedCell.className = 'summary-values summary-merged';
            const value = document.createElement('div');
            value.textContent = '-';
            mergedCell.appendChild(value);
            summaryRows.ecosystem = value;
            row.appendChild(mergedCell);
          } else {
            ['physical', 'chemical', 'biological'].forEach((key) => {
              const cell = document.createElement('td');
              cell.className = `summary-values col-${key}`;
              const value = document.createElement('div');
              value.textContent = '-';
              summaryRows[key].push(value);
              cell.appendChild(value);
              row.appendChild(cell);
            });
          }

          summaryTarget.appendChild(row);
        });
      };

      const summary = document.createElement('div');
      summary.className = 'outcome-summary';
      const summaryNote = document.createElement('span');
      summaryNote.className = 'mapping-summary-text';
      summary.appendChild(summaryNote);

      const mappingEditToggleLabel = document.createElement('label');
      mappingEditToggleLabel.className =
        'screening-advanced-toggle mapping-edit-toggle-label';
      const mappingEditToggleInput = document.createElement('input');
      mappingEditToggleInput.type = 'checkbox';
      mappingEditToggleInput.className = 'mapping-edit-toggle-input';
      mappingEditToggleInput.setAttribute(
        'aria-label',
        'Enable editing for function mapping linkages'
      );
      const mappingEditToggleText = document.createElement('span');
      mappingEditToggleText.textContent = 'Edit Mappings';
      mappingEditToggleLabel.appendChild(mappingEditToggleInput);
      mappingEditToggleLabel.appendChild(mappingEditToggleText);
      summary.appendChild(mappingEditToggleLabel);

      const mappingWeightField = document.createElement('label');
      mappingWeightField.className = 'mapping-weight-field';
      mappingWeightField.hidden = true;
      const mappingWeightLabel = document.createElement('span');
      mappingWeightLabel.textContent = 'Indirect Mapping Weight';
      const mappingWeightInput = document.createElement('input');
      mappingWeightInput.type = 'number';
      mappingWeightInput.className = 'mapping-weight-input';
      mappingWeightInput.min = '0';
      mappingWeightInput.max = '1';
      mappingWeightInput.step = '0.01';
      mappingWeightInput.value = formatMappingWeight(widgetState.indirectMappingWeight);
      mappingWeightInput.setAttribute('aria-label', 'Indirect Mapping Weight');
      mappingWeightField.appendChild(mappingWeightLabel);
      mappingWeightField.appendChild(mappingWeightInput);
      summary.appendChild(mappingWeightField);

      const updateMappingSummaryNote = () => {
        summaryNote.textContent =
          `Direct mapping weight = 1.0, Indirect mapping weight = ${formatMappingWeight(widgetState.indirectMappingWeight)}`;
      };

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
        summaryTable.querySelectorAll('tr[data-rollup-row="true"]').forEach((row) => {
          row.hidden = !showRollup;
        });
      };

      const syncMappingEditorMode = () => {
        const enableEditing = Boolean(widgetState.enableMappingEditing);
        mappingEditToggleInput.checked = enableEditing;
        table.classList.toggle('mapping-edit-mode', enableEditing);
        mappingWeightField.hidden = !enableEditing;
        inputs.forEach(({ mappings }) => {
          if (!mappings) {
            return;
          }
          mappingDimensions.forEach(({ key }) => {
            const editor = mappings[key];
            if (!editor) {
              return;
            }
            editor.valueEl.hidden = enableEditing;
            editor.select.hidden = !enableEditing;
            editor.select.disabled = !enableEditing;
            if (editor.select.value !== editor.code) {
              editor.select.value = editor.code;
            }
          });
        });
      };

      const applyIndirectMappingWeight = (rawValue) => {
        const parsedValue =
          typeof rawValue === 'string' && rawValue.trim() === ''
            ? Number.NaN
            : Number(rawValue);
        const nextWeight = clampIndirectMappingWeight(
          parsedValue,
          widgetState.indirectMappingWeight
        );
        widgetState.indirectMappingWeight = nextWeight;
        mappingWeightInput.value = formatMappingWeight(nextWeight);
        updateMappingSummaryNote();
        updateScores();
      };

      const syncViewState = () => {
        const showMappings = Boolean(widgetState.showFunctionMappings);
        mappingToggle.checked = showMappings;
        rollupToggle.checked = Boolean(widgetState.showRollupComputations);
        sliderLabelsToggle.checked = Boolean(widgetState.showFunctionScoreCueLabels);
        table.classList.toggle('show-function-mappings', showMappings);
        table.classList.toggle(
          'show-function-score-cue-labels',
          widgetState.showFunctionScoreCueLabels
        );
        summaryTable.classList.toggle('show-function-mappings', showMappings);
        summaryTable.hidden = showMappings;
        chartsShell.classList.toggle(
          'show-function-score-cue-labels',
          widgetState.showFunctionScoreCueLabels
        );
        updateMappingSummaryNote();
        mappingWeightInput.value = formatMappingWeight(widgetState.indirectMappingWeight);
        syncMappingEditorMode();
        return showMappings;
      };

      const updateToggleView = ({
        refreshHeader = false,
        refreshSummary = false,
        refreshRollupRows = false,
        refreshScores = false
      } = {}) => {
        const showMappings = syncViewState();
        if (refreshHeader) {
          renderHeader(showMappings);
        }
        if (refreshSummary) {
          buildSummary(showMappings);
          refreshScores = true;
        }
        if (refreshRollupRows) {
          applyRollupRowsVisibility();
        }
        if (refreshScores) {
          updateScores();
        }
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

        const indirectWeight = clampIndirectMappingWeight(widgetState.indirectMappingWeight, 0.1);

        inputs.forEach(({ input, mappings }, functionId) => {
          const score = parseFloat(input.value) || 0;
          functionScores.set(functionId, score);
          const pCode = normalizeMappingCode(mappings?.physical?.code);
          const cCode = normalizeMappingCode(mappings?.chemical?.code);
          const bCode = normalizeMappingCode(mappings?.biological?.code);
          const pWeight = weightFromCode(pCode, indirectWeight);
          const cWeight = weightFromCode(cCode, indirectWeight);
          const bWeight = weightFromCode(bCode, indirectWeight);
          pSum += pWeight;
          pWeighted += score * pWeight;
          cSum += cWeight;
          cWeighted += score * cWeight;
          bSum += bWeight;
          bWeighted += score * bWeight;
          if (pCode === 'D') {
            pDirect += 1;
          } else if (pCode === 'i') {
            pIndirect += 1;
          }
          if (cCode === 'D') {
            cDirect += 1;
          } else if (cCode === 'i') {
            cIndirect += 1;
          }
          if (bCode === 'D') {
            bDirect += 1;
          } else if (bCode === 'i') {
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
        if (summaryRows.ecosystem) {
          summaryRows.ecosystem.textContent = formatNumber(ecosystemCondition);
        }

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
        updateToggleView({
          refreshHeader: true,
          refreshSummary: true
        });
      });

      rollupToggle.addEventListener('change', () => {
        widgetState.showRollupComputations = rollupToggle.checked;
        updateToggleView({ refreshRollupRows: true });
      });

      sliderLabelsToggle.addEventListener('change', () => {
        widgetState.showFunctionScoreCueLabels = sliderLabelsToggle.checked;
        updateToggleView();
      });

      mappingEditToggleInput.addEventListener('change', () => {
        widgetState.enableMappingEditing = mappingEditToggleInput.checked;
        syncMappingEditorMode();
      });

      mappingWeightInput.addEventListener('change', () => {
        applyIndirectMappingWeight(mappingWeightInput.value);
      });

      mappingWeightInput.addEventListener('blur', () => {
        applyIndirectMappingWeight(mappingWeightInput.value);
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

      updateToggleView({
        refreshHeader: true,
        refreshSummary: true,
        refreshRollupRows: true
      });
      ui.appendChild(scoringControls);
      ui.appendChild(table);
      ui.appendChild(summaryTable);
      ui.appendChild(summary);
      ui.appendChild(chartsShell);
    } catch (error) {
      if (ui) {
        ui.textContent = 'Scoring sandbox failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
