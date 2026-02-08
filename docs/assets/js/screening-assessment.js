(() => {
  const container = document.querySelector('.screening-assessment');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const assetPath = (path) => `${baseUrl}${path}`;
  const dataUrl =
    container.dataset.metricsUrl || assetPath('/assets/data/screening-metrics.tsv');
  const curvesUrl =
    container.dataset.curvesUrl || assetPath('/assets/data/screening-reference-curves.json');
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
  const fallbackCriteriaName = 'Screening';
  const defaultProfileId = 'screening-default';
  const collapsedGlyph = '&#9656;';
  const expandedGlyph = '&#9662;';
  const storageKey = 'staf_screening_assessments_v1';
  const predefinedName = 'Stream Condition Screening (SCS)';
  const legacyPredefinedName = 'Predefined Screening Assessment';
  const notifyAssessmentUpdate = () => {
    if (window.dispatchEvent) {
      window.dispatchEvent(
        new CustomEvent('staf:assessment-updated', { detail: { tier: 'screening' } })
      );
    }
  };

  const normalizeText = (value) =>
    value
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '');

  const slugify = (value) =>
    value
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 80);

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

  const normalizeCurveType = (value) => {
    const raw = (value || '').toString().toLowerCase();
    if (raw === 'qualitative') {
      return 'categorical';
    }
    if (raw === 'categorical' || raw === 'quantitative') {
      return raw;
    }
    return raw || 'categorical';
  };

  const isCategoricalCurve = (curve) =>
    normalizeCurveType(curve?.xType) === 'categorical';

  const parseScore = (value) => {
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const clampScore = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));

  const roundScore = (value, places = 2) => {
    const factor = 10 ** places;
    return Math.round(value * factor) / factor;
  };

  const applyIndexRanges = (points) => {
    const scored = points
      .map((point) => ({ point, y: parseScore(point.y) }))
      .filter((entry) => entry.y !== null);
    if (scored.length === 0) {
      return;
    }
    const sorted = [...scored].sort((a, b) => b.y - a.y);
    const boundaries = sorted.slice(0, -1).map((entry, index) =>
      (entry.y + sorted[index + 1].y) / 2
    );
    sorted.forEach((entry, index) => {
      const max = index === 0 ? 1 : boundaries[index - 1];
      const min = index === sorted.length - 1 ? 0 : boundaries[index];
      entry.point.yMax = roundScore(clampScore(max));
      entry.point.yMin = roundScore(clampScore(min));
    });
  };

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

  const buildRatings = (metricIds, existingRatings, optionsForMetric, scenario) => {
    const ratings = {};
    const fallbackLabels = ratingOptions.map((opt) => opt.label);
    metricIds.forEach((id) => {
      const current = existingRatings ? existingRatings[id] : null;
      const options =
        typeof optionsForMetric === 'function' ? optionsForMetric(id, scenario) : fallbackLabels;
      const labels = Array.isArray(options) && options.length ? options : fallbackLabels;
      const isValid = labels.includes(current);
      let next = isValid ? current : null;
      if (!next) {
        next = labels.includes(defaultRating) ? defaultRating : labels[0] || defaultRating;
      }
      ratings[id] = next;
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
      const [metricsResult, functionsResult, mappingResult, curvesResult] =
        await Promise.allSettled([
          fetchText(dataUrl),
          fetchJson(functionsUrl),
          fetchJson(mappingUrl),
          curvesUrl ? fetchJson(curvesUrl) : Promise.resolve(null),
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
      const functionAliases = new Map([
        ['bed composition and bedform dynamics', 'bed composition and large wood'],
      ]);
      const functionByName = new Map();
      const functionOrderById = new Map();
      const functionOrderByName = new Map();
      const disciplineOrder = [];
      const disciplineOrderByName = new Map();
      functionsList.forEach((fn, index) => {
        const functionName = fn.name || '';
        const discipline = fn.category || '';
        const normalizedFunction = normalizeText(functionName);
        if (normalizedFunction) {
          functionByName.set(normalizedFunction, fn);
          functionOrderByName.set(normalizedFunction, index);
        }
        if (fn.id) {
          functionByName.set(normalizeText(String(fn.id).replace(/-/g, ' ')), fn);
          functionOrderById.set(fn.id, index);
        }
        const normalizedDiscipline = normalizeText(discipline);
        if (normalizedDiscipline && !disciplineOrderByName.has(normalizedDiscipline)) {
          disciplineOrderByName.set(normalizedDiscipline, disciplineOrder.length);
          disciplineOrder.push(discipline);
        }
      });
      functionAliases.forEach((target, alias) => {
        const match = functionByName.get(normalizeText(target));
        if (match) {
          functionByName.set(normalizeText(alias), match);
        }
      });
      const getFunctionMatch = (value) => functionByName.get(normalizeText(value || '')) || null;
      const resolveFunctionAlias = (value) => {
        const raw = (value || '').trim();
        const aliasTarget = functionAliases.get(raw.toLowerCase());
        return aliasTarget || raw;
      };
      const functionById = new Map(functionsList.map((fn) => [fn.id, fn]));
      const mappingById = mappingList.reduce((acc, item) => {
        acc[item.id] = item;
        return acc;
      }, {});
      const curveOverrides =
        curvesResult && curvesResult.status === 'fulfilled' && curvesResult.value
          ? curvesResult.value.curves || {}
          : {};

      const libraryIdCounts = new Map();
      const buildLibraryId = (functionName, metricName, explicitId = '') => {
        const explicit = (explicitId || '').trim();
        if (explicit) {
          return explicit;
        }
        const baseId =
          slugify(`${functionName}-${metricName}`) || slugify(metricName) || 'metric';
        const count = libraryIdCounts.get(baseId) || 0;
        libraryIdCounts.set(baseId, count + 1);
        return count ? `${baseId}-${count + 1}` : baseId;
      };

      const metrics = metricsRaw.map((row, index) => {
        const rawFunctionName = row.Function || row.function || '';
        const rawDiscipline = row.Discipline || row.discipline || '';
        const functionMatch = getFunctionMatch(resolveFunctionAlias(rawFunctionName));
        const libraryId = buildLibraryId(
          rawFunctionName,
          row.Metric || row.metric || '',
          row['Metric ID'] || row.metric_id || row.metricId || ''
        );
        return {
          id: `metric-${index + 1}`,
          libraryId,
          discipline: functionMatch?.category || rawDiscipline || '',
          functionName: functionMatch?.name || rawFunctionName,
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
          sourceOrder: index,
        };
      });
      const getDisciplineRank = (discipline) => {
        const key = normalizeText(discipline || '');
        const rank = disciplineOrderByName.get(key);
        return Number.isFinite(rank) ? rank : 9999;
      };
      const getFunctionRank = (metric) => {
        if (metric && metric.functionId && functionOrderById.has(metric.functionId)) {
          return functionOrderById.get(metric.functionId);
        }
        const key = normalizeText(
          resolveFunctionAlias((metric && metric.functionName) || '')
        );
        if (functionOrderByName.has(key)) {
          return functionOrderByName.get(key);
        }
        return 9999;
      };
      const sortMetricsForDisplay = (a, b) => {
        const disciplineRankA = getDisciplineRank(a?.discipline);
        const disciplineRankB = getDisciplineRank(b?.discipline);
        if (disciplineRankA !== disciplineRankB) {
          return disciplineRankA - disciplineRankB;
        }
        const functionRankA = getFunctionRank(a);
        const functionRankB = getFunctionRank(b);
        if (functionRankA !== functionRankB) {
          return functionRankA - functionRankB;
        }
        const sourceOrderA = Number.isFinite(a?.sourceOrder) ? a.sourceOrder : 9999;
        const sourceOrderB = Number.isFinite(b?.sourceOrder) ? b.sourceOrder : 9999;
        if (sourceOrderA !== sourceOrderB) {
          return sourceOrderA - sourceOrderB;
        }
        return (a?.metric || '').localeCompare(b?.metric || '');
      };

      const metricById = new Map(metrics.map((metric) => [metric.id, metric]));
      const metricIdSet = new Set(metrics.map((metric) => metric.id));
      const libraryIdToMetricId = new Map(
        metrics
          .filter((metric) => metric.libraryId)
          .map((metric) => [metric.libraryId, metric.id])
      );

      const resolveMetricId = (metricId) =>
        libraryIdToMetricId.get(metricId) || metricId;

      const resolveLibraryId = (metricId) =>
        metricById.get(metricId)?.libraryId || metricId;

      const buildCriteriaFromProfile = (profile) => {
        if (!profile || !profile.scoring) {
          return {
            optimal: '',
            suboptimal: '',
            marginal: '',
            poor: '',
          };
        }
        if (profile.scoring.type !== 'categorical') {
          return {
            optimal: '',
            suboptimal: '',
            marginal: '',
            poor: '',
          };
        }
        const levels = profile.scoring.rubric?.levels || [];
        const findLevel = (label) =>
          levels.find(
            (level) =>
              normalizeText(level.label || '') === normalizeText(label) ||
              normalizeText(level.ratingId || '') === normalizeText(label)
          );
        return {
          optimal: findLevel('optimal')?.criteriaMarkdown || '',
          suboptimal: findLevel('suboptimal')?.criteriaMarkdown || '',
          marginal: findLevel('marginal')?.criteriaMarkdown || '',
          poor: findLevel('poor')?.criteriaMarkdown || '',
        };
      };

      const ensureLibraryMetric = (detail, profileId) => {
        if (!detail) {
          return null;
        }
        const existing =
          metricById.get(detail.metricId) || metricById.get(resolveMetricId(detail.metricId));
        if (existing) {
          return existing;
        }
        const functionMatch = getFunctionMatch(resolveFunctionAlias(detail.function || ''));
        const profile =
          detail.profiles?.find((p) => p.profileId === profileId) ||
          detail.profiles?.[0];
        const criteria = buildCriteriaFromProfile(profile);
        const metric = {
          id: detail.metricId,
          libraryId: detail.metricId,
          discipline: detail.discipline || functionMatch?.category || 'Other',
          functionName: detail.function || '',
          functionStatement: detail.functionStatement || '',
          functionId: functionMatch ? functionMatch.id : null,
          metric: detail.name || detail.metricId,
          metricStatement: detail.descriptionMarkdown || '',
          isPredefined: false,
          context: detail.methodContextMarkdown || '',
          method: detail.methodContextMarkdown || '',
          howToMeasure: detail.howToMeasureMarkdown || '',
          criteria,
          references: Array.isArray(detail.references)
            ? detail.references.join('; ')
            : '',
          sourceOrder: metrics.length,
        };
        metrics.push(metric);
        metricById.set(metric.id, metric);
        metricIdSet.add(metric.id);
        libraryIdToMetricId.set(metric.libraryId, metric.id);
        return metric;
      };

      const predefinedMetricIds = metrics
        .filter((metric) => metric.isPredefined)
        .map((metric) => metric.id);
      if (!predefinedMetricIds.length) {
        const starMetricIdsByFunction = new Map();
        metrics.forEach((metric) => {
          if (!metric.functionId) {
            return;
          }
          if (!starMetricIdsByFunction.has(metric.functionId)) {
            starMetricIdsByFunction.set(metric.functionId, metric.id);
          }
        });
        predefinedMetricIds.push(...Array.from(starMetricIdsByFunction.values()));
      }

      const defaultCurveIndexValues = [0, 0.3, 0.69, 1];
  const defaultCurveRanges = new Map([
    ['optimal', { min: 0.76, max: 1 }],
    ['suboptimal', { min: 0.5, max: 0.75 }],
    ['marginal', { min: 0.25, max: 0.49 }],
    ['poor', { min: 0, max: 0.24 }],
  ]);

  const applyDefaultRanges = (points) => {
    if (!Array.isArray(points) || points.length === 0) {
      return false;
    }
    const allMatch = points.every((point) =>
      defaultCurveRanges.has(normalizeText(point.x))
    );
    if (!allMatch) {
      return false;
    }
    points.forEach((point) => {
      const range = defaultCurveRanges.get(normalizeText(point.x));
      if (range) {
        point.yMin = range.min;
        point.yMax = range.max;
      }
    });
    return true;
  };

      const buildDefaultCurve = (metric) => {
        const points = [
          {
            x: 'Optimal',
            y: 1,
            description: metric?.criteria?.optimal || '',
          },
          {
            x: 'Suboptimal',
            y: 0.69,
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
        points.forEach((point) => {
          const key = normalizeText(point.x);
          const range = defaultCurveRanges.get(key);
          if (range) {
            point.yMin = range.min;
            point.yMax = range.max;
          }
        });
        return {
          name: fallbackCriteriaName,
          xType: 'categorical',
          indexRange: true,
          units: '',
          layers: [
            {
              id: layerId,
              name: fallbackCriteriaName,
              points,
            },
          ],
          activeLayerId: layerId,
        };
      };

      const cloneCurve = (curve) => JSON.parse(JSON.stringify(curve));

      const normalizeCurve = (curve, metric) => {
        if (!curve) {
          return buildDefaultCurve(metric);
        }
        const rawXType = curve.xType || curve.x_type || 'categorical';
        const xType = normalizeCurveType(rawXType);
        const indexRange =
          typeof curve.indexRange === 'boolean'
            ? curve.indexRange
            : typeof curve.index_range === 'boolean'
            ? curve.index_range
            : xType === 'categorical';
        const units = curve.units || '';
        const legacyDefault = normalizeText('Default');
        const nameValue = curve.name || fallbackCriteriaName;
        const name =
          normalizeText(nameValue) === legacyDefault ? fallbackCriteriaName : nameValue;
        const layers = Array.isArray(curve.layers) && curve.layers.length > 0 ? curve.layers : [];
        if (!layers.length) {
          return buildDefaultCurve(metric);
        }
        const normalizedLayers = layers.map((layer) => ({
          id: layer.id || generateId(),
          name: (() => {
            const rawName = layer.name || fallbackCriteriaName;
            return normalizeText(rawName) === legacyDefault ? fallbackCriteriaName : rawName;
          })(),
          points: Array.isArray(layer.points)
            ? layer.points.map((point) => ({
                x: point.x ?? '',
                y: point.y ?? '',
                yMin: point.yMin ?? point.y_min ?? '',
                yMax: point.yMax ?? point.y_max ?? '',
                description: point.description ?? '',
              }))
            : [],
        }));
        if (indexRange) {
          normalizedLayers.forEach((layer) => {
            const usedDefaults =
              xType === 'categorical' && applyDefaultRanges(layer.points);
            const missingRange = layer.points.some(
              (point) =>
                parseScore(point.yMin) === null || parseScore(point.yMax) === null
            );
            if (!usedDefaults && missingRange) {
              applyIndexRanges(layer.points);
            }
          });
        }
        return {
          name,
          xType,
          indexRange,
          units,
          layers: normalizedLayers,
          activeLayerId: curve.activeLayerId || normalizedLayers[0].id,
        };
      };

      const buildCurveMap = (metricIds) => {
        const curves = {};
        metricIds.forEach((id) => {
          const metric = metricById.get(id);
          if (metric) {
            curves[id] = normalizeCurve(curveOverrides[id], metric);
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
            scenario.curves[metricId] = normalizeCurve(curveOverrides[metricId], metric);
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
          metricProfiles: ids.reduce((acc, id) => {
            acc[id] = defaultProfileId;
            return acc;
          }, {}),
          defaultCriteriaName: fallbackCriteriaName,
          criteriaOverrides: {},
          showAdvancedScoring: false,
          showRollupComputations: false,
          showFunctionMappings: false,
          showSuggestedFunctionScoresCue: false,
          showFunctionScoreCueLabels: false,
          showCondensedView: true,
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
        const legacyDefault = normalizeText('Default');
        const normalizedDefaultCriteriaName =
          source.defaultCriteriaName &&
          normalizeText(source.defaultCriteriaName) === legacyDefault
            ? fallbackCriteriaName
            : source.defaultCriteriaName || fallbackCriteriaName;
        const newScenario = {
          id: generateId(),
          type: 'custom',
          name: `${baseName} Copy`,
          applicability: source.applicability || '',
          notes: source.notes || '',
          metricIds: Array.from(metricIds),
          ratings: buildRatings(metricIds, source.ratings),
          curves: source.curves ? cloneCurve(source.curves) : buildCurveMap(metricIds),
          functionScores: source.functionScores ? { ...source.functionScores } : {},
          metricProfiles: source.metricProfiles
            ? { ...source.metricProfiles }
            : Array.from(metricIds).reduce((acc, id) => {
                acc[id] = defaultProfileId;
                return acc;
              }, {}),
          defaultCriteriaName: normalizedDefaultCriteriaName,
          criteriaOverrides: source.criteriaOverrides
            ? { ...source.criteriaOverrides }
            : {},
          showAdvancedScoring: Boolean(source.showAdvancedScoring),
          showRollupComputations: Boolean(source.showRollupComputations),
          showFunctionMappings: Boolean(source.showFunctionMappings),
          showSuggestedFunctionScoresCue: Boolean(source.showSuggestedFunctionScoresCue),
          showFunctionScoreCueLabels: Boolean(source.showFunctionScoreCueLabels),
          showCondensedView:
            typeof source.showCondensedView === 'boolean'
              ? source.showCondensedView
              : true,
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
        const legacyDefault = normalizeText('Default');
        const rawDefaultCriteriaName =
          scenario.defaultCriteriaName || fallbackCriteriaName;
        const normalizedDefaultCriteriaName =
          normalizeText(rawDefaultCriteriaName) === legacyDefault
            ? fallbackCriteriaName
            : rawDefaultCriteriaName;
        const curves = scenario.curves ? cloneCurve(scenario.curves) : buildCurveMap(ids);
        ids.forEach((id) => {
          if (curves[id]) {
            curves[id] = normalizeCurve(curves[id], metricById.get(id));
          }
        });
        const metricProfiles = ids.reduce((acc, id) => {
          const profileId = scenario.metricProfiles ? scenario.metricProfiles[id] : null;
          acc[id] = profileId || defaultProfileId;
          return acc;
        }, {});
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
          curves,
          metricProfiles,
          defaultCriteriaName: normalizedDefaultCriteriaName,
          criteriaOverrides: scenario.criteriaOverrides ? { ...scenario.criteriaOverrides } : {},
          functionScores: scenario.functionScores ? { ...scenario.functionScores } : {},
          showAdvancedScoring: Boolean(scenario.showAdvancedScoring),
          showRollupComputations: Boolean(scenario.showRollupComputations),
          showFunctionMappings: Boolean(scenario.showFunctionMappings),
          showSuggestedFunctionScoresCue: Boolean(
            scenario.showSuggestedFunctionScoresCue
          ),
          showFunctionScoreCueLabels: Boolean(scenario.showFunctionScoreCueLabels),
          showCondensedView:
            typeof scenario.showCondensedView === 'boolean'
              ? scenario.showCondensedView
              : true,
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
      const expandedFunctions = new Set();
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

      const getCurveLayerByName = (curve, name) => {
        if (!curve || !name) {
          return null;
        }
        const target = normalizeText(name);
        return (
          curve.layers.find((layer) => normalizeText(layer.name || '') === target) || null
        );
      };

      const getCriteriaOverrides = () => {
        if (!activeScenario) {
          return {};
        }
        if (!activeScenario.criteriaOverrides) {
          activeScenario.criteriaOverrides = {};
        }
        return activeScenario.criteriaOverrides;
      };

      const getAvailableCriteriaNames = () => {
        if (!activeScenario) {
          return [fallbackCriteriaName];
        }
        const names = new Set();
        (activeScenario.metricIds || []).forEach((metricId) => {
          const curve = ensureCurve(activeScenario, metricId);
          if (!curve || !Array.isArray(curve.layers)) {
            return;
          }
          curve.layers.forEach((layer) => {
            const name = (layer.name || '').trim();
            if (name) {
              names.add(name);
            }
          });
        });
        const list = Array.from(names);
        if (list.length === 0) {
          return [fallbackCriteriaName];
        }
        list.sort((a, b) => a.localeCompare(b));
        const fallbackIndex = list.findIndex(
          (name) => normalizeText(name) === normalizeText(fallbackCriteriaName)
        );
        if (fallbackIndex > 0) {
          const [fallbackName] = list.splice(fallbackIndex, 1);
          list.unshift(fallbackName);
        }
        return list;
      };

      const resolveDefaultCriteriaName = () => {
        const available = getAvailableCriteriaNames();
        if (!available.length) {
          return fallbackCriteriaName;
        }
        if (activeScenario?.defaultCriteriaName) {
          const match = available.find(
            (name) =>
              normalizeText(name) === normalizeText(activeScenario.defaultCriteriaName)
          );
          if (match) {
            return match;
          }
        }
        const fallbackMatch = available.find(
          (name) => normalizeText(name) === normalizeText(fallbackCriteriaName)
        );
        return fallbackMatch || available[0];
      };

      const getCriteriaLayerForMetric = (metricId, scenario = activeScenario) => {
        if (!scenario || !metricId) {
          return null;
        }
        const curve = ensureCurve(scenario, metricId);
        if (!curve) {
          return null;
        }
        const overrides =
          scenario === activeScenario ? getCriteriaOverrides() : scenario.criteriaOverrides || {};
        const overrideId = overrides[metricId];
        if (overrideId) {
          const overrideLayer = curve.layers.find((layer) => layer.id === overrideId);
          if (overrideLayer) {
            return overrideLayer;
          }
        }
        const defaultName =
          scenario === activeScenario
            ? resolveDefaultCriteriaName()
            : scenario.defaultCriteriaName || fallbackCriteriaName;
        return getCurveLayerByName(curve, defaultName) || getCurveLayer(curve);
      };

      const fallbackRatingLabels = ratingOptions.map((opt) => opt.label);

      const getRatingOptionsFromCurve = (curve, layerOverride) => {
        if (!curve || !Array.isArray(curve.layers) || !curve.layers.length) {
          return fallbackRatingLabels;
        }
        const layer = layerOverride || getCurveLayer(curve);
        if (!layer || !Array.isArray(layer.points)) {
          return fallbackRatingLabels;
        }
        const labels = [];
        const seen = new Set();
        layer.points.forEach((point) => {
          const label = point && point.x != null ? String(point.x).trim() : '';
          const key = normalizeText(label);
          if (!key || seen.has(key)) {
            return;
          }
          seen.add(key);
          labels.push(label);
        });
        return labels.length ? labels : fallbackRatingLabels;
      };

      const getMetricRatingOptions = (metricId, scenario) => {
        if (!metricId) {
          return fallbackRatingLabels;
        }
        let curve = null;
        let layer = null;
        if (scenario) {
          curve = ensureCurve(scenario, metricId);
          layer = getCriteriaLayerForMetric(metricId, scenario);
        }
        if (!curve) {
          const metric = metricById.get(metricId);
          if (metric) {
            curve = normalizeCurve(curveOverrides[metricId], metric);
          }
        }
        return getRatingOptionsFromCurve(curve, layer);
      };

      const resolveMetricRating = (metricId, options) => {
        const labels = Array.isArray(options) && options.length ? options : fallbackRatingLabels;
        const current = metricRatings.get(metricId);
        const isValid = labels.includes(current);
        const next = isValid
          ? current
          : labels.includes(defaultRating)
          ? defaultRating
          : labels[0] || defaultRating;
        const changed = next !== current;
        if (changed) {
          metricRatings.set(metricId, next);
          if (activeScenario) {
            if (!activeScenario.ratings) {
              activeScenario.ratings = {};
            }
            activeScenario.ratings[metricId] = next;
          }
        }
        return { value: next, changed };
      };

      const getMetricIndexRange = (metricId) => {
        if (!metricId || !activeScenario) {
          return null;
        }
        const curve = ensureCurve(activeScenario, metricId);
        const layer = getCriteriaLayerForMetric(metricId);
        if (!curve || !layer || !isCategoricalCurve(curve)) {
          return null;
        }
        const rating = metricRatings.get(metricId) || defaultRating;
        const target = normalizeText(rating);
        const point = layer.points.find(
          (entry) => normalizeText(String(entry.x ?? '')) === target
        );
        if (!point) {
          return null;
        }
        const yMin = parseScore(point.yMin);
        const yMax = parseScore(point.yMax);
        const yValue = parseScore(point.y);
        if (
          curve.indexRange &&
          yMin !== null &&
          yMax !== null
        ) {
          return { min: yMin, max: yMax, avg: (yMin + yMax) / 2, hasRange: true };
        }
        if (yValue !== null) {
          return { min: yValue, max: yValue, avg: yValue, hasRange: false };
        }
        return null;
      };

      const getMetricIndexScore = (metricId) => {
        const range = getMetricIndexRange(metricId);
        return range ? range.avg : null;
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
        const isCategorical = isCategoricalCurve(curve);
        const useIndexRange = Boolean(curve.indexRange);
        const table = document.createElement('table');
        table.className = 'curve-table';
        const thead = document.createElement('thead');
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
          if (useIndexRange) {
            const rangeWrap = document.createElement('div');
            rangeWrap.className = 'curve-index-range-inputs';
            const yMaxInput = document.createElement('input');
            yMaxInput.type = 'number';
            yMaxInput.step = '0.01';
            yMaxInput.min = '0';
            yMaxInput.max = '1';
            yMaxInput.placeholder = 'Max';
            yMaxInput.value = point.yMax ?? '';
            yMaxInput.disabled = curveReadOnly;
            const yMinInput = document.createElement('input');
            yMinInput.type = 'number';
            yMinInput.step = '0.01';
            yMinInput.min = '0';
            yMinInput.max = '1';
            yMinInput.placeholder = 'Min';
            yMinInput.value = point.yMin ?? '';
            yMinInput.disabled = curveReadOnly;
            const updateRange = () => {
              if (curveReadOnly) {
                return;
              }
              point.yMax = yMaxInput.value;
              point.yMin = yMinInput.value;
              const min = parseScore(point.yMin);
              const max = parseScore(point.yMax);
              if (min !== null && max !== null) {
                point.y = roundScore((min + max) / 2);
              }
              renderCurveChart();
            };
            yMaxInput.addEventListener('input', updateRange);
            yMinInput.addEventListener('input', updateRange);
            rangeWrap.appendChild(yMaxInput);
            rangeWrap.appendChild(yMinInput);
            yCell.appendChild(rangeWrap);
          } else {
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
          }

          let descCell = null;
          if (isCategorical) {
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
            layer.points.splice(index, 0, {
              x: '',
              y: '',
              yMin: useIndexRange ? '' : undefined,
              yMax: useIndexRange ? '' : undefined,
              description: '',
            });
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
        const isCategorical = isCategoricalCurve(curve);
        const points = isCategorical
          ? layer.points
              .map((point, index) => {
                const yMin = parseScore(point.yMin);
                const yMax = parseScore(point.yMax);
                const y = parseScore(point.y);
                const avg =
                  y !== null
                    ? y
                    : yMin !== null && yMax !== null
                    ? (yMin + yMax) / 2
                    : null;
                return {
                  x: index,
                  y: avg,
                  yMin,
                  yMax,
                  label: point.x ?? '',
                };
              })
              .filter((point) => point.y !== null || (point.yMin !== null && point.yMax !== null))
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
        const scaleY = (value) => padding.top + (1 - clampScore(value)) * height;

        if (isCategorical) {
          const step = points.length > 1 ? width / (points.length - 1) : width;
          const boxWidth = Math.min(28, step * 0.6);
          points.forEach((point) => {
            const x = scaleX(point.x);
            const min = point.yMin !== null ? point.yMin : point.y ?? 0;
            const max = point.yMax !== null ? point.yMax : point.y ?? 0;
            const top = scaleY(Math.max(min, max));
            const bottom = scaleY(Math.min(min, max));
            const boxHeight = Math.max(2, bottom - top);
            ctx.fillStyle = 'rgba(59, 130, 246, 0.18)';
            ctx.strokeStyle = '#3b82f6';
            ctx.lineWidth = 1.5;
            ctx.fillRect(x - boxWidth / 2, top, boxWidth, boxHeight);
            ctx.strokeRect(x - boxWidth / 2, top, boxWidth, boxHeight);
            if (point.y !== null) {
              const midY = scaleY(point.y);
              ctx.strokeStyle = '#111111';
              ctx.lineWidth = 2;
              ctx.beginPath();
              ctx.moveTo(x - boxWidth / 2, midY);
              ctx.lineTo(x + boxWidth / 2, midY);
              ctx.stroke();
            }
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

      const buildCurveSummaryTable = (curve, layerOverride) => {
        const table = document.createElement('table');
        table.className = 'curve-summary-table';
        const layer = layerOverride || getCurveLayer(curve);
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
          const min = parseScore(point.yMin);
          const max = parseScore(point.yMax);
          if (curve.indexRange && min !== null && max !== null) {
            cell.textContent = `${min.toFixed(2)}-${max.toFixed(2)}`;
          } else {
            const value = parseScore(point.y);
            cell.textContent = value !== null ? value.toFixed(2) : '-';
          }
          indexRow.appendChild(cell);
        });

        const functionScoreRow = document.createElement('tr');
        const functionScoreLabel = document.createElement('th');
        functionScoreLabel.textContent = 'Function Score';
        functionScoreRow.appendChild(functionScoreLabel);
        layer.points.forEach((point) => {
          const cell = document.createElement('td');
          const min = parseScore(point.yMin);
          const max = parseScore(point.yMax);
          if (curve.indexRange && min !== null && max !== null) {
            const minScore = Math.floor(min * 15);
            const maxScore = Math.ceil(max * 15);
            cell.textContent = `${minScore}-${maxScore}`;
          } else {
            const value = parseScore(point.y);
            const score = value !== null ? Math.round(value * 15) : null;
            cell.textContent = score === null ? '-' : String(score);
          }
          functionScoreRow.appendChild(cell);
        });

        table.appendChild(valueRow);
        table.appendChild(indexRow);
        table.appendChild(functionScoreRow);
        return table;
      };

      const openCurveModal = (metricId, preferredLayerId) => {
        if (window.dispatchEvent) {
          const profileId =
            activeScenario?.metricProfiles?.[metricId] || defaultProfileId;
          window.dispatchEvent(
            new CustomEvent('staf:open-inspector', {
              detail: {
                tier: 'screening',
                metricId: resolveLibraryId(metricId),
                profileId,
                tab: 'curves',
              },
            })
          );
          return;
        }
        if (!curveModal) {
          return;
        }
        const metric = metricById.get(metricId);
        activeCurveMetricId = metricId;
        curveReadOnly = activeScenario?.type === 'predefined';
        const curve = ensureCurve(activeScenario, metricId);
        if (curve && preferredLayerId) {
          const hasLayer = curve.layers.find((layer) => layer.id === preferredLayerId);
          if (hasLayer) {
            curve.activeLayerId = preferredLayerId;
          }
        }
        if (curveMetricName && metric) {
          curveMetricName.value = metric.metric;
        }
        if (curveUnitsInput) {
          curveUnitsInput.value = curve.units || '';
          curveUnitsInput.disabled = curveReadOnly;
        }
        if (curveXType) {
          curveXType.value = normalizeCurveType(curve.xType) || 'categorical';
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
          curve.xType = normalizeCurveType(curveXType.value);
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
          renderTable();
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
          renderTable();
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

      const mappingToggleLabel = document.createElement('label');
      mappingToggleLabel.className = 'screening-advanced-toggle';
      const mappingToggle = document.createElement('input');
      mappingToggle.type = 'checkbox';
      mappingToggle.className = 'screening-mapping-toggle-input';
      const mappingToggleText = document.createElement('span');
      mappingToggleText.textContent = 'Show Function Mappings';
      mappingToggleLabel.appendChild(mappingToggle);
      mappingToggleLabel.appendChild(mappingToggleText);

      scoringControls.appendChild(advancedToggleLabel);
      scoringControls.appendChild(mappingToggleLabel);
      scoringControls.appendChild(rollupToggleLabel);
      scoringControls.appendChild(suggestedCueToggleLabel);
      scoringControls.appendChild(sliderLabelsToggleLabel);
      // Always show condensed view (function score column).

      const controls = document.createElement('div');
      controls.className = 'screening-controls';

      const search = document.createElement('input');
      search.type = 'search';
      search.placeholder = 'Search';
      search.setAttribute('aria-label', 'Search');

      const disciplineFilter = document.createElement('select');
      disciplineFilter.setAttribute('aria-label', 'Filter by discipline');
      const disciplineValues = [];
      const seenDisciplines = new Set();
      disciplineOrder.forEach((discipline) => {
        const key = normalizeText(discipline);
        if (!key || seenDisciplines.has(key)) {
          return;
        }
        if (metrics.some((metric) => normalizeText(metric.discipline || '') === key)) {
          disciplineValues.push(discipline);
          seenDisciplines.add(key);
        }
      });
      metrics.forEach((metric) => {
        const key = normalizeText(metric.discipline || '');
        if (!key || seenDisciplines.has(key)) {
          return;
        }
        disciplineValues.push(metric.discipline);
        seenDisciplines.add(key);
      });
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
      libraryButton.setAttribute('aria-label', 'View Metric Library');
      libraryButton.setAttribute('data-open-metric-library', 'true');
      libraryButton.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
        '<path d="M3 5.5h7.5c1.7 0 3 1.1 3 2.5v11H6c-1.7 0-3-1.1-3-2.5V5.5z" fill="none" stroke="currentColor" stroke-width="1.5"></path>' +
        '<path d="M21 5.5h-7.5c-1.7 0-3 1.1-3 2.5v11h7.5c1.7 0 3-1.1 3-2.5V5.5z" fill="none" stroke="currentColor" stroke-width="1.5"></path>' +
        '<path d="M6 8h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path>' +
        '<path d="M6 11h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path>' +
        '</svg>';
      const libraryText = document.createElement('span');
      libraryText.textContent = 'Metric Library';
      libraryButton.appendChild(libraryText);

      controls.appendChild(search);
      controls.appendChild(disciplineFilter);
      controls.appendChild(libraryButton);

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

      const setMetricLibraryFilters = (discipline, functionName) => {
        const workbench = container.querySelector('.assessment-workbench');
        if (!workbench) {
          return;
        }
        const disciplineSelect = workbench.querySelector('.metric-library-discipline');
        const functionSelect = workbench.querySelector('.metric-library-function');
        const setSelectValue = (select, value) => {
          if (!select) {
            return;
          }
          const normalizedValue = normalizeText(value || '');
          const match = Array.from(select.options).find(
            (option) => normalizeText(option.value || '') === normalizedValue
          );
          select.value = match ? match.value : 'all';
          select.dispatchEvent(new Event('change', { bubbles: true }));
        };
        if (disciplineSelect) {
          setSelectValue(disciplineSelect, discipline);
        }
        if (functionSelect) {
          setSelectValue(functionSelect, functionName);
        }
      };

      const openMetricLibraryWithFilters = (discipline, functionName) => {
        if (window.dispatchEvent) {
          window.dispatchEvent(
            new CustomEvent('staf:set-library-filters', {
              detail: { tier: 'screening', discipline, functionName },
            })
          );
          return;
        }
        const workbench = container.querySelector('.assessment-workbench');
        const leftSidebar = workbench?.querySelector('.metric-library-sidebar');
        if (leftSidebar) {
          leftSidebar.classList.remove('is-collapsed');
          workbench.classList.remove('is-left-collapsed');
        } else if (libraryButton) {
          libraryButton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        }
        setMetricLibraryFilters(discipline, functionName);
      };

      advancedToggle.addEventListener('change', () => {
        if (!activeScenario) {
          return;
        }
        activeScenario.showAdvancedScoring = advancedToggle.checked;
        store.save();
        renderTable();
      });

      rollupToggle.addEventListener('change', () => {
        if (!activeScenario) {
          return;
        }
        activeScenario.showRollupComputations = rollupToggle.checked;
        store.save();
        renderTable();
      });

      mappingToggle.addEventListener('change', () => {
        if (!activeScenario) {
          return;
        }
        activeScenario.showFunctionMappings = mappingToggle.checked;
        store.save();
        renderTable();
      });

      suggestedCueToggle.addEventListener('change', () => {
        if (!activeScenario) {
          return;
        }
        activeScenario.showSuggestedFunctionScoresCue = suggestedCueToggle.checked;
        store.save();
        renderTable();
      });

      sliderLabelsToggle.addEventListener('change', () => {
        if (!activeScenario) {
          return;
        }
        activeScenario.showFunctionScoreCueLabels = sliderLabelsToggle.checked;
        store.save();
        renderTable();
      });

      const table = document.createElement('table');
      table.className = 'screening-table';
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
        '<div>0.30 - 0.69</div>' +
        '<div>0.00 - 0.29</div>' +
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

      if (controlsHost) {
        controlsHost.innerHTML = '';
        controlsHost.appendChild(scoringControls);
        controlsHost.appendChild(controls);
      }
      if (tableHost) {
        tableHost.innerHTML = '';
        tableHost.appendChild(table);
        tableHost.appendChild(summaryTable);
        const parent = tableHost.parentElement;
        if (parent) {
          const existing = parent.querySelector('.screening-charts-panel');
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
          '<th class="col-metric-score">Metric<br>value</th>' +
          '<th class="col-scoring-criteria">Scoring<br>criteria</th>' +
          '<th class="col-index-score">Metric<br>Index</th>' +
          '<th class="col-function-estimate">Function<br>Estimate</th>' +
          '<th class="col-function-score">Function<br>Score</th>' +
          mappingHeaders +
          '</tr>';
      };

      const buildSummary = (showAdvanced, showCondensed, showMappings) => {
        const labelItems = [
          { label: 'Direct Effect', rollup: true },
          { label: 'Indirect Effect', rollup: true },
          { label: 'Weighted Score Total', rollup: true },
          { label: 'Max Weighted Score Total', rollup: true },
          { label: 'Condition Sub-Index', rollup: false },
          { label: 'Ecosystem Condition Index', rollup: false },
        ];
        const baseLabelSpan = (showAdvanced ? 7 : 4) + (showCondensed ? 1 : 0);
        const labelSpan = showMappings ? baseLabelSpan : Math.max(1, baseLabelSpan - 3);
        const totalSpan = labelSpan + 3;

        if (summaryColGroup) {
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
        }

        tfoot.innerHTML = '';
        summaryBody.innerHTML = '';
        summaryCells.physical = [];
        summaryCells.chemical = [];
        summaryCells.biological = [];
        summaryCells.ecosystem = null;
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

      const getShowAdvancedScoring = () => Boolean(activeScenario?.showAdvancedScoring);
      const getShowRollupComputations = () =>
        Boolean(activeScenario?.showRollupComputations);
      const getShowFunctionMappings = () =>
        Boolean(activeScenario?.showFunctionMappings);
      const getShowSuggestedFunctionScoresCue = () =>
        Boolean(activeScenario?.showSuggestedFunctionScoresCue);
      const getShowFunctionScoreCueLabels = () =>
        Boolean(activeScenario?.showFunctionScoreCueLabels);
      const getShowCondensedView = () => true;

      const renderDefaultCriteriaControls = () => {
        if (!activeScenario) {
          return;
        }
        const resolved = resolveDefaultCriteriaName();
        if (activeScenario.defaultCriteriaName !== resolved) {
          activeScenario.defaultCriteriaName = resolved;
          store.save();
        }
        if (advancedToggle) {
          advancedToggle.checked = getShowAdvancedScoring();
        }
        if (rollupToggle) {
          rollupToggle.checked = getShowRollupComputations();
        }
        if (mappingToggle) {
          mappingToggle.checked = getShowFunctionMappings();
        }
        if (suggestedCueToggle) {
          suggestedCueToggle.checked = getShowSuggestedFunctionScoresCue();
        }
        if (sliderLabelsToggle) {
          sliderLabelsToggle.checked = getShowFunctionScoreCueLabels();
        }
      // Condensed view is always enabled.
      };

      const summaryColorForValue = (value) => {
        if (value <= 0.29) {
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
        rangeInput.style.setProperty(
          '--screening-score-color-active',
          palette.active
        );
      };

      const updateScreeningSuggestedBracket = (
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

      const updateScores = (
        indexRows,
        estimateRows,
        functionScoreControls,
        functionOrder
      ) => {
        const functionBuckets = new Map();
        const functionRangeBuckets = new Map();
        const metricScoreRanges = new Map();

        const resolveMetricScoreRange = (metricId) => {
          const indexRange = getMetricIndexRange(metricId);
          if (indexRange) {
            const minScore = Math.floor(indexRange.min * 15);
            const maxScore = Math.ceil(indexRange.max * 15);
            const avgScore = Math.round(indexRange.avg * 15);
            return {
              indexRange,
              scoreRange: {
                minScore,
                maxScore,
                avgScore,
                hasRange: indexRange.hasRange,
              },
            };
          }
          const rating = metricRatings.get(metricId) || defaultRating;
          const ratingMatch = ratingOptions.find((opt) => opt.label === rating);
          const score = ratingMatch ? ratingMatch.score : 0;
          return {
            indexRange: null,
            scoreRange: {
              minScore: score,
              maxScore: score,
              avgScore: score,
              hasRange: false,
            },
          };
        };

        metrics.forEach((metric) => {
          if (!selectedMetricIds.has(metric.id)) {
            return;
          }
          if (!metric.functionId) {
            return;
          }
          if (!functionBuckets.has(metric.functionId)) {
            functionBuckets.set(metric.functionId, []);
            functionRangeBuckets.set(metric.functionId, []);
          }
          const rangeMeta = resolveMetricScoreRange(metric.id);
          metricScoreRanges.set(metric.id, rangeMeta);
          functionBuckets.get(metric.functionId).push(rangeMeta.scoreRange.avgScore);
          functionRangeBuckets.get(metric.functionId).push(rangeMeta.scoreRange);
        });

        const functionSuggestions = new Map();
        functionBuckets.forEach((scores, functionId) => {
          const average =
            scores.length > 0
              ? scores.reduce((sum, value) => sum + value, 0) / scores.length
              : 0;
          functionSuggestions.set(functionId, average);
        });

        const functionRanges = new Map();
        functionRangeBuckets.forEach((ranges, functionId) => {
          if (!ranges.length) {
            return;
          }
          const min = Math.min(...ranges.map((range) => range.minScore));
          const max = Math.max(...ranges.map((range) => range.maxScore));
          const avg =
            ranges.reduce((sum, range) => sum + range.avgScore, 0) / ranges.length;
          const hasSuggestedRange = ranges.some((range) => range.hasRange);
          functionRanges.set(functionId, { min, max, avg, hasSuggestedRange });
        });

        const functionScores = new Map();
        const functionMeta = new Map();
        const functionIds = new Set(
          (Array.isArray(functionOrder) ? functionOrder : [])
            .map((entry) => entry.functionId)
            .filter(Boolean)
        );
        functionBuckets.forEach((_, functionId) => {
          functionIds.add(functionId);
        });

        functionIds.forEach((functionId) => {
          const suggestion = functionSuggestions.get(functionId) ?? 0;
          const range = functionRanges.get(functionId);
          const minLimit = range ? Math.floor(range.min) : 0;
          const maxLimit = range ? Math.ceil(range.max) : 15;
          const hasSuggestedRange = Boolean(range?.hasSuggestedRange);
          const hasStored = Number.isFinite(activeScenario?.functionScores?.[functionId]);
          let value = hasStored ? activeScenario.functionScores[functionId] : Math.round(suggestion);
          if (!hasStored && activeScenario) {
            if (!activeScenario.functionScores) {
              activeScenario.functionScores = {};
            }
            activeScenario.functionScores[functionId] = value;
            store.save();
          }
          value = Math.min(15, Math.max(0, value));
          if (hasStored && activeScenario && activeScenario.functionScores[functionId] !== value) {
            activeScenario.functionScores[functionId] = value;
            store.save();
          }
          if (activeScenario) {
            if (!activeScenario.functionScoreLimits) {
              activeScenario.functionScoreLimits = {};
            }
            activeScenario.functionScoreLimits[functionId] = { minLimit, maxLimit };
          }
          const isOutsideSuggestedRange =
            hasSuggestedRange && (value < minLimit || value > maxLimit);
          functionScores.set(functionId, value);
          functionMeta.set(functionId, {
            value,
            minLimit,
            maxLimit,
            hasSuggestedRange,
            isOutsideSuggestedRange,
          });
        });

        const showSuggestedCue = getShowSuggestedFunctionScoresCue();

        if (Array.isArray(functionScoreControls)) {
          functionScoreControls.forEach(
            ({
              functionId,
              rangeInput,
              valueEl,
              suggestedBracketEl,
              interactionState,
            }) => {
            if (!rangeInput || !functionId) {
              return;
            }
            const meta = functionMeta.get(functionId) || {
              value: 0,
              minLimit: 0,
              maxLimit: 15,
              hasSuggestedRange: false,
              isOutsideSuggestedRange: false,
            };
            rangeInput.min = '0';
            rangeInput.max = '15';
            rangeInput.value = String(meta.value);
            if (valueEl) {
              valueEl.textContent = String(meta.value);
            }
            updateScreeningSliderVisual(rangeInput, meta.value);
            updateScreeningSuggestedBracket(
              suggestedBracketEl,
              meta.minLimit,
              meta.maxLimit,
              meta.hasSuggestedRange
            );
            const interactionActive = Boolean(
              interactionState && interactionState.active
            );
            const showOutOfRangeCue = Boolean(
              meta.isOutsideSuggestedRange &&
                (showSuggestedCue || interactionActive)
            );
            if (valueEl) {
              valueEl.classList.toggle('is-outside-suggested', showOutOfRangeCue);
              if (showOutOfRangeCue) {
                valueEl.title = 'Score is outside suggested range';
              } else {
                valueEl.removeAttribute('title');
              }
            }
          }
          );
        }

        if (Array.isArray(indexRows)) {
          indexRows.forEach(({ metricId, indexCell }) => {
            if (!indexCell) {
              return;
            }
            const value = getMetricIndexScore(metricId);
            indexCell.textContent = value === null ? '-' : value.toFixed(2);
            const layer = getCriteriaLayerForMetric(metricId);
            if (layer && layer.name) {
              indexCell.title = `Criteria: ${layer.name}`;
            } else {
              indexCell.removeAttribute('title');
            }
          });
        }

        if (Array.isArray(estimateRows)) {
          estimateRows.forEach(({ metricId, estimateCell }) => {
            if (!estimateCell) {
              return;
            }
            const meta = metricScoreRanges.get(metricId);
            const indexRange = meta?.indexRange;
            if (indexRange && indexRange.hasRange) {
              const range = meta.scoreRange;
              estimateCell.textContent = `${range.avgScore} (${range.minScore}-${range.maxScore})`;
            } else {
              estimateCell.textContent = '-';
            }
          });
        }

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

        renderCharts(functionOrder, functionScores, {
          physical: physicalIndex,
          chemical: chemicalIndex,
          biological: biologicalIndex,
          ecosystem: ecosystemIndex,
        });
      };

      const renderTable = () => {
        tbody.innerHTML = '';
        let ratingsDirty = false;
        const showAdvanced = getShowAdvancedScoring();
        const showCondensed = getShowCondensedView();
        const showMappings = getShowFunctionMappings();
        const showSuggestedCue = getShowSuggestedFunctionScoresCue();
        const showSliderLabels = getShowFunctionScoreCueLabels();
        if (table) {
          table.classList.toggle('show-advanced-scoring', showAdvanced);
          table.classList.toggle('show-condensed-view', showCondensed);
          table.classList.toggle('show-function-mappings', showMappings);
          table.classList.toggle('show-suggested-function-cues', showSuggestedCue);
          table.classList.toggle('show-function-score-cue-labels', showSliderLabels);
        }
        if (summaryTable) {
          summaryTable.classList.toggle('show-advanced-scoring', showAdvanced);
          summaryTable.classList.toggle('show-condensed-view', showCondensed);
          summaryTable.classList.toggle('show-function-mappings', showMappings);
          summaryTable.hidden = showMappings;
        }
        renderHeader(showMappings, showAdvanced);
        buildSummary(showAdvanced, showCondensed, showMappings);
        renderDefaultCriteriaControls();

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

          const unmatchedFunctionMetrics = metrics
            .filter((metric) => {
              if (!selectedMetricIds.has(metric.id) || metric.functionId) {
                return false;
              }
              if (disciplineValue !== 'all' && metric.discipline !== disciplineValue) {
                return false;
              }
              const metricHaystack = [
                metric.discipline,
                metric.functionName,
                metric.metric,
                metric.context,
                metric.method,
              ]
                .join(' ')
                .toLowerCase();
              return !term || metricHaystack.includes(term);
            })
            .sort(sortMetricsForDisplay);
          unmatchedFunctionMetrics.forEach((metric) => {
            rowsToRender.push({
              type: 'metric',
              discipline: metric.discipline,
              metric,
              functionId: metric.functionId,
              functionMeta: null,
            });
          });
        } else {
          const visibleMetrics = metrics
            .filter((metric) => {
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
            })
            .sort(sortMetricsForDisplay);

          rowsToRender = visibleMetrics.map((metric) => ({
            type: 'metric',
            discipline: metric.discipline,
            metric,
            functionId: metric.functionId,
            functionMeta: metric.functionId ? functionById.get(metric.functionId) : null,
          }));
        }

        const functionOrder = [];
        const seenFunctions = new Set();
        rowsToRender.forEach((rowItem) => {
          const functionId = rowItem.functionId;
          if (!functionId || seenFunctions.has(functionId)) {
            return;
          }
          seenFunctions.add(functionId);
          const name =
            rowItem.functionMeta?.name || rowItem.metric?.functionName || 'Function';
          functionOrder.push({
            functionId,
            name,
            discipline: rowItem.discipline,
          });
        });

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

        const indexRows = [];
        const estimateRows = [];
        const functionScoreControls = [];

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
          const baseColumns = 7 + (showAdvanced ? 3 : 0) + (showCondensed ? 1 : 0);
          const totalColumns = showMappings ? baseColumns : Math.max(1, baseColumns - 3);
          emptyCell.colSpan = totalColumns;
          emptyCell.className = 'empty-cell';
          emptyCell.textContent = 'No metrics selected for this assessment.';
          emptyRow.appendChild(emptyCell);
          tbody.appendChild(emptyRow);
          updateScores(indexRows, estimateRows, functionScoreControls, functionOrder);
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
          let firstMetricIndex = -1;
          let firstExpandedMetricIndex = -1;
          let j = i;
          while (j < renderRows.length && renderRows[j].functionId === rowItem.functionId) {
            if (renderRows[j].type === 'criteria') {
              criteriaCount += 1;
            } else {
              metricCount += 1;
              if (firstMetricIndex === -1) {
                firstMetricIndex = j;
              }
              if (
                firstExpandedMetricIndex === -1 &&
                expandedMetrics.has(renderRows[j].metric?.id)
              ) {
                firstExpandedMetricIndex = j;
              }
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
          const functionScoreOwnerIndex =
            firstExpandedMetricIndex !== -1
              ? firstExpandedMetricIndex
              : firstMetricIndex;
          const hasExpandedMetricOwner = firstExpandedMetricIndex !== -1;
          const functionScoreSpan = hasExpandedMetricOwner
            ? 1
            : Math.max(1, metricCount);
          if (functionScoreOwnerIndex !== -1) {
            for (let k = i; k < j; k += 1) {
              if (renderRows[k].type !== 'metric') {
                continue;
              }
              if (hasExpandedMetricOwner) {
                renderRows[k]._functionScoreSkip = false;
                renderRows[k]._functionScorePlaceholder = k !== functionScoreOwnerIndex;
                renderRows[k]._functionScoreSpan = 1;
              } else {
                renderRows[k]._functionScoreSkip = k !== functionScoreOwnerIndex;
                renderRows[k]._functionScorePlaceholder = false;
                if (k === functionScoreOwnerIndex) {
                  renderRows[k]._functionScoreSpan = functionScoreSpan;
                }
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
          const hasFunctionStatement = Boolean(functionStatement && functionStatement.trim());
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
          if (rowItem.type === 'metric' && expandedMetrics.has(rowItem.metric.id)) {
            row.classList.add('metric-expanded');
          }

          if (!rowItem._disciplineSkip) {
            const disciplineCell = document.createElement('td');
            const disciplineLink = document.createElement('button');
            disciplineLink.type = 'button';
            disciplineLink.className = 'metric-curve-link';
            disciplineLink.textContent = rowItem.discipline;
            disciplineLink.addEventListener('click', () => {
              openMetricLibraryWithFilters(rowItem.discipline, 'all');
            });
            disciplineCell.appendChild(disciplineLink);
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
          let functionScoreSuggestedBracket = null;
          let functionScoreCell = null;
          let functionScoreInteractionState = null;
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
            functionNameText.className = 'metric-curve-link';
            functionNameText.setAttribute('role', 'button');
            functionNameText.tabIndex = 0;
            functionNameText.appendChild(document.createTextNode(functionName));
            const openFunctionLibrary = () => {
              openMetricLibraryWithFilters(rowItem.discipline, functionName);
            };
            functionNameText.addEventListener('click', openFunctionLibrary);
            functionNameText.addEventListener('keydown', (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openFunctionLibrary();
              }
            });
            const functionToggle = document.createElement('button');
            functionToggle.type = 'button';
            functionToggle.className = 'criteria-toggle function-toggle';
            const isFunctionExpanded = expandedFunctions.has(rowFunctionId);
            functionToggle.innerHTML = isFunctionExpanded ? expandedGlyph : collapsedGlyph;
            functionToggle.setAttribute('aria-expanded', String(isFunctionExpanded));
            functionToggle.setAttribute('aria-label', 'Toggle function statement');
            functionToggle.addEventListener('mousedown', (event) => {
              if (event.detail > 0) {
                event.preventDefault();
              }
            });
            functionNameText.appendChild(document.createTextNode('\u00A0'));
            functionNameText.appendChild(functionToggle);
            functionNameLine.appendChild(functionNameText);
            functionCell.appendChild(functionNameLine);
            const statementLine = document.createElement('div');
            statementLine.className = 'function-statement';
            statementLine.textContent = functionStatement;
            statementLine.hidden = !hasFunctionStatement || !isFunctionExpanded;
            functionCell.appendChild(statementLine);
            functionToggle.addEventListener('click', (event) => {
              event.stopPropagation();
              if (!hasFunctionStatement) {
                return;
              }
              if (expandedFunctions.has(rowFunctionId)) {
                expandedFunctions.delete(rowFunctionId);
              } else {
                expandedFunctions.add(rowFunctionId);
              }
              const nowOpen = expandedFunctions.has(rowFunctionId);
              if (hasFunctionStatement) {
                statementLine.hidden = !nowOpen;
              }
              functionToggle.setAttribute('aria-expanded', String(nowOpen));
              functionToggle.innerHTML = nowOpen ? expandedGlyph : collapsedGlyph;
              if (event.detail > 0) {
                setTimeout(() => functionToggle.blur(), 0);
              }
            });
            row.appendChild(functionCell);
          }
          if (rowItem.type === 'criteria') {
            const detailsCell = document.createElement('td');
            const baseSpan = (showAdvanced ? 8 : 5) + (showCondensed ? 1 : 0);
            detailsCell.colSpan = showMappings ? baseSpan : Math.max(1, baseSpan - 3);
            const details = document.createElement('div');
            details.className = 'criteria-details';
            const detailsMetric = rowItem.metric || metric;
            if (!detailsMetric) {
              detailsCell.textContent = 'Details unavailable.';
              row.appendChild(detailsCell);
              tbody.appendChild(row);
              return;
            }
            const curve = ensureCurve(activeScenario, detailsMetric.id);
            const criteriaLayer = getCriteriaLayerForMetric(detailsMetric.id);
            if (curve) {
              const headerRow = document.createElement('div');
              headerRow.className = 'criteria-summary-header';
              const headerLabel = document.createElement('span');
              headerLabel.textContent = 'Scoring Criteria';
              headerRow.appendChild(headerLabel);
              details.appendChild(headerRow);

              const summaryTable = buildCurveSummaryTable(curve, criteriaLayer);
              details.appendChild(summaryTable);
            }
            detailsCell.appendChild(details);
            row.appendChild(detailsCell);
            tbody.appendChild(row);
            return;
          }

          const metricCell = document.createElement('td');
          metricCell.className = 'col-metric';
          const metricTitle = document.createElement('span');
          metricTitle.className = 'metric-title';
          const metricNameText = document.createElement('span');
          metricNameText.className = 'metric-curve-link';
          const metricLabel =
            rowItem.type === 'placeholder'
              ? 'No metrics selected for this function'
              : metric.metric;
          metricNameText.appendChild(document.createTextNode(metricLabel));
          if (rowItem.type === 'metric') {
            metricNameText.setAttribute('role', 'button');
            metricNameText.tabIndex = 0;
            const openMetricInspector = () => {
              if (window.dispatchEvent) {
                const profileId =
                  activeScenario?.metricProfiles?.[metric.id] || defaultProfileId;
                window.dispatchEvent(
                  new CustomEvent('staf:open-inspector', {
                    detail: {
                      tier: 'screening',
                      metricId: resolveLibraryId(metric.id),
                      profileId,
                      tab: 'details',
                    },
                  })
                );
              }
            };
            metricNameText.addEventListener('click', openMetricInspector);
            metricNameText.addEventListener('keydown', (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openMetricInspector();
              }
            });
          }
          metricTitle.appendChild(metricNameText);
          metricCell.appendChild(metricTitle);
          let criteriaBtn = null;
          if (rowItem.type === 'metric') {
            criteriaBtn = document.createElement('button');
            criteriaBtn.type = 'button';
            criteriaBtn.className = 'criteria-toggle';
            const criteriaExpanded = expandedMetrics.has(metric.id);
            criteriaBtn.innerHTML = criteriaExpanded ? expandedGlyph : collapsedGlyph;
            criteriaBtn.setAttribute(
              'aria-expanded',
              criteriaExpanded ? 'true' : 'false'
            );
            criteriaBtn.setAttribute('aria-label', 'Toggle criteria details');
            criteriaBtn.addEventListener('mousedown', (event) => {
              if (event.detail > 0) {
                event.preventDefault();
              }
            });
            metricNameText.appendChild(document.createTextNode('\u00A0'));
            metricNameText.appendChild(criteriaBtn);
          }


          const scoreCell = document.createElement('td');
          scoreCell.className = 'col-metric-score';
          let scoreSelect = null;
          if (rowItem.type === 'metric') {
            scoreSelect = document.createElement('select');
            scoreSelect.className = 'metric-score-select';
            const ratingLabels = getMetricRatingOptions(metric.id, activeScenario);
            ratingLabels.forEach((label) => {
              const opt = document.createElement('option');
              opt.value = label;
              opt.textContent = label;
              scoreSelect.appendChild(opt);
            });
            const ratingState = resolveMetricRating(metric.id, ratingLabels);
            if (ratingState.changed) {
              ratingsDirty = true;
            }
            scoreSelect.value = ratingState.value;
            scoreCell.appendChild(scoreSelect);

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
          if (!isPredefined && rowItem.type === 'metric') {
            metricActions.appendChild(removeBtn);
          }
          metricCell.appendChild(metricActions);

          const mapping = rowItem.functionId ? mappingById[rowItem.functionId] : null;
          row.appendChild(metricCell);
          row.appendChild(scoreCell);

          const criteriaCell = document.createElement('td');
          criteriaCell.className = 'col-scoring-criteria';
          const indexCell = document.createElement('td');
          indexCell.className = 'col-index-score';
          const estimateCell = document.createElement('td');
          estimateCell.className = 'col-function-estimate';
          if (rowItem.type === 'metric') {
            const overrides = getCriteriaOverrides();
            const overrideId = overrides[metric.id];
            const curve = ensureCurve(activeScenario, metric.id);
            const criteriaLayer = getCriteriaLayerForMetric(metric.id);
            const showAdvanced = getShowAdvancedScoring();
            const overrideLayer =
              overrideId && curve ? curve.layers.find((layer) => layer.id === overrideId) : null;
            const isDefaultOverride =
              overrideLayer &&
              normalizeText(overrideLayer.name || '') ===
                normalizeText(fallbackCriteriaName);
            const overrideLayerExists = Boolean(overrideLayer) && !isDefaultOverride;
            if (overrideId && (!overrideLayer || isDefaultOverride)) {
              delete overrides[metric.id];
              if (activeScenario) {
                activeScenario.criteriaOverrides = overrides;
                store.save();
              }
            }
            if (showAdvanced && curve) {
              const criteriaWrap = document.createElement('div');
              criteriaWrap.className = 'criteria-override';
              const criteriaSelect = document.createElement('select');
              criteriaSelect.className = 'criteria-select';
              criteriaSelect.setAttribute('aria-label', 'Scoring criteria');
              const defaultOption = document.createElement('option');
              defaultOption.value = '';
              defaultOption.textContent = fallbackCriteriaName;
              criteriaSelect.appendChild(defaultOption);
              curve.layers.forEach((layer) => {
                if (
                  normalizeText(layer.name || '') ===
                  normalizeText(fallbackCriteriaName)
                ) {
                  return;
                }
                const option = document.createElement('option');
                option.value = layer.id;
                option.textContent = layer.name || 'Untitled';
                criteriaSelect.appendChild(option);
              });
              criteriaSelect.value = overrideLayerExists ? overrideId : '';
              criteriaSelect.addEventListener('change', () => {
                if (!activeScenario) {
                  return;
                }
                if (!criteriaSelect.value) {
                  delete overrides[metric.id];
                } else {
                  overrides[metric.id] = criteriaSelect.value;
                }
                activeScenario.criteriaOverrides = overrides;
                store.save();
                renderTable();
              });
              criteriaWrap.appendChild(criteriaSelect);
              criteriaCell.appendChild(criteriaWrap);
            } else {
              const defaultPill = document.createElement('button');
              defaultPill.type = 'button';
              defaultPill.className = 'criteria-pill';
              defaultPill.textContent = fallbackCriteriaName;
              const defaultCriteriaName = resolveDefaultCriteriaName();
              defaultPill.title = `Default criteria: ${defaultCriteriaName}`;
              defaultPill.setAttribute(
                'aria-label',
                'Default scoring criteria. Toggle advanced settings to override.'
              );
              criteriaCell.appendChild(defaultPill);
            }
            indexRows.push({ metricId: metric.id, indexCell });
            estimateRows.push({ metricId: metric.id, estimateCell });
          } else {
            criteriaCell.textContent = '-';
            indexCell.textContent = '-';
            estimateCell.textContent = '-';
          }
          row.appendChild(criteriaCell);
          row.appendChild(indexCell);
          row.appendChild(estimateCell);
          if (showCondensed && rowItem.type === 'metric' && !rowItem._functionScoreSkip) {
            functionScoreCell = document.createElement('td');
            functionScoreCell.className = 'col-function-score function-score-cell';
            functionScoreCell.rowSpan = rowItem._functionScoreSpan || 1;
            if (rowItem._functionScorePlaceholder) {
              functionScoreCell.classList.add('function-score-cell-empty');
            } else {
              const functionScoreLine = document.createElement('div');
              functionScoreLine.className = 'score-input function-score-inline';
              const sliderWrap = document.createElement('div');
              sliderWrap.className = 'function-score-slider';
              const cueLabels = document.createElement('div');
              cueLabels.className = 'function-score-cue-labels';
              [
                { text: 'F', left: '16.67%' },
                { text: 'AR', left: '50%' },
                { text: 'NF', left: '83.33%' },
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
              cueBar.setAttribute('aria-label', 'Set function score by range');
              [0, 33.33, 66.67, 100].forEach((percent) => {
                const tick = document.createElement('span');
                tick.className = 'function-score-cue-tick';
                tick.style.left = `${percent}%`;
                cueBar.appendChild(tick);
              });
              const suggestedBracketLayer = document.createElement('div');
              suggestedBracketLayer.className = 'function-score-suggested-layer';
              functionScoreSuggestedBracket = document.createElement('div');
              functionScoreSuggestedBracket.className = 'function-score-suggested-bracket';
              functionScoreSuggestedBracket.hidden = true;
              suggestedBracketLayer.appendChild(functionScoreSuggestedBracket);
              functionScoreRange = document.createElement('input');
              functionScoreRange.type = 'range';
              functionScoreRange.min = '0';
              functionScoreRange.max = '15';
              functionScoreRange.step = '1';
              functionScoreRange.disabled = false;
              const currentFunctionScore = Number.isFinite(
                activeScenario?.functionScores?.[rowFunctionId]
              )
                ? activeScenario.functionScores[rowFunctionId]
                : 0;
              functionScoreRange.value = String(currentFunctionScore);
              updateScreeningSliderVisual(functionScoreRange, currentFunctionScore);
              sliderWrap.appendChild(cueLabels);
              sliderWrap.appendChild(cueBar);
              sliderWrap.appendChild(suggestedBracketLayer);
              sliderWrap.appendChild(functionScoreRange);
              functionScoreValue = document.createElement('span');
              functionScoreValue.className = 'score-value';
              functionScoreValue.textContent = String(currentFunctionScore);
              functionScoreLine.appendChild(sliderWrap);
              functionScoreLine.appendChild(functionScoreValue);
              functionScoreInteractionState = { active: false };
              const interactionState = functionScoreInteractionState;
              const setInteractionState = (isActive) => {
                interactionState.active = Boolean(isActive);
                sliderWrap.classList.toggle('is-dragging', interactionState.active);
              };
              let cueHighlightTimer = null;
              const setScoreFromCueBar = (event) => {
                if (!functionScoreRange || functionScoreRange.disabled) {
                  return;
                }
                const rect = cueBar.getBoundingClientRect();
                if (!rect.width) {
                  return;
                }
                const min = Number(functionScoreRange.min) || 0;
                const max = Number(functionScoreRange.max) || 15;
                const relativeX = Math.max(
                  0,
                  Math.min(rect.width, event.clientX - rect.left)
                );
                const nextValue = Math.round(min + (relativeX / rect.width) * (max - min));
                setInteractionState(true);
                functionScoreRange.value = String(nextValue);
                functionScoreRange.dispatchEvent(
                  new Event('input', { bubbles: true })
                );
                if (cueHighlightTimer) {
                  clearTimeout(cueHighlightTimer);
                }
                cueHighlightTimer = setTimeout(() => {
                  setInteractionState(false);
                  updateScores(indexRows, estimateRows, functionScoreControls, functionOrder);
                  cueHighlightTimer = null;
                }, 180);
              };
              functionScoreRange.addEventListener('input', () => {
                const nextValue = Number(functionScoreRange.value);
                if (activeScenario && rowFunctionId) {
                  if (!activeScenario.functionScores) {
                    activeScenario.functionScores = {};
                  }
                  activeScenario.functionScores[rowFunctionId] = nextValue;
                  store.save();
                }
                if (functionScoreValue) {
                  functionScoreValue.textContent = Number.isFinite(nextValue)
                    ? String(nextValue)
                    : '-';
                }
                updateScreeningSliderVisual(functionScoreRange, nextValue);
                updateScores(indexRows, estimateRows, functionScoreControls, functionOrder);
              });
              const endSliderInteraction = () => {
                setInteractionState(false);
                updateScores(indexRows, estimateRows, functionScoreControls, functionOrder);
              };
              functionScoreRange.addEventListener('pointerdown', () => {
                if (cueHighlightTimer) {
                  clearTimeout(cueHighlightTimer);
                  cueHighlightTimer = null;
                }
                setInteractionState(true);
              });
              functionScoreRange.addEventListener('pointerup', () => {
                endSliderInteraction();
              });
              functionScoreRange.addEventListener('pointercancel', () => {
                endSliderInteraction();
              });
              functionScoreRange.addEventListener('blur', () => {
                endSliderInteraction();
              });
              cueBar.addEventListener('pointerdown', (event) => {
                event.preventDefault();
                setScoreFromCueBar(event);
              });
              functionScoreCell.appendChild(functionScoreLine);
            }
            row.appendChild(functionScoreCell);
          }
          if (showMappings && !rowItem._weightSkip && rowItem.type !== 'criteria') {
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
            criteriaBtn.addEventListener('click', (event) => {
              event.stopPropagation();
              if (expandedMetrics.has(metric.id)) {
                expandedMetrics.delete(metric.id);
              } else {
                expandedMetrics.add(metric.id);
              }
              renderTable();
              if (event.detail > 0) {
                setTimeout(() => criteriaBtn.blur(), 0);
              }
            });

            scoreSelect.addEventListener('change', () => {
              metricRatings.set(metric.id, scoreSelect.value);
              if (activeScenario) {
                activeScenario.ratings[metric.id] = scoreSelect.value;
                store.save();
              }
              updateScores(indexRows, estimateRows, functionScoreControls, functionOrder);
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
            if (activeScenario.criteriaOverrides) {
              delete activeScenario.criteriaOverrides[metric.id];
            }
            if (activeScenario.metricProfiles) {
              delete activeScenario.metricProfiles[metric.id];
            }
            store.save();
            renderTable();
          });

          tbody.appendChild(row);
          if (functionScoreValue && functionScoreRange) {
            functionScoreControls.push({
              functionId: rowItem.functionId,
              rangeInput: functionScoreRange,
              valueEl: functionScoreValue,
              suggestedBracketEl: functionScoreSuggestedBracket,
              interactionState: functionScoreInteractionState,
            });
          }
        });

        updateScores(indexRows, estimateRows, functionScoreControls, functionOrder);
        if (ratingsDirty && activeScenario) {
          store.save();
        }
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
        metricRatings = new Map(
          Object.entries(buildRatings(metricIds, scenario.ratings, getMetricRatingOptions, scenario))
        );

        scenario.metricIds = Array.from(selectedMetricIds);
        scenario.ratings = buildRatings(
          scenario.metricIds,
          scenario.ratings,
          getMetricRatingOptions,
          scenario
        );
        if (!scenario.curves) {
          scenario.curves = {};
        }
        scenario.metricIds.forEach((id) => ensureCurve(scenario, id));
        if (!scenario.criteriaOverrides) {
          scenario.criteriaOverrides = {};
        }
        Object.keys(scenario.criteriaOverrides).forEach((metricId) => {
          if (!selectedMetricIds.has(metricId)) {
            delete scenario.criteriaOverrides[metricId];
          }
        });
        if (!scenario.metricProfiles) {
          scenario.metricProfiles = {};
        }
        scenario.metricIds.forEach((id) => {
          if (!scenario.metricProfiles[id]) {
            scenario.metricProfiles[id] = defaultProfileId;
          }
        });
        Object.keys(scenario.metricProfiles).forEach((metricId) => {
          if (!selectedMetricIds.has(metricId)) {
            delete scenario.metricProfiles[metricId];
          }
        });
        if (!scenario.defaultCriteriaName) {
          scenario.defaultCriteriaName = fallbackCriteriaName;
        }
        if (!scenario.functionScores) {
          scenario.functionScores = {};
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
        if (typeof scenario.showCondensedView !== 'boolean') {
          scenario.showCondensedView = true;
        }
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

        renderTable();
        notifyAssessmentUpdate();
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

      const addMetricFromLibrary = ({ metricId, profileId, detail }) => {
        if (!activeScenario) {
          return;
        }
        if (activeScenario.type === 'predefined') {
          duplicateScenario(activeScenario);
          activeScenario = store.active();
        }
        if (!activeScenario || activeScenario.type === 'predefined') {
          return;
        }
        const metricDetail = detail || null;
        const metric =
          metricDetail && metricDetail.metricId
            ? ensureLibraryMetric(metricDetail, profileId)
            : metricById.get(resolveMetricId(metricId));
        if (!metric) {
          return;
        }
        selectedMetricIds.add(metric.id);
        metricRatings.set(metric.id, metricRatings.get(metric.id) || defaultRating);
        activeScenario.metricIds = Array.from(selectedMetricIds);
        activeScenario.ratings[metric.id] = metricRatings.get(metric.id);
        if (!activeScenario.metricProfiles) {
          activeScenario.metricProfiles = {};
        }
        activeScenario.metricProfiles[metric.id] = profileId || defaultProfileId;
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
        ensureCurve(activeScenario, metric.id);
        store.save();
        renderTable();
        notifyAssessmentUpdate();
      };

      const removeMetricFromLibrary = ({ metricId }) => {
        if (!activeScenario || activeScenario.type === 'predefined') {
          return;
        }
        if (!metricId) {
          return;
        }
        const resolvedId = resolveMetricId(metricId);
        selectedMetricIds.delete(resolvedId);
        metricRatings.delete(resolvedId);
        activeScenario.metricIds = Array.from(selectedMetricIds);
        delete activeScenario.ratings[resolvedId];
        if (activeScenario.curves) {
          delete activeScenario.curves[resolvedId];
        }
        if (activeScenario.criteriaOverrides) {
          delete activeScenario.criteriaOverrides[resolvedId];
        }
        if (activeScenario.metricProfiles) {
          delete activeScenario.metricProfiles[resolvedId];
        }
        store.save();
        renderTable();
        notifyAssessmentUpdate();
      };

      const isMetricAdded = (metricId, profileId) => {
        if (!metricId || !activeScenario) {
          return false;
        }
        const resolvedId = resolveMetricId(metricId);
        if (!selectedMetricIds.has(resolvedId)) {
          return false;
        }
        if (!profileId) {
          return true;
        }
        return (
          activeScenario.metricProfiles &&
          activeScenario.metricProfiles[resolvedId] === profileId
        );
      };

      if (window.STAFAssessmentRegistry) {
        window.STAFAssessmentRegistry.register('screening', {
          addMetric: addMetricFromLibrary,
          removeMetric: removeMetricFromLibrary,
          isMetricAdded,
          getProfile(metricId) {
            if (!activeScenario) {
              return null;
            }
            const resolvedId = resolveMetricId(metricId);
            return activeScenario.metricProfiles
              ? activeScenario.metricProfiles[resolvedId]
              : null;
          },
          isReadOnly() {
            return Boolean(activeScenario && activeScenario.type === 'predefined');
          },
          getCurve(metricId) {
            if (!activeScenario) {
              return null;
            }
            const resolvedId = resolveMetricId(metricId);
            return ensureCurve(activeScenario, resolvedId);
          },
          setCurve(metricId, curve) {
            if (!activeScenario) {
              return;
            }
            if (!activeScenario.curves) {
              activeScenario.curves = {};
            }
            const resolvedId = resolveMetricId(metricId);
            activeScenario.curves[resolvedId] = curve;
            store.save();
            renderTable();
          },
          refresh() {
            renderTable();
          },
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
