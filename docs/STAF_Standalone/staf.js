
/* STAF standalone logic: ports the Blazor STAF component to vanilla JS. */
(() => {
    'use strict';

    const CATEGORY_VALUES = [
        'Hydrology',
        'Hydraulics',
        'Geomorphology',
        'Physicochemical',
        'Biology'
    ];

    const VARIABLE_TYPES = [
        'CatchmentHydrology',
        'SurfaceWaterStorage',
        'ReachInflow',
        'FlowDuration',
        'FlowAlteration',
        'LowFlowDynamics',
        'BaseflowDynamics',
        'HighFlowDynamics',
        'FloodplainConnectivity',
        'HyporheicConnectivity',
        'ChannelEvolution',
        'LateralStability',
        'PlanformChange',
        'SedimentContinuity',
        'LargeWood',
        'BedComposition',
        'LightAndThermalRegime',
        'CarbonProcessing',
        'NutrientCycling',
        'WaterAndSoilQuality',
        'HabitatProvision',
        'PopulationSupport',
        'CommunityDynamics',
        'WatershedConnectivity'
    ];

    const VARIABLE_TYPE_LOOKUP = Object.create(null);
    VARIABLE_TYPES.forEach((value) => {
        VARIABLE_TYPE_LOOKUP[normalizeEnumName(value)] = value;
    });

    const state = {
        isDataLoaded: false,
        activeTab: 'variables',
        streamModelName: '',
        streamModelFunctions: [],
        toolboxMetrics: [],
        functionalCategoryItems: [],
        functionalVariableSelectableOptions: [],
        allPossibleMetricOptions: [],
        tableItems: [],
        filteredVariableSelectableOptionsForDialog: [],
        filteredMetricOptionsForDialog: [],
        selectedCategoryItem: null,
        selectedVariable: null,
        selectedMetric: null,
        lookupTableItems: [],
        originalScore: null,
        metricDialog: {
            search: '',
            filters: {
                assessment: '',
                metricId: '',
                metricName: '',
                performanceStandard: '',
                method: '',
                tier: ''
            },
            pageSize: 25,
            page: 1
        },
        variableDialogOpen: false,
        metricDialogOpen: false,
        scoreDialogOpen: false
    };

    const dom = {};
    let metricSearchTimer = null;

    document.addEventListener('DOMContentLoaded', init);

    function init() {
        cacheDom();
        bindEvents();
        loadData();
    }

    function cacheDom() {
        dom.loading = document.getElementById('loading');
        dom.appContent = document.getElementById('app-content');
        dom.modelNameInput = document.getElementById('model-name-input');
        dom.exportMenuButton = document.getElementById('export-menu-button');
        dom.exportMenu = document.getElementById('export-menu');
        dom.importMenuButton = document.getElementById('import-menu-button');
        dom.importMenu = document.getElementById('import-menu');
        dom.importFileInput = document.getElementById('import-file-input');
        dom.tabButtons = Array.from(document.querySelectorAll('.tab-button'));
        dom.tabPanels = {
            variables: document.getElementById('tab-variables'),
            metrics: document.getElementById('tab-metrics'),
            scoring: document.getElementById('tab-scoring')
        };
        dom.variablesTableBody = document.getElementById('variables-table-body');
        dom.variablesTableFoot = document.getElementById('variables-table-foot');
        dom.metricsTableBody = document.getElementById('metrics-table-body');
        dom.scoringTableBody = document.getElementById('scoring-table-body');
        dom.scoringTableFoot = document.getElementById('scoring-table-foot');

        dom.variableDialog = document.getElementById('variable-dialog');
        dom.variableDialogTitle = document.getElementById('variable-dialog-title');
        dom.variableDialogBody = document.getElementById('variable-dialog-body');

        dom.metricDialog = document.getElementById('metric-dialog');
        dom.metricDialogTitle = document.getElementById('metric-dialog-title');
        dom.metricDialogBody = document.getElementById('metric-dialog-body');
        dom.metricSearchInput = document.getElementById('metric-search-input');
        dom.metricClearFilters = document.getElementById('metric-clear-filters');
        dom.metricFilterAssessment = document.getElementById('metric-filter-assessment');
        dom.metricFilterId = document.getElementById('metric-filter-id');
        dom.metricFilterName = document.getElementById('metric-filter-name');
        dom.metricFilterStandard = document.getElementById('metric-filter-standard');
        dom.metricFilterMethod = document.getElementById('metric-filter-method');
        dom.metricFilterTier = document.getElementById('metric-filter-tier');
        dom.metricPageSize = document.getElementById('metric-page-size');
        dom.metricPagePrev = document.getElementById('metric-page-prev');
        dom.metricPageNext = document.getElementById('metric-page-next');
        dom.metricPaginationInfo = document.getElementById('metric-pagination-info');

        dom.scoreDialog = document.getElementById('score-dialog');
        dom.scoreDialogTitle = document.getElementById('score-dialog-title');
        dom.scoreDialogScoreLabel = document.getElementById('score-dialog-score-label');
        dom.lookupTableBody = document.getElementById('lookup-table-body');
        dom.scoreInput = document.getElementById('score-input');
        dom.scoreChart = document.getElementById('score-chart');

        dom.toastContainer = document.getElementById('toast-container');
    }

    function bindEvents() {
        dom.exportMenuButton.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleMenu(dom.exportMenu);
        });

        dom.importMenuButton.addEventListener('click', (event) => {
            event.stopPropagation();
            toggleMenu(dom.importMenu);
        });

        document.addEventListener('click', (event) => {
            const target = event.target;
            if (!dom.exportMenu.contains(target) && target !== dom.exportMenuButton) {
                dom.exportMenu.classList.remove('is-open');
            }
            if (!dom.importMenu.contains(target) && target !== dom.importMenuButton) {
                dom.importMenu.classList.remove('is-open');
            }
        });

        dom.modelNameInput.addEventListener('input', () => {
            state.streamModelName = dom.modelNameInput.value;
        });

        dom.importFileInput.addEventListener('change', handleImportFile);

        dom.tabButtons.forEach((button) => {
            button.addEventListener('click', () => setActiveTab(button.dataset.tab));
        });

        dom.metricSearchInput.addEventListener('input', handleMetricSearchInput);
        dom.metricClearFilters.addEventListener('click', clearMetricFilters);
        dom.metricPageSize.addEventListener('change', handleMetricPageSizeChange);
        dom.metricPagePrev.addEventListener('click', () => changeMetricPage(-1));
        dom.metricPageNext.addEventListener('click', () => changeMetricPage(1));

        [
            dom.metricFilterAssessment,
            dom.metricFilterId,
            dom.metricFilterName,
            dom.metricFilterStandard,
            dom.metricFilterMethod,
            dom.metricFilterTier
        ].forEach((input) => {
            input.addEventListener('input', handleMetricFilterInput);
        });

        dom.scoreInput.addEventListener('input', handleScoreInput);

        document.addEventListener('click', handleActionClick);
        document.addEventListener('change', handleActionChange);
        document.addEventListener('input', handleActionInput);

        window.addEventListener('resize', () => {
            if (state.scoreDialogOpen) {
                drawScoreChart();
            }
        });
    }

    async function loadData() {
        try {
            let streamText = '';
            let metricText = '';

            try {
                [streamText, metricText] = await Promise.all([
                    fetch('data/StreamModelFunctions.csv').then(assertOk),
                    fetch('data/MetricToolbox.csv').then(assertOk)
                ]);
            } catch (fetchError) {
                streamText = '';
                metricText = '';
            }

            if (!streamText || !metricText) {
                streamText = getInlineData('stream-data');
                metricText = getInlineData('metric-data');
            }

            if (!streamText || !metricText) {
                throw new Error('Missing STAF data sources.');
            }

            const streamParsed = parseCsv(streamText);
            const metricParsed = parseCsv(metricText);

            state.streamModelFunctions = streamParsed.records;
            state.toolboxMetrics = metricParsed.records;

            loadFunctionalCategoryItems();
            loadFunctionalVariableOptions();
            loadMetricsFromToolbox();

            state.isDataLoaded = true;
            updateTableItems();
            render();
        } catch (error) {
            console.error(error);
            dom.loading.textContent = 'Failed to load data.';
            showToast('Failed to load data.', 'error');
        }
    }

    function assertOk(response) {
        if (!response.ok) {
            throw new Error(`Failed to load ${response.url}`);
        }
        return response.text();
    }

    function getInlineData(elementId) {
        const element = document.getElementById(elementId);
        return element ? element.textContent.trim() : '';
    }

    function parseCsv(text) {
        const rows = [];
        let row = [];
        let field = '';
        let inQuotes = false;
        const content = text.replace(/^\uFEFF/, '');

        for (let i = 0; i < content.length; i += 1) {
            const char = content[i];

            if (char === '"') {
                if (inQuotes && content[i + 1] === '"') {
                    field += '"';
                    i += 1;
                } else {
                    inQuotes = !inQuotes;
                }
                continue;
            }

            if (char === ',' && !inQuotes) {
                row.push(field);
                field = '';
                continue;
            }

            if ((char === '\n' || char === '\r') && !inQuotes) {
                if (char === '\r' && content[i + 1] === '\n') {
                    i += 1;
                }
                row.push(field);
                field = '';
                if (row.some((cell) => cell.trim() !== '')) {
                    rows.push(row);
                }
                row = [];
                continue;
            }

            field += char;
        }

        row.push(field);
        if (row.some((cell) => cell.trim() !== '')) {
            rows.push(row);
        }

        const headers = rows.shift() || [];
        if (headers.length > 0) {
            headers[0] = headers[0].replace(/^\uFEFF/, '');
        }

        const records = rows.map((values) => {
            const record = {};
            headers.forEach((header, index) => {
                record[header] = values[index] !== undefined ? values[index] : '';
            });
            return record;
        });

        return { headers, records };
    }

    function normalizeEnumName(input) {
        return String(input || '').replace(/\s+/g, '').toLowerCase();
    }

    function parseFunctionalCategory(input) {
        const normalized = normalizeEnumName(input);
        const match = CATEGORY_VALUES.find((value) => normalizeEnumName(value) === normalized);
        return match || 'Hydrology';
    }

    function parseFunctionalVariableType(input) {
        const normalized = normalizeEnumName(input);
        return VARIABLE_TYPE_LOOKUP[normalized] || 'CatchmentHydrology';
    }

    function parseBoolean(value) {
        const normalized = String(value || '').trim().toLowerCase();
        return normalized === 'true' || normalized === '1' || normalized === 'yes';
    }

    function formatVariableType(variableType) {
        return String(variableType || '').replace(/([a-z])([A-Z])/g, '$1 $2');
    }
    function loadFunctionalCategoryItems() {
        state.functionalCategoryItems = [];

        state.streamModelFunctions.forEach((streamFunction) => {
            const functionalCategory = parseFunctionalCategory(streamFunction['Functional Category']);
            const variableType = parseFunctionalVariableType(streamFunction['Functional Variable']);

            const functionalVariable = {
                variableType,
                metrics: [],
                isSelected: true
            };

            let existingCategory = state.functionalCategoryItems.find(
                (category) => category.functionalCategory === functionalCategory
            );

            if (!existingCategory) {
                existingCategory = {
                    functionalCategory,
                    functionalVariables: []
                };
                state.functionalCategoryItems.push(existingCategory);
            }

            existingCategory.functionalVariables.push(functionalVariable);
        });
    }

    function loadFunctionalVariableOptions() {
        state.functionalVariableSelectableOptions = state.streamModelFunctions.map((streamFunction) => {
            return {
                functionalCategory: parseFunctionalCategory(streamFunction['Functional Category']),
                functionalVariable: parseFunctionalVariableType(streamFunction['Functional Variable']),
                functionalStatement: streamFunction['Functional Statement'],
                physical: streamFunction['Physical'],
                chemical: streamFunction['Chemical'],
                biological: streamFunction['Biological'],
                isSelected: true
            };
        });
    }

    function loadMetricsFromToolbox() {
        state.allPossibleMetricOptions = state.toolboxMetrics.map((toolboxMetric) => {
            const assessment = toolboxMetric['Assessment'] || '';
            const year = toolboxMetric['Year'] || '';
            const metricName = toolboxMetric['Metric Name'] || '';

            return {
                uniqueId: `${assessment} - ${year} - ${metricName}`,
                selectedCategory: '',
                selectedVariable: '',
                assessment,
                year,
                metricShortName: toolboxMetric['Metric ID'] || '',
                metricName,
                performanceStandard: toolboxMetric['Performance Standard'] || '',
                method: toolboxMetric['Method'] || '',
                tier: toolboxMetric['Tier'] || '',
                isSelected: false,
                score: 0,
                physical: '',
                chemical: '',
                biological: '',
                applicableVariables: getApplicableVariables(toolboxMetric),
                performanceStandardLookupTable: []
            };
        });
    }

    function getApplicableVariables(toolboxMetric) {
        const variables = new Set();

        Object.keys(toolboxMetric).forEach((key) => {
            const value = toolboxMetric[key];
            if (value === null || value === undefined || String(value).trim() === '') {
                return;
            }
            const normalized = normalizeEnumName(key);
            const match = VARIABLE_TYPE_LOOKUP[normalized];
            if (match) {
                variables.add(match);
            }
        });

        return Array.from(variables);
    }

    function updateTableItems() {
        state.tableItems = generateTableItems();
    }

    function generateTableItems() {
        const newItems = [];
        state.functionalCategoryItems.forEach((category) => {
            category.functionalVariables.forEach((variable) => {
                if (!variable.metrics || variable.metrics.length === 0) {
                    newItems.push({
                        category,
                        variable,
                        metric: createEmptyMetric()
                    });
                } else {
                    variable.metrics.forEach((metric) => {
                        newItems.push({ category, variable, metric });
                    });
                }
            });
        });
        return newItems;
    }

    function createEmptyMetric() {
        return {
            uniqueId: '',
            assessment: '',
            year: '',
            metricShortName: '',
            metricName: '',
            performanceStandard: '',
            method: '',
            tier: '',
            score: 0,
            physical: '',
            chemical: '',
            biological: '',
            applicableVariables: [],
            performanceStandardLookupTable: []
        };
    }
    function render() {
        if (!state.isDataLoaded) {
            dom.loading.style.display = 'block';
            dom.appContent.classList.add('is-hidden');
            return;
        }

        dom.loading.style.display = 'none';
        dom.appContent.classList.remove('is-hidden');
        dom.modelNameInput.value = state.streamModelName;

        setActiveTab(state.activeTab, true);
        renderTables();

        if (state.variableDialogOpen) {
            renderVariableDialog();
        }
        if (state.metricDialogOpen) {
            renderMetricDialog();
        }
        if (state.scoreDialogOpen) {
            renderScoreDialog();
        }
    }

    function setActiveTab(tabId, skipRender) {
        state.activeTab = tabId;
        dom.tabButtons.forEach((button) => {
            const isActive = button.dataset.tab === tabId;
            button.classList.toggle('is-active', isActive);
            button.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        Object.keys(dom.tabPanels).forEach((key) => {
            dom.tabPanels[key].classList.toggle('is-active', key === tabId);
        });

        if (!skipRender) {
            renderTables();
        }
    }

    function renderTables() {
        renderVariablesTable();
        renderMetricsTable();
        renderScoringTable();
    }

    function renderVariablesTable() {
        const rows = state.tableItems.map((item) => {
            const category = item.category;
            const variable = item.variable;
            const metric = item.metric;
            const categoryClass = getCategoryClass(category.functionalCategory);
            const isFirstVariableRow = category.functionalVariables[0] === variable;
            const isFirstMetricRow = !variable.metrics || variable.metrics.length === 0 || variable.metrics[0] === metric;
            const variableRowSpan = getRowSpanForVariable(variable);
            const categoryRowSpan = getRowSpanForCategory(category);
            const variableStatement = getFunctionalVariableStatement(variable.variableType);
            const lookup = getFunctionalVariableOption(variable.variableType) || {};

            let cells = '';

            if (isFirstVariableRow && isFirstMetricRow) {
                cells += `
                    <td class="${categoryClass}" rowspan="${categoryRowSpan}">
                        <span class="clickabletext" data-action="edit-variable" data-category="${escapeHtml(category.functionalCategory)}">${escapeHtml(category.functionalCategory)}</span>
                    </td>`;
            }

            if (isFirstMetricRow) {
                cells += `
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}">
                        <div class="variable-cell">
                            <input type="checkbox" data-action="toggle-variable" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}" ${variable.isSelected ? 'checked' : ''} />
                            <span class="clickabletext" data-action="edit-metric" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}">${escapeHtml(formatVariableType(variable.variableType))}</span>
                        </div>
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}">
                        <span class="nonclickabletext">${escapeHtml(variableStatement)}</span>
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" style="text-align:center;">
                        ${escapeHtml(getScoreText(calculatePhysicalScore(variable), lookup.physical, variable.metrics.length, true))}
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" style="text-align:center;">
                        ${escapeHtml(getScoreText(calculateChemicalScore(variable), lookup.chemical, variable.metrics.length, true))}
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" style="text-align:center;">
                        ${escapeHtml(getScoreText(calculateBiologicalScore(variable), lookup.biological, variable.metrics.length, true))}
                    </td>`;
            }

            return `<tr class="${categoryClass}">${cells}</tr>`;
        });

        dom.variablesTableBody.innerHTML = rows.join('');
        dom.variablesTableFoot.innerHTML = renderVariablesFooter();
    }

    function renderVariablesFooter() {
        const physicalDirect = getPhysicalSubIndex(false, false, false, true, false, true, false);
        const physicalIndirect = getPhysicalSubIndex(false, false, false, false, true, true, false);
        const chemicalDirect = getChemicalSubIndex(false, false, false, true, false, true, false);
        const chemicalIndirect = getChemicalSubIndex(false, false, false, false, true, true, false);
        const biologicalDirect = getBiologicalSubIndex(false, false, false, true, false, true, false);
        const biologicalIndirect = getBiologicalSubIndex(false, false, false, false, true, true, false);

        return `
            <tr>
                <td colspan="3" style="text-align:right;">
                    <div class="nonclickabletext">Direct Effect</div>
                    <div class="nonclickabletext">Indirect Effect</div>
                </td>
                <td style="text-align:center;">
                    <div class="subindex-container">${escapeHtml(String(physicalDirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(physicalIndirect))}</div>
                </td>
                <td style="text-align:center;">
                    <div class="subindex-container">${escapeHtml(String(chemicalDirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(chemicalIndirect))}</div>
                </td>
                <td style="text-align:center;">
                    <div class="subindex-container">${escapeHtml(String(biologicalDirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(biologicalIndirect))}</div>
                </td>
            </tr>`;
    }

    function renderMetricsTable() {
        const rows = state.tableItems.map((item) => {
            const category = item.category;
            const variable = item.variable;
            const metric = item.metric;
            const categoryClass = getCategoryClass(category.functionalCategory);
            const isFirstVariableRow = category.functionalVariables[0] === variable;
            const isFirstMetricRow = !variable.metrics || variable.metrics.length === 0 || variable.metrics[0] === metric;
            const variableRowSpan = getRowSpanForVariable(variable);
            const categoryRowSpan = getRowSpanForCategory(category);

            let cells = '';

            if (isFirstVariableRow && isFirstMetricRow) {
                cells += `
                    <td class="${categoryClass}" rowspan="${categoryRowSpan}">
                        <span class="clickabletext" data-action="edit-variable" data-category="${escapeHtml(category.functionalCategory)}">${escapeHtml(category.functionalCategory)}</span>
                    </td>`;
            }

            if (isFirstMetricRow) {
                cells += `
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}">
                        <div class="variable-cell">
                            <input type="checkbox" data-action="toggle-variable" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}" ${variable.isSelected ? 'checked' : ''} />
                            <span class="clickabletext" data-action="edit-metric" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}">${escapeHtml(formatVariableType(variable.variableType))}</span>
                        </div>
                    </td>`;
            }

            cells += `
                <td class="${variable.isSelected ? '' : 'cell-disabled'}">${escapeHtml(metric.metricName || '')}</td>
                <td class="${variable.isSelected ? '' : 'cell-disabled'}">${escapeHtml(metric.assessment || '')}</td>
                <td class="${variable.isSelected ? '' : 'cell-disabled'}">${escapeHtml(metric.method || '')}</td>
                <td class="${variable.isSelected ? '' : 'cell-disabled'}" style="text-align:center;">
                    ${variable.metrics.length > 0 && metric.metricName ? `<span class="clickabletext" data-action="edit-score" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}" data-metric-id="${escapeHtml(metric.uniqueId || '')}">Edit</span>` : ''}
                </td>`;

            return `<tr class="${categoryClass}">${cells}</tr>`;
        });

        dom.metricsTableBody.innerHTML = rows.join('');
    }

    function renderScoringTable() {
        const rows = state.tableItems.map((item) => {
            const category = item.category;
            const variable = item.variable;
            const metric = item.metric;
            const categoryClass = getCategoryClass(category.functionalCategory);
            const isFirstVariableRow = category.functionalVariables[0] === variable;
            const isFirstMetricRow = !variable.metrics || variable.metrics.length === 0 || variable.metrics[0] === metric;
            const variableRowSpan = getRowSpanForVariable(variable);
            const categoryRowSpan = getRowSpanForCategory(category);
            const lookup = getFunctionalVariableOption(variable.variableType) || {};

            let cells = '';

            if (isFirstVariableRow && isFirstMetricRow) {
                cells += `
                    <td class="${categoryClass}" rowspan="${categoryRowSpan}">
                        <span class="clickabletext" data-action="edit-variable" data-category="${escapeHtml(category.functionalCategory)}">${escapeHtml(category.functionalCategory)}</span>
                    </td>`;
            }

            if (isFirstMetricRow) {
                cells += `
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}">
                        <div class="variable-cell">
                            <input type="checkbox" data-action="toggle-variable" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}" ${variable.isSelected ? 'checked' : ''} />
                            <span class="clickabletext" data-action="edit-metric" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}">${escapeHtml(formatVariableType(variable.variableType))}</span>
                        </div>
                    </td>`;
            }

            const metricScore = variable.metrics.length > 0 && metric.metricName ? formatNumber(metric.score) : '';

            cells += `
                <td class="${variable.isSelected ? '' : 'cell-disabled'}">${escapeHtml(metric.metricName || '')}</td>
                <td class="${variable.isSelected ? '' : 'cell-disabled'}" style="text-align:center;">
                    ${metric.metricName ? `<span class="clickabletext" data-action="edit-score" data-category="${escapeHtml(category.functionalCategory)}" data-variable="${escapeHtml(variable.variableType)}" data-metric-id="${escapeHtml(metric.uniqueId || '')}">${escapeHtml(metricScore)}</span>` : ''}
                </td>`;

            if (isFirstMetricRow) {
                cells += `
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}" style="text-align:center;">
                        ${variable.metrics.length > 0 ? escapeHtml(formatNumber(calculateVariableScore(variable))) : ''}
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}" style="text-align:center;">
                        ${escapeHtml(getScoreText(calculatePhysicalScore(variable), lookup.physical, variable.metrics.length, false))}
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}" style="text-align:center;">
                        ${escapeHtml(getScoreText(calculateChemicalScore(variable), lookup.chemical, variable.metrics.length, false))}
                    </td>
                    <td class="${variable.isSelected ? '' : 'cell-disabled'}" rowspan="${variableRowSpan}" style="text-align:center;">
                        ${escapeHtml(getScoreText(calculateBiologicalScore(variable), lookup.biological, variable.metrics.length, false))}
                    </td>`;
            }

            return `<tr class="${categoryClass}">${cells}</tr>`;
        });

        dom.scoringTableBody.innerHTML = rows.join('');
        dom.scoringTableFoot.innerHTML = renderScoringFooter();
    }

    function renderScoringFooter() {
        const physicalWeighted = getSubIndexScore('Physical', getPhysicalSubIndex(false, true));
        const physicalDirect = getPhysicalSubIndex(false, false, false, true);
        const physicalIndirect = getPhysicalSubIndex(false, false, false, false, true);
        const physicalMax = getPhysicalSubIndex(false, false, true, false, false);
        const physicalIndex = getSubIndexScore('Physical', getPhysicalSubIndex());

        const chemicalWeighted = getSubIndexScore('Chemical', getChemicalSubIndex(false, true));
        const chemicalDirect = getChemicalSubIndex(false, false, false, true);
        const chemicalIndirect = getChemicalSubIndex(false, false, false, false, true);
        const chemicalMax = getChemicalSubIndex(false, false, true, false, false);
        const chemicalIndex = getSubIndexScore('Chemical', getChemicalSubIndex());

        const biologicalWeighted = getSubIndexScore('Biological', getBiologicalSubIndex(false, true));
        const biologicalDirect = getBiologicalSubIndex(false, false, false, true);
        const biologicalIndirect = getBiologicalSubIndex(false, false, false, false, true);
        const biologicalMax = getBiologicalSubIndex(false, false, true, false, false);
        const biologicalIndex = getSubIndexScore('Biological', getBiologicalSubIndex());

        const ecosystemIndex = getEcosystemConditionIndexOrMissing();

        return `
            <tr>
                <td colspan="5" style="text-align:right;">
                    <div class="nonclickabletext">Weighted Score Total</div>
                    <div class="nonclickabletext">Direct Effect</div>
                    <div class="nonclickabletext">Indirect Effect</div>
                    <div class="nonclickabletext">Max Score</div>
                    <div class="nonclickabletext">Condition Sub-Index</div>
                    <div class="nonclickabletext">Ecosystem Condition Index</div>
                </td>
                <td style="text-align:center;">
                    <div class="subindex-container">${escapeHtml(String(physicalWeighted))}</div>
                    <div class="subindex-container">${escapeHtml(String(physicalDirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(physicalIndirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(physicalMax))}</div>
                    <div class="subindex-container">${escapeHtml(String(physicalIndex))}</div>
                    <div class="subindex-container missingScore">&nbsp;</div>
                </td>
                <td style="text-align:center;">
                    <div class="subindex-container">${escapeHtml(String(chemicalWeighted))}</div>
                    <div class="subindex-container">${escapeHtml(String(chemicalDirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(chemicalIndirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(chemicalMax))}</div>
                    <div class="subindex-container">${escapeHtml(String(chemicalIndex))}</div>
                    <div class="subindex-container">${escapeHtml(String(ecosystemIndex))}</div>
                </td>
                <td style="text-align:center;">
                    <div class="subindex-container">${escapeHtml(String(biologicalWeighted))}</div>
                    <div class="subindex-container">${escapeHtml(String(biologicalDirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(biologicalIndirect))}</div>
                    <div class="subindex-container">${escapeHtml(String(biologicalMax))}</div>
                    <div class="subindex-container">${escapeHtml(String(biologicalIndex))}</div>
                    <div class="subindex-container missingScore">&nbsp;</div>
                </td>
            </tr>`;
    }
    function renderVariableDialog() {
        if (!state.selectedCategoryItem) {
            return;
        }

        dom.variableDialogTitle.textContent = state.selectedCategoryItem.functionalCategory;

        const rows = state.filteredVariableSelectableOptionsForDialog.map((option, index) => {
            return `
                <tr>
                    <td>
                        <input type="checkbox" data-action="toggle-variable-option" data-index="${index}" ${option.isSelected ? 'checked' : ''} />
                    </td>
                    <td>${escapeHtml(option.functionalVariable)}</td>
                    <td>${escapeHtml(option.functionalStatement || '')}</td>
                    <td>${escapeHtml(option.physical || '')}</td>
                    <td>${escapeHtml(option.chemical || '')}</td>
                    <td>${escapeHtml(option.biological || '')}</td>
                </tr>`;
        });

        dom.variableDialogBody.innerHTML = rows.join('');
        setModalOpen('variable', true);
    }

    function renderMetricDialog() {
        if (!state.selectedVariable) {
            return;
        }

        dom.metricDialogTitle.textContent = formatVariableType(state.selectedVariable.variableType);
        dom.metricSearchInput.value = state.metricDialog.search;
        dom.metricFilterAssessment.value = state.metricDialog.filters.assessment;
        dom.metricFilterId.value = state.metricDialog.filters.metricId;
        dom.metricFilterName.value = state.metricDialog.filters.metricName;
        dom.metricFilterStandard.value = state.metricDialog.filters.performanceStandard;
        dom.metricFilterMethod.value = state.metricDialog.filters.method;
        dom.metricFilterTier.value = state.metricDialog.filters.tier;
        dom.metricPageSize.value = String(state.metricDialog.pageSize);

        const filtered = applyMetricFilters();
        const totalPages = Math.max(1, Math.ceil(filtered.length / state.metricDialog.pageSize));
        state.metricDialog.page = Math.min(state.metricDialog.page, totalPages);
        const startIndex = (state.metricDialog.page - 1) * state.metricDialog.pageSize;
        const pageItems = filtered.slice(startIndex, startIndex + state.metricDialog.pageSize);

        dom.metricPaginationInfo.textContent = `Page ${state.metricDialog.page} of ${totalPages} (${filtered.length} items)`;

        if (filtered.length === 0) {
            dom.metricDialogBody.innerHTML = '<tr><td colspan="7" class="nonclickabletext">No resources found.</td></tr>';
            setModalOpen('metric', true);
            return;
        }

        const grouped = groupByAssessment(pageItems);
        const rows = [];

        grouped.forEach((group) => {
            rows.push(`<tr class="group-row"><td colspan="7">${escapeHtml(group.assessment)}</td></tr>`);
            group.items.forEach((metric) => {
                rows.push(`
                    <tr>
                        <td>
                            <input type="checkbox" data-action="toggle-metric-option" data-metric-id="${escapeHtml(metric.uniqueId)}" ${metric.isSelected ? 'checked' : ''} />
                        </td>
                        <td>${escapeHtml(metric.assessment)}</td>
                        <td>${escapeHtml(metric.metricShortName)}</td>
                        <td>${escapeHtml(metric.metricName)}</td>
                        <td>${escapeHtml(metric.performanceStandard)}</td>
                        <td>${escapeHtml(metric.method)}</td>
                        <td>${escapeHtml(metric.tier)}</td>
                    </tr>`);
            });
        });

        dom.metricDialogBody.innerHTML = rows.join('');
        setModalOpen('metric', true);
    }

    function renderScoreDialog() {
        if (!state.selectedMetric) {
            return;
        }

        dom.scoreDialogTitle.textContent = state.selectedMetric.metricName || '';
        dom.scoreDialogScoreLabel.textContent = state.selectedMetric.metricName || '';
        dom.scoreInput.value = state.selectedMetric.score;

        const rows = state.lookupTableItems.map((item, index) => {
            return `
                <tr>
                    <td><input type="number" step="0.01" data-action="lookup-input" data-index="${index}" data-field="x" value="${item.xValue}" /></td>
                    <td><input type="number" step="0.01" data-action="lookup-input" data-index="${index}" data-field="y" value="${item.yValue}" /></td>
                    <td><button class="btn btn-secondary" type="button" data-action="remove-lookup-row" data-index="${index}">Remove</button></td>
                </tr>`;
        });

        dom.lookupTableBody.innerHTML = rows.join('');
        setModalOpen('score', true);

        setTimeout(() => {
            dom.scoreInput.focus();
            dom.scoreInput.select();
            drawScoreChart();
        }, 0);
    }

    function handleActionClick(event) {
        const actionTarget = event.target.closest('[data-action]');
        if (!actionTarget) {
            return;
        }

        const action = actionTarget.dataset.action;
        switch (action) {
            case 'export-excel':
                exportExcel();
                dom.exportMenu.classList.remove('is-open');
                break;
            case 'export-csv':
                exportCsv();
                dom.exportMenu.classList.remove('is-open');
                break;
            case 'trigger-import':
                dom.importMenu.classList.remove('is-open');
                dom.importFileInput.click();
                break;
            case 'edit-variable':
                openVariableDialog(actionTarget.dataset.category);
                break;
            case 'edit-metric':
                openMetricDialog(actionTarget.dataset.category, actionTarget.dataset.variable);
                break;
            case 'edit-score':
                openScoreDialog(
                    actionTarget.dataset.category,
                    actionTarget.dataset.variable,
                    actionTarget.dataset.metricId
                );
                break;
            case 'select-all-variables':
                toggleSelectAllVariables(true);
                break;
            case 'unselect-all-variables':
                toggleSelectAllVariables(false);
                break;
            case 'save-variables':
                saveSelectedVariables();
                break;
            case 'select-all-metrics':
                toggleSelectAllMetrics(true);
                break;
            case 'unselect-all-metrics':
                toggleSelectAllMetrics(false);
                break;
            case 'save-metrics':
                saveSelectedMetrics();
                break;
            case 'close-modal':
                closeModal(actionTarget.dataset.modal);
                break;
            case 'add-lookup-row':
                addLookupRow();
                break;
            case 'remove-lookup-row':
                removeLookupRow(Number(actionTarget.dataset.index));
                break;
            case 'save-score':
                saveScore();
                break;
            case 'cancel-score':
                cancelScore();
                break;
            default:
                break;
        }
    }

    function handleActionChange(event) {
        const target = event.target;
        const action = target.dataset.action;
        if (!action) {
            return;
        }

        switch (action) {
            case 'toggle-variable': {
                const categoryName = target.dataset.category;
                const variableType = target.dataset.variable;
                const variable = findVariable(categoryName, variableType);
                if (variable) {
                    variable.isSelected = target.checked;
                    updateTableItems();
                    renderTables();
                }
                break;
            }
            case 'toggle-variable-option': {
                const index = Number(target.dataset.index);
                const option = state.filteredVariableSelectableOptionsForDialog[index];
                if (option) {
                    option.isSelected = target.checked;
                }
                break;
            }
            case 'toggle-metric-option': {
                const metricId = target.dataset.metricId;
                const metric = state.filteredMetricOptionsForDialog.find((item) => item.uniqueId === metricId);
                if (metric) {
                    metric.isSelected = target.checked;
                }
                break;
            }
            default:
                break;
        }
    }

    function handleActionInput(event) {
        const target = event.target;
        const action = target.dataset.action;
        if (action !== 'lookup-input') {
            return;
        }

        const index = Number(target.dataset.index);
        const field = target.dataset.field;
        const value = parseFloat(target.value);

        if (state.lookupTableItems[index]) {
            if (field === 'x') {
                state.lookupTableItems[index].xValue = Number.isNaN(value) ? 0 : value;
            }
            if (field === 'y') {
                state.lookupTableItems[index].yValue = Number.isNaN(value) ? 0 : value;
            }
            drawScoreChart();
        }
    }
    function openVariableDialog(categoryName) {
        state.selectedCategoryItem = state.functionalCategoryItems.find(
            (category) => category.functionalCategory === categoryName
        );

        if (!state.selectedCategoryItem) {
            return;
        }

        state.filteredVariableSelectableOptionsForDialog = state.functionalVariableSelectableOptions.filter(
            (option) => option.functionalCategory === categoryName
        );

        state.filteredVariableSelectableOptionsForDialog.forEach((option) => {
            option.isSelected = state.selectedCategoryItem.functionalVariables.some(
                (variable) => variable.variableType === option.functionalVariable && variable.isSelected
            );
        });

        state.variableDialogOpen = true;
        renderVariableDialog();
    }

    function openMetricDialog(categoryName, variableType) {
        state.selectedCategoryItem = state.functionalCategoryItems.find(
            (category) => category.functionalCategory === categoryName
        );
        state.selectedVariable = findVariable(categoryName, variableType);

        if (!state.selectedVariable) {
            return;
        }

        state.filteredMetricOptionsForDialog = state.allPossibleMetricOptions
            .filter((option) => option.applicableVariables.includes(variableType))
            .sort((a, b) => a.assessment.localeCompare(b.assessment) || a.metricName.localeCompare(b.metricName));

        state.filteredMetricOptionsForDialog.forEach((option) => {
            option.isSelected = state.selectedVariable.metrics.some(
                (metric) => metric.uniqueId === option.uniqueId
            );
        });

        state.metricDialog.search = '';
        state.metricDialog.filters = {
            assessment: '',
            metricId: '',
            metricName: '',
            performanceStandard: '',
            method: '',
            tier: ''
        };
        state.metricDialog.page = 1;

        state.metricDialogOpen = true;
        renderMetricDialog();
    }

    function openScoreDialog(categoryName, variableType, metricId) {
        const variable = findVariable(categoryName, variableType);
        if (!variable) {
            return;
        }

        const metric = variable.metrics.find((item) => item.uniqueId === metricId);
        if (!metric) {
            return;
        }

        state.selectedVariable = variable;
        state.selectedMetric = metric;
        state.originalScore = metric.score;
        state.lookupTableItems = metric.performanceStandardLookupTable || [];
        metric.performanceStandardLookupTable = state.lookupTableItems;

        state.scoreDialogOpen = true;
        renderScoreDialog();
    }

    function closeModal(modalName) {
        if (modalName === 'variable') {
            state.variableDialogOpen = false;
            setModalOpen('variable', false);
        }
        if (modalName === 'metric') {
            state.metricDialogOpen = false;
            setModalOpen('metric', false);
        }
        if (modalName === 'score') {
            state.scoreDialogOpen = false;
            setModalOpen('score', false);
        }
    }

    function setModalOpen(modalName, isOpen) {
        if (modalName === 'variable') {
            dom.variableDialog.classList.toggle('is-open', isOpen);
            dom.variableDialog.setAttribute('aria-hidden', String(!isOpen));
        }
        if (modalName === 'metric') {
            dom.metricDialog.classList.toggle('is-open', isOpen);
            dom.metricDialog.setAttribute('aria-hidden', String(!isOpen));
        }
        if (modalName === 'score') {
            dom.scoreDialog.classList.toggle('is-open', isOpen);
            dom.scoreDialog.setAttribute('aria-hidden', String(!isOpen));
        }
    }

    function toggleMenu(menuElement) {
        const isOpen = menuElement.classList.contains('is-open');
        dom.exportMenu.classList.remove('is-open');
        dom.importMenu.classList.remove('is-open');
        menuElement.classList.toggle('is-open', !isOpen);
    }

    function toggleSelectAllVariables(selectAll) {
        state.filteredVariableSelectableOptionsForDialog.forEach((option) => {
            option.isSelected = selectAll;
        });
        renderVariableDialog();
    }

    function toggleSelectAllMetrics(selectAll) {
        state.filteredMetricOptionsForDialog.forEach((option) => {
            option.isSelected = selectAll;
        });
        renderMetricDialog();
    }

    function saveSelectedVariables() {
        if (!state.selectedCategoryItem) {
            return;
        }

        state.selectedCategoryItem.functionalVariables = state.filteredVariableSelectableOptionsForDialog.map(
            (option) => {
                return {
                    variableType: option.functionalVariable,
                    metrics: [],
                    isSelected: option.isSelected
                };
            }
        );

        updateTableItems();
        renderTables();
        closeModal('variable');
    }

    function saveSelectedMetrics() {
        if (!state.selectedVariable) {
            return;
        }

        state.selectedVariable.metrics = state.selectedVariable.metrics.filter((metric) => {
            return state.filteredMetricOptionsForDialog.some(
                (option) => option.uniqueId === metric.uniqueId && option.isSelected
            );
        });

        state.filteredMetricOptionsForDialog.forEach((option) => {
            if (option.isSelected && !state.selectedVariable.metrics.some((metric) => metric.uniqueId === option.uniqueId)) {
                state.selectedVariable.metrics.push({
                    uniqueId: option.uniqueId,
                    selectedCategory: state.selectedCategoryItem ? state.selectedCategoryItem.functionalCategory : '',
                    selectedVariable: state.selectedVariable.variableType,
                    assessment: option.assessment,
                    year: option.year,
                    metricShortName: option.metricShortName,
                    metricName: option.metricName,
                    performanceStandard: option.performanceStandard,
                    method: option.method,
                    tier: option.tier,
                    score: option.score,
                    physical: option.physical,
                    chemical: option.chemical,
                    biological: option.biological,
                    applicableVariables: [...option.applicableVariables],
                    performanceStandardLookupTable: []
                });
            }
        });

        updateTableItems();
        renderTables();
        closeModal('metric');
    }

    function addLookupRow() {
        state.lookupTableItems.push({ xValue: 0, yValue: 0 });
        renderScoreDialog();
    }

    function removeLookupRow(index) {
        state.lookupTableItems.splice(index, 1);
        renderScoreDialog();
    }

    function handleScoreInput(event) {
        if (state.selectedMetric) {
            const value = parseFloat(event.target.value);
            state.selectedMetric.score = Number.isNaN(value) ? 0 : value;
        }
    }

    function saveScore() {
        updateTableItems();
        renderTables();
        state.originalScore = null;
        closeModal('score');
    }

    function cancelScore() {
        if (state.selectedMetric && state.originalScore !== null) {
            state.selectedMetric.score = state.originalScore;
        }
        state.originalScore = null;
        closeModal('score');
    }

    function applyMetricFilters() {
        const search = state.metricDialog.search.trim().toLowerCase();
        const filters = state.metricDialog.filters;

        return state.filteredMetricOptionsForDialog.filter((metric) => {
            if (search && !String(metric.metricName || '').toLowerCase().includes(search)) {
                return false;
            }
            if (filters.assessment && !String(metric.assessment || '').toLowerCase().includes(filters.assessment.toLowerCase())) {
                return false;
            }
            if (filters.metricId && !String(metric.metricShortName || '').toLowerCase().includes(filters.metricId.toLowerCase())) {
                return false;
            }
            if (filters.metricName && !String(metric.metricName || '').toLowerCase().includes(filters.metricName.toLowerCase())) {
                return false;
            }
            if (filters.performanceStandard && !String(metric.performanceStandard || '').toLowerCase().includes(filters.performanceStandard.toLowerCase())) {
                return false;
            }
            if (filters.method && !String(metric.method || '').toLowerCase().includes(filters.method.toLowerCase())) {
                return false;
            }
            if (filters.tier && !String(metric.tier || '').toLowerCase().includes(filters.tier.toLowerCase())) {
                return false;
            }
            return true;
        });
    }

    function groupByAssessment(metrics) {
        const map = new Map();
        metrics.forEach((metric) => {
            const key = metric.assessment || 'Unspecified';
            if (!map.has(key)) {
                map.set(key, []);
            }
            map.get(key).push(metric);
        });

        return Array.from(map.entries()).map(([assessment, items]) => ({
            assessment,
            items
        }));
    }

    function handleMetricSearchInput(event) {
        const value = event.target.value;
        state.metricDialog.search = value;
        state.metricDialog.page = 1;

        if (metricSearchTimer) {
            clearTimeout(metricSearchTimer);
        }

        metricSearchTimer = setTimeout(() => {
            renderMetricDialog();
        }, 300);
    }

    function handleMetricFilterInput(event) {
        const target = event.target;
        const value = target.value;

        if (target === dom.metricFilterAssessment) {
            state.metricDialog.filters.assessment = value;
        } else if (target === dom.metricFilterId) {
            state.metricDialog.filters.metricId = value;
        } else if (target === dom.metricFilterName) {
            state.metricDialog.filters.metricName = value;
        } else if (target === dom.metricFilterStandard) {
            state.metricDialog.filters.performanceStandard = value;
        } else if (target === dom.metricFilterMethod) {
            state.metricDialog.filters.method = value;
        } else if (target === dom.metricFilterTier) {
            state.metricDialog.filters.tier = value;
        }

        state.metricDialog.page = 1;
        renderMetricDialog();
    }

    function clearMetricFilters() {
        state.metricDialog.search = '';
        state.metricDialog.filters = {
            assessment: '',
            metricId: '',
            metricName: '',
            performanceStandard: '',
            method: '',
            tier: ''
        };
        state.metricDialog.page = 1;
        renderMetricDialog();
    }

    function handleMetricPageSizeChange(event) {
        state.metricDialog.pageSize = Number(event.target.value);
        state.metricDialog.page = 1;
        renderMetricDialog();
    }

    function changeMetricPage(delta) {
        const filtered = applyMetricFilters();
        const totalPages = Math.max(1, Math.ceil(filtered.length / state.metricDialog.pageSize));
        state.metricDialog.page = Math.min(totalPages, Math.max(1, state.metricDialog.page + delta));
        renderMetricDialog();
    }

    function handleImportFile(event) {
        const file = event.target.files[0];
        if (!file) {
            return;
        }

        const reader = new FileReader();
        reader.onload = () => {
            const parsed = parseCsv(reader.result || '');
            importMetrics(parsed.records, file.name);
        };
        reader.readAsText(file);

        event.target.value = '';
    }

    function importMetrics(records, fileName) {
        state.functionalCategoryItems.forEach((category) => {
            category.functionalVariables.forEach((variable) => {
                variable.metrics = [];
                variable.isSelected = false;
            });
        });

        state.functionalVariableSelectableOptions.forEach((option) => {
            option.isSelected = false;
        });

        const name = fileName ? fileName.replace(/\.[^/.]+$/, '') : '';
        state.streamModelName = name || 'Stream Model';
        dom.modelNameInput.value = state.streamModelName;

        records.forEach((record) => {
            const categoryName = getRecordValue(record, 'selectedCategory');
            const variableName = getRecordValue(record, 'selectedVariable');
            if (!categoryName || !variableName) {
                return;
            }

            const category = state.functionalCategoryItems.find(
                (item) => item.functionalCategory === categoryName
            );
            if (!category) {
                return;
            }

            const variable = category.functionalVariables.find(
                (item) => item.variableType === variableName
            );
            if (!variable) {
                return;
            }

            const isSelected = parseBoolean(getRecordValue(record, 'IsSelected'));
            const hasMetric = Boolean(getRecordValue(record, 'Metric Name') || getRecordValue(record, 'Metric ID'));

            if (isSelected || hasMetric) {
                variable.isSelected = true;
                const option = state.functionalVariableSelectableOptions.find(
                    (item) => item.functionalCategory === categoryName && item.functionalVariable === variableName
                );
                if (option) {
                    option.isSelected = true;
                }
            }

            if (hasMetric) {
                const metric = {
                    uniqueId: `${getRecordValue(record, 'Assessment')} - ${getRecordValue(record, 'Year')} - ${getRecordValue(record, 'Metric Name')}`,
                    selectedCategory: categoryName,
                    selectedVariable: variableName,
                    assessment: getRecordValue(record, 'Assessment'),
                    year: getRecordValue(record, 'Year'),
                    metricShortName: getRecordValue(record, 'Metric ID'),
                    metricName: getRecordValue(record, 'Metric Name'),
                    performanceStandard: getRecordValue(record, 'Performance Standard'),
                    method: getRecordValue(record, 'Method'),
                    tier: getRecordValue(record, 'Tier'),
                    score: parseFloat(getRecordValue(record, 'Score')) || 0,
                    physical: getRecordValue(record, 'Physical'),
                    chemical: getRecordValue(record, 'Chemical'),
                    biological: getRecordValue(record, 'Biological'),
                    applicableVariables: [],
                    performanceStandardLookupTable: []
                };

                if (!variable.metrics.some((item) => item.uniqueId === metric.uniqueId)) {
                    variable.metrics.push(metric);
                }
            }
        });

        updateTableItems();
        renderTables();
        showToast('Stream model imported.', 'info');
    }

    function getRecordValue(record, key) {
        if (record[key] !== undefined) {
            return record[key];
        }
        const match = Object.keys(record).find((item) => item.toLowerCase() === key.toLowerCase());
        return match ? record[match] : '';
    }

    function exportCsv() {
        const selectedMetrics = [];
        state.functionalCategoryItems.forEach((category) => {
            category.functionalVariables.forEach((variable) => {
                selectedMetrics.push(...variable.metrics);
            });
        });

        state.functionalVariableSelectableOptions.forEach((option) => {
            if (option.isSelected && !selectedMetrics.some((metric) => metric.selectedVariable === option.functionalVariable)) {
                selectedMetrics.push({
                    selectedCategory: option.functionalCategory,
                    selectedVariable: option.functionalVariable,
                    assessment: '',
                    year: '',
                    metricShortName: '',
                    metricName: '',
                    performanceStandard: '',
                    isSelected: true,
                    method: '',
                    tier: '',
                    score: 0,
                    physical: '',
                    chemical: '',
                    biological: ''
                });
            }
        });

        const records = selectedMetrics.map((metric) => {
            return {
                selectedCategory: metric.selectedCategory || '',
                selectedVariable: metric.selectedVariable || '',
                Assessment: metric.assessment || '',
                Year: metric.year || '',
                MetricShortName: metric.metricShortName || '',
                MetricName: metric.metricName || '',
                PerformanceStandard: metric.performanceStandard || '',
                IsSelected: metric.isSelected || false,
                Method: metric.method || '',
                Tier: metric.tier || '',
                Score: metric.score || 0,
                Physical: metric.physical || '',
                Chemical: metric.chemical || '',
                Biological: metric.biological || ''
            };
        });

        const headerOrder = [
            'selectedCategory',
            'selectedVariable',
            'Assessment',
            'Year',
            'MetricShortName',
            'MetricName',
            'PerformanceStandard',
            'IsSelected',
            'Method',
            'Tier',
            'Score',
            'Physical',
            'Chemical',
            'Biological'
        ];

        const csvData = toCsv(records, headerOrder);
        const fileName = state.streamModelName ? `${state.streamModelName}.csv` : 'new model.csv';
        downloadCsv(fileName, csvData);
    }
    async function exportExcel() {
        showToast('Exporting model and starting download.', 'info');

        const ExcelJS = window.ExcelJS;
        if (!ExcelJS) {
            showToast('Excel export library not loaded.', 'error');
            return;
        }

        const workbook = new ExcelJS.Workbook();
        const worksheet = workbook.addWorksheet('Stream Model Data');

        worksheet.getCell(1, 1).value = 'Functional Category';
        worksheet.getCell(1, 2).value = 'Functional Variable';
        worksheet.getCell(1, 3).value = 'Metric Name';
        worksheet.getCell(1, 4).value = 'Metric Score (0-1.0)';
        worksheet.getCell(1, 5).value = 'Variable Score (0-1.0)';
        worksheet.getCell(1, 6).value = 'Physical';
        worksheet.getCell(1, 7).value = 'Chemical';
        worksheet.getCell(1, 8).value = 'Biological';

        for (let col = 4; col <= 8; col += 1) {
            worksheet.getColumn(col).alignment = { horizontal: 'center' };
            worksheet.getColumn(col).numFmt = '0.00';
        }

        let row = 2;
        let currentCategory = '';
        let currentVariable = '';
        let trackingFirstRowOfVariable = 1;
        let trackingFirstRowOfCategory = 1;

        state.tableItems.forEach((tableItem) => {
            worksheet.getCell(row, 3).value = tableItem.metric.metricName || '';

            if (!tableItem.metric.metricName) {
                worksheet.getCell(row, 4).value = '';
            } else {
                worksheet.getCell(row, 4).value = tableItem.metric.score;
            }

            if (tableItem.category.functionalCategory !== currentCategory) {
                trackingFirstRowOfCategory = row;
                worksheet.getCell(row, 1).value = tableItem.category.functionalCategory;
                currentCategory = tableItem.category.functionalCategory;
            } else {
                worksheet.mergeCells(trackingFirstRowOfCategory, 1, row, 1);
            }

            if (currentVariable !== tableItem.variable.variableType) {
                trackingFirstRowOfVariable = row;
                worksheet.getCell(row, 2).value = formatVariableType(tableItem.variable.variableType);
                currentVariable = tableItem.variable.variableType;

                if (tableItem.variable.isSelected) {
                    worksheet.getCell(row, 5).value = {
                        formula: `IF(COUNTBLANK(D${trackingFirstRowOfVariable}:D${row}) > 0, "-", AVERAGE(D${trackingFirstRowOfVariable}:D${row}))`
                    };
                } else {
                    worksheet.getCell(row, 5).value = '-';
                }

                const option = getFunctionalVariableOption(tableItem.variable.variableType) || {};
                const variableScore = `E${row}`;

                if (option.physical === 'D') {
                    worksheet.getCell(row, 6).value = {
                        formula: `IF(${variableScore}="-", "D", "D (" & ROUND(${variableScore}, 2) & ")")`
                    };
                } else if (option.physical === 'i') {
                    worksheet.getCell(row, 6).value = {
                        formula: `IF(${variableScore}="-", "i", "i (" & ROUND(${variableScore}*0.25, 2) & ")")`
                    };
                } else {
                    worksheet.getCell(row, 6).value = '';
                }

                if (option.chemical === 'D') {
                    worksheet.getCell(row, 7).value = {
                        formula: `IF(${variableScore}="-", "D", "D (" & ROUND(${variableScore}, 2) & ")")`
                    };
                } else if (option.chemical === 'i') {
                    worksheet.getCell(row, 7).value = {
                        formula: `IF(${variableScore}="-", "i", "i (" & ROUND(${variableScore}*0.25, 2) & ")")`
                    };
                } else {
                    worksheet.getCell(row, 7).value = '';
                }

                if (option.biological === 'D') {
                    worksheet.getCell(row, 8).value = {
                        formula: `IF(${variableScore}="-", "D", "D (" & ROUND(${variableScore}, 2) & ")")`
                    };
                } else if (option.biological === 'i') {
                    worksheet.getCell(row, 8).value = {
                        formula: `IF(${variableScore}="-", "i", "i (" & ROUND(${variableScore}*0.25, 2) & ")")`
                    };
                } else {
                    worksheet.getCell(row, 8).value = '';
                }
            } else {
                if (tableItem.variable.isSelected) {
                    worksheet.getCell(trackingFirstRowOfVariable, 5).value = {
                        formula: `IF(COUNTBLANK(D${trackingFirstRowOfVariable}:D${row}) > 0, "-", AVERAGE(D${trackingFirstRowOfVariable}:D${row}))`
                    };
                } else {
                    worksheet.getCell(trackingFirstRowOfVariable, 5).value = '-';
                }

                worksheet.mergeCells(trackingFirstRowOfVariable, 2, row, 2);
                worksheet.mergeCells(trackingFirstRowOfVariable, 5, row, 5);
                worksheet.mergeCells(trackingFirstRowOfVariable, 6, row, 6);
                worksheet.mergeCells(trackingFirstRowOfVariable, 7, row, 7);
                worksheet.mergeCells(trackingFirstRowOfVariable, 8, row, 8);
            }

            const rowCategoryColor = getCategoryColor(tableItem.category.functionalCategory);
            const rangeFirstCol = worksheet.getCell(row, 1);
            rangeFirstCol.fill = makeFill(rowCategoryColor);

            for (let col = 2; col <= 8; col += 1) {
                const cell = worksheet.getCell(row, col);
                if (!tableItem.variable.isSelected) {
                    cell.fill = makeFill('D3D3D3');
                } else {
                    cell.fill = makeFill(rowCategoryColor);
                }
            }

            applyBorder(worksheet, row, 1, row, 8);

            row += 1;
        });

        applyBorder(worksheet, 1, 1, row - 1, 8);

        worksheet.getColumn(1).alignment = { horizontal: 'center', vertical: 'top' };
        worksheet.getColumn(2).alignment = { vertical: 'center' };
        worksheet.getColumn(5).alignment = { vertical: 'center' };
        worksheet.getColumn(6).alignment = { vertical: 'center' };
        worksheet.getColumn(7).alignment = { vertical: 'center' };
        worksheet.getColumn(8).alignment = { vertical: 'center' };

        worksheet.getCell(row, 5).value = 'Weighted Score Total';
        worksheet.getCell(row, 5).font = { bold: true };
        worksheet.getCell(row, 5).alignment = { horizontal: 'right' };
        worksheet.getCell(row, 6).value = {
            formula: `SUMPRODUCT(IFERROR(--MID(F2:F${row - 1}, FIND("(", F2:F${row - 1}) + 1, FIND(")", F2:F${row - 1}) - FIND("(", F2:F${row - 1}) - 1), 0))`
        };
        worksheet.getCell(row, 7).value = {
            formula: `SUMPRODUCT(IFERROR(--MID(G2:G${row - 1}, FIND("(", G2:G${row - 1}) + 1, FIND(")", G2:G${row - 1}) - FIND("(", G2:G${row - 1}) - 1), 0))`
        };
        worksheet.getCell(row, 8).value = {
            formula: `SUMPRODUCT(IFERROR(--MID(H2:H${row - 1}, FIND("(", H2:H${row - 1}) + 1, FIND(")", H2:H${row - 1}) - FIND("(", H2:H${row - 1}) - 1), 0))`
        };

        worksheet.getCell(row + 1, 5).value = 'Direct Effect';
        worksheet.getCell(row + 1, 5).font = { bold: true };
        worksheet.getCell(row + 1, 5).alignment = { horizontal: 'right' };
        worksheet.getCell(row + 1, 6).value = { formula: `COUNTIF(F2:F${row - 1}, "D (*")` };
        worksheet.getCell(row + 1, 7).value = { formula: `COUNTIF(G2:G${row - 1}, "D (*")` };
        worksheet.getCell(row + 1, 8).value = { formula: `COUNTIF(H2:H${row - 1}, "D (*")` };

        worksheet.getCell(row + 2, 5).value = 'Indirect Effect';
        worksheet.getCell(row + 2, 5).font = { bold: true };
        worksheet.getCell(row + 2, 5).alignment = { horizontal: 'right' };
        worksheet.getCell(row + 2, 6).value = { formula: `COUNTIF(F2:F${row - 1}, "i (*")` };
        worksheet.getCell(row + 2, 7).value = { formula: `COUNTIF(G2:G${row - 1}, "i (*")` };
        worksheet.getCell(row + 2, 8).value = { formula: `COUNTIF(H2:H${row - 1}, "i (*")` };

        worksheet.getCell(row + 3, 5).value = 'Max Score';
        worksheet.getCell(row + 3, 5).font = { bold: true };
        worksheet.getCell(row + 3, 5).alignment = { horizontal: 'right' };
        worksheet.getCell(row + 3, 6).value = { formula: `=(F${row + 1} * 1) + (F${row + 2} * 0.25)` };
        worksheet.getCell(row + 3, 7).value = { formula: `=(G${row + 1} * 1) + (G${row + 2} * 0.25)` };
        worksheet.getCell(row + 3, 8).value = { formula: `=(H${row + 1} * 1) + (H${row + 2} * 0.25)` };

        worksheet.getCell(row + 4, 5).value = 'Condition Sub-Index';
        worksheet.getCell(row + 4, 5).font = { bold: true };
        worksheet.getCell(row + 4, 5).alignment = { horizontal: 'right' };
        worksheet.getCell(row + 4, 6).value = { formula: `=F${row} / F${row + 3}` };
        worksheet.getCell(row + 4, 7).value = { formula: `=G${row} / G${row + 3}` };
        worksheet.getCell(row + 4, 8).value = { formula: `=H${row} / H${row + 3}` };

        worksheet.getCell(row + 5, 5).value = 'Ecosystem Condition Index';
        worksheet.getCell(row + 5, 5).font = { bold: true };
        worksheet.getCell(row + 5, 5).alignment = { horizontal: 'right' };
        worksheet.mergeCells(row + 5, 6, row + 5, 8);
        worksheet.getCell(row + 5, 6).alignment = { horizontal: 'center' };
        worksheet.getCell(row + 5, 6).value = { formula: `AVERAGE(F${row + 4}:H${row + 4})` };

        applyBorder(worksheet, row, 6, row + 5, 8);

        worksheet.getRow(1).font = { bold: true };

        autoFitColumns(worksheet, 50);
        worksheet.getColumn(6).width = 10;
        worksheet.getColumn(7).width = 10;
        worksheet.getColumn(8).width = 10;

        const buffer = await workbook.xlsx.writeBuffer();
        const blob = new Blob([buffer], {
            type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        });
        const fileName = state.streamModelName ? `${state.streamModelName}.xlsx` : 'StreamModel.xlsx';
        downloadBlob(fileName, blob);
    }

    function makeFill(hex) {
        return {
            type: 'pattern',
            pattern: 'solid',
            fgColor: { argb: `FF${hex.replace('#', '').toUpperCase()}` }
        };
    }

    function applyBorder(worksheet, startRow, startCol, endRow, endCol) {
        for (let r = startRow; r <= endRow; r += 1) {
            for (let c = startCol; c <= endCol; c += 1) {
                worksheet.getCell(r, c).border = {
                    top: { style: 'thin' },
                    left: { style: 'thin' },
                    bottom: { style: 'thin' },
                    right: { style: 'thin' }
                };
            }
        }
    }

    function autoFitColumns(worksheet, maxWidth) {
        worksheet.columns.forEach((column) => {
            let maxLength = 0;
            column.eachCell({ includeEmpty: true }, (cell) => {
                const value = cell.value && cell.value.formula ? cell.value.formula : cell.value;
                const text = value ? String(value) : '';
                maxLength = Math.max(maxLength, text.length);
            });
            const width = Math.min(maxWidth, maxLength + 2);
            column.width = Math.max(width, 10);
        });
    }

    function toCsv(records, headerOrder) {
        const headers = headerOrder || Object.keys(records[0] || {});
        const lines = [];

        lines.push(headers.map(escapeCsvField).join(','));

        records.forEach((record) => {
            const line = headers.map((header) => escapeCsvField(record[header]));
            lines.push(line.join(','));
        });

        return lines.join('\n');
    }

    function escapeCsvField(value) {
        if (value === null || value === undefined) {
            return '';
        }
        const text = String(value);
        if (/[",\n\r]/.test(text)) {
            return `"${text.replace(/"/g, '""')}"`;
        }
        return text;
    }

    function downloadCsv(fileName, content) {
        const blob = new Blob([content], { type: 'text/csv' });
        downloadBlob(fileName, blob);
    }

    function downloadBlob(fileName, blob) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = fileName;
        anchor.style.display = 'none';
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        URL.revokeObjectURL(url);
    }
    function drawScoreChart() {
        const canvas = dom.scoreChart;
        if (!canvas) {
            return;
        }

        const rect = canvas.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) {
            return;
        }

        const dpr = window.devicePixelRatio || 1;
        canvas.width = rect.width * dpr;
        canvas.height = rect.height * dpr;

        const ctx = canvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, rect.width, rect.height);

        const points = state.lookupTableItems
            .map((item) => ({
                x: Number(item.xValue),
                y: Number(item.yValue)
            }))
            .filter((point) => !Number.isNaN(point.x) && !Number.isNaN(point.y))
            .sort((a, b) => a.x - b.x);

        const padding = { left: 50, right: 20, top: 20, bottom: 30 };

        if (points.length === 0) {
            ctx.fillStyle = '#666666';
            ctx.font = '12px Roboto, Arial, sans-serif';
            ctx.fillText('No lookup data', padding.left, rect.height / 2);
            return;
        }

        const minX = Math.min(0, ...points.map((point) => point.x));
        const maxX = Math.max(1, ...points.map((point) => point.x));
        const minY = Math.min(0, ...points.map((point) => point.y));
        const maxY = Math.max(1, ...points.map((point) => point.y));
        const xRange = maxX - minX || 1;
        const yRange = maxY - minY || 1;

        const xScale = (rect.width - padding.left - padding.right) / xRange;
        const yScale = (rect.height - padding.top - padding.bottom) / yRange;

        const toX = (value) => padding.left + (value - minX) * xScale;
        const toY = (value) => rect.height - padding.bottom - (value - minY) * yScale;

        ctx.strokeStyle = '#333333';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padding.left, padding.top);
        ctx.lineTo(padding.left, rect.height - padding.bottom);
        ctx.lineTo(rect.width - padding.right, rect.height - padding.bottom);
        ctx.stroke();

        ctx.strokeStyle = '#1e88e5';
        ctx.lineWidth = 2;
        ctx.beginPath();
        points.forEach((point, index) => {
            const x = toX(point.x);
            const y = toY(point.y);
            if (index === 0) {
                ctx.moveTo(x, y);
            } else {
                ctx.lineTo(x, y);
            }
        });
        ctx.stroke();

        ctx.fillStyle = '#1e88e5';
        points.forEach((point) => {
            const x = toX(point.x);
            const y = toY(point.y);
            ctx.beginPath();
            ctx.arc(x, y, 3, 0, Math.PI * 2);
            ctx.fill();
        });
    }

    function calculateVariableScore(variable) {
        if (!variable.metrics || variable.metrics.length === 0) {
            return 0.0;
        }
        const total = variable.metrics.reduce((sum, metric) => sum + (Number(metric.score) || 0), 0);
        return roundTo2(total / variable.metrics.length);
    }

    function calculatePhysicalScore(variable) {
        const option = getFunctionalVariableOption(variable.variableType) || {};
        const variableScore = calculateVariableScore(variable);
        if (Number.isNaN(variableScore)) {
            return Number.NaN;
        }
        if (option.physical === 'D') {
            return variableScore;
        }
        if (option.physical === 'i') {
            return variableScore * 0.25;
        }
        return Number.NaN;
    }

    function calculateChemicalScore(variable) {
        const option = getFunctionalVariableOption(variable.variableType) || {};
        const variableScore = calculateVariableScore(variable);
        if (Number.isNaN(variableScore)) {
            return Number.NaN;
        }
        if (option.chemical === 'D') {
            return variableScore;
        }
        if (option.chemical === 'i') {
            return variableScore * 0.25;
        }
        return Number.NaN;
    }

    function calculateBiologicalScore(variable) {
        const option = getFunctionalVariableOption(variable.variableType) || {};
        const variableScore = calculateVariableScore(variable);
        if (Number.isNaN(variableScore)) {
            return Number.NaN;
        }
        if (option.biological === 'D') {
            return variableScore;
        }
        if (option.biological === 'i') {
            return variableScore * 0.25;
        }
        return Number.NaN;
    }

    function getScoreText(score, designation, metricsCount, onlyDesignation) {
        const value = designation || '';

        if (onlyDesignation) {
            return value ? value : '';
        }

        if (metricsCount === 0) {
            return value ? value : '';
        }

        if (Number.isNaN(score)) {
            return value ? value : '-';
        }

        return `${value} (${formatFixed(score)})`;
    }

    function getPhysicalSubIndex(index = true, total = false, max = false, dCount = false, iCount = false, ignoreVarUnselected = true, ignoreBlankMetrics = true) {
        let totalScore = 0.0;
        let maxScore = 0.0;
        let dVal = 0;
        let iVal = 0;

        state.functionalCategoryItems.forEach((category) => {
            category.functionalVariables.forEach((variable) => {
                if (ignoreVarUnselected && !variable.isSelected) {
                    return;
                }
                if (ignoreBlankMetrics && variable.metrics.length === 0) {
                    return;
                }

                const lookup = getFunctionalVariableOption(variable.variableType) || {};
                if (lookup.physical === 'D') {
                    dVal += 1;
                    maxScore += 1.0;
                    totalScore += calculatePhysicalScore(variable);
                } else if (lookup.physical === 'i') {
                    iVal += 1;
                    maxScore += 0.25;
                    totalScore += calculatePhysicalScore(variable) * 0.25;
                }
            });
        });

        if (index) {
            return maxScore > 0 ? roundTo2(totalScore / maxScore) : 0.0;
        }
        if (total) {
            return totalScore > 0 ? roundTo2(totalScore) : 0.0;
        }
        if (max) {
            return maxScore > 0 ? roundTo2(maxScore) : 0.0;
        }
        if (dCount) {
            return dVal;
        }
        if (iCount) {
            return iVal;
        }
        return maxScore > 0 ? roundTo2(totalScore / maxScore) : 0.0;
    }

    function getChemicalSubIndex(index = true, total = false, max = false, dCount = false, iCount = false, ignoreVarUnselected = true, ignoreBlankMetrics = true) {
        let totalScore = 0.0;
        let maxScore = 0.0;
        let dVal = 0;
        let iVal = 0;

        state.functionalCategoryItems.forEach((category) => {
            category.functionalVariables.forEach((variable) => {
                if (ignoreVarUnselected && !variable.isSelected) {
                    return;
                }
                if (ignoreBlankMetrics && variable.metrics.length === 0) {
                    return;
                }

                const lookup = getFunctionalVariableOption(variable.variableType) || {};
                if (lookup.chemical === 'D') {
                    dVal += 1;
                    maxScore += 1.0;
                    totalScore += calculateChemicalScore(variable);
                } else if (lookup.chemical === 'i') {
                    iVal += 1;
                    maxScore += 0.25;
                    totalScore += calculateChemicalScore(variable) * 0.25;
                }
            });
        });

        if (index) {
            return maxScore > 0 ? roundTo2(totalScore / maxScore) : 0.0;
        }
        if (total) {
            return totalScore > 0 ? roundTo2(totalScore) : 0.0;
        }
        if (max) {
            return maxScore > 0 ? roundTo2(maxScore) : 0.0;
        }
        if (dCount) {
            return dVal;
        }
        if (iCount) {
            return iVal;
        }
        return maxScore > 0 ? roundTo2(totalScore / maxScore) : 0.0;
    }

    function getBiologicalSubIndex(index = true, total = false, max = false, dCount = false, iCount = false, ignoreVarUnselected = true, ignoreBlankMetrics = true) {
        let totalScore = 0.0;
        let maxScore = 0.0;
        let dVal = 0;
        let iVal = 0;

        state.functionalCategoryItems.forEach((category) => {
            category.functionalVariables.forEach((variable) => {
                if (ignoreVarUnselected && !variable.isSelected) {
                    return;
                }
                if (ignoreBlankMetrics && variable.metrics.length === 0) {
                    return;
                }

                const lookup = getFunctionalVariableOption(variable.variableType) || {};
                if (lookup.biological === 'D') {
                    dVal += 1;
                    maxScore += 1.0;
                    totalScore += calculateBiologicalScore(variable);
                } else if (lookup.biological === 'i') {
                    iVal += 1;
                    maxScore += 0.25;
                    totalScore += calculateBiologicalScore(variable) * 0.25;
                }
            });
        });

        if (index) {
            return maxScore > 0 ? roundTo2(totalScore / maxScore) : 0.0;
        }
        if (total) {
            return totalScore > 0 ? roundTo2(totalScore) : 0.0;
        }
        if (max) {
            return maxScore > 0 ? roundTo2(maxScore) : 0.0;
        }
        if (dCount) {
            return dVal;
        }
        if (iCount) {
            return iVal;
        }
        return maxScore > 0 ? roundTo2(totalScore / maxScore) : 0.0;
    }

    function getSubIndexScore(type, subIndexScore) {
        const property = type.toLowerCase();
        const hasMetrics = state.functionalCategoryItems.some((category) =>
            category.functionalVariables.some((variable) => {
                const option = getFunctionalVariableOption(variable.variableType) || {};
                const designation = option[property];
                return variable.metrics.length > 0 && (designation === 'D' || designation === 'i');
            })
        );

        return hasMetrics ? formatNumber(subIndexScore) : '-';
    }

    function getEcosystemConditionIndex() {
        const totalScore = (getPhysicalSubIndex(true) + getChemicalSubIndex(true) + getBiologicalSubIndex(true)) / 3.0;
        return roundTo2(totalScore);
    }

    function getEcosystemConditionIndexOrMissing() {
        const hasPhysical = state.functionalCategoryItems.some((category) =>
            category.functionalVariables.some((variable) => {
                const option = getFunctionalVariableOption(variable.variableType) || {};
                return variable.metrics.length > 0 && (option.physical === 'D' || option.physical === 'i');
            })
        );
        const hasChemical = state.functionalCategoryItems.some((category) =>
            category.functionalVariables.some((variable) => {
                const option = getFunctionalVariableOption(variable.variableType) || {};
                return variable.metrics.length > 0 && (option.chemical === 'D' || option.chemical === 'i');
            })
        );
        const hasBiological = state.functionalCategoryItems.some((category) =>
            category.functionalVariables.some((variable) => {
                const option = getFunctionalVariableOption(variable.variableType) || {};
                return variable.metrics.length > 0 && (option.biological === 'D' || option.biological === 'i');
            })
        );

        if (!hasPhysical || !hasChemical || !hasBiological) {
            return '-';
        }

        return formatNumber(getEcosystemConditionIndex());
    }

    function getFunctionalVariableOption(variableType) {
        return state.functionalVariableSelectableOptions.find(
            (option) => option.functionalVariable === variableType
        );
    }

    function getFunctionalVariableStatement(variableType) {
        const option = getFunctionalVariableOption(variableType);
        return option ? option.functionalStatement : 'Statement not found';
    }

    function getRowSpanForCategory(category) {
        return category.functionalVariables.reduce((total, variable) => {
            return total + Math.max(1, variable.metrics.length || 0);
        }, 0);
    }

    function getRowSpanForVariable(variable) {
        return Math.max(1, variable.metrics.length || 0);
    }

    function getCategoryClass(categoryName) {
        switch (categoryName) {
            case 'Hydrology':
                return 'category-hydrology';
            case 'Hydraulics':
                return 'category-hydraulics';
            case 'Geomorphology':
                return 'category-geomorphology';
            case 'Physicochemical':
                return 'category-physicochemical';
            case 'Biology':
                return 'category-biology';
            default:
                return '';
        }
    }

    function getCategoryColor(categoryName) {
        switch (categoryName) {
            case 'Hydrology':
                return 'D9E1F2';
            case 'Hydraulics':
                return 'B4C6E7';
            case 'Geomorphology':
                return 'FCE4D6';
            case 'Physicochemical':
                return 'FFF2CC';
            case 'Biology':
                return 'E2EFDA';
            default:
                return 'FFFFFF';
        }
    }

    function findVariable(categoryName, variableType) {
        const category = state.functionalCategoryItems.find(
            (item) => item.functionalCategory === categoryName
        );
        if (!category) {
            return null;
        }
        return category.functionalVariables.find((item) => item.variableType === variableType) || null;
    }

    function formatNumber(value) {
        const number = Number(value);
        if (Number.isNaN(number)) {
            return '-';
        }
        return number.toFixed(2).replace(/\.00$/, '').replace(/(\.\d)0$/, '$1');
    }

    function formatFixed(value) {
        const number = Number(value);
        if (Number.isNaN(number)) {
            return '0.00';
        }
        return number.toFixed(2);
    }

    function roundTo2(value) {
        return Math.round(Number(value) * 100) / 100;
    }

    function escapeHtml(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function showToast(message, type) {
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.textContent = message;

        if (type === 'error') {
            toast.style.background = '#b00020';
        }

        dom.toastContainer.appendChild(toast);
        setTimeout(() => toast.classList.add('show'), 10);

        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 200);
        }, 2000);
    }
})();
