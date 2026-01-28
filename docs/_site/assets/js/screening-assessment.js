(() => {
  const container = document.querySelector('.screening-assessment');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const assetPath = (path) => `${baseUrl}${path}`;
  const dataUrl =
    container.dataset.metricsUrl || assetPath('/assets/data/screening-metrics.tsv');
  const functionsUrl =
    container.dataset.functionsUrl || assetPath('/assets/data/functions.json');
  const mappingUrl =
    container.dataset.mappingUrl || assetPath('/assets/data/cwa-mapping.json');
  const fallback = container.querySelector('.screening-assessment-fallback');
  const loading = container.querySelector('.screening-assessment-loading');
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

  const stripBaseUrl = (url) =>
    baseUrl && url.startsWith(baseUrl) ? url.slice(baseUrl.length) || '/' : url;

  const fetchWithFallback = async (url, parser) => {
    const fallbackUrl = stripBaseUrl(url);
    try {
      const response = await fetch(url);
      if (response.ok) {
        return parser(response);
      }
    } catch (error) {
      // fall through to fallback
    }
    if (fallbackUrl && fallbackUrl !== url) {
      const response = await fetch(fallbackUrl);
      if (response.ok) {
        return parser(response);
      }
    }
    const response = await fetch(url);
    throw new Error(`Failed to load ${url} (${response.status})`);
  };

  const fetchText = (url) => fetchWithFallback(url, (response) => response.text());
  const fetchJson = (url) => fetchWithFallback(url, (response) => response.json());

  const init = async () => {
    try {
      if (fallback) {
        fallback.hidden = true;
      }
      if (loading) {
        loading.remove();
      }
      if (ui) {
        ui.hidden = false;
      }
      const [metricsResult, functionsResult, mappingResult] = await Promise.allSettled([
        fetchText(dataUrl),
        fetchJson(functionsUrl),
        fetchJson(mappingUrl),
      ]);

      if (metricsResult.status !== 'fulfilled') {
        throw new Error('Failed to load screening metrics.');
      }
      const metricsText = metricsResult.value;
      const functionsList =
        functionsResult.status === 'fulfilled' && Array.isArray(functionsResult.value)
          ? functionsResult.value
          : [];
      const mappingList =
        mappingResult.status === 'fulfilled' && Array.isArray(mappingResult.value)
          ? mappingResult.value
          : [];

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
          metricStatement:
            row['Metric statement'] ||
            row['Metric statements'] ||
            row.metric_statement ||
            row.metricStatement ||
            '',
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

      const metricById = new Map(metrics.map((metric) => [metric.id, metric]));
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

      const defaultCurveIndexValues = [0, 0.3, 0.7, 1];

      const buildDefaultCurve = (metric) => {
        const points = [
          {
            x: 'Optimal',
            y: 1,
            description: metric?.criteria?.optimal || '',
          },
          {
            x: 'Suboptimal',
            y: 0.7,
            description: metric?.criteria?.suboptimal || '',
          },
          {
            x: 'Marginal',
            y: 0.3,
            description: metric?.criteria?.marginal || '',
          },
          {
            x: 'Poor',
            y: 0,
            description: metric?.criteria?.poor || '',
          },
        ];
        const layerId = generateId();
        return {
          name: 'Default',
          xType: 'qualitative',
          units: '',
          layers: [
            {
              id: layerId,
              name: 'Default',
              points,
            },
          ],
          activeLayerId: layerId,
        };
      };

      const cloneCurve = (curve) => JSON.parse(JSON.stringify(curve));

      const buildCurveMap = (metricIds) => {
        const curves = {};
        metricIds.forEach((id) => {
          const metric = metricById.get(id);
          if (metric) {
            curves[id] = buildDefaultCurve(metric);
          }
        });
        return curves;
      };

      const ensureCurve = (scenario, metricId) => {
        if (!scenario) {
          return null;
        }
        if (!scenario.curves) {
          scenario.curves = {};
        }
        if (!scenario.curves[metricId]) {
          const metric = metricById.get(metricId);
          if (metric) {
            scenario.curves[metricId] = buildDefaultCurve(metric);
          }
        }
        return scenario.curves[metricId] || null;
      };

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
          curves: buildCurveMap(ids),
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
          curves: source.curves ? cloneCurve(source.curves) : buildCurveMap(metricIds),
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
          curves: scenario.curves ? cloneCurve(scenario.curves) : buildCurveMap(ids),
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
      const curveModal = ui.querySelector('.detailed-curve-modal');
      const curveBackdrop = ui.querySelector('.detailed-curve-backdrop');
      const curveClose = ui.querySelector('.detailed-curve-close');
      const curveMetricName = ui.querySelector('.curve-metric-name');
      const curveUnitsInput = ui.querySelector('.curve-units');
      const curveXType = ui.querySelector('.curve-x-type');
      const curveLayerSelect = ui.querySelector('.curve-layer-select');
      const curveLayerAdd = ui.querySelector('.curve-layer-add');
      const curveLayerRemove = ui.querySelector('.curve-layer-remove');
      const curveLayerName = ui.querySelector('.curve-layer-name');
      const curveReset = ui.querySelector('.curve-reset');
      const curveTableWrap = ui.querySelector('.curve-table-wrap');
      const curveChart = ui.querySelector('.curve-chart');

      let selectedMetricIds = new Set();
      let metricRatings = new Map();
      const expandedMetrics = new Set();
      let activeScenario = null;
      let librarySearchTerm = '';
      let libraryDiscipline = 'all';
      const libraryCollapse = {
        disciplines: new Map(),
        functions: new Map(),
      };
      let activeCurveMetricId = null;
      let curveReadOnly = false;

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

      const getActiveCurve = () => {
        if (!activeScenario || !activeCurveMetricId) {
          return null;
        }
        return ensureCurve(activeScenario, activeCurveMetricId);
      };

      const renderCurveLayerControls = (curve) => {
        if (!curveLayerSelect || !curve) {
          return;
        }
        curveLayerSelect.innerHTML = '';
        curve.layers.forEach((layer) => {
          const option = document.createElement('option');
          option.value = layer.id;
          option.textContent = layer.name;
          curveLayerSelect.appendChild(option);
        });
        const activeLayer = getCurveLayer(curve);
        if (activeLayer) {
          curveLayerSelect.value = activeLayer.id;
          if (curveLayerName) {
            curveLayerName.value = activeLayer.name || '';
          }
        }
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
        const isQualitative = curve.xType === 'qualitative';
        const table = document.createElement('table');
        table.className = 'curve-table';
        const thead = document.createElement('thead');
        thead.innerHTML =
          '<tr><th>Field value (X)</th><th>Index value (Y)</th>' +
          (isQualitative ? '<th>Description</th>' : '') +
          '<th>Insert</th><th>X</th></tr>';
        const tbody = document.createElement('tbody');

        layer.points.forEach((point, index) => {
          const row = document.createElement('tr');
          const xCell = document.createElement('td');
          const xInput = document.createElement('input');
          xInput.type = isQualitative ? 'text' : 'number';
          xInput.step = isQualitative ? undefined : 'any';
          xInput.value = point.x ?? '';
          xInput.disabled = curveReadOnly;
          xInput.addEventListener('input', () => {
            if (curveReadOnly) {
              return;
            }
            point.x = xInput.value;
            renderCurveChart();
          });
          xCell.appendChild(xInput);

          const yCell = document.createElement('td');
          const yInput = document.createElement('input');
          yInput.type = 'number';
          yInput.step = '0.01';
          yInput.min = '0';
          yInput.max = '1';
          yInput.value = point.y ?? '';
          yInput.disabled = curveReadOnly;
          yInput.addEventListener('input', () => {
            if (curveReadOnly) {
              return;
            }
            point.y = yInput.value;
            renderCurveChart();
          });
          yCell.appendChild(yInput);

          let descCell = null;
          if (isQualitative) {
            descCell = document.createElement('td');
            const descInput = document.createElement('input');
            descInput.type = 'text';
            descInput.value = point.description ?? '';
            descInput.disabled = curveReadOnly;
            descInput.addEventListener('input', () => {
              if (curveReadOnly) {
                return;
              }
              point.description = descInput.value;
            });
            descCell.appendChild(descInput);
          }

          const insertCell = document.createElement('td');
          const insertBtn = document.createElement('button');
          insertBtn.type = 'button';
          insertBtn.className = 'btn btn-small';
          insertBtn.textContent = 'Insert';
          insertBtn.disabled = curveReadOnly;
          insertBtn.addEventListener('click', () => {
            if (curveReadOnly) {
              return;
            }
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
          removeBtn.disabled = curveReadOnly;
          removeBtn.addEventListener('click', () => {
            if (curveReadOnly || layer.points.length <= 2) {
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
        const isQualitative = curve.xType === 'qualitative';
        const points = isQualitative
          ? layer.points
              .map((point, index) => ({
                x: index,
                y: Number.parseFloat(point.y),
                label: point.x ?? '',
              }))
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
        const scaleY = (value) => padding.top + (1 - Math.min(1, Math.max(0, value))) * height;

        if (points.length > 1) {
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

        const xTicks = isQualitative
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
          if (isQualitative) {
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

      const buildCurveSummaryTable = (curve) => {
        const table = document.createElement('table');
        table.className = 'curve-summary-table';
        const layer = getCurveLayer(curve);
        if (!layer || !layer.points.length) {
          const empty = document.createElement('caption');
          empty.textContent = 'No reference curve points defined.';
          table.appendChild(empty);
          return table;
        }
        const valueRow = document.createElement('tr');
        const valueLabel = document.createElement('th');
        valueLabel.textContent = 'Value';
        valueRow.appendChild(valueLabel);
        layer.points.forEach((point) => {
          const cell = document.createElement('td');
          const value = document.createElement('div');
          value.className = 'curve-point-value';
          value.textContent = point.x ?? '-';
          cell.appendChild(value);
          if (point.description) {
            const desc = document.createElement('div');
            desc.className = 'curve-point-desc';
            desc.textContent = point.description;
            cell.appendChild(desc);
          }
          valueRow.appendChild(cell);
        });

        const indexRow = document.createElement('tr');
        const indexLabel = document.createElement('th');
        indexLabel.textContent = 'Index';
        indexRow.appendChild(indexLabel);
        layer.points.forEach((point) => {
          const cell = document.createElement('td');
          const value = Number.parseFloat(point.y);
          cell.textContent = Number.isFinite(value) ? value.toFixed(2) : '-';
          indexRow.appendChild(cell);
        });

        table.appendChild(valueRow);
        table.appendChild(indexRow);
        return table;
      };

      const openCurveModal = (metricId) => {
        if (!curveModal) {
          return;
        }
        const metric = metricById.get(metricId);
        activeCurveMetricId = metricId;
        curveReadOnly = activeScenario?.type === 'predefined';
        const curve = ensureCurve(activeScenario, metricId);
        if (curveMetricName && metric) {
          curveMetricName.value = metric.metric;
        }
        if (curveUnitsInput) {
          curveUnitsInput.value = curve.units || '';
          curveUnitsInput.disabled = curveReadOnly;
        }
        if (curveXType) {
          curveXType.value = curve.xType || 'qualitative';
          curveXType.disabled = curveReadOnly;
        }
        if (curveLayerAdd) {
          curveLayerAdd.disabled = curveReadOnly;
        }
        if (curveLayerRemove) {
          curveLayerRemove.disabled = curveReadOnly;
        }
        if (curveLayerName) {
          curveLayerName.disabled = curveReadOnly;
        }
        if (curveReset) {
          curveReset.disabled = curveReadOnly;
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
        curveReadOnly = false;
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

      if (curveUnitsInput) {
        curveUnitsInput.addEventListener('input', () => {
          if (curveReadOnly) {
            return;
          }
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
          if (curveReadOnly) {
            return;
          }
          const curve = getActiveCurve();
          if (!curve) {
            return;
          }
          curve.xType = curveXType.value;
          renderCurveTable();
          renderCurveChart();
        });
      }

      if (curveLayerSelect) {
        curveLayerSelect.addEventListener('change', () => {
          if (curveReadOnly) {
            return;
          }
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
          if (curveReadOnly) {
            return;
          }
          const curve = getActiveCurve();
          if (!curve) {
            return;
          }
          const newLayer = {
            id: generateId(),
            name: `Stratification layer ${curve.layers.length + 1}`,
            points: defaultCurveIndexValues.map((value) => ({
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
          if (curveReadOnly) {
            return;
          }
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
            curve.activeLayerId = curve.layers[0].id;
            renderCurveLayerControls(curve);
            renderCurveTable();
            renderCurveChart();
          }
        });
      }

      if (curveLayerName) {
        curveLayerName.addEventListener('input', () => {
          if (curveReadOnly) {
            return;
          }
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
          if (curveReadOnly) {
            return;
          }
          const curve = getActiveCurve();
          const activeLayer = getCurveLayer(curve);
          if (!curve || !activeLayer) {
            return;
          }
          activeLayer.points = defaultCurveIndexValues.map((value) => ({
            x: '',
            y: value,
            description: '',
          }));
          renderCurveTable();
          renderCurveChart();
        });
      }

      const renderLibraryTable = () => {
        if (!libraryTableWrap || !libraryModal || libraryModal.hidden) {
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
        libraryModal.hidden = false;
        document.body.classList.add('modal-open');
        renderLibraryTable();
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

        const renderRows = [];
        rowsToRender.forEach((rowItem) => {
          renderRows.push(rowItem);
          if (rowItem.type === 'metric' && expandedMetrics.has(rowItem.metric.id)) {
            renderRows.push({
              type: 'criteria',
              discipline: rowItem.discipline,
              metric: rowItem.metric,
              functionId: rowItem.functionId,
              functionMeta: rowItem.functionMeta,
            });
          }
        });

        const metricRows = [];

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

        if (renderRows.length === 0) {
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

        for (let i = 0; i < renderRows.length; i += 1) {
          const rowItem = renderRows[i];
          if (rowItem._disciplineSkip) {
            continue;
          }
          let span = 1;
          for (let j = i + 1; j < renderRows.length; j += 1) {
            if (renderRows[j].discipline !== rowItem.discipline) {
              break;
            }
            span += 1;
            renderRows[j]._disciplineSkip = true;
          }
          rowItem._disciplineSpan = span;
        }

        for (let i = 0; i < renderRows.length; i += 1) {
          const rowItem = renderRows[i];
          if (rowItem._functionSkip || rowItem.type === 'criteria') {
            continue;
          }
          let metricCount = 0;
          let criteriaCount = 0;
          let j = i;
          while (j < renderRows.length && renderRows[j].functionId === rowItem.functionId) {
            if (renderRows[j].type === 'criteria') {
              criteriaCount += 1;
            } else {
              metricCount += 1;
            }
            if (j > i) {
              renderRows[j]._functionSkip = true;
            }
            j += 1;
          }
          rowItem._functionSpan = metricCount + criteriaCount;
          const mergeWeights = criteriaCount === 0 && metricCount > 1;
          rowItem._weightSpan = mergeWeights ? metricCount : 1;
          if (mergeWeights) {
            for (let k = i + 1; k < j; k += 1) {
              if (renderRows[k].type === 'metric') {
                renderRows[k]._weightSkip = true;
              }
            }
          }
          i = j - 1;
        }

        renderRows.forEach((rowItem, index) => {
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
          row.dataset.discipline = rowItem.discipline;
          const rowFunctionId = rowItem.functionId || `row-${index}`;
          row.dataset.functionId = rowFunctionId;
          if (rowItem.type === 'placeholder') {
            row.classList.add('inactive-row');
          }
          if (rowItem.type === 'criteria') {
            row.classList.add('criteria-row');
          }

          if (!rowItem._disciplineSkip) {
            const disciplineCell = document.createElement('td');
            disciplineCell.textContent = rowItem.discipline;
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = rowItem._disciplineSpan || 1;
            if (disciplineActive.get(rowItem.discipline)) {
              disciplineCell.classList.add(
                'screening-group-active',
                slugCategory(rowItem.discipline)
              );
            }
            row.appendChild(disciplineCell);
          }
          let functionScoreValue = null;
          let functionScoreRange = null;
          if (!rowItem._functionSkip) {
            const functionCell = document.createElement('td');
            functionCell.className = 'col-function';
            functionCell.rowSpan = rowItem._functionSpan || 1;
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
            functionScoreRange = document.createElement('input');
            functionScoreRange.type = 'range';
            functionScoreRange.min = '0';
            functionScoreRange.max = '15';
            functionScoreRange.step = '1';
            functionScoreRange.disabled = true;
            functionScoreValue = document.createElement('span');
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
            row.appendChild(functionCell);
          }
          if (rowItem.type === 'criteria') {
            const detailsCell = document.createElement('td');
            detailsCell.colSpan = 5;
            const details = document.createElement('div');
            details.className = 'criteria-details';
            const detailsMetric = rowItem.metric || metric;
            if (!detailsMetric) {
              detailsCell.textContent = 'Details unavailable.';
              row.appendChild(detailsCell);
              tbody.appendChild(row);
              return;
            }
            const statementBlock = document.createElement('div');
            statementBlock.className = 'criteria-block';
            statementBlock.innerHTML = `<strong>Metric statement</strong><div>${
              detailsMetric.metricStatement || '-'
            }</div>`;
            details.appendChild(statementBlock);

            const curve = ensureCurve(activeScenario, detailsMetric.id);
            const curveRow = document.createElement('div');
            curveRow.className = 'reference-curve-row';
            const curveLabel = document.createElement('span');
            curveLabel.textContent = `Reference curve: ${curve?.name || 'Default'}`;
            const curveButton = document.createElement('button');
            curveButton.type = 'button';
            curveButton.className = 'btn btn-small';
            curveButton.textContent = 'View/Edit Curve';
            curveButton.addEventListener('click', () => openCurveModal(detailsMetric.id));
            curveRow.appendChild(curveLabel);
            curveRow.appendChild(curveButton);
            details.appendChild(curveRow);

            if (curve) {
              const summaryTable = buildCurveSummaryTable(curve);
              details.appendChild(summaryTable);
            }
            detailsCell.appendChild(details);
            row.appendChild(detailsCell);
            tbody.appendChild(row);
            return;
          }

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
          let criteriaBtn = null;
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

            criteriaBtn = document.createElement('button');
            criteriaBtn.type = 'button';
            criteriaBtn.className = 'criteria-toggle';
            criteriaBtn.innerHTML = '&#9662;';
            criteriaBtn.setAttribute(
              'aria-expanded',
              expandedMetrics.has(metric.id) ? 'true' : 'false'
            );
            criteriaBtn.setAttribute('aria-label', 'Toggle criteria details');
          } else {
            scoreCell.textContent = '-';
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
          row.appendChild(metricCell);
          row.appendChild(scoreCell);
          if (!rowItem._weightSkip && rowItem.type !== 'criteria') {
            const physicalCell = document.createElement('td');
            const chemicalCell = document.createElement('td');
            const biologicalCell = document.createElement('td');
            physicalCell.className = 'weight-cell col-physical';
            chemicalCell.className = 'weight-cell col-chemical';
            biologicalCell.className = 'weight-cell col-biological';
            physicalCell.textContent = mapping ? weightLabelFromCode(mapping.physical) : '-';
            chemicalCell.textContent = mapping ? weightLabelFromCode(mapping.chemical) : '-';
            biologicalCell.textContent = mapping ? weightLabelFromCode(mapping.biological) : '-';
            const weightSpan = rowItem._weightSpan || 1;
            physicalCell.rowSpan = weightSpan;
            chemicalCell.rowSpan = weightSpan;
            biologicalCell.rowSpan = weightSpan;
            row.appendChild(physicalCell);
            row.appendChild(chemicalCell);
            row.appendChild(biologicalCell);
          }

          if (rowItem.type === 'metric') {
            criteriaBtn.addEventListener('click', () => {
              if (expandedMetrics.has(metric.id)) {
                expandedMetrics.delete(metric.id);
              } else {
                expandedMetrics.add(metric.id);
              }
              renderTable();
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
            if (activeScenario.curves) {
              delete activeScenario.curves[metric.id];
            }
            store.save();
            buildAddOptions();
            renderTable();
          });

          tbody.appendChild(row);
          if (functionScoreValue && functionScoreRange) {
            metricRows.push({
              metric: metric || { functionId: rowItem.functionId },
              functionScoreValue,
              functionScoreRange,
            });
          }
        });

        updateScores(metricRows);
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
        if (!scenario.curves) {
          scenario.curves = {};
        }
        scenario.metricIds.forEach((id) => ensureCurve(scenario, id));
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
        ensureCurve(activeScenario, metricId);
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
        activeScenario.curves = {};
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

      requestAnimationFrame(() => {
        renderTabs();
        setTimeout(() => {
          applyScenario(store.active());
        }, 0);
      });
    } catch (error) {
      console.error('Screening assessment widget failed to load.', error);
      if (loading) {
        loading.remove();
      }
      if (ui) {
        const message =
          error && error.message
            ? `Screening assessment widget failed to load. ${error.message}`
            : 'Screening assessment widget failed to load.';
        ui.innerHTML = `<div class="widget-error">${message}</div>`;
        ui.hidden = false;
      }
    }
  };

  init();
})();
