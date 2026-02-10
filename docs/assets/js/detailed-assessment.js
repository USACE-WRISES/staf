

(() => {
  const container = document.querySelector('.detailed-assessment');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const dataUrl = `${baseUrl}/assets/data/detailed-metrics.tsv`;
  const functionsUrl = `${baseUrl}/assets/data/functions.json`;
  const mappingUrl = `${baseUrl}/assets/data/cwa-mapping.json`;
  const metricLibraryIndexUrl = `${baseUrl}/assets/data/metric-library/index.json`;
  const fallback = container.querySelector('.detailed-assessment-fallback');
  const ui = container.querySelector('.detailed-assessment-ui');
  const collapsedGlyph = '&#9656;';
  const expandedGlyph = '&#9662;';

  const normalizeText = (value) =>
    value
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '');

  const makeMetricLookupKey = (functionName, metricName) =>
    `${normalizeText(functionName || '')}|${normalizeText(metricName || '')}`;

  const slugify = (value) =>
    (value || '')
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80);

  const slugCategory = (category) =>
    `category-${category.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;

  const generateId = () => {
    if (window.crypto && window.crypto.randomUUID) {
      return window.crypto.randomUUID();
    }
    return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  };

  const parseQuotedTsv = (text) => {
    const rows = [];
    let row = [];
    let cell = '';
    let inQuotes = false;
    const source = (text || '').replace(/^\ufeff/, '');
    for (let i = 0; i < source.length; i += 1) {
      const char = source[i];
      if (char === '"') {
        const next = source[i + 1];
        if (inQuotes && next === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = !inQuotes;
        }
        continue;
      }
      if (char === '\t' && !inQuotes) {
        row.push(cell);
        cell = '';
        continue;
      }
      if ((char === '\n' || char === '\r') && !inQuotes) {
        if (char === '\r' && source[i + 1] === '\n') {
          i += 1;
        }
        row.push(cell);
        cell = '';
        if (row.some((entry) => entry.length > 0)) {
          rows.push(row);
        }
        row = [];
        continue;
      }
      cell += char;
    }
    if (cell.length || row.length) {
      row.push(cell);
      if (row.some((entry) => entry.length > 0)) {
        rows.push(row);
      }
    }
    if (!rows.length) {
      return [];
    }
    const header = rows[0].map((value) => value.trim());
    return rows.slice(1).map((cells) => {
      const parsed = {};
      header.forEach((key, index) => {
        parsed[key] = (cells[index] || '').trim();
      });
      return parsed;
    });
  };

  const compactWhitespace = (value) =>
    (value || '')
      .replace(/\r\n/g, '\n')
      .replace(/\t/g, ' ')
      .replace(/[ ]{2,}/g, ' ')
      .trim();

  const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  const stripSourceSuffixFromMetricName = (metricName, sourceCitation) => {
    const base = compactWhitespace(metricName);
    const source = compactWhitespace(sourceCitation);
    if (!base || !source) {
      return base;
    }
    const pattern = new RegExp(`\\s*\\(${escapeRegExp(source)}\\)\\s*$`, 'i');
    const stripped = base.replace(pattern, '').trim();
    return stripped || base;
  };

  const buildLayerName = (sourceCitation, stratification, fallbackIndex) => {
    const source = compactWhitespace(sourceCitation);
    const strat = compactWhitespace(stratification);
    if (source && strat) {
      return normalizeText(source) === normalizeText(strat)
        ? source
        : `${source} - ${strat}`;
    }
    if (source) {
      return source;
    }
    if (strat) {
      return strat;
    }
    return fallbackIndex === 1 ? 'Default' : `Stratification ${fallbackIndex}`;
  };

  const toBooleanOn = (value) => /^(on|yes|true|1)$/i.test((value || '').trim());

  const parseMethodContextCell = (value) => {
    const source = (value || '').replace(/\r\n/g, '\n');
    const contexts = Array.from(
      new Set(
        [...source.matchAll(/Context:\s*([^\n\r]+)/gi)]
          .map((match) => compactWhitespace(match[1] || ''))
          .filter(Boolean)
      )
    );
    const methods = Array.from(
      new Set(
        [...source.matchAll(/Method:\s*([^\n\r]+)/gi)]
          .map((match) => compactWhitespace(match[1] || ''))
          .filter(Boolean)
      )
    );
    return {
      context: contexts.join('; '),
      method: methods.join('; '),
      markdown:
        contexts.length || methods.length
          ? `Context: ${contexts.join('; ') || 'Unspecified'}\n\nMethod: ${
              methods.join('; ') || 'Unspecified'
            }`
          : compactWhitespace(source),
    };
  };

  const parseIndexNumber = (value) => {
    const parsed = Number.parseFloat((value || '').trim());
    return Number.isFinite(parsed) ? parsed : null;
  };

  const parseFieldNumber = (value) => {
    const raw = (value || '').trim();
    if (!raw) {
      return null;
    }
    const match = raw.match(/-?\d+(?:\.\d+)?/);
    if (!match) {
      return null;
    }
    const parsed = Number.parseFloat(match[0]);
    if (!Number.isFinite(parsed)) {
      return null;
    }
    // Keep strict inequalities from the source table distinct from exact cut points.
    if (raw.startsWith('>')) {
      return parsed + 0.0001;
    }
    if (raw.startsWith('<')) {
      return parsed - 0.0001;
    }
    return parsed;
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
  const defaultIndexValues = [0, 0.4, 0.7, 1];
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

  const cloneCurve = (curve) =>
    curve ? JSON.parse(JSON.stringify(curve)) : null;

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

  const resolveDetailedIndexMeta = (curve, fieldValue) => {
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
      const minIndex = parseScore(min.yMin);
      const maxIndex = parseScore(min.yMax);
      return {
        indexScore: clamp(min.y, 0, 1),
        minIndex: minIndex === null ? null : clamp(minIndex, 0, 1),
        maxIndex: maxIndex === null ? null : clamp(maxIndex, 0, 1),
      };
    }
    if (fieldValue >= max.x) {
      const minIndex = parseScore(max.yMin);
      const maxIndex = parseScore(max.yMax);
      return {
        indexScore: clamp(max.y, 0, 1),
        minIndex: minIndex === null ? null : clamp(minIndex, 0, 1),
        maxIndex: maxIndex === null ? null : clamp(maxIndex, 0, 1),
      };
    }
    for (let i = 0; i < points.length - 1; i += 1) {
      const left = points[i];
      const right = points[i + 1];
      if (fieldValue >= left.x && fieldValue <= right.x) {
        const t = (fieldValue - left.x) / (right.x - left.x);
        const y = left.y + t * (right.y - left.y);
        const leftMin = parseScore(left.yMin);
        const rightMin = parseScore(right.yMin);
        const leftMax = parseScore(left.yMax);
        const rightMax = parseScore(right.yMax);
        const minIndex =
          leftMin !== null && rightMin !== null
            ? clamp(leftMin + t * (rightMin - leftMin), 0, 1)
            : null;
        const maxIndex =
          leftMax !== null && rightMax !== null
            ? clamp(leftMax + t * (rightMax - leftMax), 0, 1)
            : null;
        return {
          indexScore: clamp(y, 0, 1),
          minIndex,
          maxIndex,
        };
      }
    }
    return null;
  };

  const computeIndexScore = (curve, fieldValue) => {
    const meta = resolveDetailedIndexMeta(curve, fieldValue);
    return meta ? meta.indexScore : null;
  };

  const init = async () => {
    try {
      const [metricsText, functionsList, mappingList, metricLibraryIndex] = await Promise.all([
        fetchText(dataUrl),
        fetchJson(functionsUrl),
        fetchJson(mappingUrl),
        fetchJson(metricLibraryIndexUrl).catch(() => ({ metrics: [] })),
      ]);

      if (fallback) {
        fallback.hidden = true;
      }
      if (ui) {
        ui.hidden = false;
      }

      const metricsRaw = parseQuotedTsv(metricsText);
      const functionByName = new Map(
        functionsList.map((fn) => [normalizeText(fn.name), fn])
      );
      const functionById = new Map(functionsList.map((fn) => [fn.id, fn]));
      const functionOrder = new Map(
        functionsList.map((fn, index) => [normalizeText(fn.name), index])
      );
      const functionAliases = new Map([
        [
          normalizeText('Bed composition and bedform dynamics'),
          normalizeText('Bed composition and large wood'),
        ],
      ]);
      const resolveFunctionKey = (name) => {
        const key = normalizeText(name || '');
        return functionAliases.get(key) || key;
      };
      const mappingById = mappingList.reduce((acc, item) => {
        acc[item.id] = item;
        return acc;
      }, {});
      const detailedMetricIdByLookup = new Map();
      const indexEntries = Array.isArray(metricLibraryIndex?.metrics)
        ? metricLibraryIndex.metrics
        : [];
      indexEntries.forEach((entry) => {
        const supportsDetailed =
          entry?.minimumTier === 'detailed' ||
          entry?.profileAvailability?.detailed === true ||
          (Array.isArray(entry?.tags) && entry.tags.includes('detailed'));
        if (!supportsDetailed || !entry?.metricId || !entry?.function || !entry?.name) {
          return;
        }
        const functionName = compactWhitespace(entry.function);
        const key = makeMetricLookupKey(functionName, compactWhitespace(entry.name));
        if (key && !detailedMetricIdByLookup.has(key)) {
          detailedMetricIdByLookup.set(key, entry.metricId);
        }
        const shortName = compactWhitespace(entry.shortName || '');
        if (shortName) {
          const shortKey = makeMetricLookupKey(functionName, shortName);
          if (shortKey && !detailedMetricIdByLookup.has(shortKey)) {
            detailedMetricIdByLookup.set(shortKey, entry.metricId);
          }
        }
      });

      const getRowValue = (row, key, fallbackKeys = []) => {
        if (row[key] !== undefined) {
          return row[key];
        }
        for (const fallback of fallbackKeys) {
          if (row[fallback] !== undefined) {
            return row[fallback];
          }
        }
        return '';
      };

      const parseRowCurvePoints = (row) => {
        const points = [];
        const usedX = new Set();
        const rawFieldValues = [];
        const rawIndexValues = [];
        for (let i = 1; i <= 7; i += 1) {
          const fieldRaw = compactWhitespace(
            getRowValue(row, `Field Value ${i}`, [`field value ${i}`])
          );
          const indexRaw = compactWhitespace(
            getRowValue(row, `Index Value ${i}`, [`index value ${i}`])
          );
          rawFieldValues.push(fieldRaw);
          rawIndexValues.push(indexRaw);
          const x = parseFieldNumber(fieldRaw);
          const y = parseIndexNumber(indexRaw);
          if (x === null || y === null) {
            continue;
          }
          let adjustedX = x;
          let key = adjustedX.toFixed(6);
          while (usedX.has(key)) {
            adjustedX += 0.0001;
            key = adjustedX.toFixed(6);
          }
          usedX.add(key);
          points.push({
            x: roundScore(adjustedX, 6),
            y: clamp(y, 0, 1),
            description: fieldRaw && indexRaw ? `Field ${fieldRaw}, Index ${indexRaw}` : '',
          });
        }
        const sortedPoints = points.sort((a, b) => a.x - b.x);
        return {
          points:
            sortedPoints.length >= 2
              ? sortedPoints
              : defaultIndexValues.map((value, index) => ({
                  x: index,
                  y: value,
                  description: '',
                })),
          rawFieldValues,
          rawIndexValues,
        };
      };

      const buildCurveSignature = (points) =>
        (Array.isArray(points) ? points : [])
          .map((point) => {
            const x = parseScore(point?.x);
            const y = parseScore(point?.y);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
              return null;
            }
            return `${roundScore(x, 6)}:${roundScore(y, 6)}`;
          })
          .filter(Boolean)
          .join('|');

      const collapseMetricLayers = (metric) => {
        const layers = Array.isArray(metric?.referenceCurve?.layers)
          ? metric.referenceCurve.layers
          : [];
        const sources = Array.isArray(metric?.sources) ? metric.sources.filter(Boolean) : [];
        if (layers.length <= 1 || sources.length <= 1) {
          return;
        }
        const allSourcesAreSqt = sources.every((source) => /sqt/i.test(source));
        if (!allSourcesAreSqt) {
          return;
        }
        const signatures = new Set(layers.map((layer) => buildCurveSignature(layer.points)));
        if (signatures.size !== 1) {
          return;
        }
        const mergedRowIndexes = Array.from(
          new Set(
            layers.flatMap((layer) =>
              Array.isArray(layer.sourceRowIndexes) && layer.sourceRowIndexes.length
                ? layer.sourceRowIndexes
                : [layer.sourceRowIndex]
            )
          )
        )
          .filter((value) => Number.isFinite(value))
          .sort((a, b) => a - b);
        const mergedSources = Array.from(
          new Set(
            layers.flatMap((layer) =>
              Array.isArray(layer.sourceNames) && layer.sourceNames.length
                ? layer.sourceNames
                : [layer.sourceName]
            )
          )
        ).filter(Boolean);
        const representative = layers[0];
        metric.referenceCurve.layers = [
          {
            ...representative,
            name: 'Multiple SQTs',
            stratification: 'Multiple SQTs',
            sourceRowIndex: mergedRowIndexes[0] || representative.sourceRowIndex,
            sourceRowIndexes: mergedRowIndexes.length
              ? mergedRowIndexes
              : [representative.sourceRowIndex],
            sourceName: mergedSources[0] || representative.sourceName || '',
            sourceNames: mergedSources.length
              ? mergedSources
              : representative.sourceName
                ? [representative.sourceName]
                : [],
          },
        ];
        metric.referenceCurve.activeLayerId = metric.referenceCurve.layers[0]?.id || null;
      };

      const metricsByKey = new Map();
      metricsRaw.forEach((row, index) => {
        const functionName = compactWhitespace(
          getRowValue(row, 'Mapped Function', ['Function', 'function'])
        );
        const metricName = compactWhitespace(
          getRowValue(row, 'Metric Name', ['Metric', 'metric'])
        );
        if (!functionName || !metricName) {
          return;
        }
        const sourceName = compactWhitespace(getRowValue(row, 'Source', ['source']));
        const canonicalMetricName = stripSourceSuffixFromMetricName(metricName, sourceName);
        const functionKey = resolveFunctionKey(functionName);
        const functionMatch = functionByName.get(functionKey);
        const methodContextRaw = getRowValue(row, 'Method/Context', [
          'Method',
          'method',
        ]);
        const methodContext = parseMethodContextCell(methodContextRaw);
        const references = compactWhitespace(getRowValue(row, 'References', ['references']));
        const howToMeasure = compactWhitespace(
          getRowValue(row, 'How To Measure', ['How to measure', 'how_to_measure'])
        );
        const metricDataSource = compactWhitespace(
          getRowValue(row, 'Metric Data Source', ['Metric data source'])
        );
        const stratification = compactWhitespace(
          getRowValue(row, 'Stratification', ['stratification'])
        );
        const exampleOn = toBooleanOn(
          getRowValue(row, 'Example Detailed Assessment as it "On" (only 1 metric per function)', [
            'Example Detailed Assessment as it On (only 1 metric per function)',
          ])
        );
        const rowCurve = parseRowCurvePoints(row);
        const metricKey = [
          normalizeText(functionMatch?.name || functionName),
          normalizeText(canonicalMetricName || metricName),
        ].join('|');

        if (!metricsByKey.has(metricKey)) {
          metricsByKey.set(metricKey, {
            _order: index,
            discipline: functionMatch?.category || compactWhitespace(row.Discipline || ''),
            functionName: functionMatch?.name || functionName,
            functionId: functionMatch ? functionMatch.id : null,
            functionOrder: functionOrder.has(functionKey) ? functionOrder.get(functionKey) : 9999,
            functionStatement:
              compactWhitespace(functionMatch?.function_statement || '') ||
              compactWhitespace(getRowValue(row, 'Metric Statement', ['metric statement'])) ||
              '',
            metricBaseName: canonicalMetricName || metricName,
            metricLabel: canonicalMetricName || metricName,
            source: sourceName,
            sources: sourceName ? [sourceName] : [],
            context: methodContext.context,
            method: methodContext.method,
            methodMarkdown: methodContext.markdown,
            howToMeasure,
            references,
            metricDataSource,
            exampleOn,
            referenceCurve: {
              name: 'Detailed reference',
              xType: 'quantitative',
              indexRange: false,
              units: '',
              activeLayerId: null,
              layers: [],
            },
            sourceRows: [],
          });
        }

        const metric = metricsByKey.get(metricKey);
        if (sourceName && !metric.sources.includes(sourceName)) {
          metric.sources.push(sourceName);
        }
        if (!metric.context && methodContext.context) {
          metric.context = methodContext.context;
        }
        if (!metric.method && methodContext.method) {
          metric.method = methodContext.method;
        }
        if (!metric.methodMarkdown && methodContext.markdown) {
          metric.methodMarkdown = methodContext.markdown;
        }
        if (!metric.howToMeasure && howToMeasure) {
          metric.howToMeasure = howToMeasure;
        }
        if (!metric.references && references) {
          metric.references = references;
        }
        if (!metric.metricDataSource && metricDataSource) {
          metric.metricDataSource = metricDataSource;
        }
        if (exampleOn) {
          metric.exampleOn = true;
        }

        const nextLayerIndex = metric.referenceCurve.layers.length + 1;
        const baseLayerName = buildLayerName(sourceName, stratification, nextLayerIndex);
        let layerName = baseLayerName;
        let layerNameSuffix = 2;
        while (metric.referenceCurve.layers.some((layer) => layer.name === layerName)) {
          layerName = `${baseLayerName} (${layerNameSuffix})`;
          layerNameSuffix += 1;
        }
        const layerId = `${
          slugify(`${metricKey}-${layerName || `layer-${nextLayerIndex}`}`)
          || `layer-${nextLayerIndex}`
        }`;
        metric.referenceCurve.layers.push({
          id: layerId,
          name: layerName,
          stratification,
          points: rowCurve.points,
          sourceRowIndex: index + 1,
          sourceRowIndexes: [index + 1],
          sourceName,
          sourceNames: sourceName ? [sourceName] : [],
        });
        metric.referenceCurve.activeLayerId = metric.referenceCurve.layers[0]?.id || null;
        metric.sourceRows.push({
          stratification,
          rawFieldValues: rowCurve.rawFieldValues,
          rawIndexValues: rowCurve.rawIndexValues,
        });
      });
      metricsByKey.forEach((metric) => collapseMetricLayers(metric));

      const allocatedMetricIds = new Set();
      const allocateMetricId = (functionName, metricName) => {
        const base =
          slugify(`${functionName}-${metricName}-detailed`) ||
          `metric-${allocatedMetricIds.size + 1}`;
        let candidate = base;
        let suffix = 2;
        while (allocatedMetricIds.has(candidate)) {
          candidate = `${base}-${suffix}`;
          suffix += 1;
        }
        allocatedMetricIds.add(candidate);
        return candidate;
      };
      const resolveMetricId = (metric) => {
        const preferredId =
          detailedMetricIdByLookup.get(
            makeMetricLookupKey(metric.functionName, metric.metricLabel)
          ) ||
          detailedMetricIdByLookup.get(
            makeMetricLookupKey(metric.functionName, metric.metricBaseName)
          );
        if (preferredId) {
          if (!allocatedMetricIds.has(preferredId)) {
            allocatedMetricIds.add(preferredId);
            return preferredId;
          }
          let suffix = 2;
          let candidate = `${preferredId}-${suffix}`;
          while (allocatedMetricIds.has(candidate)) {
            suffix += 1;
            candidate = `${preferredId}-${suffix}`;
          }
          allocatedMetricIds.add(candidate);
          return candidate;
        }
        return allocateMetricId(metric.functionName, metric.metricBaseName);
      };

      const metrics = Array.from(metricsByKey.values())
        .sort((a, b) => {
          if ((a.functionOrder || 9999) !== (b.functionOrder || 9999)) {
            return (a.functionOrder || 9999) - (b.functionOrder || 9999);
          }
          if ((a.functionName || '').localeCompare(b.functionName || '') !== 0) {
            return (a.functionName || '').localeCompare(b.functionName || '');
          }
          if ((a.metricBaseName || '').localeCompare(b.metricBaseName || '') !== 0) {
            return (a.metricBaseName || '').localeCompare(b.metricBaseName || '');
          }
          return 0;
        })
        .map((metric) => ({
          id: resolveMetricId(metric),
          discipline: metric.discipline || '',
          functionName: metric.functionName,
          functionId: metric.functionId,
          functionStatement: metric.functionStatement || '',
          metric: metric.metricLabel,
          context: metric.context || '',
          method: metric.method || '',
          methodMarkdown: metric.methodMarkdown || '',
          howToMeasure: metric.howToMeasure || '',
          references: metric.references || '',
          metricDataSource: metric.metricDataSource || '',
          source: metric.source || '',
          exampleOn: !!metric.exampleOn,
          referenceCurve: metric.referenceCurve,
          sourceRows: metric.sourceRows || [],
        }));

      const metricById = new Map(metrics.map((metric) => [metric.id, metric]));
      const defaultMetricIdsByFunction = new Map();
      metrics.forEach((metric) => {
        if (!metric.exampleOn) {
          return;
        }
        const key = metric.functionId || normalizeText(metric.functionName || '');
        if (key && !defaultMetricIdsByFunction.has(key)) {
          defaultMetricIdsByFunction.set(key, metric.id);
        }
      });
      const defaultMetricIds = Array.from(defaultMetricIdsByFunction.values());

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
          functionStatement:
            compactWhitespace(functionMatch?.function_statement || '') ||
            compactWhitespace(detail.functionStatement || '') ||
            '',
          metric: detail.name || detail.metricId,
          context: detail.methodContextMarkdown || '',
          method: detail.methodContextMarkdown || '',
          methodMarkdown: detail.methodContextMarkdown || '',
          howToMeasure: detail.howToMeasureMarkdown || '',
          references: Array.isArray(detail.references)
            ? detail.references.join('; ')
            : '',
          metricDataSource: '',
          source: '',
          exampleOn: false,
          referenceCurve: buildDefaultCurve(),
          sourceRows: [],
        };
        metrics.push(metric);
        metricById.set(metric.id, metric);
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

      const buildScenarioMetricsState = (metricIds) => {
        const curves = {};
        const metricProfiles = {};
        Array.from(metricIds || []).forEach((metricId) => {
          const metric = metricById.get(metricId);
          curves[metricId] =
            metric && metric.referenceCurve
              ? cloneCurve(metric.referenceCurve)
              : buildDefaultCurve();
          metricProfiles[metricId] = defaultProfileId;
        });
        return {
          metricIds: Array.from(metricIds || []),
          curves,
          metricProfiles,
        };
      };

      const getNextCustomScenarioName = () => {
        let max = 0;
        scenarios.forEach((scenario) => {
          const match = /^Custom Detailed Assessment (\d+)$/i.exec(
            (scenario?.name || '').trim()
          );
          if (!match) {
            return;
          }
          const index = Number.parseInt(match[1], 10);
          if (Number.isFinite(index) && index > max) {
            max = index;
          }
        });
        return `Custom Detailed Assessment ${max + 1}`;
      };

      const createExampleScenario = () => {
        const state = buildScenarioMetricsState(defaultMetricIds);
        return {
          id: generateId(),
          name: 'Example Detailed Assessment',
          applicability: '',
          notes: '',
          fieldValues: {},
          metricIds: state.metricIds,
          curves: state.curves,
          metricProfiles: state.metricProfiles,
          showAdvancedScoring: false,
          showRollupComputations: false,
          showFunctionMappings: false,
          showSuggestedFunctionScoresCue: false,
          showFunctionScoreCueLabels: false,
        };
      };

      const createBlankScenario = () => {
        const state = buildScenarioMetricsState([]);
        return {
          id: generateId(),
          name: getNextCustomScenarioName(),
          applicability: '',
          notes: '',
          fieldValues: {},
          metricIds: state.metricIds,
          curves: state.curves,
          metricProfiles: state.metricProfiles,
          showAdvancedScoring: false,
          showRollupComputations: false,
          showFunctionMappings: false,
          showSuggestedFunctionScoresCue: false,
          showFunctionScoreCueLabels: false,
        };
      };

      const getActiveScenario = () =>
        scenarios.find((scenario) => scenario.id === activeScenarioId);

      const ensureScenarioViewOptions = (scenario) => {
        if (!scenario) {
          return;
        }
        if (typeof scenario.showAdvancedScoring !== 'boolean') {
          scenario.showAdvancedScoring = false;
        }
        if (typeof scenario.showRollupComputations !== 'boolean') {
          scenario.showRollupComputations = false;
        }
        if (typeof scenario.showFunctionMappings !== 'boolean') {
          scenario.showFunctionMappings = false;
        }
        if (typeof scenario.showSuggestedFunctionScoresCue !== 'boolean') {
          scenario.showSuggestedFunctionScoresCue = false;
        }
        if (typeof scenario.showFunctionScoreCueLabels !== 'boolean') {
          scenario.showFunctionScoreCueLabels = false;
        }
      };

      const ensureCurve = (scenario, metricId) => {
        if (!scenario.curves[metricId]) {
          const metric = metricById.get(metricId);
          scenario.curves[metricId] =
            metric && metric.referenceCurve
              ? cloneCurve(metric.referenceCurve)
              : buildDefaultCurve();
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
          const scenario = createBlankScenario();
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
          ensureScenarioViewOptions(copy);
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

      const getShowAdvancedScoring = () =>
        Boolean(getActiveScenario()?.showAdvancedScoring);
      const getShowRollupComputations = () =>
        Boolean(getActiveScenario()?.showRollupComputations);
      const getShowFunctionMappings = () =>
        Boolean(getActiveScenario()?.showFunctionMappings);
      const getShowSuggestedFunctionScoresCue = () =>
        Boolean(getActiveScenario()?.showSuggestedFunctionScoresCue);
      const getShowFunctionScoreCueLabels = () =>
        Boolean(getActiveScenario()?.showFunctionScoreCueLabels);

      const scoringControls = document.createElement('div');
      scoringControls.className = 'screening-scoring-controls';

      const advancedToggleLabel = document.createElement('label');
      advancedToggleLabel.className = 'screening-advanced-toggle';
      const advancedToggle = document.createElement('input');
      advancedToggle.type = 'checkbox';
      advancedToggle.className = 'screening-advanced-toggle-input';
      const advancedToggleText = document.createElement('span');
      advancedToggleText.textContent = 'Show advanced scoring columns';
      advancedToggleLabel.appendChild(advancedToggle);
      advancedToggleLabel.appendChild(advancedToggleText);

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

      const suggestedCueToggleLabel = document.createElement('label');
      suggestedCueToggleLabel.className = 'screening-advanced-toggle';
      const suggestedCueToggle = document.createElement('input');
      suggestedCueToggle.type = 'checkbox';
      suggestedCueToggle.className = 'screening-suggested-cue-toggle-input';
      const suggestedCueToggleText = document.createElement('span');
      suggestedCueToggleText.textContent = 'Show Suggested Function Scores';
      suggestedCueToggleLabel.appendChild(suggestedCueToggle);
      suggestedCueToggleLabel.appendChild(suggestedCueToggleText);

      const sliderLabelsToggleLabel = document.createElement('label');
      sliderLabelsToggleLabel.className = 'screening-advanced-toggle';
      const sliderLabelsToggle = document.createElement('input');
      sliderLabelsToggle.type = 'checkbox';
      sliderLabelsToggle.className = 'screening-slider-labels-toggle-input';
      const sliderLabelsToggleText = document.createElement('span');
      sliderLabelsToggleText.textContent = 'Show F/AR/NF labels';
      sliderLabelsToggleLabel.appendChild(sliderLabelsToggle);
      sliderLabelsToggleLabel.appendChild(sliderLabelsToggleText);

      scoringControls.appendChild(advancedToggleLabel);
      scoringControls.appendChild(mappingToggleLabel);
      scoringControls.appendChild(rollupToggleLabel);
      scoringControls.appendChild(suggestedCueToggleLabel);
      scoringControls.appendChild(sliderLabelsToggleLabel);

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

      const libraryButton = document.createElement('button');
      libraryButton.type = 'button';
      libraryButton.className = 'btn btn-small library-open-btn';
      libraryButton.setAttribute('data-open-metric-library', 'true');
      libraryButton.textContent = 'Metric Library';

      const openMetricLibraryWithFilters = (discipline, functionName) => {
        if (!window.dispatchEvent) {
          return;
        }
        window.dispatchEvent(
          new CustomEvent('staf:set-library-filters', {
            detail: { tier: 'detailed', discipline, functionName },
          })
        );
      };

      const openMetricInspectorForMetric = (metricId) => {
        if (!window.dispatchEvent || !metricId) {
          return;
        }
        const scenario = getActiveScenario();
        const profileId = scenario?.metricProfiles?.[metricId] || defaultProfileId;
        window.dispatchEvent(
          new CustomEvent('staf:open-inspector', {
            detail: {
              tier: 'detailed',
              metricId,
              profileId,
              tab: 'details',
            },
          })
        );
      };

      controls.appendChild(search);
      controls.appendChild(disciplineFilter);
      controls.appendChild(libraryButton);

      if (controlsHost) {
        controlsHost.innerHTML = '';
        controlsHost.appendChild(scoringControls);
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
        '<th class="col-field">Metric Value</th>' +
        '<th class="col-scoring-criteria">Scoring<br>criteria</th>' +
        '<th class="col-index">Metric<br>index</th>' +
        '<th class="col-function-estimate">Function<br>Estimate</th>' +
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

      const summaryTable = document.createElement('table');
      summaryTable.className =
        'screening-table screening-summary-table detailed-summary-table';
      const summaryColGroup = document.createElement('colgroup');
      const summaryHead = document.createElement('thead');
      const summaryBody = document.createElement('tbody');
      summaryTable.appendChild(summaryColGroup);
      summaryTable.appendChild(summaryHead);
      summaryTable.appendChild(summaryBody);

      const chartsShell = document.createElement('div');
      chartsShell.className =
        'screening-settings-panel screening-charts-panel rapid-charts-panel detailed-charts-panel';
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
        '<div class="detailed-legend-abbrev" hidden>F / AR / NF</div>' +
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

      if (tableHost) {
        tableHost.innerHTML = '';
        tableHost.appendChild(table);
        tableHost.appendChild(summaryTable);
        const parent = tableHost.parentElement;
        if (parent) {
          const existing = parent.querySelector('.detailed-charts-panel');
          if (existing && existing !== chartsShell) {
            existing.remove();
          }
          parent.appendChild(chartsShell);
        }
      }

      const summaryCells = {
        physical: [],
        chemical: [],
        biological: [],
        ecosystem: null,
      };

      const renderHeader = (showMappings, showAdvanced) => {
        const useAbbrev = showMappings && showAdvanced;
        const mappingHeaders = showMappings
          ? `<th class="col-physical"${useAbbrev ? ' title="Physical"' : ''}>${
              useAbbrev ? 'Phy' : 'Physical'
            }</th>` +
            `<th class="col-chemical"${useAbbrev ? ' title="Chemical"' : ''}>${
              useAbbrev ? 'Chem' : 'Chemical'
            }</th>` +
            `<th class="col-biological"${useAbbrev ? ' title="Biological"' : ''}>${
              useAbbrev ? 'Bio' : 'Biological'
            }</th>`
          : '';
        thead.innerHTML =
          '<tr>' +
          '<th class="col-discipline">Discipline</th>' +
          '<th class="col-function">Function</th>' +
          '<th class="col-metric">Metric</th>' +
          '<th class="col-field">Metric Value</th>' +
          '<th class="col-scoring-criteria">Scoring<br>criteria</th>' +
          '<th class="col-index">Metric<br>index</th>' +
          '<th class="col-function-estimate">Function<br>Estimate</th>' +
          '<th class="col-function-score">Function<br>Score</th>' +
          mappingHeaders +
          '</tr>';
      };

      const buildSummary = (showAdvanced, showMappings) => {
        const labelItems = [
          { label: 'Direct Effect', rollup: true },
          { label: 'Indirect Effect', rollup: true },
          { label: 'Weighted Score Total', rollup: true },
          { label: 'Max Weighted Score Total', rollup: true },
          { label: 'Condition Sub-Index', rollup: false },
          { label: 'Ecosystem Condition Index', rollup: false },
        ];
        const baseLabelSpan = showAdvanced ? 8 : 5;
        const labelSpan = showMappings ? baseLabelSpan : Math.max(1, baseLabelSpan - 3);
        const totalSpan = labelSpan + 3;

        tfoot.innerHTML = '';
        summaryColGroup.innerHTML = '';
        summaryHead.innerHTML = '';
        summaryBody.innerHTML = '';
        summaryCells.physical = [];
        summaryCells.chemical = [];
        summaryCells.biological = [];
        summaryCells.ecosystem = null;
        const summaryTarget = showMappings ? tfoot : summaryBody;

        const labelCol = document.createElement('col');
        labelCol.span = labelSpan;
        labelCol.className = 'summary-label-col';
        summaryColGroup.appendChild(labelCol);
        ['physical', 'chemical', 'biological'].forEach((key) => {
          const col = document.createElement('col');
          col.className = `col-${key}`;
          summaryColGroup.appendChild(col);
        });

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
            { label: 'Biological', className: 'col-biological' },
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
          if (item.rollup && !getShowRollupComputations()) {
            row.hidden = true;
          }
          const labelCell = document.createElement('td');
          labelCell.colSpan = labelSpan;
          labelCell.className = 'summary-labels';
          labelCell.textContent = item.label;
          row.appendChild(labelCell);

          if (item.label === 'Ecosystem Condition Index') {
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
              cell.className = `summary-values col-${key}`;
              const value = document.createElement('div');
              value.textContent = '-';
              summaryCells[key].push(value);
              cell.appendChild(value);
              row.appendChild(cell);
            });
          }
          summaryTarget.appendChild(row);
        });
      };

      const expandedMetrics = new Set();

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

      const getDetailedFunctionScorePalette = (score) => {
        if (score >= 11) {
          return { base: '#a7c7f2', active: '#7faee9' };
        }
        if (score >= 6) {
          return { base: '#f7e088', active: '#f0cd52' };
        }
        return { base: '#ef9a9a', active: '#e07070' };
      };

      const updateDetailedFunctionScoreVisual = (rangeInput, scoreValue) => {
        if (!rangeInput) {
          return;
        }
        const min = Number(rangeInput.min) || 0;
        const max = Number(rangeInput.max) || 15;
        const safeValue = Number.isFinite(scoreValue) ? scoreValue : min;
        const clamped = Math.min(max, Math.max(min, safeValue));
        const palette = getDetailedFunctionScorePalette(clamped);
        const percent = max > min ? ((clamped - min) / (max - min)) * 100 : 0;
        rangeInput.style.setProperty('--screening-score-pct', `${percent}%`);
        rangeInput.style.setProperty('--screening-score-color', palette.base);
        rangeInput.style.setProperty('--screening-score-color-active', palette.active);
      };

      const updateDetailedSuggestedBracket = (
        bracketEl,
        minValue,
        maxValue,
        hasSuggestedRange
      ) => {
        if (!bracketEl) {
          return;
        }
        if (!hasSuggestedRange || !Number.isFinite(minValue) || !Number.isFinite(maxValue)) {
          bracketEl.hidden = true;
          return;
        }
        const sliderMin = 0;
        const sliderMax = 15;
        const safeMin = Math.min(sliderMax, Math.max(sliderMin, minValue));
        const safeMax = Math.min(sliderMax, Math.max(sliderMin, maxValue));
        const low = Math.min(safeMin, safeMax);
        const high = Math.max(safeMin, safeMax);
        const leftPct = ((low - sliderMin) / (sliderMax - sliderMin)) * 100;
        const rightPct = ((high - sliderMin) / (sliderMax - sliderMin)) * 100;
        bracketEl.style.left = `${leftPct}%`;
        bracketEl.style.width = `${Math.max(0, rightPct - leftPct)}%`;
        bracketEl.hidden = false;
      };

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

      const renderCharts = (functionOrder, functionScores, summaryValues) => {
        if (!functionChartBody || !summaryChartBody) {
          return;
        }
        functionChartBody.innerHTML = '';
        summaryChartBody.innerHTML = '';

        const functionsToShow = Array.isArray(functionOrder) ? functionOrder : [];
        if (!functionsToShow.length) {
          const empty = document.createElement('div');
          empty.className = 'screening-chart-empty';
          empty.textContent = 'No function scores available.';
          functionChartBody.appendChild(empty);
        } else {
          let lastDiscipline = null;
          functionsToShow.forEach((fn, index) => {
            const disciplineKey = (fn.discipline || '').toLowerCase();
            if (index > 0 && lastDiscipline !== null && disciplineKey !== lastDiscipline) {
              const divider = document.createElement('div');
              divider.className = 'screening-bar-divider';
              functionChartBody.appendChild(divider);
            }
            const score = functionScores.get(fn.id);
            if (score === undefined || score === null) {
              return;
            }
            const rounded = Math.round(score);
            functionChartBody.appendChild(
              buildBarRow({
                label: fn.name,
                value: rounded,
                maxValue: 15,
                color: functionScoreColorForValue(rounded),
                valueText: String(rounded),
              })
            );
            lastDiscipline = disciplineKey;
          });
        }

        const summaryItems = [
          { label: 'Physical', value: summaryValues.physical },
          { label: 'Chemical', value: summaryValues.chemical },
          { label: 'Biological', value: summaryValues.biological },
          { label: 'Overall Ecosystem', value: summaryValues.ecosystem },
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
              valueText: item.value.toFixed(2),
            })
          );
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

        return {
          physical: physicalIndex,
          chemical: chemicalIndex,
          biological: biologicalIndex,
          ecosystem: ecosystemIndex,
        };
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

      const renderViewOptionControls = () => {
        const scenario = getActiveScenario();
        if (advancedToggle) {
          advancedToggle.checked = Boolean(scenario?.showAdvancedScoring);
        }
        if (mappingToggle) {
          mappingToggle.checked = Boolean(scenario?.showFunctionMappings);
        }
        if (rollupToggle) {
          rollupToggle.checked = Boolean(scenario?.showRollupComputations);
        }
        if (suggestedCueToggle) {
          suggestedCueToggle.checked = Boolean(
            scenario?.showSuggestedFunctionScoresCue
          );
        }
        if (sliderLabelsToggle) {
          sliderLabelsToggle.checked = Boolean(
            scenario?.showFunctionScoreCueLabels
          );
        }
      };

      const formatCurveNumber = (value, places = 6) => {
        const parsed = parseScore(value);
        if (parsed === null) {
          return '-';
        }
        const rounded = roundScore(parsed, places);
        return Number.isInteger(rounded) ? String(rounded) : String(rounded);
      };

      const toFunctionScore = (indexValue) => Math.round(clamp(indexValue, 0, 1) * 15);
      const toFunctionScoreRange = (minIndex, maxIndex, fallbackIndex = null) => {
        const minScore = Math.ceil(clamp(minIndex, 0, 1) * 15);
        const maxScore = Math.floor(clamp(maxIndex, 0, 1) * 15);
        if (minScore <= maxScore) {
          return {
            minScore,
            maxScore,
            hasRange: minScore < maxScore,
          };
        }
        const fallbackScore = Number.isFinite(fallbackIndex)
          ? toFunctionScore(fallbackIndex)
          : toFunctionScore((clamp(minIndex, 0, 1) + clamp(maxIndex, 0, 1)) / 2);
        return {
          minScore: fallbackScore,
          maxScore: fallbackScore,
          hasRange: false,
        };
      };

      const buildDetailedCurveSummaryTable = (curve) => {
        const table = document.createElement('table');
        table.className = 'curve-summary-table';

        const layer = getCurveLayer(curve);
        let rawPoints = Array.isArray(layer?.points) ? [...layer.points] : [];
        const curveType = normalizeCurveType(curve?.xType);
        const useRange = curveType === 'categorical' && !!curve?.indexRange;
        if (useRange) {
          const needsRanges = rawPoints.some(
            (point) => parseScore(point.yMin) === null || parseScore(point.yMax) === null
          );
          if (needsRanges) {
            applyIndexRanges(rawPoints);
          }
        }

        const points = rawPoints
          .map((point, index) => {
            const numericX = parseScore(point.x);
            const valueLabel =
              curveType === 'categorical'
                ? compactWhitespace(point.x ?? '') || `Category ${index + 1}`
                : numericX !== null
                  ? formatCurveNumber(numericX)
                  : compactWhitespace(point.x ?? '') || '-';
            return {
              order: index,
              numericX,
              valueLabel,
              y: parseScore(point.y),
              yMin: parseScore(point.yMin),
              yMax: parseScore(point.yMax),
            };
          })
          .sort((a, b) => {
            if (curveType === 'categorical') {
              return a.order - b.order;
            }
            if (a.numericX === null && b.numericX === null) {
              return a.order - b.order;
            }
            if (a.numericX === null) {
              return 1;
            }
            if (b.numericX === null) {
              return -1;
            }
            return a.numericX - b.numericX;
          });

        const buildRow = (label, valueBuilder) => {
          const row = document.createElement('tr');
          const rowLabel = document.createElement('th');
          rowLabel.textContent = label;
          row.appendChild(rowLabel);
          if (!points.length) {
            const empty = document.createElement('td');
            empty.textContent = '-';
            row.appendChild(empty);
            return row;
          }
          points.forEach((point) => {
            const cell = document.createElement('td');
            cell.textContent = valueBuilder(point);
            row.appendChild(cell);
          });
          return row;
        };

        table.appendChild(buildRow('Value', (point) => point.valueLabel));
        table.appendChild(
          buildRow('Index', (point) => {
            if (useRange && point.yMin !== null && point.yMax !== null) {
              return `${point.yMin.toFixed(2)}-${point.yMax.toFixed(2)}`;
            }
            return point.y === null ? '-' : point.y.toFixed(2);
          })
        );
        table.appendChild(
          buildRow('Function Score', (point) => {
            if (useRange && point.yMin !== null && point.yMax !== null) {
              const scoreRange = toFunctionScoreRange(point.yMin, point.yMax, point.y);
              return `${scoreRange.minScore}-${scoreRange.maxScore}`;
            }
            return point.y === null ? '-' : String(toFunctionScore(point.y));
          })
        );

        return table;
      };

      const getFunctionEstimateText = (curve, fieldValue) => {
        const meta = resolveDetailedIndexMeta(curve, fieldValue);
        if (!meta || !Number.isFinite(meta.indexScore)) {
          return '-';
        }
        const avgScore = toFunctionScore(meta.indexScore);
        if (
          Number.isFinite(meta.minIndex) &&
          Number.isFinite(meta.maxIndex)
        ) {
          const scoreRange = toFunctionScoreRange(meta.minIndex, meta.maxIndex, meta.indexScore);
          if (scoreRange.hasRange) {
            return `${avgScore} (${scoreRange.minScore}-${scoreRange.maxScore})`;
          }
        }
        return String(avgScore);
      };

      const renderTable = () => {
        tbody.innerHTML = '';
        const scenario = getActiveScenario();
        ensureScenarioViewOptions(scenario);
        const showAdvanced = getShowAdvancedScoring();
        const showMappings = getShowFunctionMappings();
        const showSuggestedCue = getShowSuggestedFunctionScoresCue();
        const showSliderLabels = getShowFunctionScoreCueLabels();
        table.classList.toggle('show-advanced-scoring', showAdvanced);
        table.classList.toggle('show-function-mappings', showMappings);
        table.classList.toggle('show-suggested-function-cues', showSuggestedCue);
        table.classList.toggle('show-function-score-cue-labels', showSliderLabels);
        summaryTable.classList.toggle('show-advanced-scoring', showAdvanced);
        summaryTable.classList.toggle('show-function-mappings', showMappings);
        summaryTable.hidden = showMappings;
        chartsShell.classList.toggle('show-suggested-function-cues', showSuggestedCue);
        chartsShell.classList.toggle('show-function-score-cue-labels', showSliderLabels);
        renderHeader(showMappings, showAdvanced);
        renderViewOptionControls();
        const abbrevLegend = chartsShell.querySelector('.detailed-legend-abbrev');
        if (abbrevLegend) {
          abbrevLegend.hidden = !showSliderLabels;
        }
        if (!scenario) {
          tfoot.innerHTML = '';
          summaryHead.innerHTML = '';
          summaryBody.innerHTML = '';
          summaryTable.hidden = true;
          if (search) {
            search.disabled = true;
          }
          if (disciplineFilter) {
            disciplineFilter.disabled = true;
          }
          if (functionChartBody) {
            functionChartBody.innerHTML = '';
            const empty = document.createElement('div');
            empty.className = 'screening-chart-empty';
            empty.textContent = 'No function scores available.';
            functionChartBody.appendChild(empty);
          }
          if (summaryChartBody) {
            summaryChartBody.innerHTML = '';
            const empty = document.createElement('div');
            empty.className = 'screening-chart-empty';
            empty.textContent = 'No condition indices available.';
            summaryChartBody.appendChild(empty);
          }
          const emptyRow = document.createElement('tr');
          emptyRow.className = 'empty-row';
          const emptyCell = document.createElement('td');
          emptyCell.colSpan = 5 + (showAdvanced ? 3 : 0) + (showMappings ? 3 : 0);
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

        buildSummary(showAdvanced, showMappings);
        tfoot.hidden = !showMappings;
        const term = search.value.trim().toLowerCase();
        const disciplineValue = disciplineFilter.value;
        const selectedMetricIds = new Set(scenario.metricIds || []);

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
        const functionOrder = [];
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
            functionOrder.push({
              id: fn.id,
              name: fn.name,
              discipline,
            });
            filteredMetrics.forEach((metric) => {
              renderRows.push({
                type: 'metric',
                discipline,
                functionId: fn.id,
                functionName: fn.name,
                functionStatement:
                  metric.functionStatement ||
                  compactWhitespace(functionById.get(fn.id)?.function_statement || ''),
                metric,
              });
              if (expandedMetrics.has(metric.id)) {
                renderRows.push({
                  type: 'details',
                  discipline,
                  functionId: fn.id,
                  functionName: fn.name,
                  functionStatement:
                    metric.functionStatement ||
                    compactWhitespace(functionById.get(fn.id)?.function_statement || ''),
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
              functionStatement: compactWhitespace(fn.function_statement || ''),
            });
          }
        });

        const functionScores = new Map();
        const functionScoreMeta = new Map();
        functionsList.forEach((fn) => {
          const fnMetrics = metricsByFunction.get(fn.id) || [];
          const metas = fnMetrics
            .map((metric) => {
              const value = Number.parseFloat(scenario.fieldValues[metric.id]);
              const curve = ensureCurve(scenario, metric.id);
              return resolveDetailedIndexMeta(curve, value);
            })
            .filter((meta) => meta && Number.isFinite(meta.indexScore));
          const indexScores = metas.map((meta) => clamp(meta.indexScore, 0, 1));
          const avgIndex = indexScores.length
            ? indexScores.reduce((sum, value) => sum + value, 0) / indexScores.length
            : 0;
          const functionScore = toFunctionScore(avgIndex);
          functionScores.set(fn.id, functionScore);

          const rangeEntries = metas.map((meta) => {
            const avgScore = toFunctionScore(meta.indexScore);
            const scoreRange =
              Number.isFinite(meta.minIndex) && Number.isFinite(meta.maxIndex)
                ? toFunctionScoreRange(meta.minIndex, meta.maxIndex, meta.indexScore)
                : {
                    minScore: avgScore,
                    maxScore: avgScore,
                    hasRange: false,
                  };
            return {
              minScore: scoreRange.minScore,
              maxScore: scoreRange.maxScore,
              hasRange: scoreRange.hasRange,
            };
          });
          const minLimit = rangeEntries.length
            ? Math.min(...rangeEntries.map((range) => range.minScore))
            : 0;
          const maxLimit = rangeEntries.length
            ? Math.max(...rangeEntries.map((range) => range.maxScore))
            : 15;
          const hasSuggestedRange =
            rangeEntries.some((range) => range.hasRange) && minLimit < maxLimit;
          functionScoreMeta.set(fn.id, {
            value: functionScore,
            minLimit,
            maxLimit,
            hasSuggestedRange,
            isOutsideSuggestedRange:
              hasSuggestedRange && (functionScore < minLimit || functionScore > maxLimit),
          });
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
          let hasExpandedCriteria = row.type === 'details';
          let firstExpandedMetricIndex =
            row.type === 'metric' && row.metric && expandedMetrics.has(row.metric.id)
              ? i
              : null;
          for (let j = i + 1; j < renderRows.length; j += 1) {
            if (renderRows[j].functionId !== row.functionId) {
              break;
            }
            span += 1;
            renderRows[j]._functionSkip = true;
            if (renderRows[j].type === 'details') {
              hasExpandedCriteria = true;
            }
            if (
              firstExpandedMetricIndex === null &&
              renderRows[j].type === 'metric' &&
              renderRows[j].metric &&
              expandedMetrics.has(renderRows[j].metric.id)
            ) {
              firstExpandedMetricIndex = j;
            }
          }
          row._functionSpan = span;
          const functionMeta = {
            startIndex: i,
            totalSpan: span,
            hasExpandedCriteria,
            sliderOwnerIndex:
              hasExpandedCriteria && Number.isInteger(firstExpandedMetricIndex)
                ? firstExpandedMetricIndex
                : i,
          };
          for (let k = i; k < i + span; k += 1) {
            renderRows[k]._functionMeta = functionMeta;
          }
        }

        const buildFunctionScoreCell = (functionId, rowSpan = 1) => {
          const functionScoreCell = document.createElement('td');
          functionScoreCell.className = 'col-function-score function-score-cell';
          functionScoreCell.rowSpan = Math.max(1, rowSpan);
          const scoreMeta = functionScoreMeta.get(functionId) || {
            value: 0,
            minLimit: 0,
            maxLimit: 15,
            hasSuggestedRange: false,
            isOutsideSuggestedRange: false,
          };
          const scoreWrap = document.createElement('div');
          scoreWrap.className = 'score-input function-score-inline';
          const sliderWrap = document.createElement('div');
          sliderWrap.className = 'function-score-slider';

          const cueLabels = document.createElement('div');
          cueLabels.className = 'function-score-cue-labels';
          [
            { text: 'NF', left: '16.67%' },
            { text: 'AR', left: '50%' },
            { text: 'F', left: '83.33%' },
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
          cueBar.setAttribute('aria-label', 'Function score cue ranges');
          cueBar.tabIndex = -1;
          [0, 33.33, 66.67, 100].forEach((percent) => {
            const tick = document.createElement('span');
            tick.className = 'function-score-cue-tick';
            tick.style.left = `${percent}%`;
            cueBar.appendChild(tick);
          });

          const suggestedBracketLayer = document.createElement('div');
          suggestedBracketLayer.className = 'function-score-suggested-layer';
          const suggestedBracket = document.createElement('div');
          suggestedBracket.className = 'function-score-suggested-bracket';
          suggestedBracketLayer.appendChild(suggestedBracket);

          const range = document.createElement('input');
          range.type = 'range';
          range.min = '0';
          range.max = '15';
          range.step = '1';
          range.value = String(scoreMeta.value);
          range.disabled = true;
          updateDetailedFunctionScoreVisual(range, scoreMeta.value);

          const valueLabel = document.createElement('span');
          valueLabel.className = 'score-value';
          valueLabel.textContent = String(scoreMeta.value);

          updateDetailedSuggestedBracket(
            suggestedBracket,
            scoreMeta.minLimit,
            scoreMeta.maxLimit,
            scoreMeta.hasSuggestedRange
          );
          const showOutOfRangeCue = Boolean(
            scoreMeta.isOutsideSuggestedRange && showSuggestedCue
          );
          valueLabel.classList.toggle('is-outside-suggested', showOutOfRangeCue);
          if (showOutOfRangeCue) {
            valueLabel.title = 'Score is outside suggested range';
          } else {
            valueLabel.removeAttribute('title');
          }
          suggestedBracket.hidden = !showSuggestedCue || !scoreMeta.hasSuggestedRange;

          sliderWrap.appendChild(cueLabels);
          sliderWrap.appendChild(cueBar);
          sliderWrap.appendChild(suggestedBracketLayer);
          sliderWrap.appendChild(range);
          scoreWrap.appendChild(sliderWrap);
          scoreWrap.appendChild(valueLabel);
          functionScoreCell.appendChild(scoreWrap);
          return functionScoreCell;
        };

        renderRows.forEach((row, rowIndex) => {
          const tr = document.createElement('tr');
          tr.classList.add(slugCategory(row.discipline));
          if (row.type === 'placeholder') {
            tr.classList.add('is-empty');
          }
          const functionMeta = row._functionMeta || {
            startIndex: rowIndex,
            totalSpan: row._functionSpan || 1,
            hasExpandedCriteria: false,
            sliderOwnerIndex: rowIndex,
          };
          const functionHasExpandedCriteria = Boolean(functionMeta.hasExpandedCriteria);
          const isFunctionSliderOwner = functionMeta.sliderOwnerIndex === rowIndex;

          if (!row._disciplineSkip) {
            const disciplineCell = document.createElement('td');
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = row._disciplineSpan;
            const disciplineLink = document.createElement('button');
            disciplineLink.type = 'button';
            disciplineLink.className = 'metric-curve-link';
            disciplineLink.textContent = row.discipline;
            disciplineLink.addEventListener('click', () => {
              openMetricLibraryWithFilters(row.discipline, 'all');
            });
            disciplineCell.appendChild(disciplineLink);
            tr.appendChild(disciplineCell);
          }

          if (!row._functionSkip) {
            const functionCell = document.createElement('td');
            functionCell.className = 'function-cell col-function';
            functionCell.rowSpan = row._functionSpan;
            const nameLine = document.createElement('div');
            nameLine.className = 'function-title';
            const functionText = document.createElement('span');
            functionText.className = 'metric-curve-link';
            functionText.setAttribute('role', 'button');
            functionText.tabIndex = 0;
            functionText.appendChild(document.createTextNode(row.functionName));
            const openFunctionLibrary = () => {
              openMetricLibraryWithFilters(row.discipline, row.functionName);
            };
            functionText.addEventListener('click', openFunctionLibrary);
            functionText.addEventListener('keydown', (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openFunctionLibrary();
              }
            });
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
            functionText.appendChild(document.createTextNode('\u00A0'));
            functionText.appendChild(functionToggle);
            nameLine.appendChild(functionText);
            functionCell.appendChild(nameLine);
            const statementLine = document.createElement('div');
            statementLine.className = 'function-statement';
            statementLine.textContent = row.functionStatement || '';
            statementLine.hidden = true;
            if (!statementLine.textContent) {
              functionToggle.disabled = true;
            }
            functionToggle.addEventListener('click', (event) => {
              event.stopPropagation();
              if (!statementLine.textContent) {
                return;
              }
              const isOpen = !statementLine.hidden;
              statementLine.hidden = isOpen;
              functionToggle.setAttribute('aria-expanded', String(!isOpen));
              functionToggle.innerHTML = isOpen ? collapsedGlyph : expandedGlyph;
            });
            functionCell.appendChild(statementLine);
            tr.appendChild(functionCell);
          }

          if (row.type === 'details') {
            tr.classList.add('criteria-row');
            tr.id = `detailed-criteria-${row.metric.id}`;
            const detailsCell = document.createElement('td');
            detailsCell.colSpan =
              2 +
              (showAdvanced ? 3 : 0) +
              (showMappings ? 3 : 0) +
              (functionHasExpandedCriteria ? 1 : 0);
            const details = document.createElement('div');
            details.className = 'criteria-details';
            const headerRow = document.createElement('div');
            headerRow.className = 'criteria-summary-header';
            const headerLabel = document.createElement('span');
            headerLabel.textContent = 'Scoring Criteria';
            headerRow.appendChild(headerLabel);
            details.appendChild(headerRow);
            const curve = ensureCurve(scenario, row.metric.id);
            details.appendChild(buildDetailedCurveSummaryTable(curve));

            detailsCell.appendChild(details);
            tr.appendChild(detailsCell);
          } else {
            const metricCell = document.createElement('td');
            metricCell.className = 'col-metric';
            if (row.type === 'placeholder') {
              metricCell.textContent = 'No metrics selected for this function';
              tr.appendChild(metricCell);
            } else {
              const metricTitle = document.createElement('span');
              metricTitle.className = 'metric-title';
              const metricText = document.createElement('span');
              metricText.className = 'metric-curve-link';
              metricText.setAttribute('role', 'button');
              metricText.tabIndex = 0;
              metricText.appendChild(document.createTextNode(row.metric.metric));
              const openMetricInspector = () => {
                openMetricInspectorForMetric(row.metric.id);
              };
              metricText.addEventListener('click', openMetricInspector);
              metricText.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  openMetricInspector();
                }
              });
              const detailsId = `detailed-criteria-${row.metric.id}`;
              const criteriaExpanded = expandedMetrics.has(row.metric.id);
              const toggleBtn = document.createElement('button');
              toggleBtn.type = 'button';
              toggleBtn.className = 'criteria-toggle';
              toggleBtn.innerHTML = criteriaExpanded ? expandedGlyph : collapsedGlyph;
              toggleBtn.setAttribute(
                'aria-expanded',
                criteriaExpanded ? 'true' : 'false'
              );
              toggleBtn.setAttribute('aria-controls', detailsId);
              toggleBtn.setAttribute('aria-label', 'Toggle criteria details');
              toggleBtn.addEventListener('mousedown', (event) => {
                if (event.detail > 0) {
                  event.preventDefault();
                }
              });
              toggleBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                if (expandedMetrics.has(row.metric.id)) {
                  expandedMetrics.delete(row.metric.id);
                } else {
                  expandedMetrics.add(row.metric.id);
                }
                renderTable();
                if (event.detail > 0) {
                  setTimeout(() => toggleBtn.blur(), 0);
                }
              });
              metricText.appendChild(document.createTextNode('\u00A0'));
              metricText.appendChild(toggleBtn);
              metricTitle.appendChild(metricText);
              metricCell.appendChild(metricTitle);
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

            const criteriaCell = document.createElement('td');
            criteriaCell.className = 'col-scoring-criteria';
            if (row.type === 'metric') {
              const curve = ensureCurve(scenario, row.metric.id);
              const layerSelect = document.createElement('select');
              const layers = Array.isArray(curve?.layers) ? curve.layers : [];
              const activeLayerId =
                curve?.activeLayerId && layers.some((layer) => layer.id === curve.activeLayerId)
                  ? curve.activeLayerId
                  : layers[0]?.id || '';
              if (curve && activeLayerId && curve.activeLayerId !== activeLayerId) {
                curve.activeLayerId = activeLayerId;
              }
              layers.forEach((layer) => {
                const option = document.createElement('option');
                option.value = layer.id;
                option.textContent = layer.name || 'Default';
                layerSelect.appendChild(option);
              });
              layerSelect.value = activeLayerId;
              layerSelect.addEventListener('change', () => {
                if (curve) {
                  curve.activeLayerId = layerSelect.value;
                }
                renderTable();
              });
              criteriaCell.appendChild(layerSelect);
            } else {
              criteriaCell.textContent = '-';
            }
            tr.appendChild(criteriaCell);

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

            const functionEstimateCell = document.createElement('td');
            functionEstimateCell.className = 'col-function-estimate';
            if (row.type === 'metric') {
              const curve = ensureCurve(scenario, row.metric.id);
              const fieldValue = Number.parseFloat(scenario.fieldValues[row.metric.id]);
              functionEstimateCell.textContent = getFunctionEstimateText(curve, fieldValue);
            } else {
              functionEstimateCell.textContent = '-';
            }
            tr.appendChild(functionEstimateCell);

            if (isFunctionSliderOwner) {
              const scoreRowSpan = functionHasExpandedCriteria
                ? 1
                : functionMeta.totalSpan || row._functionSpan || 1;
              tr.appendChild(buildFunctionScoreCell(row.functionId, scoreRowSpan));
            } else if (functionHasExpandedCriteria) {
              const placeholderScoreCell = document.createElement('td');
              placeholderScoreCell.className =
                'col-function-score function-score-cell function-score-cell-empty';
              tr.appendChild(placeholderScoreCell);
            }

            if (showMappings) {
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
          }

          tbody.appendChild(tr);
        });

        const summaryValues = updateSummary(functionScores);
        renderCharts(functionOrder, functionScores, summaryValues);
      };

      advancedToggle.addEventListener('change', () => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        scenario.showAdvancedScoring = advancedToggle.checked;
        renderTable();
      });
      mappingToggle.addEventListener('change', () => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        scenario.showFunctionMappings = mappingToggle.checked;
        renderTable();
      });
      rollupToggle.addEventListener('change', () => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        scenario.showRollupComputations = rollupToggle.checked;
        renderTable();
      });
      suggestedCueToggle.addEventListener('change', () => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        scenario.showSuggestedFunctionScoresCue = suggestedCueToggle.checked;
        renderTable();
      });
      sliderLabelsToggle.addEventListener('change', () => {
        const scenario = getActiveScenario();
        if (!scenario) {
          return;
        }
        scenario.showFunctionScoreCueLabels = sliderLabelsToggle.checked;
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

      if (!scenarios.length) {
        const exampleScenario = createExampleScenario();
        scenarios.push(exampleScenario);
        activeScenarioId = exampleScenario.id;
      }
      scenarios.forEach((scenario) => ensureScenarioViewOptions(scenario));

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

