

(() => {
  const container = document.querySelector('.detailed-assessment');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const dataUrl = `${baseUrl}/assets/data/detailed-metrics.tsv`;
  const functionsUrl = `${baseUrl}/assets/data/functions.json`;
  const mappingUrl = `${baseUrl}/assets/data/cwa-mapping.json`;
  const fallback = container.querySelector('.detailed-assessment-fallback');
  const ui = container.querySelector('.detailed-assessment-ui');

  const normalizeText = (value) =>
    value
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '');

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

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const defaultIndexValues = [0, 0.3, 0.7, 1];
  const defaultProfileId = 'detailed-default';

  const normalizeCurveType = (value) => {
    if (!value) {
      return 'quantitative';
    }
    return value === 'qualitative' ? 'categorical' : value;
  };

  const parseScore = (value) => {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const roundScore = (value, places = 2) => {
    const factor = 10 ** places;
    return Math.round(value * factor) / factor;
  };

  const applyIndexRanges = (points) => {
    const scored = points
      .map((point) => ({ point, y: parseScore(point.y) }))
      .filter((entry) => entry.y !== null);
    if (!scored.length) {
      return;
    }
    const sorted = [...scored].sort((a, b) => b.y - a.y);
    const boundaries = sorted.slice(0, -1).map((entry, index) =>
      (entry.y + sorted[index + 1].y) / 2
    );
    sorted.forEach((entry, index) => {
      const max = index === 0 ? 1 : boundaries[index - 1];
      const min = index === sorted.length - 1 ? 0 : boundaries[index];
      entry.point.yMax = roundScore(clamp(max, 0, 1));
      entry.point.yMin = roundScore(clamp(min, 0, 1));
    });
  };

  const buildDefaultCurve = () => ({
    name: 'Default',
    xType: 'quantitative',
    indexRange: false,
    units: '',
    activeLayerId: null,
    layers: [
      {
        id: generateId(),
        name: 'Default',
        points: defaultIndexValues.map((value) => ({ x: '', y: value, description: '' })),
      },
    ],
  });

  const getCurveLayer = (curve) => {
    if (!curve || !curve.layers?.length) {
      return null;
    }
    if (curve.activeLayerId) {
      const activeLayer = curve.layers.find((layer) => layer.id === curve.activeLayerId);
      if (activeLayer) {
        return activeLayer;
      }
    }
    curve.activeLayerId = curve.layers[0].id;
    return curve.layers[0];
  };

  const computeIndexScore = (curve, fieldValue) => {
    if (!Number.isFinite(fieldValue) || !curve || !curve.layers?.length) {
      return null;
    }
    if (normalizeCurveType(curve.xType) === 'categorical') {
      return null;
    }
    const layer = getCurveLayer(curve);
    if (!layer) {
      return null;
    }
    const points = layer.points
      .map((point) => ({
        x: Number.parseFloat(point.x),
        y: Number.parseFloat(point.y),
      }))
      .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
      .sort((a, b) => a.x - b.x);
    if (points.length < 2) {
      return null;
    }
    const min = points[0];
    const max = points[points.length - 1];
    if (fieldValue <= min.x) {
      return clamp(min.y, 0, 1);
    }
    if (fieldValue >= max.x) {
      return clamp(max.y, 0, 1);
    }
    for (let i = 0; i < points.length - 1; i += 1) {
      const left = points[i];
      const right = points[i + 1];
      if (fieldValue >= left.x && fieldValue <= right.x) {
        const t = (fieldValue - left.x) / (right.x - left.x);
        const y = left.y + t * (right.y - left.y);
        return clamp(y, 0, 1);
      }
    }
    return null;
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
      const mappingById = mappingList.reduce((acc, item) => {
        acc[item.id] = item;
        return acc;
      }, {});

      const metrics = metricsRaw.map((row, index) => {
        const functionName = row.Function || row.function || '';
        const functionKey = normalizeText(functionName);
        const functionMatch = functionByName.get(functionKey);
        return {
          id: `metric-${index + 1}`,
          discipline: row.Discipline || row.discipline || functionMatch?.category || '',
          functionName,
          functionId: functionMatch ? functionMatch.id : null,
          metric: row.Metric || row.metric || '',
          context: row.Context || row.context || '',
          method: row.Method || row.method || '',
          howToMeasure: row['How to measure'] || row.how_to_measure || '',
          references: row.References || row.references || '',
        };
      });

      const metricById = new Map(metrics.map((metric) => [metric.id, metric]));
      const metricIdSet = new Set(metrics.map((metric) => metric.id));

      const ensureLibraryMetric = (detail) => {
        if (!detail) {
          return null;
        }
        const existing = metricById.get(detail.metricId);
        if (existing) {
          return existing;
        }
        const functionName = detail.function || '';
        const functionKey = normalizeText(functionName);
        const functionMatch = functionByName.get(functionKey);
        const metric = {
          id: detail.metricId,
          discipline: detail.discipline || functionMatch?.category || 'Other',
          functionName,
          functionId: functionMatch ? functionMatch.id : null,
          metric: detail.name || detail.metricId,
          context: detail.methodContextMarkdown || '',
          method: detail.methodContextMarkdown || '',
          howToMeasure: detail.howToMeasureMarkdown || '',
          references: Array.isArray(detail.references)
            ? detail.references.join('; ')
            : '',
        };
        metrics.push(metric);
        metricById.set(metric.id, metric);
        metricIdSet.add(metric.id);
        return metric;
      };

      const tabsHost = container.querySelector('.detailed-tabs');
      const addTabButton = container.querySelector('.detailed-tab-add');
      const nameInput = container.querySelector('.settings-name');
      const applicabilityInput = container.querySelector('.settings-applicability');
      const notesInput = container.querySelector('.settings-notes-input');
      const duplicateButton = container.querySelector('.detailed-duplicate');
      const deleteButton = container.querySelector('.detailed-delete');
      const controlsHost = container.querySelector('.detailed-controls-host');
      const tableHost = container.querySelector('.detailed-table-wrap');

      const curveModal = container.querySelector('.detailed-curve-modal');
      const curveBackdrop = container.querySelector('.detailed-curve-backdrop');
      const curveClose = container.querySelector('.detailed-curve-close');
      const curveMetricName = container.querySelector('.curve-metric-name');
      const curveUnitsInput = container.querySelector('.curve-units');
      const curveXType = container.querySelector('.curve-x-type');
      const curveLayerSelect = container.querySelector('.curve-layer-select');
      const curveLayerAdd = container.querySelector('.curve-layer-add');
      const curveLayerRemove = container.querySelector('.curve-layer-remove');
      const curveLayerName = container.querySelector('.curve-layer-name');
      const curveReset = container.querySelector('.curve-reset');
      const curveTableWrap = container.querySelector('.curve-table-wrap');
      const curveChart = container.querySelector('.curve-chart');

      let scenarios = [];
      let activeScenarioId = null;
      let activeCurveMetricId = null;

      const createScenario = () => ({
        id: generateId(),
        name: `Custom Detailed Assessment ${scenarios.length + 1}`,
        applicability: '',
        notes: '',
        metricIds: [],
        fieldValues: {},
        curves: {},
        metricProfiles: {},
      });

      const getActiveScenario = () =>
        scenarios.find((scenario) => scenario.id === activeScenarioId);

      const ensureCurve = (scenario, metricId) => {
        if (!scenario.curves[metricId]) {
          scenario.curves[metricId] = buildDefaultCurve();
        }
        return scenario.curves[metricId];
      };

      const openCurveModal = (metricId) => {
        if (window.dispatchEvent) {
          const scenario = getActiveScenario();
          const profileId =
            scenario?.metricProfiles?.[metricId] || defaultProfileId;
          window.dispatchEvent(
            new CustomEvent('staf:open-inspector', {
              detail: {
                tier: 'detailed',
                metricId,
                profileId,
                tab: 'curves',
              },
            })
          );
          return;
        }
        const scenario = getActiveScenario();
        if (!scenario || !curveModal) {
          return;
        }
        activeCurveMetricId = metricId;
        const metric = metrics.find((item) => item.id === metricId);
        const curve = ensureCurve(scenario, metricId);
        if (curveMetricName && metric) {
          curveMetricName.value = metric.metric;
        }
        if (curveUnitsInput) {
          curveUnitsInput.value = curve.units || '';
        }
        if (curveXType) {
          curveXType.value = normalizeCurveType(curve.xType || 'quantitative');
        }
        renderCurveLayerControls(curve);
        renderCurveTable();
        renderCurveChart();
        curveModal.hidden = false;
      };

      const closeCurveModal = () => {
        if (!curveModal) {
          return;
        }
        curveModal.hidden = true;
        activeCurveMetricId = null;
        renderTable();
      };

      if (curveBackdrop) {
        curveBackdrop.addEventListener('click', closeCurveModal);
      }
      if (curveClose) {
        curveClose.addEventListener('click', closeCurveModal);
      }
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          closeCurveModal();
        }
      });

      const renderTabs = () => {
        if (!tabsHost) {
          return;
        }
        tabsHost.innerHTML = '';
        scenarios.forEach((scenario) => {
          const tab = document.createElement('button');
          tab.type = 'button';
          tab.className = 'assessment-tab';
          if (scenario.id === activeScenarioId) {
            tab.classList.add('is-active');
            tab.setAttribute('aria-selected', 'true');
          }
          tab.textContent = scenario.name || 'Custom Assessment';
          tab.addEventListener('click', () => {
            activeScenarioId = scenario.id;
            renderAll();
          });
          tabsHost.appendChild(tab);
        });
      };

      const renderSettings = () => {
        const scenario = getActiveScenario();
        const hasScenario = Boolean(scenario);
        if (nameInput) {
          nameInput.value = scenario ? scenario.name || '' : '';
          nameInput.disabled = !hasScenario;
          nameInput.oninput = (event) => {
            if (!scenario) {
              return;
            }
            scenario.name = event.target.value;
            renderTabs();
          };
        }
        if (applicabilityInput) {
          applicabilityInput.value = scenario ? scenario.applicability || '' : '';
          applicabilityInput.disabled = !hasScenario;
          applicabilityInput.oninput = (event) => {
            if (!scenario) {
              return;
            }
            scenario.applicability = event.target.value;
          };
        }
        if (notesInput) {
          notesInput.value = scenario ? scenario.notes || '' : '';
          notesInput.disabled = !hasScenario;
          notesInput.oninput = (event) => {
            if (!scenario) {
              return;
            }
            scenario.notes = event.target.value;
          };
        }
        if (duplicateButton) {
          duplicateButton.disabled = !hasScenario;
        }
        if (deleteButton) {
          deleteButton.disabled = !hasScenario;
        }
      };

      if (addTabButton) {
        addTabButton.addEventListener('click', () => {
          const scenario = createScenario();
          scenarios.push(scenario);
          activeScenarioId = scenario.id;
          renderAll();
        });
      }

      if (duplicateButton) {
        duplicateButton.addEventListener('click', () => {
          const scenario = getActiveScenario();
          if (!scenario) {
            return;
          }
          const copy = {
            ...scenario,
            id: generateId(),
            name: `${scenario.name || 'Custom Assessment'} Copy`,
            metricIds: Array.from(scenario.metricIds || []),
            fieldValues: { ...(scenario.fieldValues || {}) },
            curves: JSON.parse(JSON.stringify(scenario.curves || {})),
          };
          scenarios.push(copy);
          activeScenarioId = copy.id;
          renderAll();
        });
      }

      if (deleteButton) {
        deleteButton.addEventListener('click', () => {
          const index = scenarios.findIndex((scenario) => scenario.id === activeScenarioId);
          if (index < 0) {
            return;
          }
          scenarios.splice(index, 1);
          if (scenarios.length === 0) {
            activeScenarioId = null;
          } else {
            const nextIndex = Math.max(0, index - 1);
            activeScenarioId = scenarios[nextIndex].id;
          }
          renderAll();
        });
      }

      const controls = document.createElement('div');
      controls.className = 'detailed-controls';

      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search';
      search.className = 'metric-search';

      const disciplineFilter = document.createElement('select');
      disciplineFilter.className = 'discipline-filter';
      const allOption = document.createElement('option');
      allOption.value = 'all';
      allOption.textContent = 'All disciplines';
      disciplineFilter.appendChild(allOption);
      Array.from(new Set(functionsList.map((fn) => fn.category))).forEach((category) => {
        const option = document.createElement('option');
        option.value = category;
        option.textContent = category;
        disciplineFilter.appendChild(option);
      });

      const addSelect = document.createElement('select');
      addSelect.className = 'metric-add-select';
      const addPlaceholder = document.createElement('option');
      addPlaceholder.value = '';
      addPlaceholder.textContent = 'Quick add metric...';
      addSelect.appendChild(addPlaceholder);

      const addButton = document.createElement('button');
      addButton.type = 'button';
      addButton.className = 'btn btn-small';
      addButton.textContent = 'Add';

      const libraryButton = document.createElement('button');
      libraryButton.type = 'button';
      libraryButton.className = 'btn btn-small library-open-btn';
      libraryButton.setAttribute('data-open-metric-library', 'true');
      libraryButton.textContent = 'Metric Library';

      controls.appendChild(search);
      controls.appendChild(disciplineFilter);
      controls.appendChild(addSelect);
      controls.appendChild(addButton);
      controls.appendChild(libraryButton);

      if (controlsHost) {
        controlsHost.innerHTML = '';
        controlsHost.appendChild(controls);
      }

      const table = document.createElement('table');
      table.className = 'detailed-table';
      const thead = document.createElement('thead');
      thead.innerHTML =
        '<tr>' +
        '<th class="col-discipline">Discipline</th>' +
        '<th class="col-function">Function</th>' +
        '<th class="col-metric">Metric</th>' +
        '<th class="col-field">Field value</th>' +
        '<th class="col-index">Metric<br>index</th>' +
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
          labelCell.colSpan = 5;
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

      const expandedMetrics = new Set();

      const buildAddOptions = (selectedMetricIds) => {
        addSelect.innerHTML = '';
        addSelect.appendChild(addPlaceholder);
        const disciplineValue = disciplineFilter.value;
        metrics
          .filter((metric) => !selectedMetricIds.has(metric.id))
          .filter(
            (metric) =>
              disciplineValue === 'all' ||
              (metric.discipline || '') === disciplineValue ||
              (functionByName.get(normalizeText(metric.functionName))?.category || '') ===
                disciplineValue
          )
          .forEach((metric) => {
            const option = document.createElement('option');
            option.value = metric.id;
            option.textContent = `${metric.functionName}: ${metric.metric}`;
            addSelect.appendChild(option);
          });
      };

      const updateSummary = (functionScores) => {
        const outcomeTotals = {
          physical: { weighted: 0, max: 0, direct: 0, indirect: 0 },
          chemical: { weighted: 0, max: 0, direct: 0, indirect: 0 },
          biological: { weighted: 0, max: 0, direct: 0, indirect: 0 },
        };

        functionsList.forEach((fn) => {
          const functionScore = functionScores.get(fn.id) ?? 0;
          const mapping = mappingById[fn.id] || {
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

        const fillOutcome = (key) => {
          const total = outcomeTotals[key];
          const subIndex = total.max > 0 ? total.weighted / total.max : 0;
          summaryCells[key][0].textContent = formatCount(total.direct);
          summaryCells[key][1].textContent = formatCount(total.indirect);
          summaryCells[key][2].textContent = formatNumber(total.weighted);
          summaryCells[key][3].textContent = formatNumber(total.max);
          summaryCells[key][4].textContent = formatNumber(subIndex);
          return subIndex;
        };

        const physicalIndex = fillOutcome('physical');
        const chemicalIndex = fillOutcome('chemical');
        const biologicalIndex = fillOutcome('biological');
        const ecosystemIndex = (physicalIndex + chemicalIndex + biologicalIndex) / 3;
        if (summaryCells.ecosystem) {
          summaryCells.ecosystem.textContent = formatNumber(ecosystemIndex);
        }
      };

      const renderCurveLayerControls = (curve) => {
        if (!curveLayerSelect) {
          return;
        }
        curveLayerSelect.innerHTML = '';
        curve.layers.forEach((layer) => {
          const option = document.createElement('option');
          option.value = layer.id;
          option.textContent = layer.name || 'Stratification layer';
          curveLayerSelect.appendChild(option);
        });
        const activeLayer = getCurveLayer(curve);
        curveLayerSelect.value = activeLayer ? activeLayer.id : '';
        if (curveLayerRemove) {
          curveLayerRemove.disabled = curve.layers.length <= 1;
        }
        if (curveLayerName && activeLayer) {
          curveLayerName.value = activeLayer.name || '';
        }
      };

      const getActiveCurve = () => {
        const scenario = getActiveScenario();
        if (!scenario || !activeCurveMetricId) {
          return null;
        }
        return ensureCurve(scenario, activeCurveMetricId);
      };

      const renderCurveTable = () => {
        if (!curveTableWrap) {
          return;
        }
        const curve = getActiveCurve();
        if (!curve) {
          curveTableWrap.innerHTML = '';
          return;
        }
        const layer = getCurveLayer(curve);
        if (!layer) {
          curveTableWrap.innerHTML = '';
          return;
        }
        const table = document.createElement('table');
        table.className = 'curve-table';
        const thead = document.createElement('thead');
        const curveType = normalizeCurveType(curve.xType);
        const isCategorical = curveType === 'categorical';
        if (isCategorical && curve.indexRange == null) {
          curve.indexRange = true;
        }
        if (!isCategorical) {
          curve.indexRange = false;
        }
        const useRange = isCategorical && !!curve.indexRange;
        if (useRange) {
          const needsRanges = layer.points.some(
            (point) => parseScore(point.yMin) === null || parseScore(point.yMax) === null
          );
          if (needsRanges) {
            applyIndexRanges(layer.points);
          }
        }
        thead.innerHTML =
          '<tr><th>Field value (X)</th><th>Index value (Y)</th>' +
          (isCategorical ? '<th>Description</th>' : '') +
          '<th>Insert</th><th>X</th></tr>';
        const tbody = document.createElement('tbody');

        layer.points.forEach((point, index) => {
          const row = document.createElement('tr');
          const xCell = document.createElement('td');
          const xInput = document.createElement('input');
          xInput.type = isCategorical ? 'text' : 'number';
          xInput.step = isCategorical ? undefined : 'any';
          xInput.value = point.x ?? '';
          xInput.addEventListener('input', () => {
            point.x = xInput.value;
            renderCurveChart();
          });
          xCell.appendChild(xInput);

          const yCell = document.createElement('td');
          if (useRange) {
            const rangeWrap = document.createElement('div');
            rangeWrap.className = 'curve-index-range';
            const maxInput = document.createElement('input');
            maxInput.type = 'number';
            maxInput.step = '0.01';
            maxInput.min = '0';
            maxInput.max = '1';
            maxInput.placeholder = 'Max';
            maxInput.value = point.yMax ?? point.y ?? '';
            const minInput = document.createElement('input');
            minInput.type = 'number';
            minInput.step = '0.01';
            minInput.min = '0';
            minInput.max = '1';
            minInput.placeholder = 'Min';
            minInput.value = point.yMin ?? point.y ?? '';
            const syncRange = () => {
              const nextMin = parseScore(minInput.value);
              const nextMax = parseScore(maxInput.value);
              if (nextMin !== null) {
                point.yMin = roundScore(clamp(nextMin, 0, 1));
              }
              if (nextMax !== null) {
                point.yMax = roundScore(clamp(nextMax, 0, 1));
              }
              if (nextMin !== null && nextMax !== null) {
                point.y = roundScore((nextMin + nextMax) / 2);
              }
              renderCurveChart();
            };
            maxInput.addEventListener('input', syncRange);
            minInput.addEventListener('input', syncRange);
            rangeWrap.appendChild(maxInput);
            rangeWrap.appendChild(minInput);
            yCell.appendChild(rangeWrap);
          } else {
            const yInput = document.createElement('input');
            yInput.type = 'number';
            yInput.step = '0.01';
            yInput.min = '0';
            yInput.max = '1';
            yInput.value = point.y ?? '';
            yInput.addEventListener('input', () => {
              point.y = yInput.value;
              renderCurveChart();
            });
            yCell.appendChild(yInput);
          }

          let descCell = null;
          if (isCategorical) {
            descCell = document.createElement('td');
            const descInput = document.createElement('input');
            descInput.type = 'text';
            descInput.value = point.description ?? '';
            descInput.addEventListener('input', () => {
              point.description = descInput.value;
            });
            descCell.appendChild(descInput);
          }

          const insertCell = document.createElement('td');
          const insertBtn = document.createElement('button');
          insertBtn.type = 'button';
          insertBtn.className = 'btn btn-small';
          insertBtn.textContent = 'Insert';
          insertBtn.addEventListener('click', () => {
            layer.points.splice(index, 0, { x: '', y: '', description: '' });
            renderCurveTable();
            renderCurveChart();
          });
          insertCell.appendChild(insertBtn);

          const removeCell = document.createElement('td');
          const removeBtn = document.createElement('button');
          removeBtn.type = 'button';
          removeBtn.className = 'criteria-toggle criteria-remove';
          removeBtn.textContent = 'X';
          removeBtn.addEventListener('click', () => {
            if (layer.points.length <= 2) {
              return;
            }
            layer.points.splice(index, 1);
            renderCurveTable();
            renderCurveChart();
          });
          removeCell.appendChild(removeBtn);

          row.appendChild(xCell);
          row.appendChild(yCell);
          if (descCell) {
            row.appendChild(descCell);
          }
          row.appendChild(insertCell);
          row.appendChild(removeCell);
          tbody.appendChild(row);
        });

        table.appendChild(thead);
        table.appendChild(tbody);
        curveTableWrap.innerHTML = '';
        curveTableWrap.appendChild(table);
      };

      const renderCurveChart = () => {
        if (!curveChart) {
          return;
        }
        const curve = getActiveCurve();
        const ctx = curveChart.getContext('2d');
        if (!ctx || !curve) {
          return;
        }
        ctx.clearRect(0, 0, curveChart.width, curveChart.height);

        const padding = { top: 30, right: 30, bottom: 50, left: 50 };
        const width = curveChart.width - padding.left - padding.right;
        const height = curveChart.height - padding.top - padding.bottom;

        ctx.strokeStyle = '#333';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, padding.top);
        ctx.lineTo(padding.left, padding.top + height);
        ctx.lineTo(padding.left + width, padding.top + height);
        ctx.stroke();

        const layer = getCurveLayer(curve);
        if (!layer) {
          return;
        }
        const curveType = normalizeCurveType(curve.xType);
        const isCategorical = curveType === 'categorical';
        const useRange = isCategorical && !!curve.indexRange;
        if (useRange) {
          const needsRanges = layer.points.some(
            (point) => parseScore(point.yMin) === null || parseScore(point.yMax) === null
          );
          if (needsRanges) {
            applyIndexRanges(layer.points);
          }
        }
        const points = isCategorical
          ? layer.points
              .map((point, index) => {
                const y = parseScore(point.y);
                const yMin = useRange ? parseScore(point.yMin) : y;
                const yMax = useRange ? parseScore(point.yMax) : y;
                const min = yMin ?? y ?? 0;
                const max = yMax ?? y ?? min;
                const mid = y ?? (min + max) / 2;
                return {
                  x: index,
                  y: mid,
                  yMin: min,
                  yMax: max,
                  label: point.x ?? '',
                };
              })
              .filter((point) => Number.isFinite(point.y))
          : layer.points
              .map((point) => ({
                x: Number.parseFloat(point.x),
                y: Number.parseFloat(point.y),
              }))
              .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y))
              .sort((a, b) => a.x - b.x);

        const minX = points.length ? points[0].x : 0;
        const maxX = points.length ? points[points.length - 1].x : 1;
        const domainX = maxX - minX || 1;
        const scaleX = (value) => padding.left + ((value - minX) / domainX) * width;
        const scaleY = (value) => padding.top + (1 - clamp(value, 0, 1)) * height;

        if (isCategorical) {
          const bandWidth = points.length ? width / points.length : width;
          const boxWidth = Math.min(40, bandWidth * 0.6);
          points.forEach((point) => {
            const x = scaleX(point.x);
            const top = scaleY(point.yMax);
            const bottom = scaleY(point.yMin);
            const boxHeight = Math.max(2, bottom - top);
            ctx.fillStyle = '#bfdbfe';
            ctx.strokeStyle = '#2563eb';
            ctx.lineWidth = 1;
            ctx.fillRect(x - boxWidth / 2, top, boxWidth, boxHeight);
            ctx.strokeRect(x - boxWidth / 2, top, boxWidth, boxHeight);
            ctx.strokeStyle = '#111111';
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(x - boxWidth / 2, scaleY(point.y));
            ctx.lineTo(x + boxWidth / 2, scaleY(point.y));
            ctx.stroke();
          });
        } else if (points.length > 1) {
          ctx.strokeStyle = '#3b82f6';
          ctx.lineWidth = 2;
          ctx.beginPath();
          points.forEach((point, index) => {
            const x = scaleX(point.x);
            const y = scaleY(point.y);
            if (index === 0) {
              ctx.moveTo(x, y);
            } else {
              ctx.lineTo(x, y);
            }
          });
          ctx.stroke();

          ctx.fillStyle = '#1d4ed8';
          points.forEach((point) => {
            const x = scaleX(point.x);
            const y = scaleY(point.y);
            ctx.beginPath();
            ctx.arc(x, y, 4, 0, Math.PI * 2);
            ctx.fill();
          });
        }

        const xTicks = isCategorical
          ? points.map((point) => point.x)
          : points.length
          ? [minX, (minX + maxX) / 2, maxX]
          : [0, 0.5, 1];
        const yTicks = [0, 0.5, 1];

        ctx.fillStyle = '#111';
        ctx.font = '12px sans-serif';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        yTicks.forEach((tick) => {
          const y = scaleY(tick);
          ctx.beginPath();
          ctx.moveTo(padding.left - 4, y);
          ctx.lineTo(padding.left, y);
          ctx.stroke();
          ctx.fillText(tick.toFixed(2), padding.left - 6, y);
        });

        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        xTicks.forEach((tick) => {
          const x = scaleX(tick);
          ctx.beginPath();
          ctx.moveTo(x, padding.top + height);
          ctx.lineTo(x, padding.top + height + 4);
          ctx.stroke();
          if (isCategorical) {
            const label = points.find((point) => point.x === tick)?.label || '';
            ctx.fillText(label, x, padding.top + height + 8);
          } else {
            ctx.fillText(tick.toFixed(2), x, padding.top + height + 8);
          }
        });

        const metricName = curveMetricName ? curveMetricName.value : 'Metric';
        const units = curve.units ? ` (${curve.units})` : '';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(
          `${metricName}${units}`,
          padding.left + width / 2,
          padding.top + height + 28
        );
        ctx.save();
        ctx.translate(16, padding.top + height / 2);
        ctx.rotate(-Math.PI / 2);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText('Metric index score (0-1)', 0, 0);
        ctx.restore();
      };

      if (curveUnitsInput) {
        curveUnitsInput.addEventListener('input', () => {
          const curve = getActiveCurve();
          if (!curve) {
            return;
          }
          curve.units = curveUnitsInput.value;
          renderCurveChart();
        });
      }

      if (curveXType) {
        curveXType.addEventListener('change', () => {
          const curve = getActiveCurve();
          if (!curve) {
            return;
          }
          curve.xType = normalizeCurveType(curveXType.value);
          const isCategorical = curve.xType === 'categorical';
          if (isCategorical && curve.indexRange == null) {
            curve.indexRange = true;
          }
          if (!isCategorical) {
            curve.indexRange = false;
          }
          renderCurveTable();
          renderCurveChart();
        });
      }

      if (curveLayerSelect) {
        curveLayerSelect.addEventListener('change', () => {
          const curve = getActiveCurve();
          if (!curve) {
            return;
          }
          curve.activeLayerId = curveLayerSelect.value;
          renderCurveLayerControls(curve);
          renderCurveTable();
          renderCurveChart();
        });
      }

      if (curveLayerAdd) {
        curveLayerAdd.addEventListener('click', () => {
          const curve = getActiveCurve();
          if (!curve) {
            return;
          }
          const newLayer = {
            id: generateId(),
            name: `Stratification layer ${curve.layers.length + 1}`,
            points: defaultIndexValues.map((value) => ({
              x: '',
              y: value,
              description: '',
            })),
          };
          curve.layers.push(newLayer);
          curve.activeLayerId = newLayer.id;
          renderCurveLayerControls(curve);
          renderCurveTable();
          renderCurveChart();
        });
      }

      if (curveLayerRemove) {
        curveLayerRemove.addEventListener('click', () => {
          const curve = getActiveCurve();
          if (!curve || curve.layers.length <= 1) {
            return;
          }
          const activeLayer = getCurveLayer(curve);
          const removeIndex = activeLayer
            ? curve.layers.findIndex((layer) => layer.id === activeLayer.id)
            : -1;
          if (removeIndex >= 0) {
            curve.layers.splice(removeIndex, 1);
          }
          curve.activeLayerId = curve.layers[0]?.id || null;
          renderCurveLayerControls(curve);
          renderCurveTable();
          renderCurveChart();
        });
      }

      if (curveLayerName) {
        curveLayerName.addEventListener('input', () => {
          const curve = getActiveCurve();
          const activeLayer = getCurveLayer(curve);
          if (!curve || !activeLayer) {
            return;
          }
          activeLayer.name = curveLayerName.value;
          renderCurveLayerControls(curve);
        });
      }

      if (curveReset) {
        curveReset.addEventListener('click', () => {
          const curve = getActiveCurve();
          const activeLayer = getCurveLayer(curve);
          if (!curve || !activeLayer) {
            return;
          }
          activeLayer.points = defaultIndexValues.map((value) => ({
            x: '',
            y: value,
            description: '',
          }));
          renderCurveTable();
          renderCurveChart();
        });
      }

      const renderTable = () => {
        tbody.innerHTML = '';
        const scenario = getActiveScenario();
        if (!scenario) {
          tfoot.innerHTML = '';
          if (search) {
            search.disabled = true;
          }
          if (disciplineFilter) {
            disciplineFilter.disabled = true;
          }
          if (addSelect) {
            addSelect.disabled = true;
          }
          if (addButton) {
            addButton.disabled = true;
          }
          const emptyRow = document.createElement('tr');
          emptyRow.className = 'empty-row';
          const emptyCell = document.createElement('td');
          emptyCell.colSpan = 8;
          emptyCell.textContent = 'No detailed assessments yet. Click + to add one.';
          emptyRow.appendChild(emptyCell);
          tbody.appendChild(emptyRow);
          return;
        }
        if (search) {
          search.disabled = false;
        }
        if (disciplineFilter) {
          disciplineFilter.disabled = false;
        }
        if (addSelect) {
          addSelect.disabled = false;
        }
        if (addButton) {
          addButton.disabled = false;
        }

        buildSummary();
        const term = search.value.trim().toLowerCase();
        const disciplineValue = disciplineFilter.value;
        const selectedMetricIds = new Set(scenario.metricIds || []);

        buildAddOptions(selectedMetricIds);

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

        const renderRows = [];
        functionsList.forEach((fn) => {
          const discipline = fn.category || '';
          if (disciplineValue !== 'all' && disciplineValue !== discipline) {
            return;
          }
          const fnMetrics = metricsByFunction.get(fn.id) || [];
          const filteredMetrics = fnMetrics.filter((metric) => {
            if (!term) {
              return true;
            }
            return (
              metric.metric.toLowerCase().includes(term) ||
              metric.functionName.toLowerCase().includes(term)
            );
          });
          if (filteredMetrics.length) {
            filteredMetrics.forEach((metric) => {
              renderRows.push({
                type: 'metric',
                discipline,
                functionId: fn.id,
                functionName: fn.name,
                metric,
              });
              if (expandedMetrics.has(metric.id)) {
                renderRows.push({
                  type: 'details',
                  discipline,
                  functionId: fn.id,
                  functionName: fn.name,
                  metric,
                });
              }
            });
          } else if (!fnMetrics.length && (!term || fn.name.toLowerCase().includes(term))) {
            renderRows.push({
              type: 'placeholder',
              discipline,
              functionId: fn.id,
              functionName: fn.name,
            });
          }
        });

        const functionScores = new Map();
        functionsList.forEach((fn) => {
          const fnMetrics = metricsByFunction.get(fn.id) || [];
          const scores = fnMetrics
            .map((metric) => {
              const value = Number.parseFloat(scenario.fieldValues[metric.id]);
              const curve = ensureCurve(scenario, metric.id);
              return computeIndexScore(curve, value);
            })
            .filter((score) => Number.isFinite(score));
          const avg = scores.length
            ? scores.reduce((sum, value) => sum + value, 0) / scores.length
            : 0;
          functionScores.set(fn.id, avg * 15);
        });

        for (let i = 0; i < renderRows.length; i += 1) {
          const row = renderRows[i];
          if (row._disciplineSkip) {
            continue;
          }
          let span = 1;
          for (let j = i + 1; j < renderRows.length; j += 1) {
            if (renderRows[j].discipline !== row.discipline) {
              break;
            }
            span += 1;
            renderRows[j]._disciplineSkip = true;
          }
          row._disciplineSpan = span;
        }

        for (let i = 0; i < renderRows.length; i += 1) {
          const row = renderRows[i];
          if (row._functionSkip) {
            continue;
          }
          let span = 1;
          for (let j = i + 1; j < renderRows.length; j += 1) {
            if (renderRows[j].functionId !== row.functionId) {
              break;
            }
            span += 1;
            renderRows[j]._functionSkip = true;
          }
          row._functionSpan = span;
        }

        renderRows.forEach((row) => {
          const tr = document.createElement('tr');
          tr.classList.add(slugCategory(row.discipline));
          if (row.type === 'placeholder') {
            tr.classList.add('is-empty');
          }

          if (!row._disciplineSkip) {
            const disciplineCell = document.createElement('td');
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = row._disciplineSpan;
            disciplineCell.textContent = row.discipline;
            tr.appendChild(disciplineCell);
          }

          if (!row._functionSkip) {
            const functionCell = document.createElement('td');
            functionCell.className = 'function-cell col-function';
            functionCell.rowSpan = row._functionSpan;
            const functionName = document.createElement('div');
            functionName.className = 'function-name';
            functionName.textContent = row.functionName;
            functionCell.appendChild(functionName);
            const score = functionScores.get(row.functionId) ?? 0;
            const scoreWrap = document.createElement('div');
            scoreWrap.className = 'function-score-wrap';
            const range = document.createElement('input');
            range.type = 'range';
            range.min = '0';
            range.max = '15';
            range.step = '1';
            range.value = Math.round(score).toString();
            range.disabled = true;
            const scoreValue = document.createElement('span');
            scoreValue.textContent = score.toFixed(2);
            scoreWrap.appendChild(range);
            scoreWrap.appendChild(scoreValue);
            functionCell.appendChild(scoreWrap);
            tr.appendChild(functionCell);
          }

          if (row.type === 'details') {
            tr.classList.add('criteria-row');
            const detailsCell = document.createElement('td');
            detailsCell.className = 'criteria-details-cell';
            detailsCell.colSpan = 6;
            const details = document.createElement('div');
            details.className = 'criteria-details';

            const curveRow = document.createElement('div');
            curveRow.className = 'reference-curve-row';
            const curveLabel = document.createElement('span');
            curveLabel.textContent = 'Reference curve: Default';
            const curveButton = document.createElement('button');
            curveButton.type = 'button';
            curveButton.className = 'btn btn-small';
            curveButton.textContent = 'View/Edit Curve';
            curveButton.addEventListener('click', () => openCurveModal(row.metric.id));
            curveRow.appendChild(curveLabel);
            curveRow.appendChild(curveButton);
            details.appendChild(curveRow);

            const metaBlock = document.createElement('div');
            metaBlock.className = 'criteria-block';
            metaBlock.innerHTML = `<strong>Context/Method</strong><div>Context: ${
              row.metric.context || '-'
            }</div><div>Method: ${row.metric.method || '-'}</div>`;
            details.appendChild(metaBlock);

            const measureBlock = document.createElement('div');
            measureBlock.className = 'criteria-block';
            measureBlock.innerHTML = `<strong>How to measure</strong><div>${
              row.metric.howToMeasure || '-'
            }</div>`;
            details.appendChild(measureBlock);

            const refBlock = document.createElement('div');
            refBlock.className = 'criteria-block';
            refBlock.innerHTML = `<strong>References</strong><div>${
              row.metric.references || '-'
            }</div>`;
            details.appendChild(refBlock);

            detailsCell.appendChild(details);
            tr.appendChild(detailsCell);
          } else {
            const metricCell = document.createElement('td');
            metricCell.className = 'metric-cell col-metric';
            if (row.type === 'placeholder') {
              metricCell.textContent = 'No metrics selected for this function';
              tr.appendChild(metricCell);
            } else {
              const metricText = document.createElement('span');
              metricText.textContent = row.metric.metric;
              const metricInline = document.createElement('div');
              metricInline.className = 'metric-inline';
              metricInline.appendChild(metricText);
              const toggleBtn = document.createElement('button');
              toggleBtn.type = 'button';
              toggleBtn.className = 'criteria-toggle';
              toggleBtn.innerHTML = '&#9662;';
              toggleBtn.addEventListener('click', () => {
                if (expandedMetrics.has(row.metric.id)) {
                  expandedMetrics.delete(row.metric.id);
                } else {
                  expandedMetrics.add(row.metric.id);
                }
                renderTable();
              });
              metricInline.appendChild(toggleBtn);

              const removeBtn = document.createElement('button');
              removeBtn.type = 'button';
              removeBtn.className = 'criteria-toggle criteria-remove';
              removeBtn.textContent = 'X';
              removeBtn.addEventListener('click', () => {
                scenario.metricIds = scenario.metricIds.filter((id) => id !== row.metric.id);
                delete scenario.fieldValues[row.metric.id];
                renderTable();
              });
              metricInline.appendChild(removeBtn);
              metricCell.appendChild(metricInline);
              tr.appendChild(metricCell);
            }

            const fieldCell = document.createElement('td');
            fieldCell.className = 'field-cell col-field';
            if (row.type === 'metric') {
              const fieldInput = document.createElement('input');
              fieldInput.type = 'number';
              fieldInput.step = 'any';
              fieldInput.value = scenario.fieldValues[row.metric.id] || '';
              fieldInput.addEventListener('input', () => {
                scenario.fieldValues[row.metric.id] = fieldInput.value;
              });
              fieldInput.addEventListener('change', renderTable);
              fieldInput.addEventListener('blur', renderTable);
              fieldInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  fieldInput.blur();
                }
              });
              fieldCell.appendChild(fieldInput);
            } else {
              fieldCell.textContent = '-';
            }
            tr.appendChild(fieldCell);

            const indexCell = document.createElement('td');
            indexCell.className = 'index-cell col-index';
            if (row.type === 'metric') {
              const curve = ensureCurve(scenario, row.metric.id);
              const fieldValue = Number.parseFloat(scenario.fieldValues[row.metric.id]);
              const indexScore = computeIndexScore(curve, fieldValue);
              indexCell.textContent = Number.isFinite(indexScore)
                ? indexScore.toFixed(2)
                : '-';
            } else {
              indexCell.textContent = '-';
            }
            tr.appendChild(indexCell);

            const mapping =
              mappingById[row.functionId] || {
                physical: '-',
                chemical: '-',
                biological: '-',
              };
            const physicalCell = document.createElement('td');
            const chemicalCell = document.createElement('td');
            const biologicalCell = document.createElement('td');
            physicalCell.className = 'weight-cell col-physical physical-cell';
            chemicalCell.className = 'weight-cell col-chemical chemical-cell';
            biologicalCell.className = 'weight-cell col-biological biological-cell';
            physicalCell.textContent = mapping.physical || '-';
            chemicalCell.textContent = mapping.chemical || '-';
            biologicalCell.textContent = mapping.biological || '-';
            tr.appendChild(physicalCell);
            tr.appendChild(chemicalCell);
            tr.appendChild(biologicalCell);
          }

          tbody.appendChild(tr);
        });

        updateSummary(functionScores);
      };

      addButton.addEventListener('click', () => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        const value = addSelect.value;
        if (!value) {
          return;
        }
        if (!scenario.metricIds.includes(value)) {
          scenario.metricIds.push(value);
        }
        if (!scenario.metricProfiles) {
          scenario.metricProfiles = {};
        }
        scenario.metricProfiles[value] = defaultProfileId;
        ensureCurve(scenario, value);
        addSelect.value = '';
        renderTable();
      });

      search.addEventListener('input', renderTable);
      disciplineFilter.addEventListener('change', renderTable);

      const addMetricFromLibrary = ({ metricId, profileId, detail }) => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        const metric =
          detail && detail.metricId ? ensureLibraryMetric(detail) : metricById.get(metricId);
        if (!metric) {
          return;
        }
        if (!scenario.metricIds.includes(metric.id)) {
          scenario.metricIds.push(metric.id);
        }
        if (!scenario.metricProfiles) {
          scenario.metricProfiles = {};
        }
        scenario.metricProfiles[metric.id] = profileId || defaultProfileId;
        ensureCurve(scenario, metric.id);
        if (disciplineFilter && metric.discipline) {
          const exists = Array.from(disciplineFilter.options).some(
            (option) => option.value === metric.discipline
          );
          if (!exists) {
            const option = document.createElement('option');
            option.value = metric.discipline;
            option.textContent = metric.discipline;
            disciplineFilter.appendChild(option);
          }
        }
        renderTable();
      };

      const removeMetricFromLibrary = ({ metricId }) => {
        const scenario = getActiveScenario();
        if (!scenario || !metricId) {
          return;
        }
        scenario.metricIds = scenario.metricIds.filter((id) => id !== metricId);
        delete scenario.fieldValues[metricId];
        if (scenario.curves) {
          delete scenario.curves[metricId];
        }
        if (scenario.metricProfiles) {
          delete scenario.metricProfiles[metricId];
        }
        renderTable();
      };

      const isMetricAdded = (metricId, profileId) => {
        const scenario = getActiveScenario();
        if (!scenario || !metricId) {
          return false;
        }
        if (!scenario.metricIds.includes(metricId)) {
          return false;
        }
        if (!profileId) {
          return true;
        }
        return scenario.metricProfiles && scenario.metricProfiles[metricId] === profileId;
      };

      const renderAll = () => {
        renderTabs();
        renderSettings();
        renderTable();
      };

      if (window.STAFAssessmentRegistry) {
        window.STAFAssessmentRegistry.register('detailed', {
          addMetric: addMetricFromLibrary,
          removeMetric: removeMetricFromLibrary,
          isMetricAdded,
          getCurve(metricId) {
            const scenario = getActiveScenario();
            if (!scenario) {
              return null;
            }
            return ensureCurve(scenario, metricId);
          },
          setCurve(metricId, curve) {
            const scenario = getActiveScenario();
            if (!scenario) {
              return;
            }
            if (!scenario.curves) {
              scenario.curves = {};
            }
            scenario.curves[metricId] = curve;
            renderTable();
          },
          refresh() {
            renderTable();
          },
        });
      }

      renderAll();
    } catch (error) {
      if (ui) {
        ui.textContent = 'Detailed assessment widget failed to load.';
        ui.hidden = false;
      }
    }
  };

  init();
})();

