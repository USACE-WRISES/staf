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
    { value: 'SA', label: 'Strongly Agree', title: 'Strongly Agree' },
    { value: 'A', label: 'Agree', title: 'Agree' },
    { value: 'N', label: 'Neutral', title: 'Neutral' },
    { value: 'D', label: 'Disagree', title: 'Disagree' },
    { value: 'SD', label: 'Strongly Disagree', title: 'Strongly Disagree' },
    { value: 'NA', label: 'Not Applicable', title: 'Not Applicable' },
  ];

  const defaultIndicatorScore = 'N';
  const defaultFunctionScore = 10;
  const collapsedGlyph = '&#9656;';
  const expandedGlyph = '&#9662;';
  const isReadOnlyAssessment = true;
  const indicatorIndexByScore = {
    SA: 0.93,
    A: 0.73,
    N: 0.53,
    D: 0.33,
    SD: 0.13,
  };
  const indicatorIndexRangeByScore = {
    SA: { min: 0.84, max: 1.0, avg: 0.93, hasRange: true },
    A: { min: 0.64, max: 0.83, avg: 0.73, hasRange: true },
    N: { min: 0.4, max: 0.63, avg: 0.53, hasRange: true },
    D: { min: 0.24, max: 0.39, avg: 0.33, hasRange: true },
    SD: { min: 0.0, max: 0.23, avg: 0.13, hasRange: true },
  };

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

      const metricLibraryStore = window.STAFMetricLibraryStore || null;
      const rapidLibraryByPair = new Map();
      const rapidProfileIdByMetricId = new Map();

      const criteriaMap = {};
      criteriaRaw.forEach((row) => {
        const key = normalize(row.Metric || row.Indicators || row.Indicator || '');
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

      if (
        metricLibraryStore &&
        typeof metricLibraryStore.loadMetricIndex === 'function'
      ) {
        try {
          const metricIndex = await metricLibraryStore.loadMetricIndex();
          (metricIndex.metrics || []).forEach((entry) => {
            if (!entry.profileAvailability || !entry.profileAvailability.rapid) {
              return;
            }
            const pairKey = `${normalize(entry.function)}||${normalize(entry.name)}`;
            if (!rapidLibraryByPair.has(pairKey)) {
              rapidLibraryByPair.set(pairKey, entry);
            }
            const rapidProfileId = entry.profileSummaries?.rapid?.profileId || null;
            if (rapidProfileId) {
              rapidProfileIdByMetricId.set(entry.metricId, rapidProfileId);
            }
          });
        } catch (error) {
          // Metric library index is optional for rapid table rendering.
        }
      }

      const getRapidProfileIdFromDetail = (detail) => {
        if (!detail || !Array.isArray(detail.profiles)) {
          return rapidProfileIdByMetricId.get(detail?.metricId) || null;
        }
        const rapidProfiles = detail.profiles.filter(
          (profile) => profile.tier === 'rapid'
        );
        if (!rapidProfiles.length) {
          return rapidProfileIdByMetricId.get(detail.metricId) || null;
        }
        const recommended = rapidProfiles.find((profile) => profile.recommended);
        return (
          recommended?.profileId ||
          rapidProfiles[0]?.profileId ||
          rapidProfileIdByMetricId.get(detail.metricId) ||
          null
        );
      };

      const indicators = indicatorsRaw.map((row, index) => {
        const functionName = row.Functions || row.Function || '';
        const functionKey = normalize(functionName);
        const indicatorName = row.Metric || row.Indicators || row.Indicator || '';
        const pairKey = `${functionKey}||${normalize(indicatorName)}`;
        const matchedEntry = rapidLibraryByPair.get(pairKey) || null;
        const metricId = matchedEntry?.metricId || `indicator-${index + 1}`;
        return {
          id: metricId,
          libraryMetricId: matchedEntry?.metricId || null,
          libraryProfileId:
            rapidProfileIdByMetricId.get(matchedEntry?.metricId || '') || null,
          discipline: disciplineMap[functionKey] || 'Hydrology',
          functionName,
          functionKey,
          functionStatement: row['Function statement'] || '',
          indicator: indicatorName,
          indicatorStatement:
            row['Metric statements'] ||
            row['Metric statement'] ||
            row['Indicator statements'] ||
            row['Indicator statement'] ||
            '',
          context: row.Context || '',
          method: row.Method || '',
          howToMeasure: row['How to measure'] || '',
          criteriaKey: normalize(
            row['Criteria key'] || row.CriteriaKey || row.Metric || row.Indicators || ''
          ),
        };
      });

      const indicatorById = new Map(indicators.map((item) => [item.id, item]));
      const indicatorIdSet = new Set(indicators.map((item) => item.id));

      const ensureLibraryIndicator = (detail) => {
        if (!detail) {
          return null;
        }
        const existing = indicatorById.get(detail.metricId);
        if (existing) {
          if (!existing.libraryMetricId) {
            existing.libraryMetricId = detail.metricId;
          }
          if (!existing.libraryProfileId) {
            existing.libraryProfileId = getRapidProfileIdFromDetail(detail);
          }
          return existing;
        }
        const functionName = detail.function || '';
        const functionKey = normalize(functionName);
        const indicator = {
          id: detail.metricId,
          libraryMetricId: detail.metricId,
          libraryProfileId: getRapidProfileIdFromDetail(detail),
          discipline: detail.discipline || disciplineMap[functionKey] || 'Hydrology',
          functionName,
          functionKey,
          functionStatement: detail.functionStatement || '',
          indicator: detail.name || detail.metricId,
          indicatorStatement: detail.descriptionMarkdown || '',
          context: detail.methodContextMarkdown || '',
          method: detail.methodContextMarkdown || '',
          howToMeasure: detail.howToMeasureMarkdown || '',
          criteriaKey: normalize(detail.name || detail.metricId),
        };
        indicators.push(indicator);
        indicatorById.set(indicator.id, indicator);
        indicatorIdSet.add(indicator.id);
        return indicator;
      };

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
        nameInput.readOnly = true;
        nameInput.disabled = true;
      }
      if (applicabilityInput) {
        applicabilityInput.value = 'Nationwide, wide-able streams';
        applicabilityInput.readOnly = true;
        applicabilityInput.disabled = true;
      }
      if (notesInput) {
        notesInput.readOnly = true;
        notesInput.disabled = true;
      }

      const indicatorScores = new Map();
      const functionScores = new Map();
      const expandedIndicators = new Set();

      indicators.forEach((item) => {
        indicatorScores.set(item.id, defaultIndicatorScore);
        if (!functionScores.has(item.functionName)) {
          functionScores.set(item.functionName, defaultFunctionScore);
        }
      });

      const viewOptions = {
        showAdvancedScoring: false,
        showFunctionMappings: false,
        showRollupComputations: false,
        showSuggestedFunctionScoresCue: false,
        showFunctionScoreCueLabels: false,
      };

      const scoringControls = document.createElement('div');
      scoringControls.className = 'screening-scoring-controls';

      const advancedToggleLabel = document.createElement('label');
      advancedToggleLabel.className = 'screening-advanced-toggle';
      const advancedToggle = document.createElement('input');
      advancedToggle.type = 'checkbox';
      advancedToggle.className = 'screening-advanced-toggle-input';
      advancedToggle.checked = viewOptions.showAdvancedScoring;
      const advancedToggleText = document.createElement('span');
      advancedToggleText.textContent = 'Show advanced scoring columns';
      advancedToggleLabel.appendChild(advancedToggle);
      advancedToggleLabel.appendChild(advancedToggleText);

      const mappingToggleLabel = document.createElement('label');
      mappingToggleLabel.className = 'screening-advanced-toggle';
      const mappingToggle = document.createElement('input');
      mappingToggle.type = 'checkbox';
      mappingToggle.className = 'screening-mapping-toggle-input';
      mappingToggle.checked = viewOptions.showFunctionMappings;
      const mappingToggleText = document.createElement('span');
      mappingToggleText.textContent = 'Show Function Mappings';
      mappingToggleLabel.appendChild(mappingToggle);
      mappingToggleLabel.appendChild(mappingToggleText);

      const rollupToggleLabel = document.createElement('label');
      rollupToggleLabel.className = 'screening-advanced-toggle';
      const rollupToggle = document.createElement('input');
      rollupToggle.type = 'checkbox';
      rollupToggle.className = 'screening-rollup-toggle-input';
      rollupToggle.checked = viewOptions.showRollupComputations;
      const rollupToggleText = document.createElement('span');
      rollupToggleText.textContent = 'Show roll-up at bottom';
      rollupToggleLabel.appendChild(rollupToggle);
      rollupToggleLabel.appendChild(rollupToggleText);

      const suggestedCueToggleLabel = document.createElement('label');
      suggestedCueToggleLabel.className = 'screening-advanced-toggle';
      const suggestedCueToggle = document.createElement('input');
      suggestedCueToggle.type = 'checkbox';
      suggestedCueToggle.className = 'screening-suggested-cue-toggle-input';
      suggestedCueToggle.checked = viewOptions.showSuggestedFunctionScoresCue;
      const suggestedCueToggleText = document.createElement('span');
      suggestedCueToggleText.textContent = 'Show Suggested Function Scores';
      suggestedCueToggleLabel.appendChild(suggestedCueToggle);
      suggestedCueToggleLabel.appendChild(suggestedCueToggleText);

      const sliderLabelsToggleLabel = document.createElement('label');
      sliderLabelsToggleLabel.className = 'screening-advanced-toggle';
      const sliderLabelsToggle = document.createElement('input');
      sliderLabelsToggle.type = 'checkbox';
      sliderLabelsToggle.className = 'screening-slider-labels-toggle-input';
      sliderLabelsToggle.checked = viewOptions.showFunctionScoreCueLabels;
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
            detail: { tier: 'rapid', discipline, functionName },
          })
        );
      };

      const openMetricInspectorForIndicator = (indicator) => {
        if (!window.dispatchEvent || !indicator) {
          return;
        }
        const metricId = indicator.libraryMetricId || indicator.id;
        if (!metricId || metricId.startsWith('indicator-')) {
          return;
        }
        const detail = {
          tier: 'rapid',
          metricId,
          tab: 'details',
        };
        if (indicator.libraryProfileId) {
          detail.profileId = indicator.libraryProfileId;
        }
        window.dispatchEvent(new CustomEvent('staf:open-inspector', { detail }));
      };

      controls.appendChild(search);
      controls.appendChild(disciplineFilter);
      controls.appendChild(libraryButton);

      const table = document.createElement('table');
      table.className = 'screening-table rapid-table show-condensed-view';
      const thead = document.createElement('thead');
      let tbody = document.createElement('tbody');
      const tfoot = document.createElement('tfoot');
      table.appendChild(thead);
      table.appendChild(tbody);
      table.appendChild(tfoot);

      const summaryTable = document.createElement('table');
      summaryTable.className = 'screening-table screening-summary-table';
      const summaryColGroup = document.createElement('colgroup');
      const summaryHead = document.createElement('thead');
      const summaryBody = document.createElement('tbody');
      summaryTable.appendChild(summaryColGroup);
      summaryTable.appendChild(summaryHead);
      summaryTable.appendChild(summaryBody);

      const chartsShell = document.createElement('div');
      chartsShell.className =
        'screening-settings-panel screening-charts-panel rapid-charts-panel';
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
          const existing = parent.querySelector('.rapid-charts-panel');
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
      let activeIndexRows = [];
      let activeEstimateRows = [];
      let activeFunctionScoreControls = [];
      let activeFunctionOrder = [];
      let activeVisibleIndicators = [];

      const getShowAdvancedScoring = () => Boolean(viewOptions.showAdvancedScoring);
      const getShowRollupComputations = () =>
        Boolean(viewOptions.showRollupComputations);
      const getShowFunctionMappings = () =>
        Boolean(viewOptions.showFunctionMappings);
      const getShowSuggestedFunctionScoresCue = () =>
        Boolean(viewOptions.showSuggestedFunctionScoresCue);
      const getShowFunctionScoreCueLabels = () =>
        Boolean(viewOptions.showFunctionScoreCueLabels);
      const getShowCondensedView = () => true;
      const getIndicatorIndexRange = (indicatorId) => {
        const scoreKey = indicatorScores.get(indicatorId) || defaultIndicatorScore;
        const ranged = indicatorIndexRangeByScore[scoreKey];
        if (
          ranged &&
          Number.isFinite(ranged.min) &&
          Number.isFinite(ranged.max) &&
          Number.isFinite(ranged.avg)
        ) {
          return ranged;
        }
        const index = indicatorIndexByScore[scoreKey];
        if (!Number.isFinite(index)) {
          return null;
        }
        return { min: index, max: index, avg: index, hasRange: false };
      };
      const getIndicatorIndexScore = (indicatorId) => {
        const range = getIndicatorIndexRange(indicatorId);
        return range ? range.avg : null;
      };
      const getFunctionEstimateMeta = (indicatorId) => {
        const range = getIndicatorIndexRange(indicatorId);
        if (!range) {
          return null;
        }
        const minScore = Math.ceil(range.min * 15);
        const maxScore = Math.floor(range.max * 15);
        const avgScore = Math.round(range.avg * 15);
        return {
          indexRange: range,
          scoreRange: {
            minScore,
            maxScore,
            avgScore,
            hasRange: Boolean(range.hasRange) && minScore < maxScore,
          },
        };
      };
      const getRapidFunctionScorePalette = (score) => {
        if (score >= 11) {
          return { base: '#a7c7f2', active: '#7faee9' };
        }
        if (score >= 6) {
          return { base: '#f7e088', active: '#f0cd52' };
        }
        return { base: '#ef9a9a', active: '#e07070' };
      };
      const updateRapidFunctionScoreVisual = (rangeInput, scoreValue) => {
        if (!rangeInput) {
          return;
        }
        const min = Number(rangeInput.min) || 0;
        const max = Number(rangeInput.max) || 15;
        const safeValue = Number.isFinite(scoreValue) ? scoreValue : min;
        const clamped = Math.min(max, Math.max(min, safeValue));
        const palette = getRapidFunctionScorePalette(clamped);
        const percent = max > min ? ((clamped - min) / (max - min)) * 100 : 0;
        rangeInput.style.setProperty('--screening-score-pct', `${percent}%`);
        rangeInput.style.setProperty('--screening-score-color', palette.base);
        rangeInput.style.setProperty('--screening-score-color-active', palette.active);
      };

      const updateRapidSuggestedBracket = (
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

      const syncRapidFunctionScoreCellVisual = (cell, functionName) => {
        if (!cell || !functionName) {
          return;
        }
        const rawValue = functionScores.get(functionName);
        const scoreValue = Number.isFinite(rawValue)
          ? Math.min(15, Math.max(0, rawValue))
          : defaultFunctionScore;
        if (scoreValue !== rawValue) {
          functionScores.set(functionName, scoreValue);
        }
        const scoreValueText = String(scoreValue);
        const rangeInput = cell.querySelector('input[type="range"]');
        if (rangeInput) {
          if (rangeInput.value !== scoreValueText) {
            rangeInput.value = scoreValueText;
          }
          if (rangeInput.getAttribute('value') !== scoreValueText) {
            rangeInput.setAttribute('value', scoreValueText);
          }
          updateRapidFunctionScoreVisual(rangeInput, scoreValue);
        }
        const valueEl = cell.querySelector('.score-value');
        if (valueEl) {
          valueEl.textContent = scoreValueText;
        }
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

      const renderCharts = (functionOrder, scoresByFunction, summaryValues) => {
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
            const score = scoresByFunction.get(fn.functionName);
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
          '<th class="col-metric-score col-indicator-score">Metric<br>score</th>' +
          '<th class="col-scoring-criteria">Scoring<br>criteria</th>' +
          '<th class="col-index-score">Metric<br>Index</th>' +
          '<th class="col-function-estimate">Function<br>Estimate</th>' +
          '<th class="col-function-score">Function<br>Score</th>' +
          mappingHeaders +
          '</tr>';
      };

      const getCriteriaDetailsColSpan = (
        showAdvanced,
        showCondensed,
        showMappings
      ) => {
        const baseSpan = (showAdvanced ? 8 : 5) + (showCondensed ? 1 : 0);
        return showMappings ? baseSpan : Math.max(1, baseSpan - 3);
      };

      const applyRollupRowsVisibility = () => {
        const showRollup = getShowRollupComputations();
        if (table) {
          table
            .querySelectorAll('tr[data-rollup-row="true"]')
            .forEach((row) => {
              row.hidden = !showRollup;
            });
        }
        if (summaryTable) {
          summaryTable
            .querySelectorAll('tr[data-rollup-row="true"]')
            .forEach((row) => {
              row.hidden = !showRollup;
            });
        }
      };

      const updateCriteriaDetailsColSpans = (
        showAdvanced,
        showCondensed,
        showMappings
      ) => {
        const detailsSpan = getCriteriaDetailsColSpan(
          showAdvanced,
          showCondensed,
          showMappings
        );
        tbody.querySelectorAll('tr.criteria-row > td').forEach((cell) => {
          cell.colSpan = detailsSpan;
        });
      };

      const syncRapidViewState = () => {
        const showAdvanced = getShowAdvancedScoring();
        const showCondensed = getShowCondensedView();
        const showMappings = getShowFunctionMappings();
        const showSuggestedCue = getShowSuggestedFunctionScoresCue();
        const showSliderLabels = getShowFunctionScoreCueLabels();
        advancedToggle.checked = showAdvanced;
        mappingToggle.checked = showMappings;
        rollupToggle.checked = getShowRollupComputations();
        suggestedCueToggle.checked = showSuggestedCue;
        sliderLabelsToggle.checked = showSliderLabels;
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
        return { showAdvanced, showCondensed, showMappings };
      };

      const updateRapidToggleView = ({
        refreshHeader = false,
        refreshSummary = false,
        refreshCriteriaColSpans = false,
        refreshRollupRows = false,
        refreshScores = false,
      } = {}) => {
        const { showAdvanced, showCondensed, showMappings } = syncRapidViewState();
        if (refreshHeader) {
          renderHeader(showMappings, showAdvanced);
        }
        if (refreshSummary) {
          buildSummary(showAdvanced, showCondensed, showMappings);
        }
        if (refreshCriteriaColSpans) {
          updateCriteriaDetailsColSpans(showAdvanced, showCondensed, showMappings);
        }
        if (refreshRollupRows) {
          applyRollupRowsVisibility();
        }
        if (refreshScores) {
          updateScores();
        }
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
        summaryHead.innerHTML = '';
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
          if (item.rollup) {
            row.dataset.rollupRow = 'true';
            if (!getShowRollupComputations()) {
              row.hidden = true;
            }
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

      advancedToggle.addEventListener('change', () => {
        viewOptions.showAdvancedScoring = advancedToggle.checked;
        updateRapidToggleView({
          refreshHeader: true,
          refreshSummary: true,
          refreshCriteriaColSpans: true,
          refreshScores: true,
        });
      });
      mappingToggle.addEventListener('change', () => {
        viewOptions.showFunctionMappings = mappingToggle.checked;
        updateRapidToggleView({
          refreshHeader: true,
          refreshSummary: true,
          refreshCriteriaColSpans: true,
          refreshScores: true,
        });
      });
      rollupToggle.addEventListener('change', () => {
        viewOptions.showRollupComputations = rollupToggle.checked;
        updateRapidToggleView({ refreshRollupRows: true });
      });
      suggestedCueToggle.addEventListener('change', () => {
        viewOptions.showSuggestedFunctionScoresCue = suggestedCueToggle.checked;
        updateRapidToggleView({ refreshScores: true });
      });
      sliderLabelsToggle.addEventListener('change', () => {
        viewOptions.showFunctionScoreCueLabels = sliderLabelsToggle.checked;
        updateRapidToggleView();
      });

      const updateScores = () => {
        if (Array.isArray(activeIndexRows)) {
          activeIndexRows.forEach(({ indicatorId, cell }) => {
            if (!cell) {
              return;
            }
            const value = getIndicatorIndexScore(indicatorId);
            cell.textContent = value === null ? '-' : value.toFixed(2);
          });
        }
        if (Array.isArray(activeEstimateRows)) {
          activeEstimateRows.forEach(({ indicatorId, cell }) => {
            if (!cell) {
              return;
            }
            const meta = getFunctionEstimateMeta(indicatorId);
            if (!meta) {
              cell.textContent = '-';
              return;
            }
            const { minScore, maxScore, avgScore, hasRange } = meta.scoreRange;
            cell.textContent = hasRange
              ? `${avgScore} (${minScore}-${maxScore})`
              : String(avgScore);
          });
        }

        const functionEstimateBuckets = new Map();
        const functionEstimateRangeBuckets = new Map();
        indicators.forEach((item) => {
          const estimateMeta = getFunctionEstimateMeta(item.id);
          if (!estimateMeta) {
            return;
          }
          const estimate = estimateMeta.scoreRange.avgScore;
          if (!functionEstimateBuckets.has(item.functionName)) {
            functionEstimateBuckets.set(item.functionName, []);
          }
          functionEstimateBuckets.get(item.functionName).push(estimate);
          if (!functionEstimateRangeBuckets.has(item.functionName)) {
            functionEstimateRangeBuckets.set(item.functionName, []);
          }
          functionEstimateRangeBuckets
            .get(item.functionName)
            .push(estimateMeta.scoreRange);
        });

        const functionMeta = new Map();
        functionScores.forEach((rawScore, functionName) => {
          const value = Number.isFinite(rawScore)
            ? Math.min(15, Math.max(0, rawScore))
            : defaultFunctionScore;
          if (value !== rawScore) {
            functionScores.set(functionName, value);
          }
          const estimates = functionEstimateBuckets.get(functionName) || [];
          const ranges = functionEstimateRangeBuckets.get(functionName) || [];
          const minLimit = ranges.length
            ? Math.min(...ranges.map((range) => range.minScore))
            : estimates.length
            ? Math.round(Math.min(...estimates))
            : 0;
          const maxLimit = ranges.length
            ? Math.max(...ranges.map((range) => range.maxScore))
            : estimates.length
            ? Math.round(Math.max(...estimates))
            : 15;
          const hasSuggestedRange =
            ranges.some((range) => range.hasRange) && minLimit < maxLimit;
          const isOutsideSuggestedRange =
            hasSuggestedRange && (value < minLimit || value > maxLimit);
          functionMeta.set(functionName, {
            value,
            minLimit,
            maxLimit,
            hasSuggestedRange,
            isOutsideSuggestedRange,
          });
        });

        const showSuggestedCue = getShowSuggestedFunctionScoresCue();
        if (Array.isArray(activeFunctionScoreControls)) {
          activeFunctionScoreControls.forEach(
            ({
              functionName,
              rangeInput,
              valueEl,
              suggestedBracketEl,
              interactionState,
            }) => {
              if (!rangeInput || !functionName) {
                return;
              }
              const meta = functionMeta.get(functionName) || {
                value: defaultFunctionScore,
                minLimit: 0,
                maxLimit: 15,
                hasSuggestedRange: false,
                isOutsideSuggestedRange: false,
              };
              const nextValue = String(meta.value);
              if (rangeInput.min !== '0') {
                rangeInput.min = '0';
              }
              if (rangeInput.max !== '15') {
                rangeInput.max = '15';
              }
              if (rangeInput.value !== nextValue) {
                rangeInput.value = nextValue;
              }
              if (rangeInput.getAttribute('value') !== nextValue) {
                rangeInput.setAttribute('value', nextValue);
              }
              updateRapidFunctionScoreVisual(rangeInput, meta.value);
              updateRapidSuggestedBracket(
                suggestedBracketEl,
                meta.minLimit,
                meta.maxLimit,
                meta.hasSuggestedRange
              );
              if (valueEl) {
                valueEl.textContent = String(meta.value);
                const interactionActive = Boolean(
                  interactionState && interactionState.active
                );
                const showOutOfRangeCue = Boolean(
                  meta.isOutsideSuggestedRange &&
                    (showSuggestedCue || interactionActive)
                );
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
          const normalizedScore = Number.isFinite(score)
            ? Math.min(15, Math.max(0, score))
            : defaultFunctionScore;

          const applyWeight = (key, code) => {
            let weight = 0;
            if (code === 'D') {
              weight = 1.0;
              outcomeTotals[key].direct += 1;
            } else if (code === 'i') {
              weight = 0.1;
              outcomeTotals[key].indirect += 1;
            }
            outcomeTotals[key].weighted += normalizedScore * weight;
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

        renderCharts(activeFunctionOrder, functionScores, {
          physical: physicalIndex,
          chemical: chemicalIndex,
          biological: biologicalIndex,
          ecosystem: ecosystemIndex,
        });
      };

      const buildRapidCurveSummaryTable = (criteriaSet) => {
        const table = document.createElement('table');
        table.className = 'curve-summary-table';

        const sentimentByScore = {
          SA: '++',
          A: '+',
          N: null,
          D: '-',
          SD: '--',
        };
        const scoreOptions = indicatorScoreOptions.filter(
          (option) => option.value !== 'NA'
        );

        const valueRow = document.createElement('tr');
        const valueLabel = document.createElement('th');
        valueLabel.textContent = 'Value';
        valueRow.appendChild(valueLabel);

        scoreOptions.forEach((option) => {
          const cell = document.createElement('td');
          const value = document.createElement('div');
          value.className = 'curve-point-value';
          value.textContent = option.label || option.value;
          cell.appendChild(value);
          const sentimentKey = sentimentByScore[option.value];
          if (sentimentKey && criteriaSet?.[sentimentKey]) {
            const criteriaEntry = criteriaSet[sentimentKey];
            const parts = [];
            if (criteriaEntry.observation) {
              parts.push(`Observation: ${criteriaEntry.observation}`);
            }
            if (criteriaEntry.criteria) {
              parts.push(`Criteria: ${criteriaEntry.criteria}`);
            }
            if (parts.length) {
              const desc = document.createElement('div');
              desc.className = 'curve-point-desc';
              desc.textContent = parts.join(' ');
              cell.appendChild(desc);
            }
          }
          valueRow.appendChild(cell);
        });

        const indexRow = document.createElement('tr');
        const indexLabel = document.createElement('th');
        indexLabel.textContent = 'Index';
        indexRow.appendChild(indexLabel);
        scoreOptions.forEach((option) => {
          const cell = document.createElement('td');
          const range = indicatorIndexRangeByScore[option.value];
          if (
            range &&
            Number.isFinite(range.min) &&
            Number.isFinite(range.max)
          ) {
            cell.textContent = `${range.min.toFixed(2)}-${range.max.toFixed(2)}`;
          } else {
            const value = indicatorIndexByScore[option.value];
            cell.textContent = Number.isFinite(value) ? value.toFixed(2) : '-';
          }
          indexRow.appendChild(cell);
        });

        const functionScoreRow = document.createElement('tr');
        const functionScoreLabel = document.createElement('th');
        functionScoreLabel.textContent = 'Function Score';
        functionScoreRow.appendChild(functionScoreLabel);
        scoreOptions.forEach((option) => {
          const cell = document.createElement('td');
          const range = indicatorIndexRangeByScore[option.value];
          if (
            range &&
            Number.isFinite(range.min) &&
            Number.isFinite(range.max)
          ) {
            const minScore = Math.ceil(range.min * 15);
            const maxScore = Math.floor(range.max * 15);
            cell.textContent = `${minScore}-${maxScore}`;
          } else {
            const value = indicatorIndexByScore[option.value];
            const score = Number.isFinite(value) ? Math.round(value * 15) : null;
            cell.textContent = score === null ? '-' : String(score);
          }
          functionScoreRow.appendChild(cell);
        });

        table.appendChild(valueRow);
        table.appendChild(indexRow);
        table.appendChild(functionScoreRow);
        return table;
      };

      const buildRapidCriteriaDetailsRow = (
        item,
        showAdvanced,
        showCondensed,
        showMappings
      ) => {
        const detailsRow = document.createElement('tr');
        detailsRow.id = `rapid-criteria-${item.id}`;
        detailsRow.className = 'criteria-row';
        detailsRow.dataset.rowType = 'criteria';
        detailsRow.dataset.functionId = item.functionKey;
        detailsRow.dataset.indicatorId = item.id;
        detailsRow.classList.add(slugCategory(item.discipline));
        const detailsCell = document.createElement('td');
        detailsCell.colSpan = getCriteriaDetailsColSpan(
          showAdvanced,
          showCondensed,
          showMappings
        );
        const details = document.createElement('div');
        details.className = 'criteria-details';
        const criteriaSet = criteriaMap[item.criteriaKey] || {};
        const headerRow = document.createElement('div');
        headerRow.className = 'criteria-summary-header';
        const headerLabel = document.createElement('span');
        headerLabel.textContent = 'Scoring Criteria';
        headerRow.appendChild(headerLabel);
        details.appendChild(headerRow);
        details.appendChild(buildRapidCurveSummaryTable(criteriaSet));
        detailsCell.appendChild(details);
        detailsRow.appendChild(detailsCell);
        return detailsRow;
      };

      const appendRapidMappingCells = (row, mapping, rowSpan = 0) => {
        const physicalCell = document.createElement('td');
        const chemicalCell = document.createElement('td');
        const biologicalCell = document.createElement('td');
        physicalCell.className = 'weight-cell col-physical physical-cell';
        chemicalCell.className = 'weight-cell col-chemical chemical-cell';
        biologicalCell.className = 'weight-cell col-biological biological-cell';
        physicalCell.textContent = mapping.physical || '-';
        chemicalCell.textContent = mapping.chemical || '-';
        biologicalCell.textContent = mapping.biological || '-';
        if (rowSpan > 1) {
          physicalCell.rowSpan = rowSpan;
          chemicalCell.rowSpan = rowSpan;
          biologicalCell.rowSpan = rowSpan;
        }
        row.appendChild(physicalCell);
        row.appendChild(chemicalCell);
        row.appendChild(biologicalCell);
      };

      const updateRapidMetricExpansionInPlace = (indicatorId) => {
        const showAdvanced = getShowAdvancedScoring();
        const showCondensed = getShowCondensedView();
        const showMappings = getShowFunctionMappings();
        if (
          !showCondensed ||
          !Array.isArray(activeVisibleIndicators) ||
          !activeVisibleIndicators.length
        ) {
          renderTable({ rebuildHeader: false, rebuildSummary: false });
          return;
        }

        const targetIndex = activeVisibleIndicators.findIndex(
          (item) => item.id === indicatorId
        );
        if (targetIndex === -1) {
          renderTable({ rebuildHeader: false, rebuildSummary: false });
          return;
        }

        const rowByIndicatorId = new Map();
        tbody
          .querySelectorAll('tr[data-row-type="indicator"][data-indicator-id]')
          .forEach((row) => {
            rowByIndicatorId.set(row.dataset.indicatorId, row);
          });
        if (!rowByIndicatorId.size) {
          renderTable({ rebuildHeader: false, rebuildSummary: false });
          return;
        }

        const getFunctionKeyAt = (index) =>
          activeVisibleIndicators[index].functionKey ||
          normalize(activeVisibleIndicators[index].functionName);
        const targetFunctionKey = getFunctionKeyAt(targetIndex);
        let functionStart = targetIndex;
        while (
          functionStart > 0 &&
          getFunctionKeyAt(functionStart - 1) === targetFunctionKey
        ) {
          functionStart -= 1;
        }
        let functionEnd = targetIndex + 1;
        while (
          functionEnd < activeVisibleIndicators.length &&
          getFunctionKeyAt(functionEnd) === targetFunctionKey
        ) {
          functionEnd += 1;
        }

        const functionItems = activeVisibleIndicators.slice(functionStart, functionEnd);
        const functionRows = functionItems.map((item) => rowByIndicatorId.get(item.id));
        if (functionRows.some((row) => !row)) {
          renderTable({ rebuildHeader: false, rebuildSummary: false });
          return;
        }

        const expandedLocalIndices = [];
        functionItems.forEach((item, localIndex) => {
          if (expandedIndicators.has(item.id)) {
            expandedLocalIndices.push(localIndex);
          }
        });

        const functionMetricCount = functionItems.length;
        const functionExpandedCount = expandedLocalIndices.length;
        const functionHasExpandedCriteria = functionExpandedCount > 0;
        const functionSliderOwnerLocalIndex = functionHasExpandedCriteria
          ? expandedLocalIndices[0]
          : 0;

        const functionCell = functionRows[0].querySelector('td.col-function');
        if (functionCell) {
          functionCell.rowSpan = functionMetricCount + functionExpandedCount;
        }

        const functionIndicatorIdSet = new Set(functionItems.map((item) => item.id));
        tbody
          .querySelectorAll('tr[data-row-type="criteria"][data-indicator-id]')
          .forEach((row) => {
            if (functionIndicatorIdSet.has(row.dataset.indicatorId)) {
              row.remove();
            }
          });

        let functionScoreCell = null;
        functionRows.forEach((row) => {
          row.querySelectorAll('td.col-function-score').forEach((cell) => {
            if (!functionScoreCell && cell.querySelector('.function-score-inline')) {
              functionScoreCell = cell;
              return;
            }
            cell.remove();
          });
        });

        if (showCondensed) {
          if (!functionScoreCell) {
            renderTable({ rebuildHeader: false, rebuildSummary: false });
            return;
          }
          syncRapidFunctionScoreCellVisual(
            functionScoreCell,
            functionItems[0].functionName
          );
          functionScoreCell.className = 'col-function-score function-score-cell';
          functionScoreCell.rowSpan = functionHasExpandedCriteria ? 1 : functionMetricCount;
          const ownerRow = functionRows[functionSliderOwnerLocalIndex];
          if (ownerRow && functionScoreCell.parentElement !== ownerRow) {
            ownerRow.appendChild(functionScoreCell);
          }
          syncRapidFunctionScoreCellVisual(
            functionScoreCell,
            functionItems[0].functionName
          );
          functionRows.forEach((row, localIndex) => {
            if (localIndex !== functionSliderOwnerLocalIndex) {
              const placeholderScoreCell = document.createElement('td');
              if (functionHasExpandedCriteria) {
                placeholderScoreCell.className =
                  'col-function-score function-score-cell function-score-cell-empty';
                row.appendChild(placeholderScoreCell);
              }
            }
          });
        }

        functionRows.forEach((row) => {
          row
            .querySelectorAll('td.col-physical, td.col-chemical, td.col-biological')
            .forEach((cell) => {
              cell.remove();
            });
        });

        const mapping =
          mappingByFunction[functionItems[0].functionKey] || {
            physical: '-',
            chemical: '-',
            biological: '-',
          };
        const mergeMappingCells =
          functionExpandedCount === 0 && functionMetricCount > 1;
        functionRows.forEach((row, localIndex) => {
          if (!mergeMappingCells || localIndex === 0) {
            appendRapidMappingCells(
              row,
              mapping,
              mergeMappingCells ? functionMetricCount : 0
            );
          }
        });

        functionRows.forEach((row, localIndex) => {
          const item = functionItems[localIndex];
          const isExpanded = expandedIndicators.has(item.id);
          const indicatorToggle = row.querySelector('.indicator-cell .criteria-toggle');
          if (indicatorToggle) {
            indicatorToggle.innerHTML = isExpanded ? expandedGlyph : collapsedGlyph;
            indicatorToggle.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
          }
          if (isExpanded) {
            row.insertAdjacentElement(
              'afterend',
              buildRapidCriteriaDetailsRow(
                item,
                showAdvanced,
                showCondensed,
                showMappings
              )
            );
          }
        });

        const targetDiscipline = activeVisibleIndicators[targetIndex].discipline;
        let disciplineStart = targetIndex;
        while (
          disciplineStart > 0 &&
          activeVisibleIndicators[disciplineStart - 1].discipline === targetDiscipline
        ) {
          disciplineStart -= 1;
        }
        let disciplineEnd = targetIndex + 1;
        while (
          disciplineEnd < activeVisibleIndicators.length &&
          activeVisibleIndicators[disciplineEnd].discipline === targetDiscipline
        ) {
          disciplineEnd += 1;
        }

        let disciplineExpandedCount = 0;
        for (let i = disciplineStart; i < disciplineEnd; i += 1) {
          if (expandedIndicators.has(activeVisibleIndicators[i].id)) {
            disciplineExpandedCount += 1;
          }
        }
        const disciplineFirstRow = rowByIndicatorId.get(
          activeVisibleIndicators[disciplineStart].id
        );
        if (disciplineFirstRow) {
          const disciplineCell = disciplineFirstRow.querySelector('td.col-discipline');
          if (disciplineCell) {
            disciplineCell.rowSpan =
              disciplineEnd - disciplineStart + disciplineExpandedCount;
          }
        }

      };

      const renderTable = ({ rebuildHeader = true, rebuildSummary = true } = {}) => {
        const nextTbody = document.createElement('tbody');
        const { showAdvanced, showCondensed, showMappings } = syncRapidViewState();
        if (rebuildHeader) {
          renderHeader(showMappings, showAdvanced);
        }
        if (rebuildSummary) {
          buildSummary(showAdvanced, showCondensed, showMappings);
        }

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
        activeVisibleIndicators = visibleIndicators.slice();

        const seenFunctions = new Set();
        activeFunctionOrder = [];
        visibleIndicators.forEach((item) => {
          const functionName = item.functionName || '';
          if (!functionName || seenFunctions.has(functionName)) {
            return;
          }
          seenFunctions.add(functionName);
          activeFunctionOrder.push({
            functionName,
            name: functionName,
            discipline: item.discipline || '',
          });
        });

        const disciplineRowMeta = new Array(visibleIndicators.length);
        const functionRowMeta = new Array(visibleIndicators.length);

        for (let i = 0; i < visibleIndicators.length; ) {
          const discipline = visibleIndicators[i].discipline;
          let j = i + 1;
          while (j < visibleIndicators.length && visibleIndicators[j].discipline === discipline) {
            j += 1;
          }
          let expandedCount = 0;
          for (let k = i; k < j; k += 1) {
            if (expandedIndicators.has(visibleIndicators[k].id)) {
              expandedCount += 1;
            }
          }
          const metricsCount = j - i;
          const meta = {
            startIndex: i,
            metricsCount,
            expandedCount,
            totalSpan: metricsCount + expandedCount,
          };
          for (let k = i; k < j; k += 1) {
            disciplineRowMeta[k] = meta;
          }
          i = j;
        }

        for (let i = 0; i < visibleIndicators.length; ) {
          const fnKey =
            visibleIndicators[i].functionKey ||
            normalize(visibleIndicators[i].functionName);
          let j = i + 1;
          while (
            j < visibleIndicators.length &&
            (visibleIndicators[j].functionKey ||
              normalize(visibleIndicators[j].functionName)) === fnKey
          ) {
            j += 1;
          }
          let expandedCount = 0;
          let firstExpandedIndex = -1;
          for (let k = i; k < j; k += 1) {
            if (expandedIndicators.has(visibleIndicators[k].id)) {
              expandedCount += 1;
              if (firstExpandedIndex === -1) {
                firstExpandedIndex = k;
              }
            }
          }
          const metricsCount = j - i;
          const meta = {
            startIndex: i,
            metricsCount,
            expandedCount,
            totalSpan: metricsCount + expandedCount,
            hasExpandedCriteria: expandedCount > 0,
            firstExpandedIndex,
          };
          for (let k = i; k < j; k += 1) {
            functionRowMeta[k] = meta;
          }
          i = j;
        }

        activeIndexRows = [];
        activeEstimateRows = [];
        activeFunctionScoreControls = [];

        if (!visibleIndicators.length) {
          const emptyRow = document.createElement('tr');
          const emptyCell = document.createElement('td');
          const baseColumns = 7 + (showAdvanced ? 3 : 0) + (showCondensed ? 1 : 0);
          const totalColumns = showMappings ? baseColumns : Math.max(1, baseColumns - 3);
          emptyCell.colSpan = totalColumns;
          emptyCell.className = 'empty-cell';
          emptyCell.textContent = 'No indicators match the current filters.';
          emptyRow.appendChild(emptyCell);
          nextTbody.appendChild(emptyRow);
          activeFunctionOrder = [];
          activeVisibleIndicators = [];
          updateScores();
          table.replaceChild(nextTbody, tbody);
          tbody = nextTbody;
          return;
        }

        visibleIndicators.forEach((item, index) => {
          const row = document.createElement('tr');
          row.classList.add(slugCategory(item.discipline));
          row.dataset.rowType = 'indicator';
          row.dataset.functionId = item.functionKey;
          row.dataset.indicatorId = item.id;

          const rowDisciplineMeta = disciplineRowMeta[index] || {
            startIndex: index,
            totalSpan: 1,
          };
          const isDisciplineStart = rowDisciplineMeta.startIndex === index;
          if (isDisciplineStart) {
            const disciplineCell = document.createElement('td');
            const disciplineLink = document.createElement('button');
            disciplineLink.type = 'button';
            disciplineLink.className = 'metric-curve-link';
            disciplineLink.textContent = item.discipline;
            disciplineLink.addEventListener('click', () => {
              openMetricLibraryWithFilters(item.discipline, 'all');
            });
            disciplineCell.appendChild(disciplineLink);
            disciplineCell.className = 'discipline-cell col-discipline';
            disciplineCell.rowSpan = rowDisciplineMeta.totalSpan;
            row.appendChild(disciplineCell);
          }

          const rowFunctionMeta = functionRowMeta[index] || {
            startIndex: index,
            metricsCount: 1,
            expandedCount: 0,
            totalSpan: 1,
            hasExpandedCriteria: false,
          };
          const isFunctionStart = rowFunctionMeta.startIndex === index;
          const functionMetricCount = rowFunctionMeta.metricsCount || 1;
          const functionExpandedCount = rowFunctionMeta.expandedCount || 0;
          const functionHasExpandedCriteria = Boolean(
            rowFunctionMeta.hasExpandedCriteria
          );
          const functionTotalSpan = rowFunctionMeta.totalSpan || functionMetricCount;
          const mergeMappingCells =
            functionExpandedCount === 0 && functionMetricCount > 1;
          const isMappingOwner = rowFunctionMeta.startIndex === index;
          const functionSliderOwnerIndex = functionHasExpandedCriteria
            ? rowFunctionMeta.firstExpandedIndex
            : rowFunctionMeta.startIndex;
          const isFunctionSliderOwner = functionSliderOwnerIndex === index;
          if (isFunctionStart) {
            const functionCell = document.createElement('td');
            functionCell.className = 'function-cell col-function';
            functionCell.rowSpan = functionTotalSpan;
            const nameLine = document.createElement('div');
            nameLine.className = 'function-title';
            const nameText = document.createElement('span');
            nameText.className = 'metric-curve-link';
            nameText.setAttribute('role', 'button');
            nameText.tabIndex = 0;
            nameText.appendChild(document.createTextNode(item.functionName));
            const openFunctionLibrary = () => {
              openMetricLibraryWithFilters(item.discipline, item.functionName);
            };
            nameText.addEventListener('click', openFunctionLibrary);
            nameText.addEventListener('keydown', (event) => {
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
            nameText.appendChild(document.createTextNode('\u00A0'));
            nameText.appendChild(functionToggle);
            nameLine.appendChild(nameText);
            functionCell.appendChild(nameLine);

            const statementLine = document.createElement('div');
            statementLine.className = 'function-statement';
            statementLine.textContent = item.functionStatement || '';
            statementLine.hidden = true;
            functionCell.appendChild(statementLine);
            row.appendChild(functionCell);

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
          }

          const indicatorCell = document.createElement('td');
          indicatorCell.className = 'col-metric indicator-cell';
          const metricTitle = document.createElement('span');
          metricTitle.className = 'metric-title';
          const indicatorText = document.createElement('span');
          indicatorText.className = 'metric-curve-link';
          indicatorText.setAttribute('role', 'button');
          indicatorText.tabIndex = 0;
          indicatorText.appendChild(document.createTextNode(item.indicator));
          const openMetricInspector = () => {
            openMetricInspectorForIndicator(item);
          };
          indicatorText.addEventListener('click', openMetricInspector);
          indicatorText.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              openMetricInspector();
            }
          });
          metricTitle.appendChild(indicatorText);
          indicatorCell.appendChild(metricTitle);

          const indicatorScoreCell = document.createElement('td');
          indicatorScoreCell.className = 'col-metric-score col-indicator-score';
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
            indicatorSelect.setAttribute(
              'title',
              indicatorScoreOptions.find((opt) => opt.value === indicatorSelect.value)
                ?.title || ''
            );
            updateScores();
          });
          indicatorScoreCell.appendChild(indicatorSelect);

          const criteriaBtn = document.createElement('button');
          criteriaBtn.type = 'button';
          criteriaBtn.className = 'criteria-toggle';
          const criteriaExpanded = expandedIndicators.has(item.id);
          criteriaBtn.innerHTML = criteriaExpanded ? expandedGlyph : collapsedGlyph;
          const detailsId = `rapid-criteria-${item.id}`;
          criteriaBtn.setAttribute(
            'aria-expanded',
            criteriaExpanded ? 'true' : 'false'
          );
          criteriaBtn.setAttribute('aria-controls', detailsId);
          criteriaBtn.setAttribute('aria-label', 'Toggle criteria details');
          criteriaBtn.addEventListener('mousedown', (event) => {
            if (event.detail > 0) {
              event.preventDefault();
            }
          });
          indicatorText.appendChild(document.createTextNode('\u00A0'));
          indicatorText.appendChild(criteriaBtn);

          row.appendChild(indicatorCell);
          row.appendChild(indicatorScoreCell);
          const criteriaCell = document.createElement('td');
          criteriaCell.className = 'col-scoring-criteria';
          criteriaCell.textContent = 'SFARI';
          row.appendChild(criteriaCell);
          const indexCell = document.createElement('td');
          indexCell.className = 'col-index-score';
          row.appendChild(indexCell);
          activeIndexRows.push({ indicatorId: item.id, cell: indexCell });
          const estimateCell = document.createElement('td');
          estimateCell.className = 'col-function-estimate';
          row.appendChild(estimateCell);
          activeEstimateRows.push({ indicatorId: item.id, cell: estimateCell });
          if (showCondensed && isFunctionSliderOwner) {
            const functionScoreCell = document.createElement('td');
            functionScoreCell.className = 'col-function-score function-score-cell';
            functionScoreCell.rowSpan = functionHasExpandedCriteria
              ? 1
              : functionMetricCount;
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
            cueBar.setAttribute('aria-label', 'Set function score by range');
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
            suggestedBracket.hidden = true;
            suggestedBracketLayer.appendChild(suggestedBracket);
            const range = document.createElement('input');
            range.type = 'range';
            range.min = '0';
            range.max = '15';
            range.step = '1';
            const currentScore =
              functionScores.get(item.functionName) ?? defaultFunctionScore;
            range.value = String(currentScore);
            range.setAttribute('value', String(currentScore));
            updateRapidFunctionScoreVisual(range, currentScore);
            const interactionState = { active: false };
            const setDraggingState = (isDragging) => {
              interactionState.active = Boolean(isDragging);
              sliderWrap.classList.toggle('is-dragging', interactionState.active);
            };
            let cueHighlightTimer = null;
            const setScoreFromCueBar = (event) => {
              if (!range || range.disabled) {
                return;
              }
              const rect = cueBar.getBoundingClientRect();
              if (!rect.width) {
                return;
              }
              const min = Number(range.min) || 0;
              const max = Number(range.max) || 15;
              const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
              const nextValue = Math.round(min + (x / rect.width) * (max - min));
              setDraggingState(true);
              range.value = String(nextValue);
              range.dispatchEvent(new Event('input', { bubbles: true }));
              if (cueHighlightTimer) {
                clearTimeout(cueHighlightTimer);
              }
              cueHighlightTimer = setTimeout(() => {
                setDraggingState(false);
                updateScores();
                cueHighlightTimer = null;
              }, 180);
            };
            range.addEventListener('pointerdown', () => {
              if (cueHighlightTimer) {
                clearTimeout(cueHighlightTimer);
                cueHighlightTimer = null;
              }
              setDraggingState(true);
            });
            range.addEventListener('pointerup', () => {
              setDraggingState(false);
              updateScores();
            });
            range.addEventListener('pointercancel', () => {
              setDraggingState(false);
              updateScores();
            });
            range.addEventListener('blur', () => {
              setDraggingState(false);
              updateScores();
            });
            cueBar.addEventListener('pointerdown', (event) => {
              event.preventDefault();
              setScoreFromCueBar(event);
            });
            const valueLabel = document.createElement('span');
            valueLabel.className = 'score-value';
            valueLabel.textContent = String(currentScore);
            range.addEventListener('input', () => {
              const nextValue = Number(range.value);
              functionScores.set(item.functionName, nextValue);
              range.setAttribute('value', String(nextValue));
              valueLabel.textContent = String(nextValue);
              updateRapidFunctionScoreVisual(range, nextValue);
              updateScores();
            });
            sliderWrap.appendChild(cueLabels);
            sliderWrap.appendChild(cueBar);
            sliderWrap.appendChild(suggestedBracketLayer);
            sliderWrap.appendChild(range);
            scoreWrap.appendChild(sliderWrap);
            scoreWrap.appendChild(valueLabel);
            functionScoreCell.appendChild(scoreWrap);
            row.appendChild(functionScoreCell);
            activeFunctionScoreControls.push({
              functionName: item.functionName,
              rangeInput: range,
              valueEl: valueLabel,
              suggestedBracketEl: suggestedBracket,
              interactionState,
            });
          } else if (showCondensed && functionHasExpandedCriteria) {
            const placeholderScoreCell = document.createElement('td');
            placeholderScoreCell.className =
              'col-function-score function-score-cell function-score-cell-empty';
            row.appendChild(placeholderScoreCell);
          }

          const mapping =
            mappingByFunction[item.functionKey] || {
              physical: '-',
              chemical: '-',
              biological: '-',
            };
          if (!mergeMappingCells || isMappingOwner) {
            appendRapidMappingCells(
              row,
              mapping,
              mergeMappingCells ? functionMetricCount : 0
            );
          }

          criteriaBtn.addEventListener('click', (event) => {
            event.stopPropagation();
            if (expandedIndicators.has(item.id)) {
              expandedIndicators.delete(item.id);
            } else {
              expandedIndicators.add(item.id);
            }
            updateRapidMetricExpansionInPlace(item.id);
            if (event.detail > 0) {
              setTimeout(() => criteriaBtn.blur(), 0);
            }
          });

          nextTbody.appendChild(row);
          if (criteriaExpanded) {
            nextTbody.appendChild(
              buildRapidCriteriaDetailsRow(
                item,
                showAdvanced,
                showCondensed,
                showMappings
              )
            );
          }
        });

        updateScores();
        table.replaceChild(nextTbody, tbody);
        tbody = nextTbody;
      };

      const addMetricFromLibrary = ({ detail }) => {
        if (isReadOnlyAssessment) {
          return;
        }
        const indicator = ensureLibraryIndicator(detail);
        if (!indicator) {
          return;
        }
        indicatorScores.set(indicator.id, defaultIndicatorScore);
        if (!functionScores.has(indicator.functionName)) {
          functionScores.set(indicator.functionName, defaultFunctionScore);
        }
        if (disciplineFilter && indicator.discipline) {
          const exists = Array.from(disciplineFilter.options).some(
            (option) => option.value === indicator.discipline
          );
          if (!exists) {
            const option = document.createElement('option');
            option.value = indicator.discipline;
            option.textContent = indicator.discipline;
            disciplineFilter.appendChild(option);
          }
        }
        renderTable();
      };

      const removeMetricFromLibrary = ({ metricId }) => {
        if (isReadOnlyAssessment) {
          return;
        }
        if (!metricId || !indicatorIdSet.has(metricId)) {
          return;
        }
        const index = indicators.findIndex((item) => item.id === metricId);
        if (index >= 0) {
          indicators.splice(index, 1);
        }
        indicatorById.delete(metricId);
        indicatorIdSet.delete(metricId);
        indicatorScores.delete(metricId);
        renderTable();
      };

      const isMetricAdded = (metricId) => indicatorIdSet.has(metricId);

      search.addEventListener('input', renderTable);
      disciplineFilter.addEventListener('change', renderTable);

      if (window.STAFAssessmentRegistry) {
        window.STAFAssessmentRegistry.register('rapid', {
          addMetric: addMetricFromLibrary,
          removeMetric: removeMetricFromLibrary,
          isMetricAdded,
          isReadOnly: () => isReadOnlyAssessment,
          refresh: renderTable,
        });
      }

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
