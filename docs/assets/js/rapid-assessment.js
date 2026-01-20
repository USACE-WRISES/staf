(() => {
  const container = document.querySelector('.rapid-assessment');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const indicatorsUrl = `${baseUrl}/assets/data/rapid-indicators.tsv`;
  const criteriaUrl = `${baseUrl}/assets/data/rapid-criteria.tsv`;
  const mappingUrl = `${baseUrl}/assets/data/rapid-cwa-mapping.json`;
  const fallback = container.querySelector('.rapid-assessment-fallback');
  const ui = container.querySelector('.rapid-assessment-ui');

  const indicatorScoreOptions = [
    { value: 'SA', label: 'SA', title: 'Strongly Agree' },
    { value: 'A', label: 'A', title: 'Agree' },
    { value: 'N', label: 'N', title: 'Neutral' },
    { value: 'D', label: 'D', title: 'Disagree' },
    { value: 'SD', label: 'SD', title: 'Strongly Disagree' },
    { value: 'NA', label: 'NA', title: 'Not Applicable' },
  ];

  const defaultIndicatorScore = 'N';
  const defaultFunctionScore = 10;

  const normalize = (value) =>
    (value || '')
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();

  const parseTSV = (text) => {
    const lines = text.trim().split(/\r?\n/).filter(Boolean);
    if (!lines.length) {
      return [];
    }
    const header = lines[0]
      .replace(/^\ufeff/, '')
      .split('\t')
      .map((value) => value.trim());
    return lines.slice(1).map((line) => {
      const cells = line.split('\t');
      const row = {};
      header.forEach((key, index) => {
        row[key] = cells[index] ? cells[index].trim() : '';
      });
      return row;
    });
  };

  const slugCategory = (category) =>
    `category-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  const disciplineMap = {
    'catchment hydrology': 'Hydrology',
    'surface water storage': 'Hydrology',
    'reach inflow': 'Hydrology',
    'streamflow regime': 'Hydrology',
    'low flow and baseflow dynamics': 'Hydraulics',
    'high flow dynamics': 'Hydraulics',
    'floodplain connectivity': 'Hydraulics',
    'hyporheic connectivity': 'Hydraulics',
    'channel evolution': 'Geomorphology',
    'channel and floodplain dynamics': 'Geomorphology',
    'sediment continuity': 'Geomorphology',
    'bed composition and large wood': 'Geomorphology',
    'light and thermal regime': 'Physicochemistry',
    'carbon processing': 'Physicochemistry',
    'nutrient cycling': 'Physicochemistry',
    'water and soil quality': 'Physicochemistry',
    'habitat provision': 'Biology',
    'population support': 'Biology',
    'community dynamics': 'Biology',
    'watershed connectivity': 'Biology',
  };

  const init = async () => {
    try {
      const [indicatorText, criteriaText, mappingList] = await Promise.all([
        fetch(indicatorsUrl).then((r) => r.text()),
        fetch(criteriaUrl).then((r) => r.text()),
        fetch(mappingUrl).then((r) => r.json()),
      ]);

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

      const indicatorsRaw = parseTSV(indicatorText);
      const criteriaRaw = parseTSV(criteriaText);
      const mappingByFunction = mappingList.reduce((acc, item) => {
        acc[normalize(item.function)] = item;
        return acc;
      }, {});

      const criteriaMap = {};
      criteriaRaw.forEach((row) => {
        const key = normalize(row.Indicator || row.Metric || '');
        if (!key) {
          return;
        }
        if (!criteriaMap[key]) {
          criteriaMap[key] = {};
        }
        const sentiment = row.Sentiment || row.sentiment || '';
        if (!sentiment) {
          return;
        }
        criteriaMap[key][sentiment] = {
          observation: row.Observation || row['Example Observation'] || '',
          criteria: row.Criteria || row['Example Criteria'] || '',
        };
      });

      const indicators = indicatorsRaw.map((row, index) => {
        const functionName = row.Functions || row.Function || '';
        const functionKey = normalize(functionName);
        return {
          id: `indicator-${index + 1}`,
          discipline: disciplineMap[functionKey] || 'Hydrology',
          functionName,
          functionKey,
          functionStatement: row['Function statement'] || '',
          indicator: row.Indicators || row.Indicator || '',
          indicatorStatement: row['Indicator statements'] || row['Indicator statement'] || '',
          context: row.Context || '',
          method: row.Method || '',
          howToMeasure: row['How to measure'] || '',
          criteriaKey: normalize(row['Criteria key'] || row.CriteriaKey || row.Indicators || ''),
        };
      });

      const tabsHost = ui.querySelector('.rapid-tabs');
      const nameInput = ui.querySelector('.settings-name');
      const applicabilityInput = ui.querySelector('.settings-applicability');
      const notesInput = ui.querySelector('.settings-notes-input');
      const controlsHost = ui.querySelector('.rapid-controls-host');
      const tableHost = ui.querySelector('.rapid-table-wrap');

      if (tabsHost) {
        const tab = document.createElement('button');
        tab.type = 'button';
        tab.className = 'assessment-tab is-active';
        tab.textContent = 'Stream Functions Assessment and Rapid Index (SFARI)';
        tab.setAttribute('aria-selected', 'true');
        tabsHost.appendChild(tab);
      }

      if (nameInput) {
        nameInput.value = 'Stream Functions Assessment and Rapid Index (SFARI)';
      }
      if (applicabilityInput) {
        applicabilityInput.value = 'Nationwide, wide-able streams';
      }

      const indicatorScores = new Map();
      const functionScores = new Map();

      indicators.forEach((item) => {
        indicatorScores.set(item.id, defaultIndicatorScore);
        if (!functionScores.has(item.functionName)) {
          functionScores.set(item.functionName, defaultFunctionScore);
        }
      });

      const controls = document.createElement('div');
      controls.className = 'screening-controls';

      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search indicators';
      search.setAttribute('aria-label', 'Search indicators');

      const disciplineFilter = document.createElement('select');
      disciplineFilter.setAttribute('aria-label', 'Filter by discipline');
      const disciplineValues = Array.from(
        new Set(indicators.map((item) => item.discipline))
      );
      const disciplineAll = document.createElement('option');
      disciplineAll.value = 'all';
      disciplineAll.textContent = 'All disciplines';
      disciplineFilter.appendChild(disciplineAll);
      disciplineValues.forEach((value) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        disciplineFilter.appendChild(option);
      });

      controls.appendChild(search);
      controls.appendChild(disciplineFilter);

      if (controlsHost) {
        controlsHost.innerHTML = '';
        controlsHost.appendChild(controls);
      }

      const table = document.createElement('table');
      table.className = 'screening-table rapid-table';
      const thead = document.createElement('thead');
      thead.innerHTML =
        '<tr>' +
        '<th class="col-discipline">Discipline</th>' +
        '<th class="col-function">Function</th>' +
        '<th class="col-indicator">Indicator</th>' +
        '<th class="col-indicator-score">Indicator<br>score</th>' +
        '<th class="col-physical">Physical</th>' +
        '<th class="col-chemical">Chemical</th>' +
        '<th class="col-biological">Biological</th>' +
        '</tr>';
      const tbody = document.createElement('tbody');
      const tfoot = document.createElement('tfoot');
      table.appendChild(thead);
      table.appendChild(tbody);
      table.appendChild(tfoot);

      if (tableHost) {
        tableHost.innerHTML = '';
        tableHost.appendChild(table);
      }

      const summaryCells = {
        physical: [],
        chemical: [],
        biological: [],
        ecosystem: null,
      };

      const buildSummary = () => {
        const labelItems = [
          'Direct Effect',
          'Indirect Effect',
          'Weighted Score Total',
          'Max Weighted Score Total',
          'Condition Sub-Index',
          'Ecosystem Condition Index',
        ];

        tfoot.innerHTML = '';
        summaryCells.physical = [];
        summaryCells.chemical = [];
        summaryCells.biological = [];
        summaryCells.ecosystem = null;

        labelItems.forEach((label) => {
          const row = document.createElement('tr');
          const labelCell = document.createElement('td');
          labelCell.colSpan = 4;
          labelCell.className = 'summary-labels';
          labelCell.textContent = label;
          row.appendChild(labelCell);

          if (label === 'Ecosystem Condition Index') {
            const merged = document.createElement('td');
            merged.colSpan = 3;
            merged.className = 'summary-values summary-merged';
            const value = document.createElement('div');
            value.textContent = '-';
            merged.appendChild(value);
            summaryCells.ecosystem = value;
            row.appendChild(merged);
          } else {
            ['physical', 'chemical', 'biological'].forEach((key) => {
              const cell = document.createElement('td');
              cell.className = 'summary-values';
              const value = document.createElement('div');
              value.textContent = '-';
              summaryCells[key].push(value);
              cell.appendChild(value);
              row.appendChild(cell);
            });
          }
          tfoot.appendChild(row);
        });
      };

      const updateScores = () => {
        const outcomeTotals = {
          physical: { weighted: 0, max: 0, direct: 0, indirect: 0 },
          chemical: { weighted: 0, max: 0, direct: 0, indirect: 0 },
          biological: { weighted: 0, max: 0, direct: 0, indirect: 0 },
        };

        functionScores.forEach((score, functionName) => {
          const mapping =
            mappingByFunction[normalize(functionName)] || {
              physical: '-',
              chemical: '-',
              biological: '-',
            };

          const applyWeight = (key, code) => {
            let weight = 0;
            if (code === 'D') {
              weight = 1.0;
              outcomeTotals[key].direct += 1;
            } else if (code === 'i') {
              weight = 0.1;
              outcomeTotals[key].indirect += 1;
            }
            outcomeTotals[key].weighted += score * weight;
            outcomeTotals[key].max += 15 * weight;
          };

          applyWeight('physical', mapping.physical);
          applyWeight('chemical', mapping.chemical);
          applyWeight('biological', mapping.biological);
        });

        const formatNumber = (value) => value.toFixed(2);
        const formatCount = (value) => String(value);

        const fillOutcome = (key, offset) => {
          const total = outcomeTotals[key];
          const subIndex = total.max > 0 ? total.weighted / total.max : 0;
          summaryCells[key][offset].textContent = formatCount(total.direct);
          summaryCells[key][offset + 1].textContent = formatCount(total.indirect);
          summaryCells[key][offset + 2].textContent = formatNumber(total.weighted);
          summaryCells[key][offset + 3].textContent = formatNumber(total.max);
          summaryCells[key][offset + 4].textContent = formatNumber(subIndex);
          return subIndex;
        };

        const physicalIndex = fillOutcome('physical', 0);
        const chemicalIndex = fillOutcome('chemical', 0);
        const biologicalIndex = fillOutcome('biological', 0);
        const ecosystemIndex = (physicalIndex + chemicalIndex + biologicalIndex) / 3;
        if (summaryCells.ecosystem) {
          summaryCells.ecosystem.textContent = formatNumber(ecosystemIndex);
        }
      };

      const updateRowSpans = () => {
        const rows = Array.from(tbody.querySelectorAll('tr'));
        const updateSpan = (selector) => {
          const anchors = [];
          rows.forEach((row, rowIndex) => {
            const cell = row.querySelector(selector);
            if (cell) {
              anchors.push({ cell, rowIndex });
            }
          });
          anchors.forEach((anchor, idx) => {
            const start = anchor.rowIndex;
            const end = idx + 1 < anchors.length ? anchors[idx + 1].rowIndex : rows.length;
            let count = 0;
            for (let i = start; i < end; i += 1) {
              if (rows[i].hidden || rows[i].style.display === 'none') {
                continue;
              }
              count += 1;
            }
            anchor.cell.rowSpan = Math.max(count, 1);
          });
        };

        updateSpan('td.discipline-cell');
        updateSpan('td.function-cell');
        updateSpan('td.physical-cell');
        updateSpan('td.chemical-cell');
        updateSpan('td.biological-cell');
      };

      const buildCriteriaBlock = (criteria) => {
        const block = document.createElement('div');
        block.className = 'criteria-grid';

        const sentiments = [
          { key: '++', label: '++' },
          { key: '+', label: '+' },
          { key: '-', label: '-' },
          { key: '--', label: '--' },
        ];

        sentiments.forEach((sentiment) => {
          const item = document.createElement('div');
          const title = document.createElement('strong');
          title.textContent = sentiment.label;
          item.appendChild(title);
          const observation = document.createElement('div');
          observation.innerHTML = `<span class="criteria-label">Observation:</span> ${
            criteria?.[sentiment.key]?.observation || '-'
          }`;
          const criteriaText = document.createElement('div');
          criteriaText.innerHTML = `<span class="criteria-label">Criteria:</span> ${
            criteria?.[sentiment.key]?.criteria || '-'
          }`;
          item.appendChild(observation);
          item.appendChild(criteriaText);
          block.appendChild(item);
        });

        return block;
      };

      const renderTable = () => {
        tbody.innerHTML = '';
        buildSummary();

        const term = search.value.trim().toLowerCase();
        const disciplineValue = disciplineFilter.value;

        const visibleIndicators = indicators.filter((item) => {
          const matchesDiscipline =
            disciplineValue === 'all' || item.discipline === disciplineValue;
          const haystack = [
            item.functionName,
            item.indicator,
            item.indicatorStatement,
          ]
            .join(' ')
            .toLowerCase();
          const matchesSearch = !term || haystack.includes(term);
          return matchesDiscipline && matchesSearch;
        });

        const disciplineStarts = new Map();
        const functionStarts = new Map();

        for (let i = 0; i < visibleIndicators.length; ) {
          const discipline = visibleIndicators[i].discipline;
          let j = i + 1;
          while (j < visibleIndicators.length && visibleIndicators[j].discipline === discipline) {
            j += 1;
          }
          disciplineStarts.set(i, j - i);
          i = j;
        }

        for (let i = 0; i < visibleIndicators.length; ) {
          const fn = visibleIndicators[i].functionName;
          let j = i + 1;
          while (j < visibleIndicators.length && visibleIndicators[j].functionName === fn) {
            j += 1;
          }
          functionStarts.set(i, j - i);
          i = j;
        }

        if (!visibleIndicators.length) {
          const emptyRow = document.createElement('tr');
          const emptyCell = document.createElement('td');
          emptyCell.colSpan = 7;
          emptyCell.className = 'empty-cell';
          emptyCell.textContent = 'No indicators match the current filters.';
          emptyRow.appendChild(emptyCell);
          tbody.appendChild(emptyRow);
          updateScores();
          return;
        }

        visibleIndicators.forEach((item, index) => {
          const row = document.createElement('tr');
          row.classList.add(slugCategory(item.discipline));

          const disciplineSpan = disciplineStarts.get(index);
          if (disciplineSpan) {
            const disciplineCell = document.createElement('td');
            disciplineCell.textContent = item.discipline;
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = disciplineSpan;
            row.appendChild(disciplineCell);
          }

          const functionSpan = functionStarts.get(index);
          if (functionSpan) {
            const functionCell = document.createElement('td');
            functionCell.className = 'function-cell col-function';
            functionCell.rowSpan = functionSpan;
            const nameLine = document.createElement('div');
            nameLine.className = 'function-title';
            const nameText = document.createElement('span');
            nameText.textContent = item.functionName;
            nameLine.appendChild(nameText);
            const functionToggle = document.createElement('button');
            functionToggle.type = 'button';
            functionToggle.className = 'criteria-toggle function-toggle';
            functionToggle.innerHTML = '&#9662;';
            functionToggle.setAttribute('aria-expanded', 'false');
            functionToggle.setAttribute('aria-label', 'Toggle function statement');
            nameLine.appendChild(functionToggle);
            functionCell.appendChild(nameLine);

            const statementLine = document.createElement('div');
            statementLine.className = 'function-statement';
            statementLine.textContent = item.functionStatement || '';
            statementLine.hidden = true;
            functionCell.appendChild(statementLine);

            const scoreWrap = document.createElement('div');
            scoreWrap.className = 'score-input function-score-inline';
            const range = document.createElement('input');
            range.type = 'range';
            range.min = '0';
            range.max = '15';
            range.step = '1';
            const currentScore = functionScores.get(item.functionName) ?? defaultFunctionScore;
            range.value = String(currentScore);
            const valueLabel = document.createElement('span');
            valueLabel.className = 'score-value';
            valueLabel.textContent = String(currentScore);
            range.addEventListener('input', () => {
              const nextValue = Number(range.value);
              functionScores.set(item.functionName, nextValue);
              valueLabel.textContent = String(nextValue);
              updateScores();
            });
            scoreWrap.appendChild(range);
            scoreWrap.appendChild(valueLabel);
            functionCell.appendChild(scoreWrap);
            row.appendChild(functionCell);

            functionToggle.addEventListener('click', () => {
              if (!statementLine.textContent) {
                return;
              }
              const isOpen = !statementLine.hidden;
              statementLine.hidden = isOpen;
              functionToggle.setAttribute('aria-expanded', String(!isOpen));
            });
          }

          const indicatorCell = document.createElement('td');
          indicatorCell.className = 'col-indicator indicator-cell';
          const indicatorText = document.createElement('span');
          indicatorText.textContent = item.indicator;
          indicatorCell.appendChild(indicatorText);

          const indicatorScoreCell = document.createElement('td');
          indicatorScoreCell.className = 'col-indicator-score';
          const indicatorSelect = document.createElement('select');
          indicatorScoreOptions.forEach((option) => {
            const opt = document.createElement('option');
            opt.value = option.value;
            opt.textContent = option.label;
            if (option.title) {
              opt.title = option.title;
            }
            indicatorSelect.appendChild(opt);
          });
          indicatorSelect.value = indicatorScores.get(item.id) || defaultIndicatorScore;
          indicatorSelect.setAttribute(
            'title',
            indicatorScoreOptions.find((opt) => opt.value === indicatorSelect.value)?.title || ''
          );
          indicatorSelect.addEventListener('change', () => {
            indicatorScores.set(item.id, indicatorSelect.value);
          });
          indicatorScoreCell.appendChild(indicatorSelect);

          const criteriaBtn = document.createElement('button');
          criteriaBtn.type = 'button';
          criteriaBtn.className = 'criteria-toggle';
          criteriaBtn.innerHTML = '&#9662;';
          const detailsId = `rapid-criteria-${item.id}`;
          criteriaBtn.setAttribute('aria-expanded', 'false');
          criteriaBtn.setAttribute('aria-controls', detailsId);
          criteriaBtn.setAttribute('aria-label', 'Toggle criteria details');
          indicatorCell.appendChild(criteriaBtn);

          row.appendChild(indicatorCell);
          row.appendChild(indicatorScoreCell);
          const mapping =
            mappingByFunction[item.functionKey] || {
              physical: '-',
              chemical: '-',
              biological: '-',
            };
          if (functionSpan) {
            const physicalCell = document.createElement('td');
            const chemicalCell = document.createElement('td');
            const biologicalCell = document.createElement('td');
            physicalCell.className = 'weight-cell col-physical physical-cell';
            chemicalCell.className = 'weight-cell col-chemical chemical-cell';
            biologicalCell.className = 'weight-cell col-biological biological-cell';
            physicalCell.textContent = mapping.physical || '-';
            chemicalCell.textContent = mapping.chemical || '-';
            biologicalCell.textContent = mapping.biological || '-';
            physicalCell.rowSpan = functionSpan;
            chemicalCell.rowSpan = functionSpan;
            biologicalCell.rowSpan = functionSpan;
            row.appendChild(physicalCell);
            row.appendChild(chemicalCell);
            row.appendChild(biologicalCell);
          }

          const detailsRow = document.createElement('tr');
          detailsRow.id = detailsId;
          detailsRow.className = 'criteria-row';
          detailsRow.hidden = true;
          const detailsCell = document.createElement('td');
          detailsCell.colSpan = 2;
          const details = document.createElement('div');
          details.className = 'criteria-details';
          const criteriaSet = criteriaMap[item.criteriaKey] || {};
          details.appendChild(
            (() => {
              const block = document.createElement('div');
              block.className = 'criteria-block';
              block.innerHTML = `<strong>Indicator statement</strong><div>${
                item.indicatorStatement || '-'
              }</div>`;
              return block;
            })()
          );
          details.appendChild(
            (() => {
              const block = document.createElement('div');
              block.className = 'criteria-block';
              block.innerHTML = `<strong>Context/Method</strong><div>Context: ${
                item.context || '-'
              }</div><div>Method: ${item.method || '-'}</div>`;
              return block;
            })()
          );
          details.appendChild(
            (() => {
              const block = document.createElement('div');
              block.className = 'criteria-block';
              block.innerHTML = `<strong>How to measure</strong><div>${
                item.howToMeasure || '-'
              }</div>`;
              return block;
            })()
          );
          details.appendChild(buildCriteriaBlock(criteriaSet));
          detailsCell.appendChild(details);
          detailsRow.appendChild(detailsCell);

          criteriaBtn.addEventListener('click', () => {
            const isOpen = !detailsRow.hidden;
            detailsRow.hidden = isOpen;
            criteriaBtn.setAttribute('aria-expanded', String(!isOpen));
            updateRowSpans();
          });

          tbody.appendChild(row);
          tbody.appendChild(detailsRow);
        });

        updateScores();
        updateRowSpans();
      };

      search.addEventListener('input', renderTable);
      disciplineFilter.addEventListener('change', renderTable);

      renderTable();
    } catch (error) {
      if (ui) {
        ui.textContent = 'Rapid assessment widget failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
