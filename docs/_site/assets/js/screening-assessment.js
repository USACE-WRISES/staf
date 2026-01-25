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

  const fetchText = async (url) => {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Failed to load ${url} (${response.status})`);
    }
    return response.text();
  };

  const fetchJson = async (url) => {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Failed to load ${url} (${response.status})`);
    }
    return response.json();
  };

  const init = async () => {
    try {
      const [metricsText, functionsList, mappingList] = await Promise.all([
        fetchText(dataUrl),
        fetchJson(functionsUrl),
        fetchJson(mappingUrl),
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
      const functionById = new Map(functionsList.map((fn) => [fn.id, fn]));
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
          functionStatement:
            row['Function statement'] || row.function_statement || row.functionStatement || '',
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
      const libraryModal = ui.querySelector('.screening-library-modal');
      const libraryCloseBtn = ui.querySelector('.screening-library-close');
      const libraryBackdrop = ui.querySelector('.screening-library-backdrop');
      const libraryTableWrap = ui.querySelector('.screening-library-table-wrap');

      let selectedMetricIds = new Set();
      let metricRatings = new Map();
      let activeScenario = null;
      let librarySearchTerm = '';
      let libraryDiscipline = 'all';
      const libraryCollapse = {
        disciplines: new Map(),
        functions: new Map(),
      };

      const renderLibraryTable = () => {
        if (!libraryTableWrap) {
          return;
        }
        libraryTableWrap.innerHTML = '';
        const controls = document.createElement('div');
        controls.className = 'screening-library-controls';
        const searchInput = document.createElement('input');
        searchInput.type = 'search';
        searchInput.placeholder = 'Search metrics';
        searchInput.setAttribute('aria-label', 'Search metrics');
        searchInput.value = librarySearchTerm;
        const disciplineSelect = document.createElement('select');
        disciplineSelect.setAttribute('aria-label', 'Filter by discipline');
        const disciplineAllOption = document.createElement('option');
        disciplineAllOption.value = 'all';
        disciplineAllOption.textContent = 'All disciplines';
        disciplineSelect.appendChild(disciplineAllOption);
        Array.from(new Set(metrics.map((metric) => metric.discipline))).forEach((value) => {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = value;
          disciplineSelect.appendChild(option);
        });
        disciplineSelect.value = libraryDiscipline;
        searchInput.addEventListener('input', () => {
          librarySearchTerm = searchInput.value;
          renderLibraryTable();
        });
        disciplineSelect.addEventListener('change', () => {
          libraryDiscipline = disciplineSelect.value;
          renderLibraryTable();
        });
        controls.appendChild(searchInput);
        controls.appendChild(disciplineSelect);
        libraryTableWrap.appendChild(controls);

        const table = document.createElement('table');
        table.className = 'screening-library-table';
        const colgroup = document.createElement('colgroup');
        [
          'col-discipline',
          'col-function',
          'col-metric',
          'col-context',
          'col-how',
          'col-optimal',
          'col-suboptimal',
          'col-marginal',
          'col-poor',
          'col-references',
          'col-include',
        ].forEach((cls) => {
          const col = document.createElement('col');
          col.className = cls;
          colgroup.appendChild(col);
        });
        table.appendChild(colgroup);
        const thead = document.createElement('thead');
        thead.innerHTML =
          '<tr>' +
          '<th class="col-discipline">Discipline</th>' +
          '<th class="col-function">Function</th>' +
          '<th class="col-metric">Metric</th>' +
          '<th class="col-context">Context/Method</th>' +
          '<th class="col-how">How to Measure</th>' +
          '<th class="col-optimal">Optimal</th>' +
          '<th class="col-suboptimal">Suboptimal</th>' +
          '<th class="col-marginal">Marginal</th>' +
          '<th class="col-poor">Poor</th>' +
          '<th class="col-references">References</th>' +
          '<th class=\"col-include\">Include</th>' +
          '</tr>';
        const tbody = document.createElement('tbody');
        table.appendChild(thead);
        table.appendChild(tbody);

        const isPredefined = activeScenario && activeScenario.type === 'predefined';

        const filteredMetrics = metrics.filter((metric) => {
          if (libraryDiscipline !== 'all' && metric.discipline !== libraryDiscipline) {
            return false;
          }
          const haystack = [
            metric.discipline,
            metric.functionName,
            metric.metric,
            metric.context,
            metric.method,
            metric.howToMeasure,
            metric.criteria.optimal,
            metric.criteria.suboptimal,
            metric.criteria.marginal,
            metric.criteria.poor,
            metric.references,
          ]
            .filter(Boolean)
            .join(' ')
            .toLowerCase();
          return !librarySearchTerm.trim() || haystack.includes(librarySearchTerm.trim().toLowerCase());
        });

        if (filteredMetrics.length === 0) {
          const emptyRow = document.createElement('tr');
          const emptyCell = document.createElement('td');
          emptyCell.colSpan = 11;
          emptyCell.className = 'empty-cell';
          emptyCell.textContent = 'No metrics match your filters.';
          emptyRow.appendChild(emptyCell);
          tbody.appendChild(emptyRow);
          libraryTableWrap.appendChild(table);
          return;
        }

        const disciplineGroups = new Map();
        filteredMetrics.forEach((metric) => {
          const disciplineKey = metric.discipline || 'Other';
          if (!disciplineGroups.has(disciplineKey)) {
            disciplineGroups.set(disciplineKey, {
              discipline: disciplineKey,
              functions: new Map(),
              activeCount: 0,
            });
          }
          const group = disciplineGroups.get(disciplineKey);
          const functionKey = metric.functionName || 'Other';
          if (!group.functions.has(functionKey)) {
            group.functions.set(functionKey, {
              name: functionKey,
              metrics: [],
              activeCount: 0,
            });
          }
          const fnGroup = group.functions.get(functionKey);
          fnGroup.metrics.push(metric);
          if (selectedMetricIds.has(metric.id)) {
            fnGroup.activeCount += 1;
            group.activeCount += 1;
          }
        });

        const isFunctionCollapsed = (discipline, fn) => {
          const key = `${discipline}||${fn}`;
          if (!libraryCollapse.functions.has(key)) {
            libraryCollapse.functions.set(key, true);
          }
          return libraryCollapse.functions.get(key);
        };

        const toggleFunction = (discipline, fn) => {
          const key = `${discipline}||${fn}`;
          libraryCollapse.functions.set(key, !isFunctionCollapsed(discipline, fn));
          renderLibraryTable();
        };

        Array.from(disciplineGroups.values()).forEach((group) => {
          const functions = Array.from(group.functions.values());
          const allFunctionsCollapsed = functions.every((fn) =>
            isFunctionCollapsed(group.discipline, fn.name)
          );
          const disciplineRowCount = functions.reduce((total, fn) => {
            const showMetrics = !isFunctionCollapsed(group.discipline, fn.name);
            return total + 1 + (showMetrics ? fn.metrics.length : 0);
          }, 0);

          functions.forEach((fn, fnIndex) => {
            const functionCollapsed = isFunctionCollapsed(group.discipline, fn.name);
            const showMetrics = !functionCollapsed;
            const functionRow = document.createElement('tr');
            functionRow.classList.add(slugCategory(group.discipline));
            if (fn.activeCount === 0) {
              functionRow.classList.add('inactive-row');
            }

            if (fnIndex === 0) {
              const disciplineCell = document.createElement('td');
              disciplineCell.className = 'col-discipline';
              disciplineCell.rowSpan = Math.max(disciplineRowCount, 1);
              const disciplineToggle = document.createElement('button');
              disciplineToggle.type = 'button';
              disciplineToggle.className = 'criteria-toggle library-toggle';
              disciplineToggle.setAttribute('aria-label', 'Toggle discipline');
              disciplineToggle.setAttribute('aria-expanded', allFunctionsCollapsed ? 'false' : 'true');
              disciplineToggle.innerHTML = allFunctionsCollapsed ? '&#9656;' : '&#9662;';
              const disciplineLabel = document.createElement('span');
              disciplineLabel.textContent = group.discipline || '-';
              disciplineCell.appendChild(disciplineToggle);
              disciplineCell.appendChild(disciplineLabel);
              if (group.activeCount > 0) {
                disciplineCell.classList.add('library-group-active', slugCategory(group.discipline));
              }
              disciplineToggle.addEventListener('click', () => {
                const nextState = !allFunctionsCollapsed;
                functions.forEach((fnItem) => {
                  const key = `${group.discipline}||${fnItem.name}`;
                  libraryCollapse.functions.set(key, nextState);
                });
                renderLibraryTable();
              });
              functionRow.appendChild(disciplineCell);
            }

            const functionCell = document.createElement('td');
            functionCell.className = 'col-function';
            functionCell.rowSpan = 1 + (showMetrics ? fn.metrics.length : 0);
            const functionLabel = document.createElement('span');
            functionLabel.textContent = fn.name || '-';
            const functionToggle = document.createElement('button');
            functionToggle.type = 'button';
            functionToggle.className = 'criteria-toggle library-toggle';
            functionToggle.setAttribute('aria-label', 'Toggle function metrics');
            functionToggle.setAttribute('aria-expanded', showMetrics ? 'true' : 'false');
            functionToggle.innerHTML = showMetrics ? '&#9662;' : '&#9656;';
            functionToggle.addEventListener('click', () =>
              toggleFunction(group.discipline, fn.name)
            );
            functionCell.appendChild(functionLabel);
            functionCell.appendChild(functionToggle);
            if (fn.activeCount > 0) {
              functionCell.classList.add('library-group-active', slugCategory(group.discipline));
            }
            functionRow.appendChild(functionCell);

            if (functionCollapsed) {
              const summaryText = `${fn.activeCount} active`;
              const summaryCells = [
                { value: summaryText, className: 'col-metric' },
                { value: '', className: 'col-context' },
                { value: '', className: 'col-how' },
                { value: '', className: 'col-optimal' },
                { value: '', className: 'col-suboptimal' },
                { value: '', className: 'col-marginal' },
                { value: '', className: 'col-poor' },
                { value: '', className: 'col-references' },
              ];
              summaryCells.forEach(({ value, className }) => {
                const cell = document.createElement('td');
                cell.className = className;
                if (value) {
                  const span = document.createElement('span');
                  span.className = 'metric-summary';
                  span.textContent = value;
                  cell.appendChild(span);
                }
                functionRow.appendChild(cell);
              });
              const includeSpacer = document.createElement('td');
              includeSpacer.className = 'col-include';
              functionRow.appendChild(includeSpacer);
            }

            tbody.appendChild(functionRow);

            if (showMetrics) {
              fn.metrics.forEach((metric) => {
                const row = document.createElement('tr');
                row.classList.add(slugCategory(group.discipline));
                if (!selectedMetricIds.has(metric.id)) {
                  row.classList.add('inactive-row');
                }

                const contextMethod = document.createElement('div');
                const contextText = document.createElement('div');
                contextText.textContent = `Context: ${metric.context || '-'}`;
                const methodText = document.createElement('div');
                methodText.textContent = `Method: ${metric.method || '-'}`;
                contextMethod.appendChild(contextText);
                contextMethod.appendChild(methodText);

                const cells = [
                  { value: metric.metric || '-', className: 'col-metric' },
                  { value: contextMethod, className: 'col-context', isNode: true },
                  { value: metric.howToMeasure || '-', className: 'col-how' },
                  { value: metric.criteria.optimal || '-', className: 'col-optimal' },
                  { value: metric.criteria.suboptimal || '-', className: 'col-suboptimal' },
                  { value: metric.criteria.marginal || '-', className: 'col-marginal' },
                  { value: metric.criteria.poor || '-', className: 'col-poor' },
                  { value: metric.references || '-', className: 'col-references' },
                ];

                cells.forEach(({ value, className, isNode }) => {
                  const cell = document.createElement('td');
                  cell.className = className;
                  if (isNode && value instanceof HTMLElement) {
                    cell.appendChild(value);
                  } else {
                    cell.textContent = value;
                  }
                  row.appendChild(cell);
                });

                const includeCell = document.createElement('td');
                includeCell.className = 'col-include';
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = selectedMetricIds.has(metric.id);
                checkbox.disabled = isPredefined;
                checkbox.addEventListener('change', () => {
                  if (!activeScenario || activeScenario.type === 'predefined') {
                    checkbox.checked = selectedMetricIds.has(metric.id);
                    return;
                  }
                  if (checkbox.checked) {
                    selectedMetricIds.add(metric.id);
                    metricRatings.set(metric.id, metricRatings.get(metric.id) || defaultRating);
                    activeScenario.ratings[metric.id] = metricRatings.get(metric.id) || defaultRating;
                  } else {
                    selectedMetricIds.delete(metric.id);
                    metricRatings.delete(metric.id);
                    delete activeScenario.ratings[metric.id];
                  }
                  activeScenario.metricIds = Array.from(selectedMetricIds);
                  store.save();
                  buildAddOptions();
                  renderTable();
                  renderLibraryTable();
                });
                includeCell.appendChild(checkbox);
                row.appendChild(includeCell);

                tbody.appendChild(row);
              });
            }
          });
        });

        libraryTableWrap.appendChild(table);
      };

      const openLibrary = () => {
        if (!libraryModal) {
          return;
        }
        renderLibraryTable();
        libraryModal.hidden = false;
        document.body.classList.add('modal-open');
      };

      const closeLibrary = () => {
        if (!libraryModal) {
          return;
        }
        libraryModal.hidden = true;
        document.body.classList.remove('modal-open');
        libraryCollapse.disciplines = new Map();
        libraryCollapse.functions = new Map();
      };

      const controls = document.createElement('div');
      controls.className = 'screening-controls';

      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search';
      search.setAttribute('aria-label', 'Search');

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

      const libraryButton = document.createElement('button');
      libraryButton.type = 'button';
      libraryButton.className = 'btn btn-small library-open-btn';
      libraryButton.setAttribute('aria-label', 'View Screening Metric Toolbox');
      libraryButton.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
        '<path d="M3 5.5h7.5c1.7 0 3 1.1 3 2.5v11H6c-1.7 0-3-1.1-3-2.5V5.5z" fill="none" stroke="currentColor" stroke-width="1.5"></path>' +
        '<path d="M21 5.5h-7.5c-1.7 0-3 1.1-3 2.5v11h7.5c1.7 0 3-1.1 3-2.5V5.5z" fill="none" stroke="currentColor" stroke-width="1.5"></path>' +
        '<path d="M6 8h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path>' +
        '<path d="M6 11h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path>' +
        '</svg>';
      const libraryText = document.createElement('span');
      libraryText.textContent = 'Metric Toolbox';
      libraryButton.appendChild(libraryText);

      const addSelect = document.createElement('select');
      addSelect.className = 'metric-add-select';
      const addPlaceholder = document.createElement('option');
      addPlaceholder.value = '';
      addPlaceholder.textContent = 'Quick Add metric...';
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
      controls.appendChild(libraryButton);
      controls.appendChild(addSelect);
      controls.appendChild(addButton);
      controls.appendChild(resetButton);

      libraryButton.addEventListener('click', openLibrary);
      if (libraryCloseBtn) {
        libraryCloseBtn.addEventListener('click', closeLibrary);
      }
      if (libraryBackdrop) {
        libraryBackdrop.addEventListener('click', closeLibrary);
      }
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          closeLibrary();
        }
      });

      const table = document.createElement('table');
      table.className = 'screening-table';
      const thead = document.createElement('thead');
      thead.innerHTML =
        '<tr>' +
        '<th class="col-discipline">Discipline</th>' +
        '<th class="col-function">Function</th>' +
        '<th class="col-metric">Metric</th>' +
        '<th class="col-metric-score">Metric<br>score</th>' +
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

        metricRows.forEach(({ metric, functionScoreValue, functionScoreRange }) => {
          const score = metric.functionId ? functionScores.get(metric.functionId) : null;
          const nextScore =
            score === null || score === undefined ? null : Math.round(score);
          functionScoreValue.textContent = nextScore === null ? '-' : String(nextScore);
          if (functionScoreRange) {
            functionScoreRange.value = nextScore === null ? '0' : String(nextScore);
          }
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
        const isCustom = activeScenario && activeScenario.type === 'custom';

        let rowsToRender = [];

        if (isCustom) {
          const metricsByFunction = new Map();
          metrics.forEach((metric) => {
            if (!selectedMetricIds.has(metric.id)) {
              return;
            }
            if (!metric.functionId) {
              return;
            }
            if (!metricsByFunction.has(metric.functionId)) {
              metricsByFunction.set(metric.functionId, []);
            }
            metricsByFunction.get(metric.functionId).push(metric);
          });

          functionsList.forEach((fn) => {
            const discipline = fn.category || '';
            if (disciplineValue !== 'all' && discipline !== disciplineValue) {
              return;
            }

            const impactText = fn.impact_statement || fn.impactStatement || fn.short_description || '';
            const contextText = fn.assessment_context || fn.assessmentContext || fn.long_description || '';
            const statementText = fn.function_statement || fn.functionStatement || '';
            const functionHaystack = [
              fn.name,
              impactText,
              contextText,
              statementText,
              discipline,
            ]
              .join(' ')
              .toLowerCase();
            const matchesFunctionSearch = !term || functionHaystack.includes(term);

            const functionMetrics = metricsByFunction.get(fn.id) || [];
            let metricsToShow = functionMetrics;
            if (term && !matchesFunctionSearch) {
              metricsToShow = functionMetrics.filter((metric) => {
                const metricHaystack = [
                  metric.discipline,
                  metric.functionName,
                  metric.metric,
                  metric.context,
                  metric.method,
                ]
                  .join(' ')
                  .toLowerCase();
                return metricHaystack.includes(term);
              });
            }

            if (!matchesFunctionSearch && metricsToShow.length === 0) {
              return;
            }

            if (metricsToShow.length === 0) {
              rowsToRender.push({
                type: 'placeholder',
                discipline,
                functionId: fn.id,
                functionMeta: fn,
              });
            } else {
              metricsToShow.forEach((metric) => {
                rowsToRender.push({
                  type: 'metric',
                  discipline,
                  metric,
                  functionId: fn.id,
                  functionMeta: fn,
                });
              });
            }
          });
        } else {
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

          rowsToRender = visibleMetrics.map((metric) => ({
            type: 'metric',
            discipline: metric.discipline,
            metric,
            functionId: metric.functionId,
            functionMeta: metric.functionId ? functionById.get(metric.functionId) : null,
          }));
        }

        const metricRows = [];

        const groupStarts = new Map();
        for (let i = 0; i < rowsToRender.length; ) {
          const discipline = rowsToRender[i].discipline;
          let j = i;
          while (j < rowsToRender.length && rowsToRender[j].discipline === discipline) {
            j += 1;
          }
          groupStarts.set(i, j - i);
          i = j;
        }

        const disciplineActive = new Map();
        const functionActive = new Map();
        rowsToRender.forEach((rowItem) => {
          if (rowItem.type !== 'metric') {
            return;
          }
          disciplineActive.set(rowItem.discipline, true);
          if (rowItem.functionId) {
            functionActive.set(rowItem.functionId, true);
          }
        });

        if (rowsToRender.length === 0) {
          const emptyRow = document.createElement('tr');
          const emptyCell = document.createElement('td');
          emptyCell.colSpan = 7;
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

        const updateFunctionRowSpans = () => {
          const rows = Array.from(tbody.querySelectorAll('tr'));
          let i = 0;
          while (i < rows.length) {
            const row = rows[i];
            const rowType = row.dataset.rowType;
            if (rowType === 'criteria') {
              i += 1;
              continue;
            }
            if (rowType === 'placeholder') {
              const functionCell = row.querySelector('.col-function');
              if (functionCell) {
                functionCell.style.display = '';
                functionCell.rowSpan = 1;
              }
              ['.col-physical', '.col-chemical', '.col-biological'].forEach((sel) => {
                const cell = row.querySelector(sel);
                if (cell) {
                  cell.style.display = '';
                  cell.rowSpan = 1;
                }
              });
              i += 1;
              continue;
            }

            const functionId = row.dataset.functionId || '';
            const groupRows = [];
            let expandedCriteria = 0;
            let k = i;
            while (k < rows.length) {
              const current = rows[k];
              const currentType = current.dataset.rowType;
              if (currentType === 'criteria') {
                if (current.dataset.functionId === functionId && !current.hidden) {
                  expandedCriteria += 1;
                }
                k += 1;
                continue;
              }
              if (currentType === 'metric' && current.dataset.functionId === functionId) {
                groupRows.push(current);
                k += 1;
                continue;
              }
              break;
            }

            if (groupRows.length === 0) {
              i = k;
              continue;
            }

            const functionSpan = groupRows.length + expandedCriteria;
            const functionCell = groupRows[0].querySelector('.col-function');
            if (functionCell) {
              functionCell.style.display = '';
              functionCell.rowSpan = Math.max(functionSpan, 1);
            }
            groupRows.slice(1).forEach((r) => {
              const cell = r.querySelector('.col-function');
              if (cell) {
                cell.style.display = 'none';
                cell.rowSpan = 1;
              }
            });

            const mergeWeights = expandedCriteria === 0 && groupRows.length > 1;
            const weightSelectors = ['.col-physical', '.col-chemical', '.col-biological'];
            weightSelectors.forEach((sel) => {
              const firstCell = groupRows[0].querySelector(sel);
              if (!firstCell) {
                return;
              }
              if (mergeWeights) {
                firstCell.style.display = '';
                firstCell.rowSpan = groupRows.length;
                groupRows.slice(1).forEach((r) => {
                  const cell = r.querySelector(sel);
                  if (cell) {
                    cell.style.display = 'none';
                    cell.rowSpan = 1;
                  }
                });
              } else {
                groupRows.forEach((r) => {
                  const cell = r.querySelector(sel);
                  if (cell) {
                    cell.style.display = '';
                    cell.rowSpan = 1;
                  }
                });
              }
            });

            i = k;
          }
        };

        rowsToRender.forEach((rowItem, index) => {
          const metric = rowItem.type === 'metric' ? rowItem.metric : null;
          const functionMeta = rowItem.functionMeta || (rowItem.functionId ? functionById.get(rowItem.functionId) : null);
          const functionName = metric ? metric.functionName : functionMeta ? functionMeta.name : '';
          const functionStatement =
            (metric && metric.functionStatement) ||
            (functionMeta && (functionMeta.function_statement || functionMeta.functionStatement)) ||
            '';
          const row = document.createElement('tr');
          row.classList.add(slugCategory(rowItem.discipline));
          row.dataset.rowType = rowItem.type;
          const rowFunctionId = rowItem.functionId || `row-${index}`;
          row.dataset.functionId = rowFunctionId;
          if (rowItem.type === 'placeholder') {
            row.classList.add('inactive-row');
          }

          const groupSize = groupStarts.get(index);
          if (groupSize) {
            const disciplineCell = document.createElement('td');
            disciplineCell.textContent = rowItem.discipline;
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = groupSize;
            if (disciplineActive.get(rowItem.discipline)) {
              disciplineCell.classList.add(
                'screening-group-active',
                slugCategory(rowItem.discipline)
              );
            }
            row.appendChild(disciplineCell);
          }
          const functionCell = document.createElement('td');
          functionCell.className = 'col-function';
          if (functionActive.get(rowFunctionId)) {
            functionCell.classList.add(
              'screening-group-active',
              slugCategory(rowItem.discipline)
            );
          }
          const functionNameLine = document.createElement('div');
          functionNameLine.className = 'function-title';
          const functionNameText = document.createElement('span');
          functionNameText.textContent = functionName;
          functionNameLine.appendChild(functionNameText);
          const functionToggle = document.createElement('button');
          functionToggle.type = 'button';
          functionToggle.className = 'criteria-toggle function-toggle';
          functionToggle.innerHTML = '&#9662;';
          functionToggle.setAttribute('aria-expanded', 'false');
          functionToggle.setAttribute('aria-label', 'Toggle function statement');
          functionNameLine.appendChild(functionToggle);
          functionCell.appendChild(functionNameLine);
          const statementLine = document.createElement('div');
          statementLine.className = 'function-statement';
          statementLine.textContent = functionStatement;
          statementLine.hidden = true;
          functionCell.appendChild(statementLine);
          const functionScoreLine = document.createElement('div');
          functionScoreLine.className = 'score-input function-score-inline';
          const functionScoreRange = document.createElement('input');
          functionScoreRange.type = 'range';
          functionScoreRange.min = '0';
          functionScoreRange.max = '15';
          functionScoreRange.step = '1';
          functionScoreRange.disabled = true;
          const functionScoreValue = document.createElement('span');
          functionScoreValue.className = 'score-value';
          functionScoreValue.textContent = '-';
          functionScoreLine.appendChild(functionScoreRange);
          functionScoreLine.appendChild(functionScoreValue);
          functionCell.appendChild(functionScoreLine);
          functionToggle.addEventListener('click', () => {
            if (!statementLine.textContent) {
              return;
            }
            const isOpen = !statementLine.hidden;
            statementLine.hidden = isOpen;
            functionToggle.setAttribute('aria-expanded', String(!isOpen));
          });
          const metricCell = document.createElement('td');
          metricCell.className = 'col-metric metric-cell';
          const metricText = document.createElement('span');
          metricText.textContent =
            rowItem.type === 'placeholder'
              ? 'No metrics selected for this function'
              : metric.metric;
          metricCell.appendChild(metricText);

          const scoreCell = document.createElement('td');
          scoreCell.className = 'col-metric-score';
          let scoreSelect = null;
          if (rowItem.type === 'metric') {
            scoreSelect = document.createElement('select');
            scoreSelect.className = 'metric-score-select';
            ratingOptions.forEach((option) => {
              const opt = document.createElement('option');
              opt.value = option.label;
              opt.textContent = option.label;
              scoreSelect.appendChild(opt);
            });
            scoreSelect.value = metricRatings.get(metric.id) || defaultRating;
            scoreCell.appendChild(scoreSelect);
          } else {
            scoreCell.textContent = '-';
          }

          let criteriaBtn = null;
          let detailsId = null;
          if (rowItem.type === 'metric') {
            criteriaBtn = document.createElement('button');
            criteriaBtn.type = 'button';
            criteriaBtn.className = 'criteria-toggle';
            criteriaBtn.innerHTML = '&#9662;';
            detailsId = `criteria-${metric.id}`;
            criteriaBtn.setAttribute('aria-expanded', 'false');
            criteriaBtn.setAttribute('aria-controls', detailsId);
            criteriaBtn.setAttribute('aria-label', 'Toggle criteria details');
          }

          const removeBtn = document.createElement('button');
          removeBtn.type = 'button';
          removeBtn.className = 'criteria-toggle criteria-remove';
          removeBtn.textContent = 'X';
          removeBtn.setAttribute('aria-label', 'Remove metric');
          const metricActions = document.createElement('span');
          metricActions.className = 'metric-actions';
          if (criteriaBtn) {
            metricActions.appendChild(criteriaBtn);
          }
          if (!isPredefined && rowItem.type === 'metric') {
            metricActions.appendChild(removeBtn);
          }
          metricCell.appendChild(metricActions);

          const mapping = rowItem.functionId ? mappingById[rowItem.functionId] : null;
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
          row.appendChild(physicalCell);
          row.appendChild(chemicalCell);
          row.appendChild(biologicalCell);

          let detailsRow = null;
          if (rowItem.type === 'metric') {
            detailsRow = document.createElement('tr');
            detailsRow.id = detailsId;
            detailsRow.className = 'criteria-row';
            detailsRow.hidden = true;
            detailsRow.dataset.rowType = 'criteria';
            detailsRow.dataset.functionId = rowFunctionId;
            const detailsCell = document.createElement('td');
            detailsCell.colSpan = 5;
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
              updateFunctionRowSpans();
            });

            scoreSelect.addEventListener('change', () => {
              metricRatings.set(metric.id, scoreSelect.value);
              if (activeScenario) {
                activeScenario.ratings[metric.id] = scoreSelect.value;
                store.save();
              }
              updateScores(metricRows);
            });
          }

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
          if (detailsRow) {
            tbody.appendChild(detailsRow);
          }
          metricRows.push({
            metric: metric || { functionId: rowItem.functionId },
            functionScoreValue,
            functionScoreRange,
          });
        });

        updateScores(metricRows);
        updateDisciplineRowSpans();
        updateFunctionRowSpans();
        renderLibraryTable();
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
      console.error('Screening assessment widget failed to load.', error);
      if (ui) {
        const message =
          error && error.message
            ? `Screening assessment widget failed to load. ${error.message}`
            : 'Screening assessment widget failed to load.';
        ui.textContent = message;
        ui.hidden = false;
      }
    }
  };

  init();
})();
