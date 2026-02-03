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

  const getDisciplineCategoryClass = (discipline) => {
    const key = normalizeText(discipline).replace(/\s+/g, '');
    return key ? `category-${key}` : '';
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

  const buildScoringSummary = (profile) => {
    if (!profile || !profile.scoring) {
      return 'Scoring unavailable';
    }
    const type = profile.scoring.type;
    if (type === 'categorical') {
      const count = profile.scoring.rubric?.levels?.length || 0;
      return `Qualitative - ${count} levels`;
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
    return {
      curveId: `curve-${Date.now()}`,
      name: 'Default',
      xType: isScreening ? 'qualitative' : 'quantitative',
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
      curveListCache: new Map(),
      curveColumnWidths: {
        value: 120,
        index: 70,
      },
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
      const detail = await store.loadMetricDetail(entry.metricId, entry.detailsRef);
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
      const detail = await store.loadMetricDetail(metricId);
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
            const url = store.buildUrl('/assets/data/screening-metrics.tsv');
            const response = await fetch(url);
            if (!response.ok) {
              throw new Error('Failed to load screening metrics order');
            }
            const text = await response.text();
            const rows = parseTSV(text);
            rows.forEach((row) => {
              const discipline = normalizeText(row.Discipline || row.discipline || '');
              const func = normalizeText(row.Function || row.function || '');
              const metric = normalizeText(row.Metric || row.metric || '');
              if (!discipline || !func || !metric) {
                return;
              }
              if (!state.ordering.disciplineOrder.includes(discipline)) {
                state.ordering.disciplineOrder.push(discipline);
              }
              if (!state.ordering.functionOrder.has(discipline)) {
                state.ordering.functionOrder.set(discipline, []);
              }
              const funcList = state.ordering.functionOrder.get(discipline);
              if (funcList && !funcList.includes(func)) {
                funcList.push(func);
              }
              const metricKey = `${discipline}|${func}`;
              if (!state.ordering.metricOrder.has(metricKey)) {
                state.ordering.metricOrder.set(metricKey, []);
              }
              const metricList = state.ordering.metricOrder.get(metricKey);
              if (metricList && !metricList.includes(metric)) {
                metricList.push(metric);
              }
            });
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
      const detail = await store.loadMetricDetail(entry.metricId, entry.detailsRef);
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
      const entries = index.metrics || [];
      const searchTerm = state.searchTerm.trim().toLowerCase();

      const filtered = entries.filter((entry) => {
        if (state.tierFilter !== 'all' && !entry.profileAvailability?.[state.tierFilter]) {
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
          state.ordering.disciplineOrder.forEach((discipline) => {
            if (
              tierEntries.some(
                (entry) =>
                  normalizeText(entry.discipline || '') === normalizeText(discipline)
              )
            ) {
              disciplineOptions.push(discipline);
            }
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
              }
            });
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

      let lastGroupKey = null;

      sorted.forEach((entry) => {
        const groupKey = `${entry.discipline || ''}||${entry.function || ''}`;
        if (groupKey !== lastGroupKey) {
          lastGroupKey = groupKey;
          const groupRow = createEl('div', 'metric-library-group');
          const categoryClass = getDisciplineCategoryClass(entry.discipline);
          if (categoryClass) {
            groupRow.classList.add(categoryClass);
          }
          const disciplineLabel = createEl(
            'span',
            'metric-library-group-discipline',
            entry.discipline || 'Discipline'
          );
          const functionLabel = createEl(
            'span',
            'metric-library-group-function',
            entry.function || 'Function'
          );
          groupRow.appendChild(disciplineLabel);
          groupRow.appendChild(functionLabel);
          fragment.appendChild(groupRow);
        }
        const row = createEl('div', 'metric-library-row is-clickable');
        if (entry.metricId === state.selectedMetricId) {
          row.classList.add('is-selected');
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
        const isReadOnly = api && typeof api.isReadOnly === 'function' ? api.isReadOnly() : false;
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

        const minTier = createEl('div', 'metric-cell');
        const minTierChip = createEl(
          'span',
          'metric-min-tier',
          entry.minimumTier ? formatTierAbbrev(entry.minimumTier) : '-'
        );
        minTier.appendChild(minTierChip);

        row.appendChild(metricCell);
        row.appendChild(minTier);

        fragment.appendChild(row);
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

      const detail = await store.loadMetricDetail(state.selectedMetricId);
      if (!detail) {
        return;
      }

      const profile = detail.profiles.find((p) => p.profileId === state.selectedProfileId) ||
        getDefaultProfile(detail, state.tierFilter);
      if (profile) {
        state.selectedProfileId = profile.profileId;
      }

      inspectorContent.hidden = false;
      inspectorEmpty.hidden = true;

      if (inspectorTitle) {
        inspectorTitle.textContent = detail.name || detail.metricId;
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

      const renderScoringTab = () => {
        const panel = tabPanels.find((p) => p.dataset.tab === 'scoring');
        if (!panel || !profile) {
          return;
        }
        panel.innerHTML = '';
        const scoring = profile.scoring;
        if (scoring.type === 'categorical') {
          const table = createEl('table', 'metric-rubric-table');
          table.innerHTML =
            '<thead><tr><th>Rating</th><th>Criteria</th></tr></thead>';
          const tbody = document.createElement('tbody');
          (scoring.rubric?.levels || []).forEach((level) => {
            const row = document.createElement('tr');
            row.innerHTML =
              `<td>${level.label || '-'}</td><td>${level.criteriaMarkdown || '-'}</td>`;
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
          '<option value="qualitative">Qualitative</option><option value="quantitative">Quantitative</option>';
        metaRow.appendChild(unitsInput);
        metaRow.appendChild(xTypeSelect);

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
        if (isAdded && api && api.getCurve) {
          const curve = api.getCurve(detail.metricId);
          if (curve) {
            curves = [curve];
          }
        }

        if (!curves.length) {
          curves = await loadCurvesForProfile(profile);
          curves = curves.map((item) => item.data);
        }

        if (!curves.length) {
          curves = [buildDefaultCurve(profile)];
          updateCurveCountOverride(detail.metricId, profile.tier, curves.length);
        }

        curves.forEach((curve, index) => {
          if (!curve.curveId) {
            curve.curveId = `curve-${index + 1}`;
          }
        });

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

        const renderCurveOptions = () => {
          curveSelect.innerHTML = '';
          curves.forEach((curve) => {
            const option = document.createElement('option');
            option.value = curve.curveId;
            option.textContent = curve.name || curve.curveId;
            curveSelect.appendChild(option);
          });
          const active = getActiveCurve();
          if (active) {
            curveSelect.value = active.curveId;
            curveNameInput.value = active.name || active.curveId;
          }
        };

        const renderCurveTable = () => {
          const active = getActiveCurve();
          if (!active) {
            return;
          }
          const layer = active.layers?.[0];
          if (!layer) {
            return;
          }
          unitsInput.value = active.units || '';
          xTypeSelect.value = active.xType || 'qualitative';
          tbody.innerHTML = '';
          layer.points.forEach((point) => {
            const row = document.createElement('tr');
            const valueCell = document.createElement('td');
            valueCell.className = 'metric-curve-value-cell';
            const valueInput = document.createElement('input');
            valueInput.className = 'metric-curve-value-input';
            valueInput.placeholder = 'Value';
            valueInput.value = point.x;
            valueInput.addEventListener('input', () => {
              point.x = valueInput.value;
              if (isAdded && api?.setCurve) {
                api.setCurve(detail.metricId, active);
                api.refresh?.();
              }
              renderCurveChart();
            });
            valueCell.appendChild(valueInput);

            const indexCell = document.createElement('td');
            indexCell.className = 'metric-curve-index-cell';
            const indexInput = document.createElement('input');
            indexInput.className = 'metric-curve-index-input';
            indexInput.type = 'number';
            indexInput.step = '0.01';
            indexInput.value = point.y;
            indexInput.addEventListener('input', () => {
              point.y = Number(indexInput.value);
              if (isAdded && api?.setCurve) {
                api.setCurve(detail.metricId, active);
                api.refresh?.();
              }
              renderCurveChart();
            });
            indexCell.appendChild(indexInput);

            const descCell = document.createElement('td');
            descCell.className = 'metric-curve-desc-cell';
            const descInput = document.createElement('textarea');
            descInput.className = 'metric-curve-desc';
            descInput.placeholder = 'Description';
            descInput.value = point.description || '';
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
        };

        const renderCurveChart = (targetCanvas = curveChart) => {
          if (!targetCanvas) {
            return;
          }
          const curve = getActiveCurve();
          const ctx = targetCanvas.getContext('2d');
          if (!ctx || !curve) {
            return;
          }
          ctx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);

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
          const scaleY = (value) =>
            padding.top + (1 - Math.min(1, Math.max(0, value))) * height;

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
              ctx.arc(x, y, 3.5, 0, Math.PI * 2);
              ctx.fill();
            });
          }

          const xTicks = isQualitative
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
            if (isQualitative) {
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
          state.selectedCurveId = curveSelect.value;
          const active = getActiveCurve();
          if (active) {
            curveNameInput.value = active.name || active.curveId;
          }
          renderCurveTable();
          renderCurveChart();
        });

        curveNameInput.addEventListener('input', () => {
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
          const newCurve = buildDefaultCurve(profile);
          newCurve.name = getNextCurveName();
          curves.push(newCurve);
          state.selectedCurveId = newCurve.curveId;
          updateCurveCountOverride(detail.metricId, profile.tier, curves.length);
          renderCurveOptions();
          renderCurveTable();
          renderCurveChart();
        });

        curveRemove.addEventListener('click', () => {
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
          }
        });

        unitsInput.addEventListener('input', () => {
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
          const active = getActiveCurve();
          if (!active) {
            return;
          }
          active.xType = xTypeSelect.value;
          if (isAdded && api?.setCurve) {
            api.setCurve(detail.metricId, active);
            api.refresh?.();
          }
          renderCurveChart();
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


