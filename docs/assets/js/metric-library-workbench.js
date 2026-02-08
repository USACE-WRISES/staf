(() => {
  const store = window.STAFMetricLibraryStore;
  if (!store) {
    return;
  }

  const registry = window.STAFAssessmentRegistry || {
    entries: new Map(),
    register(tier, api) {
      this.entries.set(tier, api);
      window.dispatchEvent(new CustomEvent('staf:assessment-registered', { detail: { tier } }));
    },
    get(tier) {
      return this.entries.get(tier) || null;
    },
  };
  window.STAFAssessmentRegistry = registry;

  const workbenches = Array.from(document.querySelectorAll('.assessment-workbench'));
  if (!workbenches.length) {
    return;
  }

  const createEl = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) {
      el.className = className;
    }
    if (text !== undefined) {
      el.textContent = text;
    }
    return el;
  };

  const renderMarkdown = (markdown) => {
    const wrapper = document.createElement('div');
    if (!markdown) {
      wrapper.textContent = '-';
      return wrapper;
    }
    const lines = markdown.split(/\r?\n/).filter((line) => line.trim());
    const listLines = lines.filter((line) => line.trim().startsWith('- '));
    if (listLines.length === lines.length) {
      const list = document.createElement('ul');
      listLines.forEach((line) => {
        const item = document.createElement('li');
        item.textContent = line.replace(/^\s*-\s*/, '');
        list.appendChild(item);
      });
      wrapper.appendChild(list);
      return wrapper;
    }
    const paragraph = document.createElement('p');
    paragraph.textContent = markdown;
    wrapper.appendChild(paragraph);
    return wrapper;
  };

  const normalizeText = (value) =>
    (value || '')
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();

  const slugify = (value) =>
    (value || '')
      .toString()
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');

  const userMetricStorageKey = 'staf_metric_library_user_metrics_v1';
  const buildEmptyUserStore = () => ({
    schemaVersion: 1,
    metrics: [],
    details: {},
    curves: {},
  });

  const loadUserMetricStore = () => {
    if (!window.localStorage) {
      return buildEmptyUserStore();
    }
    try {
      const raw = window.localStorage.getItem(userMetricStorageKey);
      if (!raw) {
        return buildEmptyUserStore();
      }
      const parsed = JSON.parse(raw);
      const store = buildEmptyUserStore();
      store.schemaVersion = parsed.schemaVersion || store.schemaVersion;
      store.metrics = Array.isArray(parsed.metrics) ? parsed.metrics : [];
      store.metrics.forEach((entry) => {
        entry.isUserMetric = true;
      });
      store.details = parsed.details && typeof parsed.details === 'object' ? parsed.details : {};
      Object.keys(store.details).forEach((key) => {
        if (store.details[key] && typeof store.details[key] === 'object') {
          store.details[key].isUserMetric = true;
        }
      });
      store.curves = parsed.curves && typeof parsed.curves === 'object' ? parsed.curves : {};
      return store;
    } catch (error) {
      return buildEmptyUserStore();
    }
  };

  const persistUserMetricStore = (storeData) => {
    if (!window.localStorage) {
      return;
    }
    try {
      window.localStorage.setItem(
        userMetricStorageKey,
        JSON.stringify(storeData)
      );
    } catch (error) {
      // ignore storage errors
    }
  };

  const userMetricStore = loadUserMetricStore();

  const getUserMetricEntry = (metricId) =>
    userMetricStore.metrics.find((entry) => entry.metricId === metricId) || null;

  const getUserMetricDetail = (metricId) =>
    (userMetricStore.details && userMetricStore.details[metricId]) || null;

  const getUserMetricCurves = (metricId) =>
    (userMetricStore.curves && userMetricStore.curves[metricId]) || null;

  const saveUserMetric = (metricId, detailUpdates, entryUpdates) => {
    const entry = getUserMetricEntry(metricId);
    const detail = getUserMetricDetail(metricId);
    if (!entry || !detail) {
      return;
    }
    entry.isUserMetric = true;
    detail.isUserMetric = true;
    if (detailUpdates && typeof detailUpdates === 'object') {
      Object.assign(detail, detailUpdates);
    }
    if (entryUpdates && typeof entryUpdates === 'object') {
      Object.assign(entry, entryUpdates);
    }
    persistUserMetricStore(userMetricStore);
  };

  const getDisciplineCategoryClass = (discipline) => {
    const key = normalizeText(discipline).replace(/\s+/g, '');
    return key ? `category-${key}` : '';
  };

  const countUppercase = (value) => {
    if (!value) {
      return 0;
    }
    return String(value).replace(/[^A-Z]/g, '').length;
  };

  const pickPreferredLabel = (current, candidate) => {
    if (!current) {
      return candidate;
    }
    if (!candidate) {
      return current;
    }
    const currentScore = countUppercase(current);
    const candidateScore = countUppercase(candidate);
    if (candidateScore > currentScore) {
      return candidate;
    }
    if (candidateScore === currentScore && candidate.length > current.length) {
      return candidate;
    }
    return current;
  };

  const capitalizeFirst = (value) => {
    if (!value) {
      return value;
    }
    const text = String(value);
    return text.charAt(0).toUpperCase() + text.slice(1);
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

  const formatTier = (tier) =>
    tier ? tier.charAt(0).toUpperCase() + tier.slice(1) : '';

  const formatTierAbbrev = (tier) => {
    if (!tier) {
      return '-';
    }
    const key = tier.toLowerCase();
    if (key === 'screening') {
      return 'S';
    }
    if (key === 'rapid') {
      return 'R';
    }
    if (key === 'detailed') {
      return 'D';
    }
    return tier.charAt(0).toUpperCase();
  };

  const buildUserRubricLevels = (ratingScaleId) => {
    if (ratingScaleId === 'sfariLikert') {
      return [
        { label: 'Strongly Disagree', ratingId: 'sd', criteriaMarkdown: '' },
        { label: 'Disagree', ratingId: 'd', criteriaMarkdown: '' },
        { label: 'Neutral', ratingId: 'n', criteriaMarkdown: '' },
        { label: 'Agree', ratingId: 'a', criteriaMarkdown: '' },
        { label: 'Strongly Agree', ratingId: 'sa', criteriaMarkdown: '' },
      ];
    }
    return [
      { label: 'Optimal', ratingId: 'optimal', criteriaMarkdown: '' },
      { label: 'Suboptimal', ratingId: 'suboptimal', criteriaMarkdown: '' },
      { label: 'Marginal', ratingId: 'marginal', criteriaMarkdown: '' },
      { label: 'Poor', ratingId: 'poor', criteriaMarkdown: '' },
    ];
  };

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

  const ensureCurveRanges = (curve) => {
    if (!curve?.indexRange) {
      return;
    }
    const layer = curve.layers?.[0];
    if (!layer?.points?.length) {
      return;
    }
    const needsRanges = layer.points.some(
      (point) => parseScore(point.yMin) === null || parseScore(point.yMax) === null
    );
    if (needsRanges) {
      applyIndexRanges(layer.points);
    }
  };

  const buildScoringSummary = (profile) => {
    if (!profile || !profile.scoring) {
      return 'Scoring unavailable';
    }
    const type = profile.scoring.type;
    if (type === 'categorical') {
      const count = profile.scoring.rubric?.levels?.length || 0;
      return `Categorical - ${count} levels`;
    }
    if (type === 'thresholds') {
      const count = profile.scoring.rubric?.bands?.length || 0;
      return `Thresholds - ${count} bands`;
    }
    if (type === 'curve') {
      const count = profile.scoring.rubric?.curveSetRefs?.length || 0;
      return `Curves - ${count} set${count === 1 ? '' : 's'}`;
    }
    if (type === 'formula') {
      return 'Formula';
    }
    if (type === 'binary') {
      return 'Binary';
    }
    if (type === 'lookup') {
      return 'Lookup';
    }
    return type;
  };

  const buildDefaultCurve = (profile) => {
    const isScreening = profile?.tier === 'screening';
    const points = isScreening
      ? [
          { x: 'Optimal', y: 1, description: '' },
          { x: 'Suboptimal', y: 0.69, description: '' },
          { x: 'Marginal', y: 0.3, description: '' },
          { x: 'Poor', y: 0, description: '' },
        ]
      : [
          { x: 0, y: 0 },
          { x: 1, y: 0.3 },
          { x: 2, y: 0.69 },
          { x: 3, y: 1 },
        ];
    if (isScreening) {
      applyIndexRanges(points);
    }
    return {
      curveId: `curve-${Date.now()}`,
      name: 'Default',
      xType: isScreening ? 'categorical' : 'quantitative',
      indexRange: isScreening,
      units: '',
      layers: [
        {
          id: `layer-${Date.now()}`,
          name: 'Default',
          points,
        },
      ],
      activeLayerId: null,
    };
  };

  const getDefaultProfile = (detail, preferredTier) => {
    if (!detail || !detail.profiles || !detail.profiles.length) {
      return null;
    }
    if (preferredTier && preferredTier !== 'all') {
      const match = detail.profiles.find((profile) => profile.tier === preferredTier);
      if (match) {
        return match;
      }
    }
    const recommended = detail.profiles.find((profile) => profile.recommended);
    if (recommended) {
      return recommended;
    }
    return detail.profiles[0];
  };

  const loadCurvesForProfile = async (profile) => {
    if (!profile?.curveIntegration?.enabled) {
      return [];
    }
    const refs = profile.curveIntegration.curveSetRefs || [];
    const curves = [];
    for (const ref of refs) {
      const curveSet = await store.loadCurveSet(ref);
      if (curveSet && Array.isArray(curveSet.curves)) {
        curveSet.curves.forEach((curve) => {
          curves.push({
            curveSetId: ref,
            curveId: curve.curveId,
            name: curve.name,
            data: curve,
            tier: curveSet.tier || profile.tier,
          });
        });
      }
    }
    return curves;
  };

  workbenches.forEach((container) => {
    const pageTier = container.dataset.assessmentTier || 'screening';
    const state = {
      tierFilter: pageTier,
      searchTerm: '',
      selectedMetricId: null,
      selectedProfileId: null,
      selectedCurveId: null,
      activeTab: 'details',
      expandedRows: new Set(),
      curveCountOverrides: new Map(),
      curveDrafts: new Map(),
      curveListCache: new Map(),
      curveColumnWidths: {
        value: 120,
        index: 70,
      },
      excelPromise: null,
      exporting: false,
      ordering: {
        ready: false,
        disciplineOrder: [],
        functionOrder: new Map(),
        metricOrder: new Map(),
      },
      orderingPromise: null,
      filters: {
        discipline: 'all',
        function: 'all',
      },
    };

    const leftSidebar = container.querySelector('.metric-library-sidebar');
    const rightSidebar = container.querySelector('.metric-inspector-sidebar');
    const searchInput = container.querySelector('.metric-library-search');
    const tierChips = Array.from(container.querySelectorAll('.metric-tier-chip'));
    const libraryList = container.querySelector('.metric-library-list');
    const libraryClose = container.querySelector('.metric-library-close');
    const downloadButton = container.querySelector('.metric-library-download');
    const libraryHeader = container.querySelector('.metric-library-header');
    const columnHeader = container.querySelector('.metric-library-column-header');

    tierChips.forEach((chip) =>
      chip.classList.toggle('is-active', chip.dataset.tier === state.tierFilter)
    );

    const inspectorEmpty = container.querySelector('.metric-inspector-empty');
    const inspectorContent = container.querySelector('.metric-inspector-content');
    const inspectorTitle = container.querySelector('.metric-inspector-title');
    const inspectorSubtitle = container.querySelector('.metric-inspector-subtitle');
    const inspectorToggle = container.querySelector('.metric-inspector-toggle');
    const inspectorClose = container.querySelector('.metric-inspector-close');
    const profileSelector = container.querySelector('.metric-profile-selector');
    const profileSummary = container.querySelector('.metric-profile-summary');
    const tabs = Array.from(container.querySelectorAll('.metric-inspector-tab'));
    const tabPanels = Array.from(container.querySelectorAll('.metric-tab-panel'));

    const profileModal = container.querySelector('.metric-profile-modal');
    const profileOptions = container.querySelector('.metric-profile-options');
    const profileConfirm = container.querySelector('.metric-profile-confirm');
    const profileCancel = container.querySelector('.metric-profile-cancel');

    const loadMetricDetail = async (metricId, detailsRef) => {
      const userDetail = getUserMetricDetail(metricId);
      if (userDetail) {
        return userDetail;
      }
      return store.loadMetricDetail(metricId, detailsRef);
    };

    const toJson = (value) => {
      if (value === null || value === undefined) {
        return '';
      }
      try {
        return JSON.stringify(value);
      } catch (error) {
        return '';
      }
    };

    const toList = (value) => {
      if (!Array.isArray(value)) {
        return '';
      }
      return value.filter((item) => item !== null && item !== undefined && item !== '').join('; ');
    };

    const toNumberMaybe = (value) => {
      if (value === null || value === undefined || value === '') {
        return '';
      }
      const parsed = Number.parseFloat(value);
      if (Number.isFinite(parsed) && String(value).trim() === String(parsed)) {
        return parsed;
      }
      return value;
    };

    const resolvePointIndex = (point) => {
      const yMin = parseScore(point?.yMin);
      const yMax = parseScore(point?.yMax);
      const yValue = parseScore(point?.y);
      const minValue =
        yMin !== null && yMin !== undefined ? yMin : point?.yMin ?? '';
      const maxValue =
        yMax !== null && yMax !== undefined ? yMax : point?.yMax ?? '';
      const value =
        yValue !== null && yValue !== undefined ? yValue : point?.y ?? '';
      let range = '';
      if (minValue !== '' || maxValue !== '') {
        if (minValue !== '' && maxValue !== '') {
          range = `${minValue}-${maxValue}`;
        } else {
          range = minValue !== '' ? String(minValue) : String(maxValue);
        }
      } else if (value !== '') {
        range = value;
      }
      return {
        range,
        min: minValue === '' ? '' : minValue,
        max: maxValue === '' ? '' : maxValue,
      };
    };

    const ensureExcelJs = () => {
      if (window.ExcelJS) {
        return Promise.resolve(window.ExcelJS);
      }
      if (state.excelPromise) {
        return state.excelPromise;
      }
      state.excelPromise = new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = store.buildUrl('/assets/vendor/exceljs.min.js');
        script.async = true;
        script.onload = () => {
          if (window.ExcelJS) {
            resolve(window.ExcelJS);
          } else {
            reject(new Error('ExcelJS not available.'));
          }
        };
        script.onerror = () => reject(new Error('Failed to load ExcelJS.'));
        document.head.appendChild(script);
      });
      return state.excelPromise;
    };

    const buildMetricExportRows = (entries, detailMap) =>
      entries.map((entry) => {
        const detail = detailMap.get(entry.metricId) || {};
        const profiles = Array.isArray(detail.profiles) ? detail.profiles : [];
        const profileExport = profiles.map((profile) => ({
          profileId: profile.profileId || '',
          tier: profile.tier || '',
          status: profile.status || '',
          recommended: !!profile.recommended,
          scoringType: profile.scoring?.type || '',
          outputKind: profile.scoring?.output?.kind || '',
          ratingScaleId: profile.scoring?.output?.ratingScaleId || '',
          curveIntegrationEnabled: !!profile.curveIntegration?.enabled,
          curveSetRefs: profile.curveIntegration?.curveSetRefs || [],
        }));
        return {
          metricId: entry.metricId || '',
          name: detail.name || entry.name || '',
          shortName: detail.shortName || entry.shortName || '',
          discipline: detail.discipline || entry.discipline || '',
          function: detail.function || entry.function || '',
          category: detail.category || entry.category || '',
          tags: toList(detail.tags || entry.tags || []),
          status: detail.status || entry.status || '',
          minimumTier: entry.minimumTier || '',
          recommendedTiers: toList(entry.recommendedTiers || []),
          profileAvailability: toJson(entry.profileAvailability || {}),
          profileSummaries: toJson(entry.profileSummaries || {}),
          curvesSummary: toJson(entry.curvesSummary || {}),
          inputsSummary: toJson(entry.inputsSummary || {}),
          functionStatement: detail.functionStatement || '',
          description: detail.descriptionMarkdown || '',
          methodContext: detail.methodContextMarkdown || '',
          howToMeasure: detail.howToMeasureMarkdown || '',
          inputs: toJson(detail.inputs || []),
          references: toList(detail.references || []),
          profiles: toJson(profileExport),
          scoring: toJson(profiles.map((profile) => profile.scoring || {})),
          curveIntegration: toJson(
            profiles.map((profile) => profile.curveIntegration || {})
          ),
          isUserMetric: !!detail.isUserMetric || !!entry.isUserMetric,
        };
      });

    const buildCurveExportRows = async (entries, detailMap) => {
      const rows = [];
      let maxPoints = 0;

      for (const entry of entries) {
        const detail = detailMap.get(entry.metricId) || {};
        const profiles = Array.isArray(detail.profiles) ? detail.profiles : [];
        const isUserMetric = !!detail.isUserMetric || !!entry.isUserMetric;

        for (const profile of profiles) {
          const curveSets = [];
          if (isUserMetric) {
            const storedCurves = getUserMetricCurves(entry.metricId);
            if (Array.isArray(storedCurves) && storedCurves.length) {
              curveSets.push({
                curveSetId: 'user',
                name: 'User curves',
                tier: profile.tier || '',
                axes: {},
                curves: storedCurves,
              });
            }
          }
          if (!isUserMetric && profile?.curveIntegration?.enabled) {
            const refs = profile.curveIntegration.curveSetRefs || [];
            for (const ref of refs) {
              try {
                const curveSet = await store.loadCurveSet(ref);
                if (curveSet && Array.isArray(curveSet.curves)) {
                  curveSets.push({
                    ...curveSet,
                    curveSetId: curveSet.curveSetId || ref,
                  });
                }
              } catch (error) {
                // ignore missing curve sets for export
              }
            }
          }

          curveSets.forEach((curveSet) => {
            const curves = Array.isArray(curveSet.curves) ? curveSet.curves : [];
            curves.forEach((curve) => {
              const curveData = curve?.data ? curve.data : curve || {};
              const layers =
                Array.isArray(curveData.layers) && curveData.layers.length
                  ? curveData.layers
                  : [
                      {
                        id: curveData.activeLayerId || '',
                        name: curveData.name || '',
                        points: curveData.points || [],
                      },
                    ];
              layers.forEach((layer) => {
                const points = Array.isArray(layer.points) ? layer.points : [];
                maxPoints = Math.max(maxPoints, points.length);
                rows.push({
                  metricId: entry.metricId || '',
                  metricName: detail.name || entry.name || '',
                  profileId: profile.profileId || '',
                  profileTier: profile.tier || '',
                  curveSetId: curveSet.curveSetId || '',
                  curveSetName: curveSet.name || '',
                  curveSetTier: curveSet.tier || profile.tier || '',
                  curveId: curveData.curveId || curve.curveId || '',
                  curveName: curveData.name || curve.name || '',
                  units: curveData.units || '',
                  xType: curveData.xType || '',
                  indexRange: !!curveData.indexRange,
                  axesXLabel: curveSet.axes?.xLabel || '',
                  axesYLabel: curveSet.axes?.yLabel || '',
                  activeLayerId: curveData.activeLayerId || '',
                  layerId: layer.id || '',
                  layerName: layer.name || '',
                  isActiveLayer: curveData.activeLayerId
                    ? curveData.activeLayerId === layer.id
                    : false,
                  isUserMetric,
                  points,
                });
              });
            });
          });
        }
      }

      return { rows, maxPoints };
    };

    const downloadMetricLibraryWorkbook = async () => {
      if (state.exporting || !downloadButton) {
        return;
      }
      state.exporting = true;
      downloadButton.classList.add('is-loading');
      downloadButton.disabled = true;
      downloadButton.setAttribute('aria-busy', 'true');

      try {
        const ExcelJS = await ensureExcelJs();
        if (!ExcelJS) {
          throw new Error('Excel export library not available.');
        }

        const index = await store.loadMetricIndex();
        const baseEntries = index.metrics || [];
        const userEntries = userMetricStore.metrics || [];
        const entries = baseEntries.concat(userEntries);

        const details = await Promise.all(
          entries.map(async (entry) => [
            entry.metricId,
            await loadMetricDetail(entry.metricId, entry.detailsRef),
          ])
        );
        const detailMap = new Map(details);

        const metricRows = buildMetricExportRows(entries, detailMap);
        const { rows: curveRowsRaw, maxPoints } = await buildCurveExportRows(
          entries,
          detailMap
        );

        const workbook = new ExcelJS.Workbook();
        workbook.creator = 'STAF Metric Library';
        workbook.created = new Date();

        const metricsSheet = workbook.addWorksheet('Metrics');
        metricsSheet.columns = [
          { header: 'Metric ID', key: 'metricId', width: 28 },
          { header: 'Metric Name', key: 'name', width: 40 },
          { header: 'Short Name', key: 'shortName', width: 26 },
          { header: 'Discipline', key: 'discipline', width: 18 },
          { header: 'Function', key: 'function', width: 26 },
          { header: 'Category', key: 'category', width: 18 },
          { header: 'Tags', key: 'tags', width: 18 },
          { header: 'Status', key: 'status', width: 12 },
          { header: 'Minimum Tier', key: 'minimumTier', width: 14 },
          { header: 'Recommended Tiers', key: 'recommendedTiers', width: 18 },
          { header: 'Profile Availability', key: 'profileAvailability', width: 30 },
          { header: 'Profile Summaries', key: 'profileSummaries', width: 40 },
          { header: 'Curves Summary', key: 'curvesSummary', width: 28 },
          { header: 'Inputs Summary', key: 'inputsSummary', width: 28 },
          { header: 'Metric Statement', key: 'functionStatement', width: 50 },
          { header: 'Description', key: 'description', width: 60 },
          { header: 'Method/Context', key: 'methodContext', width: 60 },
          { header: 'How To Measure', key: 'howToMeasure', width: 60 },
          { header: 'Inputs', key: 'inputs', width: 60 },
          { header: 'References', key: 'references', width: 40 },
          { header: 'Profiles', key: 'profiles', width: 60 },
          { header: 'Scoring', key: 'scoring', width: 60 },
          { header: 'Curve Integration', key: 'curveIntegration', width: 60 },
          { header: 'User Metric', key: 'isUserMetric', width: 12 },
        ];
        metricsSheet.addRows(metricRows);
        metricsSheet.views = [{ state: 'frozen', ySplit: 1 }];

        const curveColumns = [
          { header: 'Metric ID', key: 'metricId', width: 24 },
          { header: 'Metric Name', key: 'metricName', width: 36 },
          { header: 'Profile ID', key: 'profileId', width: 24 },
          { header: 'Profile Tier', key: 'profileTier', width: 14 },
          { header: 'Curve Set ID', key: 'curveSetId', width: 28 },
          { header: 'Curve Set Name', key: 'curveSetName', width: 28 },
          { header: 'Curve Set Tier', key: 'curveSetTier', width: 14 },
          { header: 'Curve ID', key: 'curveId', width: 28 },
          { header: 'Curve Name', key: 'curveName', width: 28 },
          { header: 'Units', key: 'units', width: 16 },
          { header: 'Curve Type', key: 'xType', width: 14 },
          { header: 'Index Scores As Range', key: 'indexRange', width: 18 },
          { header: 'Axis X Label', key: 'axesXLabel', width: 18 },
          { header: 'Axis Y Label', key: 'axesYLabel', width: 18 },
          { header: 'Active Layer ID', key: 'activeLayerId', width: 24 },
          { header: 'Layer ID', key: 'layerId', width: 24 },
          { header: 'Layer Name', key: 'layerName', width: 24 },
          { header: 'Is Active Layer', key: 'isActiveLayer', width: 14 },
          { header: 'User Metric', key: 'isUserMetric', width: 12 },
        ];

        for (let i = 1; i <= maxPoints; i += 1) {
          curveColumns.push(
            { header: `Metric Value ${i}`, key: `value_${i}`, width: 18 },
            { header: `Metric Value ${i} Desc`, key: `value_desc_${i}`, width: 36 },
            { header: `Metric Index ${i}`, key: `index_${i}`, width: 16 },
            { header: `Metric Index ${i} Min`, key: `index_min_${i}`, width: 16 },
            { header: `Metric Index ${i} Max`, key: `index_max_${i}`, width: 16 }
          );
        }

        const curvesSheet = workbook.addWorksheet('Reference Curves');
        curvesSheet.columns = curveColumns;
        curvesSheet.addRows(
          curveRowsRaw.map((row) => {
            const nextRow = {
              metricId: row.metricId,
              metricName: row.metricName,
              profileId: row.profileId,
              profileTier: row.profileTier,
              curveSetId: row.curveSetId,
              curveSetName: row.curveSetName,
              curveSetTier: row.curveSetTier,
              curveId: row.curveId,
              curveName: row.curveName,
              units: row.units,
              xType: row.xType,
              indexRange: row.indexRange,
              axesXLabel: row.axesXLabel,
              axesYLabel: row.axesYLabel,
              activeLayerId: row.activeLayerId,
              layerId: row.layerId,
              layerName: row.layerName,
              isActiveLayer: row.isActiveLayer,
              isUserMetric: row.isUserMetric,
            };
            (row.points || []).forEach((point, index) => {
              const idx = index + 1;
              const { range, min, max } = resolvePointIndex(point);
              nextRow[`value_${idx}`] = toNumberMaybe(point?.x ?? '');
              nextRow[`value_desc_${idx}`] = point?.description ?? '';
              nextRow[`index_${idx}`] = range;
              nextRow[`index_min_${idx}`] = toNumberMaybe(min);
              nextRow[`index_max_${idx}`] = toNumberMaybe(max);
            });
            return nextRow;
          })
        );
        curvesSheet.views = [{ state: 'frozen', ySplit: 1 }];

        const buffer = await workbook.xlsx.writeBuffer();
        const blob = new Blob([buffer], {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        const stamp = new Date().toISOString().slice(0, 10);
        link.href = url;
        link.download = `STAF_Metric_Library_${stamp}.xlsx`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) {
        console.error('Metric library export failed.', error);
        window.alert('Metric library export failed. Please try again.');
      } finally {
        state.exporting = false;
        downloadButton.classList.remove('is-loading');
        downloadButton.disabled = false;
        downloadButton.removeAttribute('aria-busy');
      }
    };

    const openLeft = () => {
      if (leftSidebar) {
        leftSidebar.classList.remove('is-collapsed');
        container.classList.remove('is-left-collapsed');
      }
      renderLibrary();
    };

    const closeLeft = () => {
      if (leftSidebar) {
        leftSidebar.classList.add('is-collapsed');
        container.classList.add('is-left-collapsed');
      }
    };

    const openRight = () => {
      if (rightSidebar) {
        rightSidebar.classList.remove('is-collapsed');
        container.classList.remove('is-right-collapsed');
      }
    };

    const closeRight = () => {
      if (rightSidebar) {
        rightSidebar.classList.add('is-collapsed');
        container.classList.add('is-right-collapsed');
      }
    };

    const resolveUserMetricTier = () =>
      state.tierFilter !== 'all' ? state.tierFilter : pageTier || 'screening';

    const buildUserMetricEntry = (group, count) => {
      const tier = resolveUserMetricTier();
      const ratingScaleId = tier === 'rapid' ? 'sfariLikert' : 'fourBand';
      const rubricLevels = buildUserRubricLevels(ratingScaleId);
      const metricId = `user-${slugify(group.function || 'metric')}-${Date.now()}`;
      const profileId = `${tier}-user-${metricId}`;
      const entry = {
        metricId,
        name: `New ${group.function || 'Metric'} Metric ${count}`,
        shortName: '',
        discipline: group.discipline || 'Discipline',
        function: group.function || 'Function',
        category: group.discipline || 'Discipline',
        tags: ['User'],
        status: 'active',
        minimumTier: tier,
        profileAvailability: { screening: false, rapid: false, detailed: false },
        recommendedTiers: [tier],
        inputsSummary: {
          sources: ['User'],
          effort: 'custom',
        },
        profileSummaries: {
          [tier]: {
            profileId,
            scoringType: 'categorical',
            scoringShape: `${rubricLevels.length} levels`,
            rawOutput: 'rating',
            normalizedOutput: 'rating',
            curveSetCount: 0,
          },
        },
        curvesSummary: {
          totalCurveSetCount: 0,
          byTier: { screening: 0, rapid: 0, detailed: 0 },
        },
        detailsRef: null,
        isUserMetric: true,
      };
      entry.profileAvailability[tier] = true;
      entry.curvesSummary.byTier[tier] = 0;
      const detail = {
        schemaVersion: 1,
        metricId,
        name: entry.name,
        shortName: '',
        discipline: entry.discipline,
        function: entry.function,
        functionStatement: '',
        descriptionMarkdown: '',
        methodContextMarkdown: '',
        howToMeasureMarkdown: '',
        profiles: [
          {
            profileId,
            tier,
            status: 'active',
            recommended: true,
            scoring: {
              type: 'categorical',
              output: { kind: 'rating', ratingScaleId },
              rubric: { levels: rubricLevels },
            },
            curveIntegration: {
              enabled: true,
              curveSetRefs: [],
            },
          },
        ],
        references: [],
        tags: entry.tags,
        isUserMetric: true,
      };
      return { entry, detail };
    };

    const createUserMetric = (group) => {
      const functionKey = normalizeText(group.function || '');
      const count =
        userMetricStore.metrics.filter(
          (entry) => normalizeText(entry.function || '') === functionKey
        ).length + 1;
      const { entry, detail } = buildUserMetricEntry(group, count);
      userMetricStore.metrics.push(entry);
      userMetricStore.details[entry.metricId] = detail;
      if (!userMetricStore.curves) {
        userMetricStore.curves = {};
      }
      userMetricStore.curves[entry.metricId] = [];
      persistUserMetricStore(userMetricStore);
      return entry;
    };

    const deleteUserMetric = (metricId) => {
      if (!metricId) {
        return;
      }
      const detail = getUserMetricDetail(metricId);
      const entryIndex = userMetricStore.metrics.findIndex(
        (entry) => entry.metricId === metricId
      );
      if (entryIndex === -1) {
        return;
      }
      userMetricStore.metrics.splice(entryIndex, 1);
      if (userMetricStore.details) {
        delete userMetricStore.details[metricId];
      }
      if (userMetricStore.curves) {
        delete userMetricStore.curves[metricId];
      }
      persistUserMetricStore(userMetricStore);
      if (state.curveDrafts && state.curveDrafts.size) {
        Array.from(state.curveDrafts.keys()).forEach((key) => {
          if (key.startsWith(`${metricId}|`)) {
            state.curveDrafts.delete(key);
          }
        });
      }
      const api = getAssessmentApi();
      if (api && typeof api.removeMetric === 'function') {
        const profileId = detail?.profiles?.[0]?.profileId;
        api.removeMetric({ metricId, profileId });
      }
      if (state.selectedMetricId === metricId) {
        state.selectedMetricId = null;
        state.selectedCurveId = null;
        state.selectedProfileId = null;
      }
      renderLibrary();
      renderInspector();
      closeRight();
    };

    if (leftSidebar) {
      leftSidebar.classList.add('is-collapsed');
      container.classList.add('is-left-collapsed');
    }
    closeRight();

    if (leftSidebar && pageTier === 'screening') {
      if (!leftSidebar.querySelector('.metric-library-resizer')) {
        const resizer = createEl('div', 'metric-library-resizer');
        leftSidebar.appendChild(resizer);

        const minWidth = 240;
        const maxWidth = 520;

        const startResize = (event) => {
          event.preventDefault();
          const startX = event.clientX;
          const startWidth = leftSidebar.getBoundingClientRect().width;

          const handleMove = (moveEvent) => {
            const next = Math.min(
              maxWidth,
              Math.max(minWidth, startWidth + (moveEvent.clientX - startX))
            );
            container.style.setProperty('--library-sidebar-width', `${Math.round(next)}px`);
          };

          const handleUp = () => {
            document.removeEventListener('mousemove', handleMove);
            document.removeEventListener('mouseup', handleUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
          };

          document.addEventListener('mousemove', handleMove);
          document.addEventListener('mouseup', handleUp);
          document.body.style.cursor = 'col-resize';
          document.body.style.userSelect = 'none';
        };

        resizer.addEventListener('mousedown', startResize);
      }
    }

    if (rightSidebar && pageTier === 'screening') {
      if (!rightSidebar.querySelector('.metric-inspector-resizer')) {
        const resizer = createEl('div', 'metric-inspector-resizer');
        rightSidebar.appendChild(resizer);

        const minWidth = 280;
        const maxWidth = 520;

        const startResize = (event) => {
          event.preventDefault();
          const startX = event.clientX;
          const startWidth = rightSidebar.getBoundingClientRect().width;

          const handleMove = (moveEvent) => {
            const next = Math.min(
              maxWidth,
              Math.max(minWidth, startWidth + (startX - moveEvent.clientX))
            );
            container.style.setProperty('--inspector-sidebar-width', `${Math.round(next)}px`);
          };

          const handleUp = () => {
            document.removeEventListener('mousemove', handleMove);
            document.removeEventListener('mouseup', handleUp);
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
          };

          document.addEventListener('mousemove', handleMove);
          document.addEventListener('mouseup', handleUp);
          document.body.style.cursor = 'col-resize';
          document.body.style.userSelect = 'none';
        };

        resizer.addEventListener('mousedown', startResize);
      }
    }

    const handleOpenLibrary = (event) => {
      const button = event.currentTarget;
      const parentWorkbench = button.closest('.assessment-workbench');
      if (parentWorkbench === container) {
        openLeft();
      }
    };

    Array.from(container.querySelectorAll('.library-open-btn, [data-open-metric-library]')).forEach(
      (button) => {
        button.addEventListener('click', handleOpenLibrary);
      }
    );

    const handleOpenLibraryDelegated = (event) => {
      const trigger = event.target.closest('.library-open-btn, [data-open-metric-library]');
      if (!trigger) {
        return;
      }
      if (!container.contains(trigger)) {
        return;
      }
      openLeft();
    };

    container.addEventListener('click', handleOpenLibraryDelegated);

    if (libraryClose) {
      libraryClose.addEventListener('click', closeLeft);
    }

    if (downloadButton) {
      downloadButton.addEventListener('click', downloadMetricLibraryWorkbook);
    }

    if (inspectorClose) {
      inspectorClose.addEventListener('click', closeRight);
    }

    if (searchInput) {
      searchInput.addEventListener('input', (event) => {
        state.searchTerm = event.target.value;
        renderLibrary();
      });
    }

    tierChips.forEach((chip) => {
      chip.addEventListener('click', () => {
        const tier = chip.dataset.tier;
        if (!tier) {
          return;
        }
        state.tierFilter = tier;
        tierChips.forEach((c) => c.classList.toggle('is-active', c === chip));
        state.filters.function = 'all';
        renderLibrary();
        if (state.selectedMetricId) {
          const isOpen = rightSidebar && !rightSidebar.classList.contains('is-collapsed');
          selectMetric(state.selectedMetricId, { openRight: isOpen });
        }
      });
    });

    if (libraryHeader && !libraryHeader.querySelector('.metric-library-filters')) {
      const filterRow = createEl('div', 'metric-library-filters');
      const disciplineSelect = document.createElement('select');
      disciplineSelect.className = 'metric-library-filter metric-library-discipline';
      disciplineSelect.setAttribute('aria-label', 'Filter by discipline');
      const functionSelect = document.createElement('select');
      functionSelect.className = 'metric-library-filter metric-library-function';
      functionSelect.setAttribute('aria-label', 'Filter by function');

      filterRow.appendChild(disciplineSelect);
      filterRow.appendChild(functionSelect);
      libraryHeader.appendChild(filterRow);

      disciplineSelect.addEventListener('change', () => {
        state.filters.discipline = disciplineSelect.value || 'all';
        state.filters.function = 'all';
        renderLibrary();
      });

      functionSelect.addEventListener('change', () => {
        state.filters.function = functionSelect.value || 'all';
        renderLibrary();
      });
    }

    const setActiveTab = (tab) => {
      state.activeTab = tab;
      tabs.forEach((btn) => btn.classList.toggle('is-active', btn.dataset.tab === tab));
      tabPanels.forEach((panel) =>
        panel.classList.toggle('is-active', panel.dataset.tab === tab)
      );
    };

    tabs.forEach((btn) => {
      btn.addEventListener('click', () => {
        setActiveTab(btn.dataset.tab || 'details');
      });
    });

    const getAssessmentApi = () => registry.get(pageTier);

    const getEntryCurveCount = (entry) => {
      const overrideKey = `${entry.metricId}|${state.tierFilter}`;
      if (state.curveCountOverrides.has(overrideKey)) {
        return state.curveCountOverrides.get(overrideKey);
      }
      if (state.tierFilter === 'all') {
        return entry.curvesSummary?.totalCurveSetCount || 0;
      }
      return entry.curvesSummary?.byTier?.[state.tierFilter] || 0;
    };

    const updateCurveCountOverride = (metricId, tier, count) => {
      const key = `${metricId}|${tier}`;
      state.curveCountOverrides.set(key, count);
      renderLibrary();
    };

    const loadCurveList = async (entry) => {
      const cacheKey = `${entry.metricId}|${state.tierFilter}`;
      if (state.curveListCache.has(cacheKey)) {
        return state.curveListCache.get(cacheKey);
      }
      const detail = await loadMetricDetail(entry.metricId, entry.detailsRef);
      const groups = [];
      const profiles = detail.profiles || [];
      const targetProfiles =
        state.tierFilter === 'all'
          ? profiles
          : profiles.filter((profile) => profile.tier === state.tierFilter);

      for (const profile of targetProfiles) {
        const curves = await loadCurvesForProfile(profile);
        const label = state.tierFilter === 'all' ? formatTier(profile.tier) : null;
        groups.push({ label, curves, profileId: profile.profileId, tier: profile.tier });
      }

      state.curveListCache.set(cacheKey, groups);
      return groups;
    };

    const selectMetric = async (metricId, options = {}) => {
      if (!metricId) {
        return;
      }
      if (state.selectedMetricId !== metricId) {
        state.selectedCurveId = null;
      }
      state.selectedMetricId = metricId;
      if (options.tab) {
        state.activeTab = options.tab;
      }
      if (options.curveId) {
        state.selectedCurveId = options.curveId;
      }
      const detail = await loadMetricDetail(metricId);
      const api = getAssessmentApi();
      const preferredProfileId =
        options.profileId || (api && typeof api.getProfile === 'function'
          ? api.getProfile(metricId)
          : null);
      const profile = preferredProfileId
        ? detail.profiles.find((p) => p.profileId === preferredProfileId)
        : getDefaultProfile(detail, state.tierFilter);
      state.selectedProfileId = profile ? profile.profileId : null;
      if (options.openRight !== false) {
        openRight();
      }
      renderLibrary();
      renderInspector();
      setActiveTab(state.activeTab);
    };

    const buildScreeningOrder = async () => {
      if (state.ordering.ready) {
        return;
      }
      if (!state.orderingPromise) {
        state.orderingPromise = (async () => {
          try {
            const [screeningResponse, functionsResponse] = await Promise.all([
              fetch(store.buildUrl('/assets/data/screening-metrics.tsv')),
              fetch(store.buildUrl('/assets/data/functions.json')),
            ]);
            if (!screeningResponse.ok) {
              throw new Error('Failed to load screening metrics order');
            }
            const rows = parseTSV(await screeningResponse.text());
            const functionsList = functionsResponse.ok
              ? await functionsResponse.json()
              : [];
            const validFunctionsList = Array.isArray(functionsList) ? functionsList : [];

            const functionAliases = new Map([
              [
                normalizeText('bed composition and bedform dynamics'),
                normalizeText('bed composition and large wood'),
              ],
            ]);

            const disciplineOrder = [];
            const functionOrder = new Map();
            const metricOrder = new Map();
            const canonicalByFunction = new Map();

            validFunctionsList.forEach((fn) => {
              const discipline = normalizeText(fn?.category || '');
              const func = normalizeText(fn?.name || '');
              if (!discipline || !func) {
                return;
              }
              if (!disciplineOrder.includes(discipline)) {
                disciplineOrder.push(discipline);
              }
              if (!functionOrder.has(discipline)) {
                functionOrder.set(discipline, []);
              }
              const funcList = functionOrder.get(discipline);
              if (funcList && !funcList.includes(func)) {
                funcList.push(func);
              }
              const canonical = { discipline, func };
              canonicalByFunction.set(func, canonical);
              const idAsName = normalizeText(String(fn?.id || '').replace(/-/g, ' '));
              if (idAsName) {
                canonicalByFunction.set(idAsName, canonical);
              }
            });

            functionAliases.forEach((target, alias) => {
              const canonical = canonicalByFunction.get(target);
              if (canonical) {
                canonicalByFunction.set(alias, canonical);
              }
            });

            rows.forEach((row) => {
              const rawDiscipline = normalizeText(row.Discipline || row.discipline || '');
              const rawFunction = normalizeText(row.Function || row.function || '');
              const metric = normalizeText(row.Metric || row.metric || '');
              if (!metric) {
                return;
              }
              const canonical =
                canonicalByFunction.get(functionAliases.get(rawFunction) || rawFunction) ||
                canonicalByFunction.get(rawFunction);
              const discipline = canonical ? canonical.discipline : rawDiscipline;
              const func = canonical ? canonical.func : rawFunction;
              if (!discipline || !func) {
                return;
              }
              if (!disciplineOrder.includes(discipline)) {
                disciplineOrder.push(discipline);
              }
              if (!functionOrder.has(discipline)) {
                functionOrder.set(discipline, []);
              }
              const funcList = functionOrder.get(discipline);
              if (funcList && !funcList.includes(func)) {
                funcList.push(func);
              }
              const metricKey = `${discipline}|${func}`;
              if (!metricOrder.has(metricKey)) {
                metricOrder.set(metricKey, []);
              }
              const metricList = metricOrder.get(metricKey);
              if (metricList && !metricList.includes(metric)) {
                metricList.push(metric);
              }
            });

            state.ordering.disciplineOrder = disciplineOrder;
            state.ordering.functionOrder = functionOrder;
            state.ordering.metricOrder = metricOrder;
            state.ordering.ready = true;
          } catch (error) {
            state.ordering.ready = true;
          }
        })();
      }
      await state.orderingPromise;
    };

    const openProfileModal = (profiles, defaultProfile, onConfirm) => {
      if (!profileModal || !profileOptions) {
        return;
      }
      profileOptions.innerHTML = '';
      let selected = defaultProfile || profiles[0];

      profiles.forEach((profile) => {
        const option = createEl('label', 'metric-profile-option');
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'metric-profile-choice';
        radio.value = profile.profileId;
        if (profile === selected) {
          radio.checked = true;
        }
        radio.addEventListener('change', () => {
          selected = profile;
        });
        const title = createEl('div', null, `${formatTier(profile.tier)} profile`);
        const summary = createEl('div', null, buildScoringSummary(profile));
        option.appendChild(radio);
        option.appendChild(title);
        option.appendChild(summary);
        profileOptions.appendChild(option);
      });

      const closeModal = () => {
        profileModal.hidden = true;
      };

      profileModal.hidden = false;
      if (profileCancel) {
        profileCancel.onclick = closeModal;
      }
      if (profileConfirm) {
        profileConfirm.onclick = () => {
          closeModal();
          if (selected) {
            onConfirm(selected);
          }
        };
      }
    };

    const addMetricToAssessment = async (entry, profileOverride) => {
      const api = getAssessmentApi();
      if (!api) {
        return;
      }
      const detail = await loadMetricDetail(entry.metricId, entry.detailsRef);
      const profiles = detail.profiles || [];
      if (!profiles.length) {
        return;
      }
      const profile = profileOverride || getDefaultProfile(detail, state.tierFilter);
      if (!profile) {
        return;
      }
      const existingProfileCount = profiles.filter((p) => p.tier === profile.tier).length;
      const shouldPickProfile =
        (!profileOverride &&
          state.tierFilter === 'all' &&
          profiles.length > 1) ||
        (!profileOverride && state.tierFilter !== 'all' && existingProfileCount > 1);
      if (shouldPickProfile) {
        openProfileModal(profiles, profile, (selectedProfile) => {
          api.addMetric({ metricId: entry.metricId, profileId: selectedProfile.profileId, detail });
          renderLibrary();
          selectMetric(entry.metricId, { profileId: selectedProfile.profileId });
        });
        return;
      }
      api.addMetric({ metricId: entry.metricId, profileId: profile.profileId, detail });
      renderLibrary();
      selectMetric(entry.metricId, { profileId: profile.profileId });
    };

    const removeMetricFromAssessment = async (metricId, profileId) => {
      const api = getAssessmentApi();
      if (!api) {
        return;
      }
      api.removeMetric({ metricId, profileId });
      renderLibrary();
      renderInspector();
    };

    const renderLibrary = async () => {
      if (!libraryList) {
        return;
      }
      await buildScreeningOrder();
      const index = await store.loadMetricIndex();
      const baseEntries = index.metrics || [];
      const userEntries = userMetricStore.metrics || [];
      const entries = baseEntries.concat(userEntries);
      const searchTerm = state.searchTerm.trim().toLowerCase();

      const filtered = entries.filter((entry) => {
        if (state.tierFilter !== 'all' && !entry.profileAvailability?.[state.tierFilter]) {
          return false;
        }
        if (state.tierFilter !== 'all' && !entry.profileSummaries?.[state.tierFilter]) {
          return false;
        }
        if (state.filters.discipline !== 'all') {
          if (normalizeText(entry.discipline || '') !== normalizeText(state.filters.discipline)) {
            return false;
          }
        }
        if (state.filters.function !== 'all') {
          if (normalizeText(entry.function || '') !== normalizeText(state.filters.function)) {
            return false;
          }
        }
        if (!searchTerm) {
          return true;
        }
        const haystack = [
          entry.name,
          entry.shortName,
          entry.discipline,
          entry.function,
          entry.category,
          ...(entry.tags || []),
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return haystack.includes(searchTerm);
      });

      const sortByScreeningOrder = (a, b) => {
        const disciplineA = normalizeText(a.discipline || '');
        const disciplineB = normalizeText(b.discipline || '');
        const functionA = normalizeText(a.function || '');
        const functionB = normalizeText(b.function || '');
        const metricA = normalizeText(a.name || a.shortName || '');
        const metricB = normalizeText(b.name || b.shortName || '');

        const disciplineOrder = state.ordering.disciplineOrder;
        const disciplineIndexA = disciplineOrder.indexOf(disciplineA);
        const disciplineIndexB = disciplineOrder.indexOf(disciplineB);
        const disciplineRankA = disciplineIndexA === -1 ? 9999 : disciplineIndexA;
        const disciplineRankB = disciplineIndexB === -1 ? 9999 : disciplineIndexB;
        if (disciplineRankA !== disciplineRankB) {
          return disciplineRankA - disciplineRankB;
        }

        const functionsA = state.ordering.functionOrder.get(disciplineA) || [];
        const functionsB = state.ordering.functionOrder.get(disciplineB) || [];
        const functionIndexA = functionsA.indexOf(functionA);
        const functionIndexB = functionsB.indexOf(functionB);
        const functionRankA = functionIndexA === -1 ? 9999 : functionIndexA;
        const functionRankB = functionIndexB === -1 ? 9999 : functionIndexB;
        if (functionRankA !== functionRankB) {
          return functionRankA - functionRankB;
        }

        const metricKeyA = `${disciplineA}|${functionA}`;
        const metricKeyB = `${disciplineB}|${functionB}`;
        const metricsA = state.ordering.metricOrder.get(metricKeyA) || [];
        const metricsB = state.ordering.metricOrder.get(metricKeyB) || [];
        const metricIndexA = metricsA.indexOf(metricA);
        const metricIndexB = metricsB.indexOf(metricB);
        const metricRankA = metricIndexA === -1 ? 9999 : metricIndexA;
        const metricRankB = metricIndexB === -1 ? 9999 : metricIndexB;
        if (metricRankA !== metricRankB) {
          return metricRankA - metricRankB;
        }

        return (a.name || '').localeCompare(b.name || '');
      };

      const sorted = filtered.slice().sort(sortByScreeningOrder);

      if (libraryHeader) {
        const disciplineSelect = libraryHeader.querySelector('.metric-library-discipline');
        const functionSelect = libraryHeader.querySelector('.metric-library-function');
        if (disciplineSelect && functionSelect) {
          const tierEntries =
            state.tierFilter === 'all'
              ? entries
              : entries.filter((entry) => entry.profileAvailability?.[state.tierFilter]);

          const disciplineOptions = [];
          const disciplineLookup = new Map();
          state.ordering.disciplineOrder.forEach((discipline) => {
            if (
              tierEntries.some(
                (entry) =>
                  normalizeText(entry.discipline || '') === normalizeText(discipline)
              )
            ) {
              disciplineOptions.push(discipline);
              disciplineLookup.set(normalizeText(discipline), discipline);
            }
          });
          tierEntries.forEach((entry) => {
            const normalized = normalizeText(entry.discipline || '');
            if (!normalized || disciplineLookup.has(normalized)) {
              return;
            }
            disciplineLookup.set(normalized, entry.discipline || normalized);
            disciplineOptions.push(entry.discipline || normalized);
          });

          disciplineSelect.innerHTML = '';
          const disciplineAll = document.createElement('option');
          disciplineAll.value = 'all';
          disciplineAll.textContent = 'All disciplines';
          disciplineSelect.appendChild(disciplineAll);
          disciplineOptions.forEach((discipline) => {
            const option = document.createElement('option');
            option.value = discipline;
            option.textContent = capitalizeFirst(discipline);
            disciplineSelect.appendChild(option);
          });
          disciplineSelect.value = state.filters.discipline;

          const functionOptions = [];
          const activeDiscipline =
            state.filters.discipline !== 'all' ? state.filters.discipline : null;
          const disciplinesToUse = activeDiscipline
            ? [activeDiscipline]
            : disciplineOptions;
          const functionLookup = new Map();
          disciplinesToUse.forEach((discipline) => {
            const functionsForDiscipline =
              state.ordering.functionOrder.get(discipline) || [];
            functionsForDiscipline.forEach((fn) => {
              if (
                tierEntries.some(
                  (entry) =>
                    normalizeText(entry.discipline || '') === normalizeText(discipline) &&
                    normalizeText(entry.function || '') === normalizeText(fn)
                )
              ) {
                functionOptions.push(fn);
                functionLookup.set(normalizeText(fn), fn);
              }
            });
          });
          tierEntries.forEach((entry) => {
            if (
              activeDiscipline &&
              normalizeText(entry.discipline || '') !== normalizeText(activeDiscipline)
            ) {
              return;
            }
            const normalized = normalizeText(entry.function || '');
            if (!normalized || functionLookup.has(normalized)) {
              return;
            }
            functionLookup.set(normalized, entry.function || normalized);
            functionOptions.push(entry.function || normalized);
          });

          functionSelect.innerHTML = '';
          const functionAll = document.createElement('option');
          functionAll.value = 'all';
          functionAll.textContent = 'All functions';
          functionSelect.appendChild(functionAll);
          functionOptions.forEach((fn) => {
            const option = document.createElement('option');
            option.value = fn;
            option.textContent = capitalizeFirst(fn);
            functionSelect.appendChild(option);
          });
          functionSelect.value = state.filters.function;
        }
      }

      libraryList.innerHTML = '';
      const fragment = document.createDocumentFragment();

      const tierOrder = { screening: 1, rapid: 2, detailed: 3 };
      const resolveEntryTier = (entry) => {
        if (state.tierFilter !== 'all') {
          return state.tierFilter;
        }
        return entry.minimumTier || 'screening';
      };

      const groups = [];
      const groupMap = new Map();

      sorted.forEach((entry) => {
        const disciplineKey = normalizeText(entry.discipline || '');
        const functionKey = normalizeText(entry.function || '');
        const groupKey = `${disciplineKey}||${functionKey}`;
        let group = groupMap.get(groupKey);
        if (!group) {
          group = {
            discipline: entry.discipline || 'Discipline',
            function: entry.function || 'Function',
            entries: [],
          };
          groupMap.set(groupKey, group);
          groups.push(group);
        } else {
          group.discipline = pickPreferredLabel(group.discipline, entry.discipline);
          group.function = pickPreferredLabel(group.function, entry.function);
        }
        group.entries.push(entry);
      });

      groups.forEach((group) => {
        const groupRow = createEl('div', 'metric-library-group');
        const categoryClass = getDisciplineCategoryClass(group.discipline);
        if (categoryClass) {
          groupRow.classList.add(categoryClass);
        }
        const groupMain = createEl('div', 'metric-library-group-main');
        const disciplineLabel = createEl(
          'span',
          'metric-library-group-discipline',
          group.discipline || 'Discipline'
        );
        const functionLabel = createEl(
          'span',
          'metric-library-group-function',
          group.function || 'Function'
        );
        groupMain.appendChild(disciplineLabel);
        groupMain.appendChild(functionLabel);
        const groupActions = createEl('div', 'metric-library-group-actions');
        const addMetricBtn = createEl('button', 'metric-group-add', '+');
        addMetricBtn.type = 'button';
        addMetricBtn.setAttribute('aria-label', 'Add metric');
        addMetricBtn.setAttribute(
          'title',
          `Add metric to ${group.function || 'function'}`
        );
        addMetricBtn.addEventListener('click', (event) => {
          event.stopPropagation();
          const newEntry = createUserMetric(group);
          renderLibrary();
          selectMetric(newEntry.metricId, { tab: 'details' });
          openRight();
        });
        groupActions.appendChild(addMetricBtn);
        groupRow.appendChild(groupMain);
        groupRow.appendChild(groupActions);
        fragment.appendChild(groupRow);

        group.entries
          .slice()
          .sort((a, b) => {
            const tierA = resolveEntryTier(a);
            const tierB = resolveEntryTier(b);
            const tierRankA = tierOrder[tierA] || 99;
            const tierRankB = tierOrder[tierB] || 99;
            if (tierRankA !== tierRankB) {
              return tierRankA - tierRankB;
            }
            const nameA = (a.name || a.shortName || '').toLowerCase();
            const nameB = (b.name || b.shortName || '').toLowerCase();
            return nameA.localeCompare(nameB);
          })
          .forEach((entry) => {
            const row = createEl('div', 'metric-library-row is-clickable');
            const isUserMetric = Boolean(entry.isUserMetric);
            if (entry.metricId === state.selectedMetricId) {
              row.classList.add('is-selected');
            }
            if (!isUserMetric) {
              row.classList.add('is-locked');
            }
            row.setAttribute('role', 'button');
            row.setAttribute('tabindex', '0');
            row.setAttribute(
              'aria-label',
              `Open ${entry.name || 'metric'} details`
            );
            const handleRowActivate = (event) => {
              if (event.target.closest('button')) {
                return;
              }
              selectMetric(entry.metricId, { tab: 'details' });
            };
            row.addEventListener('click', handleRowActivate);
            row.addEventListener('keydown', (event) => {
              if (event.key !== 'Enter' && event.key !== ' ') {
                return;
              }
              if (event.target.closest('button')) {
                return;
              }
              event.preventDefault();
              handleRowActivate(event);
            });
            const metricCell = createEl('div', 'metric-cell');
            const metricNameRow = createEl('div', 'metric-name-row');
            const metricName = createEl('span', 'metric-name-text', entry.name || '-');
            metricNameRow.appendChild(metricName);
            const addButton = createEl('button', 'metric-add-btn');
            addButton.type = 'button';
            addButton.textContent = '+';
            addButton.setAttribute('title', 'Add Metric');

            const api = getAssessmentApi();
            const isReadOnly =
              api && typeof api.isReadOnly === 'function' ? api.isReadOnly() : false;
            const defaultSummary =
              state.tierFilter !== 'all'
                ? entry.profileSummaries?.[state.tierFilter]
                : entry.profileSummaries?.screening ||
                  entry.profileSummaries?.rapid ||
                  entry.profileSummaries?.detailed;
            const defaultProfileId = defaultSummary?.profileId || null;
            const isAdded =
              api && defaultProfileId
                ? api.isMetricAdded(entry.metricId, defaultProfileId)
                : false;
            if (isAdded) {
              addButton.textContent = '-';
              addButton.classList.add('is-remove');
              addButton.setAttribute('aria-label', 'Remove metric');
              addButton.setAttribute('title', 'Remove Metric');
            }

            if (isReadOnly) {
              addButton.disabled = true;
              addButton.classList.add('is-disabled');
            }

            addButton.addEventListener('click', () => {
              if (isReadOnly) {
                return;
              }
              if (isAdded) {
                removeMetricFromAssessment(entry.metricId, defaultProfileId);
                return;
              }
              addMetricToAssessment(entry);
            });
            metricNameRow.appendChild(addButton);
            metricCell.appendChild(metricNameRow);
            if (entry.shortName) {
              metricCell.appendChild(createEl('div', 'metric-short-name', entry.shortName));
            }

            const minTier = createEl('div', 'metric-cell metric-tier-cell');
            if (state.tierFilter === 'all') {
              const tierOrderList = ['screening', 'rapid', 'detailed'];
              const availableTiers = tierOrderList.filter(
                (tier) => entry.profileAvailability?.[tier]
              );
              if (availableTiers.length) {
                availableTiers.forEach((tier) => {
                  const chip = createEl(
                    'span',
                    'metric-min-tier',
                    formatTierAbbrev(tier)
                  );
                  minTier.appendChild(chip);
                });
              } else {
                const fallbackChip = createEl('span', 'metric-min-tier', '-');
                minTier.appendChild(fallbackChip);
              }
            } else {
              const displayTier = state.tierFilter;
              const minTierChip = createEl(
                'span',
                'metric-min-tier',
                displayTier ? formatTierAbbrev(displayTier) : '-'
              );
              minTier.appendChild(minTierChip);
            }
            if (!isUserMetric) {
              const lockIcon = createEl('span', 'metric-lock-icon metric-lock-icon-tier');
              lockIcon.setAttribute('aria-hidden', 'true');
              lockIcon.innerHTML =
                '<svg viewBox="0 0 24 24" aria-hidden="true">' +
                '<path d="M7 11V8a5 5 0 0 1 10 0v3" fill="none" stroke="currentColor" stroke-width="2" />' +
                '<rect x="5" y="11" width="14" height="10" rx="2" ry="2" fill="none" stroke="currentColor" stroke-width="2" />' +
                '</svg>';
              minTier.appendChild(lockIcon);
            }

            row.appendChild(metricCell);
            row.appendChild(minTier);

            fragment.appendChild(row);
          });
      });

      libraryList.appendChild(fragment);
    };

    const renderInspector = async () => {
      if (!inspectorContent || !inspectorEmpty) {
        return;
      }
      if (!state.selectedMetricId) {
        inspectorContent.hidden = true;
        inspectorEmpty.hidden = false;
        closeRight();
        return;
      }

      const detail = await loadMetricDetail(state.selectedMetricId);
      if (!detail) {
        return;
      }
      const isUserMetric = Boolean(detail.isUserMetric);
      const isLockedMetric = !isUserMetric;

      const profile = detail.profiles.find((p) => p.profileId === state.selectedProfileId) ||
        getDefaultProfile(detail, state.tierFilter);
      if (profile) {
        state.selectedProfileId = profile.profileId;
      }

      inspectorContent.hidden = false;
      inspectorEmpty.hidden = true;

      if (inspectorTitle) {
        inspectorTitle.innerHTML = '';
        if (isUserMetric) {
          const nameInput = document.createElement('input');
          nameInput.type = 'text';
          nameInput.className = 'metric-inspector-title-input';
          nameInput.value = detail.name || detail.metricId;
          nameInput.setAttribute('aria-label', 'Metric name');
          nameInput.addEventListener('input', () => {
            const nextValue = nameInput.value.trim() || detail.metricId;
            saveUserMetric(detail.metricId, { name: nextValue }, { name: nextValue });
            renderLibrary();
          });
          inspectorTitle.appendChild(nameInput);
        } else {
          inspectorTitle.textContent = detail.name || detail.metricId;
        }
      }
      if (inspectorSubtitle) {
        inspectorSubtitle.textContent =
          detail.discipline && detail.function
            ? `${detail.discipline} - ${detail.function}`
            : detail.function || detail.discipline || '';
      }

      const api = getAssessmentApi();
      const isAdded =
        api && profile ? api.isMetricAdded(detail.metricId, profile.profileId) : false;
      const isReadOnly =
        !api || (typeof api.isReadOnly === 'function' ? api.isReadOnly() : false);

      const curveOverrideKey = `${detail.metricId}|${profile?.tier || ''}`;
      const overrideCount = state.curveCountOverrides.get(curveOverrideKey);
      const baseCount = profile?.curveIntegration?.curveSetRefs?.length || 0;
      const curveCount = overrideCount !== undefined ? overrideCount : baseCount;

      if (inspectorToggle) {
        inspectorToggle.textContent = isAdded ? '-' : '+';
        inspectorToggle.classList.toggle('is-remove', isAdded);
        inspectorToggle.setAttribute(
          'aria-label',
          isAdded ? 'Remove metric' : 'Add metric'
        );
        inspectorToggle.setAttribute('title', isAdded ? 'Remove Metric' : 'Add Metric');
        inspectorToggle.disabled =
          isReadOnly || (!isAdded && profile?.curveIntegration?.enabled && curveCount === 0);
        inspectorToggle.classList.toggle('is-disabled', inspectorToggle.disabled);
        inspectorToggle.onclick = () => {
          if (inspectorToggle.disabled) {
            return;
          }
          if (isAdded) {
            removeMetricFromAssessment(detail.metricId, profile?.profileId);
            return;
          }
          addMetricToAssessment({ metricId: detail.metricId, detailsRef: detail.detailsRef }, profile);
        };
      }

      if (profileSelector) {
        profileSelector.innerHTML = '';
        detail.profiles.forEach((p) => {
          const chip = createEl('button', 'metric-profile-chip', formatTier(p.tier));
          chip.type = 'button';
          chip.classList.toggle('is-active', p.profileId === profile?.profileId);
          chip.addEventListener('click', () => {
            state.selectedProfileId = p.profileId;
            renderInspector();
          });
          profileSelector.appendChild(chip);
        });
      }

      if (profileSummary) {
        profileSummary.textContent = profile ? buildScoringSummary(profile) : '';
      }

      let addWarning = inspectorContent.querySelector('.metric-add-warning');
      if (!addWarning) {
        addWarning = createEl('div', 'metric-add-warning');
        inspectorContent.insertBefore(addWarning, profileSummary?.nextSibling || null);
      }
      const needsCurveWarning =
        !isReadOnly && profile?.curveIntegration?.enabled && curveCount === 0;
      if (needsCurveWarning) {
        addWarning.textContent =
          'Configure at least one reference curve to add this metric.';
        addWarning.hidden = false;
      } else {
        addWarning.hidden = true;
      }

      const renderDetailsTab = () => {
        const panel = tabPanels.find((p) => p.dataset.tab === 'details');
        if (!panel) {
          return;
        }
        panel.innerHTML = '';
        if (isLockedMetric) {
          const lockedNote = createEl(
            'div',
            'metric-detail-locked',
            'This metric is locked and cannot be edited.'
          );
          panel.appendChild(lockedNote);
        }
        if (isUserMetric) {
          const editor = createEl('div', 'metric-detail-editor');
          const buildField = (label, value, onChange, isTextarea) => {
            const field = document.createElement('label');
            field.className = 'metric-detail-field';
            const title = createEl('span', 'metric-detail-field-label', label);
            const input = isTextarea
              ? document.createElement('textarea')
              : document.createElement('input');
            if (isTextarea) {
              input.rows = 3;
            }
            input.value = value || '';
            input.addEventListener('input', () => onChange(input.value));
            field.appendChild(title);
            field.appendChild(input);
            return field;
          };

          editor.appendChild(
            buildField('Metric statement', detail.descriptionMarkdown, (value) => {
              saveUserMetric(detail.metricId, { descriptionMarkdown: value });
            }, true)
          );
          editor.appendChild(
            buildField('Method / Context', detail.methodContextMarkdown, (value) => {
              saveUserMetric(detail.metricId, { methodContextMarkdown: value });
            }, true)
          );
          editor.appendChild(
            buildField('How to measure', detail.howToMeasureMarkdown, (value) => {
              saveUserMetric(detail.metricId, { howToMeasureMarkdown: value });
            }, true)
          );
          panel.appendChild(editor);
          return;
        }
        if (detail.descriptionMarkdown) {
          const statement = createEl('div', 'metric-detail-section');
          statement.appendChild(createEl('h4', null, 'Metric statement'));
          statement.appendChild(renderMarkdown(detail.descriptionMarkdown));
          panel.appendChild(statement);
        }
        const method = createEl('div', 'metric-detail-section');
        method.appendChild(createEl('h4', null, 'Method / Context'));
        if (detail.methodContextMarkdown) {
          method.appendChild(renderMarkdown(detail.methodContextMarkdown));
        } else {
          const methodLines = [];
          if (detail.context) {
            methodLines.push(`Context: ${detail.context}`);
          }
          if (detail.method) {
            methodLines.push(`Method: ${detail.method}`);
          }
          method.appendChild(renderMarkdown(methodLines.join('\n')));
        }

        const how = createEl('div', 'metric-detail-section');
        how.appendChild(createEl('h4', null, 'How to measure'));
        how.appendChild(renderMarkdown(detail.howToMeasureMarkdown || ''));

        const curves = createEl('div', 'metric-detail-section');
        curves.appendChild(createEl('h4', null, 'Reference curves'));
        const curveCount = profile?.curveIntegration?.curveSetRefs?.length || 0;
        curves.appendChild(createEl('div', null, `${curveCount} curve set(s) configured.`));
        const openCurvesBtn = createEl('button', 'btn btn-small', 'Open curve builder');
        openCurvesBtn.type = 'button';
        openCurvesBtn.addEventListener('click', () => setActiveTab('curves'));
        curves.appendChild(openCurvesBtn);

        panel.appendChild(method);
        panel.appendChild(how);
        panel.appendChild(curves);
      };

      const renderScoringTab = async () => {
        const panel = tabPanels.find((p) => p.dataset.tab === 'scoring');
        if (!panel || !profile) {
          return;
        }
        panel.innerHTML = '';
        const scoring = profile.scoring;
        if (scoring.type === 'categorical') {
          let ratingScale = null;
          let rangeMap = new Map();
          let curveLabels = [];
          const pickActiveCurve = (curves) => {
            if (!Array.isArray(curves) || !curves.length) {
              return null;
            }
            if (state.selectedCurveId) {
              const match = curves.find((curve) => curve.curveId === state.selectedCurveId);
              if (match) {
                return match;
              }
            }
            return curves[0];
          };
          const resolveScoringCurves = async () => {
            const curveKey = `${detail.metricId}|${profile.profileId}`;
            const draftCurves = state.curveDrafts.get(curveKey);
            if (Array.isArray(draftCurves) && draftCurves.length) {
              return draftCurves;
            }
            const api = getAssessmentApi();
            const isAdded =
              api && profile ? api.isMetricAdded(detail.metricId, profile.profileId) : false;
            if (isAdded && api?.getCurve) {
              const curve = api.getCurve(detail.metricId);
              if (curve) {
                return [curve];
              }
            }
            if (profile?.curveIntegration?.enabled) {
              const curves = await loadCurvesForProfile(profile);
              return curves.map((entry) => entry.data);
            }
            return [];
          };
          try {
            const ratingData = await store.loadRatingScales();
            const ratingScaleId =
              scoring.ratingScaleId || scoring.output?.ratingScaleId;
            ratingScale = ratingData?.ratingScales?.find(
              (scale) => scale.ratingScaleId === ratingScaleId
            );
          } catch (error) {
            ratingScale = null;
          }
          try {
            const curves = await resolveScoringCurves();
            const activeCurve = pickActiveCurve(curves);
            if (activeCurve && normalizeCurveType(activeCurve.xType) === 'categorical') {
              if (activeCurve.indexRange) {
                ensureCurveRanges(activeCurve);
              }
              const activeLayerId = activeCurve.activeLayerId;
              const layer =
                (activeCurve.layers || []).find((entry) => entry.id === activeLayerId) ||
                (activeCurve.layers ? activeCurve.layers[0] : null);
              if (layer && Array.isArray(layer.points)) {
                curveLabels = layer.points
                  .map((point) => (point?.x != null ? String(point.x).trim() : ''))
                  .filter(Boolean);
                layer.points.forEach((point) => {
                  const min = parseScore(point.yMin);
                  const max = parseScore(point.yMax);
                  if (min !== null && max !== null) {
                    rangeMap.set(normalizeText(point.x), { min, max });
                  }
                });
              }
            }
          } catch (error) {
            rangeMap = new Map();
          }
          const hasScores = Boolean(
            ratingScale && ratingScale.levels?.some((level) => typeof level.score === 'number')
          );
          const hasRanges = rangeMap.size > 0;
          const hasSuggestedIndex = hasScores || hasRanges;
          const levelMap = new Map();
          (scoring.rubric?.levels || []).forEach((level) => {
            const key = normalizeText(level.label || level.ratingId || '');
            if (key) {
              levelMap.set(key, level);
            }
          });
          const rubricLevels = scoring.rubric?.levels || [];
          const rows = curveLabels.length
            ? curveLabels.map((label, index) => ({
                label,
                level: levelMap.get(normalizeText(label)) || rubricLevels[index] || null,
              }))
            : rubricLevels.map((level) => ({
                label: level.label || '-',
                level,
              }));
          const table = createEl('table', 'metric-rubric-table');
          if (hasSuggestedIndex) {
            table.classList.add('has-suggested-index');
          }
          table.innerHTML = hasSuggestedIndex
            ? '<thead><tr><th>Rating</th><th>Suggested index</th><th>Criteria</th></tr></thead>'
            : '<thead><tr><th>Rating</th><th>Criteria</th></tr></thead>';
          const tbody = document.createElement('tbody');
          rows.forEach((rowData) => {
            const level = rowData.level;
            const row = document.createElement('tr');
            const scoreCell = hasScores
              ? (() => {
                  if (!level) {
                    return '-';
                  }
                  const match = ratingScale.levels.find((entry) => entry.id === level.ratingId);
                  if (!match || typeof match.score !== 'number') {
                    return '-';
                  }
                  return match.score.toFixed(2);
                })()
              : null;
            let suggestedIndex = null;
            if (hasSuggestedIndex) {
              let rangeText = null;
              if (hasRanges) {
                const lookupKey = normalizeText(rowData.label || level?.label || level?.ratingId || '');
                const range = rangeMap.get(lookupKey);
                if (range) {
                  rangeText = `${range.min.toFixed(2)}-${range.max.toFixed(2)}`;
                }
              }
              suggestedIndex = hasRanges ? rangeText || '-' : scoreCell || '-';
            }
            row.innerHTML = hasSuggestedIndex
              ? `<td>${rowData.label || '-'}</td><td>${suggestedIndex}</td><td>${level?.criteriaMarkdown || '-'}</td>`
              : `<td>${rowData.label || '-'}</td><td>${level?.criteriaMarkdown || '-'}</td>`;
            tbody.appendChild(row);
          });
          table.appendChild(tbody);
          panel.appendChild(table);
        } else if (scoring.type === 'thresholds') {
          const table = createEl('table', 'metric-rubric-table');
          table.innerHTML =
            '<thead><tr><th>Band</th><th>Criteria</th></tr></thead>';
          const tbody = document.createElement('tbody');
          (scoring.rubric?.bands || []).forEach((band) => {
            const row = document.createElement('tr');
            row.innerHTML =
              `<td>${band.label || band.rawScore || '-'}</td><td>${band.criteriaMarkdown || '-'}</td>`;
            tbody.appendChild(row);
          });
          table.appendChild(tbody);
          panel.appendChild(table);
        } else if (scoring.type === 'curve') {
          panel.appendChild(createEl('div', null, 'Scoring is defined by reference curves.'));
        } else if (scoring.type === 'formula') {
          panel.appendChild(createEl('div', null, `Expression: ${scoring.rubric?.expression || '-'}`));
        } else {
          panel.appendChild(createEl('div', null, 'Scoring definition is minimal for this profile.'));
        }
      };

      const renderCurvesTab = async () => {
        const panel = tabPanels.find((p) => p.dataset.tab === 'curves');
        if (!panel || !profile) {
          return;
        }
        panel.innerHTML = '';
        const canEditCurves = isUserMetric;
        if (isLockedMetric) {
          const lockedNote = createEl(
            'div',
            'metric-detail-locked',
            'This metric is locked and cannot be edited.'
          );
          panel.appendChild(lockedNote);
        }

        const emptyNotice = createEl(
          'div',
          'metric-curve-empty',
          'No curves yet. Click "Add curve" to create one.'
        );
        panel.appendChild(emptyNotice);

        const builder = createEl('div', 'metric-curve-builder');
        const controls = createEl('div', 'metric-curve-controls');
        const curveSelect = document.createElement('select');
        const curveNameInput = document.createElement('input');
        curveNameInput.className = 'metric-curve-name-input';
        curveNameInput.placeholder = 'Curve name';
        const curveAdd = createEl('button', 'btn btn-small', 'Add curve');
        curveAdd.type = 'button';
        const curveRemove = createEl('button', 'btn btn-small', 'Remove curve');
        curveRemove.type = 'button';
        controls.appendChild(curveSelect);
        controls.appendChild(curveNameInput);
        controls.appendChild(curveAdd);
        controls.appendChild(curveRemove);

        const metaRow = createEl('div', 'metric-curve-controls');
        const unitsInput = document.createElement('input');
        unitsInput.placeholder = 'Units';
        const xTypeSelect = document.createElement('select');
        xTypeSelect.innerHTML =
          '<option value="categorical">Categorical</option><option value="quantitative">Quantitative</option>';
        const rangeToggle = document.createElement('label');
        rangeToggle.className = 'metric-curve-range-toggle';
        const rangeToggleInput = document.createElement('input');
        rangeToggleInput.type = 'checkbox';
        const rangeToggleText = document.createElement('span');
        rangeToggleText.textContent = 'Index scores as range';
        rangeToggle.appendChild(rangeToggleInput);
        rangeToggle.appendChild(rangeToggleText);
        metaRow.appendChild(unitsInput);
        metaRow.appendChild(xTypeSelect);
        metaRow.appendChild(rangeToggle);

        const table = createEl('table', 'metric-curve-table');
        table.innerHTML =
          '<colgroup>' +
          '<col class="metric-curve-col-value">' +
          '<col class="metric-curve-col-index">' +
          '<col class="metric-curve-col-desc">' +
          '</colgroup>' +
          '<thead><tr><th>Value</th><th>Index</th><th>Description</th></tr></thead>';
        const tbody = document.createElement('tbody');
        table.appendChild(tbody);
        if (state.curveColumnWidths) {
          table.style.setProperty(
            '--curve-col-value',
            `${state.curveColumnWidths.value}px`
          );
          table.style.setProperty(
            '--curve-col-index',
            `${state.curveColumnWidths.index}px`
          );
        }

        const chartWrap = createEl('div', 'metric-curve-chart-wrap');
        const chartExpand = createEl('button', 'metric-curve-chart-expand', 'Expand');
        chartExpand.type = 'button';
        chartExpand.setAttribute('aria-label', 'Expand curve chart');
        const curveChart = document.createElement('canvas');
        curveChart.className = 'curve-chart';
        curveChart.width = 520;
        curveChart.height = 260;
        chartWrap.appendChild(chartExpand);
        chartWrap.appendChild(curveChart);

        builder.appendChild(controls);
        builder.appendChild(metaRow);
        builder.appendChild(table);
        builder.appendChild(chartWrap);
        panel.appendChild(builder);

        const api = getAssessmentApi();
        const isAdded = api && profile ? api.isMetricAdded(detail.metricId, profile.profileId) : false;

        let curves = [];
        if (isUserMetric) {
          const storedCurves = getUserMetricCurves(detail.metricId);
          if (Array.isArray(storedCurves)) {
            curves = storedCurves;
          }
        }
        if (isAdded && api && api.getCurve) {
          const curve = api.getCurve(detail.metricId);
          if (curve) {
            curves = [curve];
          }
        }

        if (!curves.length && !isUserMetric) {
          curves = await loadCurvesForProfile(profile);
          curves = curves.map((item) => item.data);
        }

        if (!curves.length && !isUserMetric) {
          curves = [buildDefaultCurve(profile)];
          updateCurveCountOverride(detail.metricId, profile.tier, curves.length);
        }

        curves.forEach((curve, index) => {
          if (!curve.curveId) {
            curve.curveId = `curve-${index + 1}`;
          }
        });
        const curveDraftKey = `${detail.metricId}|${profile.profileId}`;
        state.curveDrafts.set(curveDraftKey, curves);

        const syncCurveDrafts = () => {
          state.curveDrafts.set(curveDraftKey, curves);
        };

        const notifyCurveChange = () => {
          syncCurveDrafts();
          if (isUserMetric) {
            userMetricStore.curves[detail.metricId] = curves;
            const entry = getUserMetricEntry(detail.metricId);
            if (entry && entry.curvesSummary && entry.curvesSummary.byTier) {
              entry.curvesSummary.totalCurveSetCount = curves.length;
              entry.curvesSummary.byTier[profile.tier] = curves.length;
              if (entry.profileSummaries && entry.profileSummaries[profile.tier]) {
                entry.profileSummaries[profile.tier].curveSetCount = curves.length;
              }
            }
            persistUserMetricStore(userMetricStore);
          }
          renderScoringTab();
        };

        const getNextCurveName = () => {
          const names = new Set(
            curves.map((curve) => (curve.name || '').toLowerCase())
          );
          let counter = 1;
          while (names.has(`newcurve${counter}`)) {
            counter += 1;
          }
          return `NewCurve${counter}`;
        };

        const getActiveCurve = () => {
          if (state.selectedCurveId) {
            return curves.find((curve) => curve.curveId === state.selectedCurveId) || curves[0];
          }
          return curves[0];
        };

        const updateCurveEmptyState = () => {
          const hasCurve = curves.length > 0;
          emptyNotice.hidden = !isUserMetric || hasCurve;
          if (curveSelect) {
            curveSelect.disabled = !hasCurve || !canEditCurves;
          }
          if (curveNameInput) {
            curveNameInput.disabled = !hasCurve || !canEditCurves;
          }
          if (unitsInput) {
            unitsInput.disabled = !hasCurve || !canEditCurves;
          }
          if (xTypeSelect) {
            xTypeSelect.disabled = !hasCurve || !canEditCurves;
          }
          if (rangeToggleInput) {
            rangeToggleInput.disabled = !hasCurve || !canEditCurves;
          }
          if (curveAdd) {
            curveAdd.disabled = !canEditCurves;
          }
          if (curveRemove) {
            curveRemove.disabled = !canEditCurves || curves.length <= 1;
          }
          builder.classList.toggle('is-locked', !canEditCurves);
        };

        const renderCurveOptions = () => {
          curveSelect.innerHTML = '';
          const active = getActiveCurve();
          if (!curves.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = 'No curves';
            curveSelect.appendChild(option);
            curveSelect.value = '';
            curveNameInput.value = '';
          } else {
            curves.forEach((curve) => {
              const option = document.createElement('option');
              option.value = curve.curveId;
              option.textContent = curve.name || curve.curveId;
              curveSelect.appendChild(option);
            });
            if (active) {
              curveSelect.value = active.curveId;
              curveNameInput.value = active.name || active.curveId;
            }
          }
          updateCurveEmptyState();
        };

        const renderCurveTable = () => {
          const active = getActiveCurve();
          tbody.innerHTML = '';
          if (!active) {
            updateCurveEmptyState();
            renderCurveChart();
            return;
          }
          const layer = active.layers?.[0];
          if (!layer) {
            updateCurveEmptyState();
            return;
          }
          const defaultType = profile?.tier === 'screening' ? 'categorical' : 'quantitative';
          active.xType = normalizeCurveType(active.xType || defaultType);
          const isCategorical = active.xType === 'categorical';
          if (isCategorical && active.indexRange == null) {
            active.indexRange = true;
          }
          if (!isCategorical) {
            active.indexRange = false;
          }
          const useRange = isCategorical && !!active.indexRange;
          if (useRange) {
            ensureCurveRanges(active);
          }
          unitsInput.value = active.units || '';
          xTypeSelect.value = active.xType;
          if (rangeToggleInput) {
            rangeToggleInput.checked = useRange;
            rangeToggleInput.disabled = !isCategorical;
          }
          layer.points.forEach((point) => {
            const row = document.createElement('tr');
            const valueCell = document.createElement('td');
            valueCell.className = 'metric-curve-value-cell';
            const valueInput = document.createElement('input');
            valueInput.className = 'metric-curve-value-input';
            valueInput.placeholder = 'Value';
            valueInput.value = point.x;
            valueInput.disabled = !canEditCurves;
            valueInput.addEventListener('input', () => {
              point.x = valueInput.value;
              if (isAdded && api?.setCurve) {
                api.setCurve(detail.metricId, active);
                api.refresh?.();
              }
              renderCurveChart();
              notifyCurveChange();
            });
            valueCell.appendChild(valueInput);

            const indexCell = document.createElement('td');
            indexCell.className = 'metric-curve-index-cell';
            if (useRange) {
              const rangeWrap = document.createElement('div');
              rangeWrap.className = 'metric-curve-index-range';
              const maxInput = document.createElement('input');
              maxInput.className = 'metric-curve-index-input';
              maxInput.type = 'number';
              maxInput.step = '0.01';
              maxInput.min = '0';
              maxInput.max = '1';
              maxInput.placeholder = 'Max';
              maxInput.value = point.yMax ?? point.y ?? '';
              maxInput.disabled = !canEditCurves;
              const minInput = document.createElement('input');
              minInput.className = 'metric-curve-index-input';
              minInput.type = 'number';
              minInput.step = '0.01';
              minInput.min = '0';
              minInput.max = '1';
              minInput.placeholder = 'Min';
              minInput.value = point.yMin ?? point.y ?? '';
              minInput.disabled = !canEditCurves;
              const syncRange = () => {
                const nextMin = parseScore(minInput.value);
                const nextMax = parseScore(maxInput.value);
                if (nextMin !== null) {
                  point.yMin = roundScore(clampScore(nextMin));
                }
                if (nextMax !== null) {
                  point.yMax = roundScore(clampScore(nextMax));
                }
                if (nextMin !== null && nextMax !== null) {
                  point.y = roundScore((nextMin + nextMax) / 2);
                }
                if (isAdded && api?.setCurve) {
                  api.setCurve(detail.metricId, active);
                  api.refresh?.();
                }
                renderCurveChart();
                notifyCurveChange();
              };
              maxInput.addEventListener('input', syncRange);
              minInput.addEventListener('input', syncRange);
              rangeWrap.appendChild(maxInput);
              rangeWrap.appendChild(minInput);
              indexCell.appendChild(rangeWrap);
            } else {
              const indexInput = document.createElement('input');
              indexInput.className = 'metric-curve-index-input';
              indexInput.type = 'number';
              indexInput.step = '0.01';
              indexInput.min = '0';
              indexInput.max = '1';
              indexInput.value = point.y ?? '';
              indexInput.disabled = !canEditCurves;
              indexInput.addEventListener('input', () => {
                point.y = Number(indexInput.value);
                if (isAdded && api?.setCurve) {
                  api.setCurve(detail.metricId, active);
                  api.refresh?.();
                }
                renderCurveChart();
                notifyCurveChange();
              });
              indexCell.appendChild(indexInput);
            }

            const descCell = document.createElement('td');
            descCell.className = 'metric-curve-desc-cell';
            const descInput = document.createElement('textarea');
            descInput.className = 'metric-curve-desc';
            descInput.placeholder = 'Description';
            descInput.value = point.description || '';
            descInput.readOnly = !canEditCurves;
            descInput.addEventListener('input', () => {
              point.description = descInput.value;
              if (isAdded && api?.setCurve) {
                api.setCurve(detail.metricId, active);
                api.refresh?.();
              }
            });
            descCell.appendChild(descInput);

            row.appendChild(valueCell);
            row.appendChild(indexCell);
            row.appendChild(descCell);
            tbody.appendChild(row);
          });
          renderCurveChart();
          updateCurveEmptyState();
        };

        const renderCurveChart = (targetCanvas = curveChart) => {
          if (!targetCanvas) {
            return;
          }
          const curve = getActiveCurve();
          const ctx = targetCanvas.getContext('2d');
          if (!ctx) {
            return;
          }
          ctx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);
          if (!curve) {
            return;
          }

          const padding = { top: 28, right: 22, bottom: 44, left: 52 };
          const width = targetCanvas.width - padding.left - padding.right;
          const height = targetCanvas.height - padding.top - padding.bottom;

          ctx.strokeStyle = '#333333';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(padding.left, padding.top);
          ctx.lineTo(padding.left, padding.top + height);
          ctx.lineTo(padding.left + width, padding.top + height);
          ctx.stroke();

          const layer = curve.layers?.[0];
          if (!layer) {
            return;
          }

          const normalizedType = normalizeCurveType(curve.xType);
          const isCategorical = normalizedType === 'categorical';
          const useRange = isCategorical && !!curve.indexRange;
          if (useRange) {
            ensureCurveRanges(curve);
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
          const scaleY = (value) =>
            padding.top + (1 - Math.min(1, Math.max(0, value))) * height;

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
              ctx.arc(x, y, 3.5, 0, Math.PI * 2);
              ctx.fill();
            });
          }

          const xTicks = isCategorical
            ? points.map((point) => point.x)
            : points.length
            ? [minX, (minX + maxX) / 2, maxX]
            : [0, 0.5, 1];
          const yTicks = [0, 0.5, 1];

          ctx.fillStyle = '#111111';
          ctx.font = '16px sans-serif';
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
              ctx.fillText(label, x, padding.top + height + 6);
            } else {
              ctx.fillText(tick.toFixed(2), x, padding.top + height + 6);
            }
          });

          // Axis labels
          ctx.fillStyle = '#111111';
          ctx.font = '16px sans-serif';
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          const unitLabel = curve.units ? ` (${curve.units})` : '';
          ctx.fillText(
            `Metric Value${unitLabel}`,
            padding.left + width / 2,
            padding.top + height + 32
          );
          ctx.save();
          ctx.translate(padding.left - 44, padding.top + height / 2);
          ctx.rotate(-Math.PI / 2);
          ctx.fillText('Metric Index', 0, 0);
          ctx.restore();
        };

        const setupCurveColumnResizers = () => {
          const headerRow = table.querySelector('thead tr');
          if (!headerRow) {
            return;
          }
          const headers = headerRow.querySelectorAll('th');
          const valueHeader = headers[0];
          const indexHeader = headers[1];
          const minValue = 90;
          const minIndex = 48;
          const minDesc = 160;

          const ensureHandle = (header, key) => {
            if (!header || header.querySelector('.metric-curve-col-resizer')) {
              return;
            }
            const handle = createEl('div', 'metric-curve-col-resizer');
            handle.addEventListener('mousedown', (event) => {
              event.preventDefault();
              const startX = event.clientX;
              const startValue = state.curveColumnWidths.value;
              const startIndex = state.curveColumnWidths.index;
              const tableWidth = table.getBoundingClientRect().width;

              const onMove = (moveEvent) => {
                const delta = moveEvent.clientX - startX;
                if (key === 'value') {
                  const nextValue = Math.min(
                    tableWidth - startIndex - minDesc,
                    Math.max(minValue, startValue + delta)
                  );
                  state.curveColumnWidths.value = nextValue;
                } else if (key === 'index') {
                  const nextIndex = Math.min(
                    tableWidth - startValue - minDesc,
                    Math.max(minIndex, startIndex + delta)
                  );
                  state.curveColumnWidths.index = nextIndex;
                }
                table.style.setProperty(
                  '--curve-col-value',
                  `${state.curveColumnWidths.value}px`
                );
                table.style.setProperty(
                  '--curve-col-index',
                  `${state.curveColumnWidths.index}px`
                );
              };

              const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
              };

              document.addEventListener('mousemove', onMove);
              document.addEventListener('mouseup', onUp);
              document.body.style.cursor = 'col-resize';
              document.body.style.userSelect = 'none';
            });
            header.appendChild(handle);
          };

          ensureHandle(valueHeader, 'value');
          ensureHandle(indexHeader, 'index');
        };

        renderCurveOptions();
        renderCurveTable();
        setupCurveColumnResizers();

        curveSelect.addEventListener('change', () => {
          if (!canEditCurves) {
            return;
          }
          state.selectedCurveId = curveSelect.value;
          const active = getActiveCurve();
          if (active) {
            curveNameInput.value = active.name || active.curveId;
          }
          renderCurveTable();
          renderCurveChart();
          notifyCurveChange();
        });

        curveNameInput.addEventListener('input', () => {
          if (!canEditCurves) {
            return;
          }
          const active = getActiveCurve();
          if (!active) {
            return;
          }
          const nextName = curveNameInput.value.trim();
          active.name = nextName || active.curveId;
          const option = curveSelect.querySelector(`option[value="${active.curveId}"]`);
          if (option) {
            option.textContent = active.name || active.curveId;
          }
          if (isAdded && api?.setCurve) {
            api.setCurve(detail.metricId, active);
            api.refresh?.();
          }
        });

        curveAdd.addEventListener('click', () => {
          if (!canEditCurves) {
            return;
          }
          const newCurve = buildDefaultCurve(profile);
          newCurve.name = getNextCurveName();
          curves.push(newCurve);
          state.selectedCurveId = newCurve.curveId;
          updateCurveCountOverride(detail.metricId, profile.tier, curves.length);
          renderCurveOptions();
          renderCurveTable();
          renderCurveChart();
          notifyCurveChange();
        });

        curveRemove.addEventListener('click', () => {
          if (!canEditCurves) {
            return;
          }
          if (curves.length <= 1) {
            return;
          }
          const active = getActiveCurve();
          const index = curves.findIndex((curve) => curve.curveId === active.curveId);
          if (index >= 0) {
            curves.splice(index, 1);
            state.selectedCurveId = curves[0].curveId;
            updateCurveCountOverride(detail.metricId, profile.tier, curves.length);
            renderCurveOptions();
            renderCurveTable();
            renderCurveChart();
            notifyCurveChange();
          }
        });

        unitsInput.addEventListener('input', () => {
          if (!canEditCurves) {
            return;
          }
          const active = getActiveCurve();
          if (!active) {
            return;
          }
          active.units = unitsInput.value;
          if (isAdded && api?.setCurve) {
            api.setCurve(detail.metricId, active);
            api.refresh?.();
          }
          renderCurveChart();
        });

        xTypeSelect.addEventListener('change', () => {
          if (!canEditCurves) {
            return;
          }
          const active = getActiveCurve();
          if (!active) {
            return;
          }
          active.xType = normalizeCurveType(xTypeSelect.value);
          const isCategorical = active.xType === 'categorical';
          if (isCategorical && active.indexRange == null) {
            active.indexRange = true;
          }
          if (!isCategorical) {
            active.indexRange = false;
          }
          if (isAdded && api?.setCurve) {
            api.setCurve(detail.metricId, active);
            api.refresh?.();
          }
          renderCurveTable();
          renderCurveChart();
          notifyCurveChange();
        });

        rangeToggleInput.addEventListener('change', () => {
          if (!canEditCurves) {
            return;
          }
          const active = getActiveCurve();
          if (!active) {
            return;
          }
          const isCategorical = normalizeCurveType(active.xType) === 'categorical';
          active.indexRange = isCategorical && rangeToggleInput.checked;
          if (active.indexRange) {
            ensureCurveRanges(active);
          }
          if (isAdded && api?.setCurve) {
            api.setCurve(detail.metricId, active);
            api.refresh?.();
          }
          renderCurveTable();
          renderCurveChart();
          notifyCurveChange();
        });

        const openCurveModal = () => {
          let modal = document.querySelector('.metric-curve-modal');
          if (!modal) {
            modal = createEl('div', 'metric-curve-modal');
            modal.innerHTML =
              '<div class="metric-curve-modal-backdrop"></div>' +
              '<div class="metric-curve-modal-card">' +
              '<div class="metric-curve-modal-header">' +
              '<span>Reference curve</span>' +
              '<button type="button" class="metric-curve-modal-close" aria-label="Close">X</button>' +
              '</div>' +
              '<canvas class="metric-curve-modal-canvas"></canvas>' +
              '</div>';
            document.body.appendChild(modal);
          }
          const backdrop = modal.querySelector('.metric-curve-modal-backdrop');
          const closeBtn = modal.querySelector('.metric-curve-modal-close');
          const modalCanvas = modal.querySelector('.metric-curve-modal-canvas');
          if (modalCanvas) {
            modalCanvas.width = 720;
            modalCanvas.height = 420;
            renderCurveChart(modalCanvas);
          }
          const closeModal = () => {
            modal.classList.remove('is-open');
          };
          if (backdrop) {
            backdrop.onclick = closeModal;
          }
          if (closeBtn) {
            closeBtn.onclick = closeModal;
          }
          modal.classList.add('is-open');
        };

        chartExpand.addEventListener('click', openCurveModal);
      };

      renderDetailsTab();
      renderScoringTab();
      renderCurvesTab();

      let inspectorFooter = inspectorContent.querySelector('.metric-inspector-footer');
      if (!inspectorFooter) {
        inspectorFooter = createEl('div', 'metric-inspector-footer');
        inspectorContent.appendChild(inspectorFooter);
      }
      inspectorFooter.innerHTML = '';
      if (isUserMetric) {
        const deleteBtn = createEl(
          'button',
          'metric-delete-btn',
          'Delete Metric from Library'
        );
        deleteBtn.type = 'button';
        deleteBtn.addEventListener('click', () => {
          deleteUserMetric(detail.metricId);
        });
        inspectorFooter.appendChild(deleteBtn);
        inspectorFooter.hidden = false;
      } else {
        inspectorFooter.hidden = true;
      }
      setActiveTab(state.activeTab);
    };

    renderLibrary();
    renderInspector();

    window.addEventListener('staf:set-library-filters', (event) => {
      const detail = event.detail || {};
      if (detail.tier && detail.tier !== pageTier) {
        return;
      }
      if (!leftSidebar) {
        return;
      }
      const normalizeFilterValue = (value) => {
        if (!value) {
          return 'all';
        }
        const normalized = normalizeText(value);
        if (
          !normalized ||
          normalized === 'all' ||
          normalized === 'alldisciplines' ||
          normalized === 'allfunctions'
        ) {
          return 'all';
        }
        return normalized;
      };
      state.filters.discipline = normalizeFilterValue(detail.discipline);
      state.filters.function = normalizeFilterValue(detail.functionName);
      openLeft();
    });

    window.addEventListener('staf:open-inspector', (event) => {
      const detail = event.detail || {};
      if (detail.tier && detail.tier !== pageTier) {
        return;
      }
      if (!detail.metricId) {
        return;
      }
      selectMetric(detail.metricId, {
        tab: detail.tab || 'details',
        profileId: detail.profileId,
        curveId: detail.curveId,
      });
    });

    window.addEventListener('staf:assessment-registered', () => {
      renderLibrary();
      renderInspector();
    });

    window.addEventListener('staf:assessment-updated', (event) => {
      const detail = event.detail || {};
      if (detail.tier && detail.tier !== pageTier) {
        return;
      }
      renderLibrary();
      renderInspector();
    });
  });
})();


