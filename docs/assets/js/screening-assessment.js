(() => {
  const container = document.querySelector('.screening-assessment');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const dataUrl = `${baseUrl}/assets/data/screening-metrics.tsv`;
  const functionsUrl = `${baseUrl}/assets/data/functions.json`;
  const mappingUrl = `${baseUrl}/assets/data/cwa-mapping.json`;
  const fallback = container.querySelector('.screening-assessment-fallback');
  const ui = container.querySelector('.screening-assessment-ui');

  const ratingOptions = [
    { label: 'Optimal', score: 15 },
    { label: 'Suboptimal', score: 10 },
    { label: 'Marginal', score: 5 },
    { label: 'Poor', score: 0 },
  ];
  const defaultRating = 'Optimal';
  const storageKey = 'staf_screening_assessments_v1';
  const predefinedName = 'Stream Condition Screening (SCS)';
  const legacyPredefinedName = 'Predefined Screening Assessment';

  const normalizeText = (value) =>
    value
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '');

  const isPredefinedFlag = (value) => {
    if (!value) {
      return false;
    }
    const normalized = value.trim().toLowerCase();
    return normalized === 'yes' || normalized === 'y' || normalized === 'true' || normalized === '1';
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

  const slugCategory = (category) =>
    `category-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  const generateId = () => {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  };

  const parseTSV = (text) => {
    const lines = text.trim().split(/\r?\n/).filter(Boolean);
    if (lines.length === 0) {
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

  const buildRatings = (metricIds, existingRatings) => {
    const ratings = {};
    metricIds.forEach((id) => {
      const current = existingRatings ? existingRatings[id] : null;
      const isValid = ratingOptions.some((opt) => opt.label === current);
      ratings[id] = isValid ? current : defaultRating;
    });
    return ratings;
  };

  const init = async () => {
    try {
      const [metricsText, functionsList, mappingList] = await Promise.all([
        fetch(dataUrl).then((r) => r.text()),
        fetch(functionsUrl).then((r) => r.json()),
        fetch(mappingUrl).then((r) => r.json()),
      ]);

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

      const metricsRaw = parseTSV(metricsText);
      const functionByName = new Map(
        functionsList.map((fn) => [normalizeText(fn.name), fn])
      );
      const mappingById = mappingList.reduce((acc, item) => {
        acc[item.id] = item;
        return acc;
      }, {});

      const metrics = metricsRaw.map((row, index) => {
        const functionKey = normalizeText(row.Function || row.function || '');
        const functionMatch = functionByName.get(functionKey);
        return {
          id: `metric-${index + 1}`,
          discipline: row.Discipline || row.discipline || '',
          functionName: row.Function || row.function || '',
          functionId: functionMatch ? functionMatch.id : null,
          metric: row.Metric || row.metric || '',
          isPredefined: isPredefinedFlag(row['Predefined SCS'] || row.predefined_scs || ''),
          context: row.Context || row.context || '',
          method: row.Method || row.method || '',
          howToMeasure: row['How to measure'] || row.how_to_measure || '',
          criteria: {
            optimal: row.Optimal || row.optimal || '',
            suboptimal: row.Suboptimal || row.suboptimal || '',
            marginal: row.Marginal || row.marginal || '',
            poor: row.Poor || row.poor || '',
          },
          references: row.References || row.references || '',
        };
      });

      const metricIdSet = new Set(metrics.map((metric) => metric.id));

      const starMetricIdsByFunction = new Map();
      metrics.forEach((metric) => {
        if (!metric.functionId) {
          return;
        }
        if (!metric.isPredefined) {
          return;
        }
        if (!starMetricIdsByFunction.has(metric.functionId)) {
          starMetricIdsByFunction.set(metric.functionId, metric.id);
        }
      });
      const predefinedMetricIds = Array.from(starMetricIdsByFunction.values());

      const createScenario = (type, name, metricIds) => {
        const ids = Array.from(new Set(metricIds || []));
        return {
          id: generateId(),
          type,
          name,
          applicability:
            type === 'predefined' ? 'Nationwide, wadeable streams' : '',
          notes: '',
          metricIds: ids,
          ratings: buildRatings(ids),
        };
      };

      const duplicateScenario = (source) => {
        if (!source) {
          return;
        }
        const baseName =
          source.name ||
          (source.type === 'predefined' ? predefinedName : 'Custom Assessment');
        const metricIds = Array.isArray(source.metricIds) ? source.metricIds : [];
        const newScenario = {
          id: generateId(),
          type: 'custom',
          name: `${baseName} Copy`,
          applicability: source.applicability || '',
          notes: source.notes || '',
          metricIds: Array.from(metricIds),
          ratings: buildRatings(metricIds, source.ratings),
        };
        store.scenarios.push(newScenario);
        store.activeId = newScenario.id;
        store.save();
        applyScenario(newScenario);
        renderTabs();
      };

      const normalizeScenario = (scenario) => {
        const type = scenario.type === 'custom' ? 'custom' : 'predefined';
        const metricIds =
          type === 'predefined'
            ? predefinedMetricIds
            : Array.isArray(scenario.metricIds)
            ? scenario.metricIds.filter((id) => metricIdSet.has(id))
            : predefinedMetricIds;
        const ids = Array.from(new Set(metricIds));
        return {
          id: scenario.id || generateId(),
          type,
          name:
            type === 'predefined'
              ? scenario.name && scenario.name !== legacyPredefinedName
                ? scenario.name
                : predefinedName
              : scenario.name || 'Custom Assessment',
          applicability:
            type === 'predefined'
              ? scenario.applicability || 'Nationwide, wadeable streams'
              : scenario.applicability || '',
          notes: scenario.notes || '',
          metricIds: ids,
          ratings: buildRatings(ids, scenario.ratings),
        };
      };

      const store = {
        scenarios: [],
        activeId: null,
        load() {
          return false;
        },
        save() {},
        active() {
          return this.scenarios.find((s) => s.id === this.activeId) || null;
        },
      };

      if (!store.load()) {
        store.scenarios = [
          normalizeScenario({
            type: 'predefined',
            metricIds: predefinedMetricIds,
          }),
        ];
        store.activeId = store.scenarios[0].id;
      }

      const tabsList = ui.querySelector('.screening-tabs');
      const addTabBtn = ui.querySelector('.screening-tab-add');
      const duplicateBtn = ui.querySelector('.screening-duplicate');
      const deleteBtn = ui.querySelector('.screening-delete');
      const nameInput = ui.querySelector('.settings-name');
      const applicabilityInput = ui.querySelector('.settings-applicability');
      const notesInput = ui.querySelector('.settings-notes-input');
      const controlsHost = ui.querySelector('.screening-controls-host');
      const tableHost = ui.querySelector('.screening-table-wrap');

      let selectedMetricIds = new Set();
      let metricRatings = new Map();
      let activeScenario = null;

      const controls = document.createElement('div');
      controls.className = 'screening-controls';

      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search metrics';
      search.setAttribute('aria-label', 'Search metrics');

      const disciplineFilter = document.createElement('select');
      disciplineFilter.setAttribute('aria-label', 'Filter by discipline');
      const disciplineValues = Array.from(
        new Set(metrics.map((metric) => metric.discipline))
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

      const addSelect = document.createElement('select');
      addSelect.className = 'metric-add-select';
      const addPlaceholder = document.createElement('option');
      addPlaceholder.value = '';
      addPlaceholder.textContent = 'Add metric...';
      addSelect.appendChild(addPlaceholder);

      const addButton = document.createElement('button');
      addButton.type = 'button';
      addButton.className = 'btn btn-small';
      addButton.textContent = 'Add';

      const resetButton = document.createElement('button');
      resetButton.type = 'button';
      resetButton.className = 'btn btn-small';
      resetButton.textContent = 'Reset metrics';

      controls.appendChild(search);
      controls.appendChild(disciplineFilter);
      controls.appendChild(addSelect);
      controls.appendChild(addButton);
      controls.appendChild(resetButton);

      const table = document.createElement('table');
      table.className = 'screening-table';
      const thead = document.createElement('thead');
      thead.innerHTML =
        '<tr>' +
        '<th class="col-discipline">Discipline</th>' +
        '<th class="col-function">Function</th>' +
        '<th class="col-metric">Metric</th>' +
        '<th class="col-metric-score">Metric<br>score</th>' +
        '<th class="col-function-score">Function score<br>(0-15)</th>' +
        '<th class="col-actions">Actions</th>' +
        '<th class="col-physical">Physical</th>' +
        '<th class="col-chemical">Chemical</th>' +
        '<th class="col-biological">Biological</th>' +
        '</tr>';
      const tbody = document.createElement('tbody');
      const tfoot = document.createElement('tfoot');
      table.appendChild(thead);
      table.appendChild(tbody);
      table.appendChild(tfoot);

      if (controlsHost) {
        controlsHost.innerHTML = '';
        controlsHost.appendChild(controls);
      }
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
          labelCell.colSpan = 6;
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

      const buildAddOptions = () => {
        addSelect.innerHTML = '';
        addSelect.appendChild(addPlaceholder);
        const disciplineValue = disciplineFilter.value;
        metrics
          .filter((metric) => !selectedMetricIds.has(metric.id))
          .filter(
            (metric) => disciplineValue === 'all' || metric.discipline === disciplineValue
          )
          .forEach((metric) => {
            const option = document.createElement('option');
            option.value = metric.id;
            option.textContent = `${metric.functionName}: ${metric.metric}`;
            addSelect.appendChild(option);
          });
      };

      const updateScores = (metricRows) => {
        const functionBuckets = new Map();
        metrics.forEach((metric) => {
          if (!selectedMetricIds.has(metric.id)) {
            return;
          }
          if (!metric.functionId) {
            return;
          }
          if (!functionBuckets.has(metric.functionId)) {
            functionBuckets.set(metric.functionId, []);
          }
          const rating = metricRatings.get(metric.id) || defaultRating;
          const ratingMatch = ratingOptions.find((opt) => opt.label === rating);
          functionBuckets.get(metric.functionId).push(ratingMatch ? ratingMatch.score : 0);
        });

        const functionScores = new Map();
        functionBuckets.forEach((scores, functionId) => {
          const average =
            scores.length > 0
              ? scores.reduce((sum, value) => sum + value, 0) / scores.length
              : 0;
          functionScores.set(functionId, average);
        });

        metricRows.forEach(({ metric, functionScoreCell }) => {
          const score = metric.functionId ? functionScores.get(metric.functionId) : null;
          functionScoreCell.textContent =
            score === null || score === undefined ? '-' : score.toFixed(0);
        });

        const outcomeTotals = {
          physical: { weighted: 0, max: 0, direct: 0, indirect: 0 },
          chemical: { weighted: 0, max: 0, direct: 0, indirect: 0 },
          biological: { weighted: 0, max: 0, direct: 0, indirect: 0 },
        };

        functionBuckets.forEach((scores, functionId) => {
          const functionScore = functionScores.get(functionId) ?? 0;
          const mapping = mappingById[functionId] || {
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
            outcomeTotals[key].weighted += functionScore * weight;
            outcomeTotals[key].max += 15 * weight;
          };

          applyWeight('physical', mapping.physical);
          applyWeight('chemical', mapping.chemical);
          applyWeight('biological', mapping.biological);
        });

        const formatNumber = (value) => value.toFixed(2);
        const formatCount = (value) => String(value);

        const fillOutcome = (key, indexOffset) => {
          const total = outcomeTotals[key];
          const subIndex = total.max > 0 ? total.weighted / total.max : 0;
          summaryCells[key][indexOffset].textContent = formatCount(total.direct);
          summaryCells[key][indexOffset + 1].textContent = formatCount(total.indirect);
          summaryCells[key][indexOffset + 2].textContent = formatNumber(total.weighted);
          summaryCells[key][indexOffset + 3].textContent = formatNumber(total.max);
          summaryCells[key][indexOffset + 4].textContent = formatNumber(subIndex);
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

      const renderTable = () => {
        tbody.innerHTML = '';
        buildSummary();

        const term = search.value.trim().toLowerCase();
        const disciplineValue = disciplineFilter.value;
        const isPredefined = activeScenario && activeScenario.type === 'predefined';

        const visibleMetrics = metrics.filter((metric) => {
          if (!selectedMetricIds.has(metric.id)) {
            return false;
          }
          const matchesDiscipline =
            disciplineValue === 'all' || metric.discipline === disciplineValue;
          const haystack = [
            metric.discipline,
            metric.functionName,
            metric.metric,
            metric.context,
            metric.method,
          ]
            .join(' ')
            .toLowerCase();
          const matchesSearch = !term || haystack.includes(term);
          return matchesDiscipline && matchesSearch;
        });

        const metricRows = [];

        const groupStarts = new Map();
        for (let i = 0; i < visibleMetrics.length; ) {
          const discipline = visibleMetrics[i].discipline;
          let j = i;
          while (j < visibleMetrics.length && visibleMetrics[j].discipline === discipline) {
            j += 1;
          }
          groupStarts.set(i, j - i);
          i = j;
        }

        if (visibleMetrics.length === 0) {
          const emptyRow = document.createElement('tr');
          const emptyCell = document.createElement('td');
          emptyCell.colSpan = 9;
          emptyCell.className = 'empty-cell';
          emptyCell.textContent = 'No metrics selected for this assessment.';
          emptyRow.appendChild(emptyCell);
          tbody.appendChild(emptyRow);
          updateScores(metricRows);
          return;
        }

        const updateDisciplineRowSpans = () => {
          const rows = Array.from(tbody.querySelectorAll('tr'));
          const anchors = [];
          rows.forEach((row, rowIndex) => {
            const cell = row.querySelector('td.discipline-cell');
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

        visibleMetrics.forEach((metric, index) => {
          const row = document.createElement('tr');
          row.classList.add(slugCategory(metric.discipline));

          const groupSize = groupStarts.get(index);
          if (groupSize) {
            const disciplineCell = document.createElement('td');
            disciplineCell.textContent = metric.discipline;
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = groupSize;
            row.appendChild(disciplineCell);
          }
          const functionCell = document.createElement('td');
          functionCell.className = 'col-function';
          functionCell.textContent = metric.functionName;
          const metricCell = document.createElement('td');
          metricCell.className = 'col-metric';
          metricCell.textContent = metric.metric;

          const scoreCell = document.createElement('td');
          scoreCell.className = 'col-metric-score';
          const scoreSelect = document.createElement('select');
          scoreSelect.className = 'metric-score-select';
          ratingOptions.forEach((option) => {
            const opt = document.createElement('option');
            opt.value = option.label;
            opt.textContent = option.label;
            scoreSelect.appendChild(opt);
          });
          scoreSelect.value = metricRatings.get(metric.id) || defaultRating;
          scoreCell.appendChild(scoreSelect);

          const functionScoreCell = document.createElement('td');
          functionScoreCell.className = 'function-score-cell col-function-score';
          functionScoreCell.textContent = '-';

          const criteriaBtn = document.createElement('button');
          criteriaBtn.type = 'button';
          criteriaBtn.className = 'btn btn-small';
          criteriaBtn.textContent = 'Criteria';
          const detailsId = `criteria-${metric.id}`;
          criteriaBtn.setAttribute('aria-expanded', 'false');
          criteriaBtn.setAttribute('aria-controls', detailsId);
          const actionsCell = document.createElement('td');
          actionsCell.className = 'col-actions';
          const actionsWrap = document.createElement('div');
          actionsWrap.className = 'action-buttons';
          actionsWrap.appendChild(criteriaBtn);

          const removeBtn = document.createElement('button');
          removeBtn.type = 'button';
          removeBtn.className = 'btn btn-small action-remove';
          removeBtn.textContent = 'X';
          removeBtn.setAttribute('aria-label', 'Remove metric');
          removeBtn.disabled = isPredefined;
          actionsWrap.appendChild(removeBtn);
          actionsCell.appendChild(actionsWrap);

          const mapping = metric.functionId ? mappingById[metric.functionId] : null;
          const physicalCell = document.createElement('td');
          const chemicalCell = document.createElement('td');
          const biologicalCell = document.createElement('td');
          physicalCell.className = 'weight-cell col-physical';
          chemicalCell.className = 'weight-cell col-chemical';
          biologicalCell.className = 'weight-cell col-biological';
          physicalCell.textContent = mapping ? weightLabelFromCode(mapping.physical) : '-';
          chemicalCell.textContent = mapping ? weightLabelFromCode(mapping.chemical) : '-';
          biologicalCell.textContent = mapping ? weightLabelFromCode(mapping.biological) : '-';

          row.appendChild(functionCell);
          row.appendChild(metricCell);
          row.appendChild(scoreCell);
          row.appendChild(functionScoreCell);
          row.appendChild(actionsCell);
          row.appendChild(physicalCell);
          row.appendChild(chemicalCell);
          row.appendChild(biologicalCell);

          const detailsRow = document.createElement('tr');
          detailsRow.id = detailsId;
          detailsRow.className = 'criteria-row';
          detailsRow.hidden = true;
          const detailsCell = document.createElement('td');
          detailsCell.colSpan = 8;
          const details = document.createElement('div');
          details.className = 'criteria-details';
          details.innerHTML =
            `<div class=\"criteria-block\"><strong>Context/Method</strong><div>Context: ${metric.context || '-'}</div><div>Method: ${metric.method || '-'}</div></div>` +
            `<div class=\"criteria-block\"><strong>How to measure</strong><div>${metric.howToMeasure}</div></div>` +
            `<div class=\"criteria-grid\">` +
            `<div><strong>Optimal</strong><div>${metric.criteria.optimal}</div></div>` +
            `<div><strong>Suboptimal</strong><div>${metric.criteria.suboptimal}</div></div>` +
            `<div><strong>Marginal</strong><div>${metric.criteria.marginal}</div></div>` +
            `<div><strong>Poor</strong><div>${metric.criteria.poor}</div></div>` +
            `</div>` +
            `<div class=\"criteria-block\"><strong>References</strong><div>${metric.references}</div></div>`;
          detailsCell.appendChild(details);
          detailsRow.appendChild(detailsCell);

          criteriaBtn.addEventListener('click', () => {
            const isOpen = !detailsRow.hidden;
            detailsRow.hidden = isOpen;
            criteriaBtn.setAttribute('aria-expanded', String(!isOpen));
            updateDisciplineRowSpans();
          });

          scoreSelect.addEventListener('change', () => {
            metricRatings.set(metric.id, scoreSelect.value);
            if (activeScenario) {
              activeScenario.ratings[metric.id] = scoreSelect.value;
              store.save();
            }
            updateScores(metricRows);
          });

          removeBtn.addEventListener('click', () => {
            if (!activeScenario || activeScenario.type === 'predefined') {
              return;
            }
            selectedMetricIds.delete(metric.id);
            metricRatings.delete(metric.id);
            activeScenario.metricIds = Array.from(selectedMetricIds);
            delete activeScenario.ratings[metric.id];
            store.save();
            buildAddOptions();
            renderTable();
          });

          tbody.appendChild(row);
          tbody.appendChild(detailsRow);
          metricRows.push({ metric, functionScoreCell });
        });

        updateScores(metricRows);
        updateDisciplineRowSpans();
      };

      const renderTabs = () => {
        if (!tabsList) {
          return;
        }
        tabsList.innerHTML = '';
        store.scenarios.forEach((scenario) => {
          const tab = document.createElement('button');
          tab.type = 'button';
          tab.className = 'assessment-tab';
          if (scenario.id === store.activeId) {
            tab.classList.add('is-active');
          }
          tab.setAttribute('role', 'tab');
          tab.setAttribute(
            'aria-selected',
            scenario.id === store.activeId ? 'true' : 'false'
          );
          tab.textContent = scenario.name;
          tab.addEventListener('click', () => {
            store.activeId = scenario.id;
            store.save();
            applyScenario(scenario);
            renderTabs();
          });
          tabsList.appendChild(tab);
        });
      };

      const applyScenario = (scenario) => {
        if (!scenario) {
          return;
        }
        activeScenario = scenario;
        const metricIds =
          scenario.type === 'predefined' ? predefinedMetricIds : scenario.metricIds;
        selectedMetricIds = new Set(metricIds);
        metricRatings = new Map(Object.entries(buildRatings(metricIds, scenario.ratings)));

        scenario.metricIds = Array.from(selectedMetricIds);
        scenario.ratings = buildRatings(scenario.metricIds, scenario.ratings);
        store.save();

        if (nameInput) {
          nameInput.value = scenario.name || '';
        }
        if (applicabilityInput) {
          applicabilityInput.value = scenario.applicability || '';
        }
        if (notesInput) {
          notesInput.value = scenario.notes || '';
        }

        const isPredefined = scenario.type === 'predefined';
        addSelect.disabled = isPredefined;
        addButton.disabled = isPredefined;
        resetButton.disabled = isPredefined;
        if (duplicateBtn) {
          duplicateBtn.disabled = false;
        }
        if (deleteBtn) {
          deleteBtn.disabled = isPredefined;
        }
        if (nameInput) {
          nameInput.readOnly = isPredefined;
        }
        if (applicabilityInput) {
          applicabilityInput.readOnly = isPredefined;
        }
        if (notesInput) {
          notesInput.readOnly = isPredefined;
        }

        buildAddOptions();
        renderTable();
      };

      if (nameInput) {
        nameInput.addEventListener('input', () => {
          if (!activeScenario) {
            return;
          }
          activeScenario.name = nameInput.value.trim() || activeScenario.name;
          store.save();
          renderTabs();
        });
      }

      if (applicabilityInput) {
        applicabilityInput.addEventListener('input', () => {
          if (!activeScenario) {
            return;
          }
          activeScenario.applicability = applicabilityInput.value;
          store.save();
        });
      }

      if (notesInput) {
        notesInput.addEventListener('input', () => {
          if (!activeScenario) {
            return;
          }
          activeScenario.notes = notesInput.value;
          store.save();
        });
      }

      search.addEventListener('input', renderTable);
      disciplineFilter.addEventListener('change', () => {
        renderTable();
        buildAddOptions();
      });

      addButton.addEventListener('click', () => {
        if (!activeScenario || activeScenario.type === 'predefined') {
          return;
        }
        const metricId = addSelect.value;
        if (!metricId) {
          return;
        }
        selectedMetricIds.add(metricId);
        metricRatings.set(metricId, metricRatings.get(metricId) || defaultRating);
        activeScenario.metricIds = Array.from(selectedMetricIds);
        activeScenario.ratings[metricId] = metricRatings.get(metricId);
        store.save();
        buildAddOptions();
        renderTable();
        addSelect.value = '';
      });

      resetButton.addEventListener('click', () => {
        if (!activeScenario || activeScenario.type === 'predefined') {
          return;
        }
        selectedMetricIds = new Set();
        metricRatings = new Map();
        activeScenario.metricIds = [];
        activeScenario.ratings = {};
        store.save();
        buildAddOptions();
        renderTable();
      });

      if (addTabBtn) {
        addTabBtn.addEventListener('click', () => {
          const customCount = store.scenarios.filter((s) => s.type === 'custom').length;
          const name = `Custom Assessment ${customCount + 1}`;
          const scenario = createScenario('custom', name, []);
          store.scenarios.push(scenario);
          store.activeId = scenario.id;
          store.save();
          applyScenario(scenario);
          renderTabs();
        });
      }

      if (duplicateBtn) {
        duplicateBtn.addEventListener('click', () => {
          duplicateScenario(activeScenario);
        });
      }

      if (deleteBtn) {
        deleteBtn.addEventListener('click', () => {
          if (!activeScenario || activeScenario.type === 'predefined') {
            return;
          }
          const currentIndex = store.scenarios.findIndex(
            (scenario) => scenario.id === activeScenario.id
          );
          store.scenarios = store.scenarios.filter((s) => s.id !== activeScenario.id);
          const nextIndex = currentIndex > 0 ? currentIndex - 1 : 0;
          store.activeId = store.scenarios[nextIndex].id;
          store.save();
          applyScenario(store.active());
          renderTabs();
        });
      }

      renderTabs();
      applyScenario(store.active());
    } catch (error) {
      if (ui) {
        ui.textContent = 'Screening assessment widget failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();
