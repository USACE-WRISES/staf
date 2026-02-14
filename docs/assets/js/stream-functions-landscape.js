(() => {
  const container = document.querySelector('.functions-landscape-explorer');
  if (!container) {
    return;
  }

  const baseUrl = container.dataset.baseurl || '';
  const imageUrl = `${baseUrl}/assets/images/LandscapeFunctions.png`;
  const data = Array.isArray(window.STAF_STREAM_FUNCTIONS_LANDSCAPE_DATA)
    ? window.STAF_STREAM_FUNCTIONS_LANDSCAPE_DATA
    : [];
  const fallback = container.querySelector('.functions-landscape-fallback');
  const ui = container.querySelector('.functions-landscape-ui');

  if (!data.length) {
    if (ui) {
      ui.hidden = false;
      ui.textContent = 'Landscape explorer data is unavailable.';
    }
    return;
  }

  const categoryLabel = {
    hydrology: 'Hydrology',
    hydraulics: 'Hydraulics',
    geomorphology: 'Geomorphology',
    physicochemistry: 'Physicochemistry',
    biology: 'Biology'
  };
  const tableIdAliases = {
    'baseflow-low-flow-dynamics': 'low-flow-baseflow-dynamics',
    'bed-composition-bedform-diversity': 'bed-composition-bedform-dynamics'
  };
  const aliasToPrimary = Object.entries(tableIdAliases).reduce((acc, [key, value]) => {
    acc[value] = key;
    return acc;
  }, {});
  const editMode = new URLSearchParams(window.location.search).get('editHotspots') === '1';
  const hotspotStorageKey = 'staf.streamFunctions.hotspots.v1';
  const parsePct = (value, fallback) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  };

  const items = data.map((item) => ({
    ...item,
    hotspot: {
      xPct: parsePct(item.hotspot?.xPct, 50),
      yPct: parsePct(item.hotspot?.yPct, 50)
    },
    callout: {
      xPct: parsePct(item.callout?.xPct, parsePct(item.hotspot?.xPct, 50)),
      yPct: parsePct(item.callout?.yPct, parsePct(item.hotspot?.yPct, 50))
    }
  }));
  const defaultHotspots = new Map(
    items.map((item) => [
      item.id,
      {
        hotspot: {
          xPct: item.hotspot.xPct,
          yPct: item.hotspot.yPct
        },
        callout: {
          xPct: item.callout.xPct,
          yPct: item.callout.yPct
        }
      }
    ])
  );
  const itemById = new Map(items.map((item) => [item.id, item]));
  const detailsById = new Map(
    items.map((item) => [
      item.id,
      {
        description: item.description || '',
        functionStatement: '',
        context: ''
      }
    ])
  );

  let selectedId = items[0].id;
  let dragState = null;
  let editorSelect = null;
  let editorTargetSelect = null;
  let editorOutput = null;
  let editorStatus = null;
  let controlsWrap = null;
  let zoomOverlay = null;
  let zoomInButton = null;
  let zoomOutButton = null;
  let zoomLevel = 1;
  let panX = 0;
  let panY = 0;
  let panState = null;

  const root = document.createElement('section');
  root.className = 'functions-landscape';
  if (editMode) {
    root.classList.add('is-edit-mode');
  }

  const layout = document.createElement('div');
  layout.className = 'functions-landscape-layout';

  const visualPane = document.createElement('div');
  visualPane.className = 'functions-landscape-visual';
  const canvas = document.createElement('div');
  canvas.className = 'functions-landscape-canvas';
  if (!editMode) {
    canvas.classList.add('is-zoom-enabled');
  }
  canvas.setAttribute('aria-label', 'Interactive stream landscape');
  const image = document.createElement('img');
  image.src = imageUrl;
  image.alt = 'Interactive watershed landscape with stream function hotspots';
  image.loading = 'lazy';
  const stage = document.createElement('div');
  stage.className = 'functions-landscape-stage';
  const hotspotLayer = document.createElement('div');
  hotspotLayer.className = 'functions-landscape-hotspot-layer';
  hotspotLayer.setAttribute('role', 'group');
  hotspotLayer.setAttribute('aria-label', 'Stream function hotspots');

  stage.appendChild(image);
  stage.appendChild(hotspotLayer);
  canvas.appendChild(stage);
  visualPane.appendChild(canvas);

  if (editMode) {
    const editHint = document.createElement('p');
    editHint.className = 'functions-landscape-edit-hint';
    editHint.textContent =
      'Hotspot edit mode is active. Select a function and target, then click the image or drag the matching marker.';
    visualPane.appendChild(editHint);

    const editor = document.createElement('div');
    editor.className = 'functions-landscape-editor';

    const editorRow = document.createElement('div');
    editorRow.className = 'functions-landscape-editor-row';

    const editorLabel = document.createElement('label');
    editorLabel.className = 'functions-landscape-editor-label';
    editorLabel.textContent = 'Active function';

    editorSelect = document.createElement('select');
    editorSelect.className = 'functions-landscape-editor-select';
    editorSelect.setAttribute('aria-label', 'Select hotspot to place');
    items.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.name;
      editorSelect.appendChild(option);
    });
    editorLabel.appendChild(editorSelect);
    editorRow.appendChild(editorLabel);

    const editorTargetLabel = document.createElement('label');
    editorTargetLabel.className = 'functions-landscape-editor-label';
    editorTargetLabel.textContent = 'Edit target';

    editorTargetSelect = document.createElement('select');
    editorTargetSelect.className = 'functions-landscape-editor-select';
    editorTargetSelect.setAttribute('aria-label', 'Choose whether to edit hotspot or callout');
    const hotspotTargetOption = document.createElement('option');
    hotspotTargetOption.value = 'hotspot';
    hotspotTargetOption.textContent = 'Hotspot';
    const calloutTargetOption = document.createElement('option');
    calloutTargetOption.value = 'callout';
    calloutTargetOption.textContent = 'Callout';
    editorTargetSelect.appendChild(hotspotTargetOption);
    editorTargetSelect.appendChild(calloutTargetOption);

    editorTargetLabel.appendChild(editorTargetSelect);
    editorRow.appendChild(editorTargetLabel);

    const editorActions = document.createElement('div');
    editorActions.className = 'functions-landscape-editor-actions';

    const saveButton = document.createElement('button');
    saveButton.type = 'button';
    saveButton.className = 'btn btn-small';
    saveButton.textContent = 'Save';

    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'btn btn-small';
    copyButton.textContent = 'Copy JSON';

    const resetButton = document.createElement('button');
    resetButton.type = 'button';
    resetButton.className = 'btn btn-small';
    resetButton.textContent = 'Reset defaults';

    editorActions.appendChild(saveButton);
    editorActions.appendChild(copyButton);
    editorActions.appendChild(resetButton);
    editorRow.appendChild(editorActions);

    editorOutput = document.createElement('textarea');
    editorOutput.className = 'functions-landscape-editor-output';
    editorOutput.rows = 10;
    editorOutput.readOnly = true;
    editorOutput.setAttribute('aria-label', 'Hotspot coordinates JSON readout');

    editorStatus = document.createElement('p');
    editorStatus.className = 'functions-landscape-editor-status';
    editorStatus.textContent = 'Coordinates can be copied and pasted into chat.';

    editor.appendChild(editorRow);
    editor.appendChild(editorOutput);
    editor.appendChild(editorStatus);
    visualPane.appendChild(editor);

    editorSelect.addEventListener('change', () => {
      selectFunction(editorSelect.value, { jump: false });
    });

    saveButton.addEventListener('click', () => {
      persistHotspots({ announce: 'Coordinates saved to this browser.' });
    });

    copyButton.addEventListener('click', async () => {
      if (!editorOutput) {
        return;
      }
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(editorOutput.value);
          if (editorStatus) {
            editorStatus.textContent = 'Copied JSON to clipboard.';
          }
        } else if (editorOutput.select) {
          editorOutput.select();
          document.execCommand('copy');
          if (editorStatus) {
            editorStatus.textContent = 'Copied JSON to clipboard.';
          }
        }
      } catch (error) {
        if (editorStatus) {
          editorStatus.textContent = 'Copy failed. Select and copy the JSON manually.';
        }
      }
    });

    resetButton.addEventListener('click', () => {
      items.forEach((item) => {
        const defaults = defaultHotspots.get(item.id);
        if (!defaults?.hotspot || !defaults?.callout) {
          return;
        }
        updateHotspot(item.id, defaults.hotspot.xPct, defaults.hotspot.yPct);
        updateCallout(item.id, defaults.callout.xPct, defaults.callout.yPct);
      });
      persistHotspots({ announce: 'Reset to default hotspot and callout coordinates.' });
    });
  }

  if (!editMode) {
    controlsWrap = document.createElement('div');
    controlsWrap.className = 'functions-landscape-controls';

    zoomOverlay = document.createElement('div');
    zoomOverlay.className = 'functions-landscape-zoom';
    zoomOverlay.setAttribute('aria-label', 'Zoom controls');

    zoomInButton = document.createElement('button');
    zoomInButton.type = 'button';
    zoomInButton.className = 'functions-landscape-zoom-step';
    zoomInButton.setAttribute('aria-label', 'Zoom in');
    zoomInButton.textContent = '+';

    zoomOutButton = document.createElement('button');
    zoomOutButton.type = 'button';
    zoomOutButton.className = 'functions-landscape-zoom-step';
    zoomOutButton.setAttribute('aria-label', 'Zoom out');
    zoomOutButton.textContent = '-';

    zoomOverlay.appendChild(zoomInButton);
    zoomOverlay.appendChild(zoomOutButton);

    controlsWrap.appendChild(zoomOverlay);
    canvas.appendChild(controlsWrap);
  }

  const detailsPane = document.createElement('aside');
  detailsPane.className = 'functions-landscape-details';
  detailsPane.setAttribute('aria-live', 'polite');
  detailsPane.setAttribute('aria-atomic', 'true');

  const detailsCard = document.createElement('div');
  detailsCard.className = 'functions-landscape-details-card';
  const detailsName = document.createElement('h3');
  detailsName.className = 'functions-landscape-details-name';
  const detailsMeta = document.createElement('p');
  detailsMeta.className = 'functions-landscape-details-meta';
  const detailsSections = document.createElement('div');
  detailsSections.className = 'functions-landscape-details-sections';

  const detailsDescriptionSection = document.createElement('section');
  detailsDescriptionSection.className = 'functions-landscape-details-section';
  const detailsDescriptionTitle = document.createElement('h4');
  detailsDescriptionTitle.className = 'functions-landscape-details-section-title';
  detailsDescriptionTitle.textContent = 'Description';
  const detailsDescriptionValue = document.createElement('p');
  detailsDescriptionValue.className = 'functions-landscape-details-section-value';
  detailsDescriptionSection.appendChild(detailsDescriptionTitle);
  detailsDescriptionSection.appendChild(detailsDescriptionValue);

  const detailsStatementSection = document.createElement('section');
  detailsStatementSection.className = 'functions-landscape-details-section';
  const detailsStatementTitle = document.createElement('h4');
  detailsStatementTitle.className = 'functions-landscape-details-section-title';
  detailsStatementTitle.textContent = 'Function Statement';
  const detailsStatementValue = document.createElement('p');
  detailsStatementValue.className = 'functions-landscape-details-section-value';
  detailsStatementSection.appendChild(detailsStatementTitle);
  detailsStatementSection.appendChild(detailsStatementValue);

  const detailsContextSection = document.createElement('section');
  detailsContextSection.className = 'functions-landscape-details-section';
  const detailsContextTitle = document.createElement('h4');
  detailsContextTitle.className = 'functions-landscape-details-section-title';
  detailsContextTitle.textContent = 'Context';
  const detailsContextValue = document.createElement('p');
  detailsContextValue.className = 'functions-landscape-details-section-value';
  detailsContextSection.appendChild(detailsContextTitle);
  detailsContextSection.appendChild(detailsContextValue);

  detailsSections.appendChild(detailsDescriptionSection);
  detailsSections.appendChild(detailsStatementSection);
  detailsSections.appendChild(detailsContextSection);

  const jumpButton = document.createElement('button');
  jumpButton.type = 'button';
  jumpButton.className = 'btn btn-small functions-landscape-jump';
  jumpButton.textContent = 'Jump to table';
  detailsCard.appendChild(detailsName);
  detailsCard.appendChild(detailsMeta);
  detailsCard.appendChild(detailsSections);
  detailsCard.appendChild(jumpButton);

  detailsPane.appendChild(detailsCard);

  layout.appendChild(visualPane);
  layout.appendChild(detailsPane);

  root.appendChild(layout);

  if (fallback) {
    fallback.hidden = true;
  }
  if (ui) {
    ui.hidden = false;
    ui.appendChild(root);
  }

  const hotspotButtons = new Map();
  const calloutButtons = new Map();

  const emitRowSelect = (id, scroll) => {
    const scrollEnabled = scroll !== false;
    if (typeof window.stafHighlightFunctionRow === 'function') {
      const handled = window.stafHighlightFunctionRow(id, { scroll: scrollEnabled });
      if (handled) {
        return;
      }
    }

    const tryHighlight = (remainingAttempts) => {
      if (typeof window.stafHighlightFunctionRow === 'function') {
        const handled = window.stafHighlightFunctionRow(id, { scroll: scrollEnabled });
        if (handled) {
          return true;
        }
      }
      if (remainingAttempts <= 0) {
        return false;
      }
      setTimeout(() => {
        tryHighlight(remainingAttempts - 1);
      }, 120);
      return false;
    };

    window.dispatchEvent(
      new CustomEvent('staf:function-select', {
        detail: { id, scroll: scrollEnabled }
      })
    );
    tryHighlight(15);
  };

  const clampPct = (value) => Math.max(0, Math.min(100, value));
  const clampValue = (value, min, max) => Math.max(min, Math.min(max, value));
  const normalizeText = (value) => (typeof value === 'string' ? value.trim() : '');
  const isMarkerTarget = (target) =>
    target instanceof Element &&
    Boolean(target.closest('.functions-landscape-hotspot, .functions-landscape-callout'));
  const isControlTarget = (target) =>
    target instanceof Element && Boolean(target.closest('.functions-landscape-controls'));

  const resolvePrimaryId = (id) => aliasToPrimary[id] || id;
  const readDetails = (id) =>
    detailsById.get(id) || {
      description: '',
      functionStatement: '',
      context: ''
    };

  const applyCanvasCursor = () => {
    if (editMode) {
      return;
    }
    if (!canvas.classList.contains('is-zoom-enabled')) {
      return;
    }
    if (panState?.isDragging) {
      canvas.style.cursor = 'grabbing';
    } else {
      canvas.style.cursor = 'grab';
    }
  };

  const clampPan = () => {
    const canvasWidth = canvas.clientWidth;
    const canvasHeight = canvas.clientHeight;
    const stageWidth = stage.clientWidth;
    const stageHeight = stage.clientHeight;
    if (!canvasWidth || !canvasHeight || !stageWidth || !stageHeight) {
      panX = 0;
      panY = 0;
      return;
    }

    const minPanX = Math.min(0, canvasWidth - stageWidth * zoomLevel);
    const minPanY = Math.min(0, canvasHeight - stageHeight * zoomLevel);
    panX = clampValue(panX, minPanX, 0);
    panY = clampValue(panY, minPanY, 0);
  };

  const applyViewTransform = () => {
    clampPan();
    stage.style.transform = `translate3d(${panX}px, ${panY}px, 0) scale(${zoomLevel})`;
    applyCanvasCursor();
  };

  const setZoomLevel = (nextZoom, { anchorClientX = null, anchorClientY = null } = {}) => {
    if (editMode) {
      return;
    }

    const previousZoom = zoomLevel;
    zoomLevel = clampValue(nextZoom, 1, 4);

    if (Math.abs(zoomLevel - previousZoom) < 0.001) {
      applyViewTransform();
      return;
    }

    if (anchorClientX != null && anchorClientY != null) {
      const rect = canvas.getBoundingClientRect();
      const localX = anchorClientX - rect.left;
      const localY = anchorClientY - rect.top;
      const worldX = (localX - panX) / previousZoom;
      const worldY = (localY - panY) / previousZoom;
      panX = localX - worldX * zoomLevel;
      panY = localY - worldY * zoomLevel;
    } else {
      const canvasWidth = canvas.clientWidth;
      const canvasHeight = canvas.clientHeight;
      const worldCenterX = (canvasWidth / 2 - panX) / previousZoom;
      const worldCenterY = (canvasHeight / 2 - panY) / previousZoom;
      panX = canvasWidth / 2 - worldCenterX * zoomLevel;
      panY = canvasHeight / 2 - worldCenterY * zoomLevel;
    }

    applyViewTransform();
  };

  const centerOnWorldPoint = (worldX, worldY, nextZoom = zoomLevel) => {
    const canvasWidth = canvas.clientWidth;
    const canvasHeight = canvas.clientHeight;
    if (!canvasWidth || !canvasHeight) {
      return;
    }
    zoomLevel = clampValue(nextZoom, 1, 4);
    panX = canvasWidth / 2 - worldX * zoomLevel;
    panY = canvasHeight / 2 - worldY * zoomLevel;
    applyViewTransform();
  };

  if (!editMode) {
    applyCanvasCursor();

    const stopCanvasPropagation = (event) => {
      event.stopPropagation();
    };
    controlsWrap?.addEventListener('pointerdown', stopCanvasPropagation);
    controlsWrap?.addEventListener('pointerup', stopCanvasPropagation);
    controlsWrap?.addEventListener('click', stopCanvasPropagation);
    controlsWrap?.addEventListener('wheel', stopCanvasPropagation);

    zoomInButton?.addEventListener('click', () => {
      setZoomLevel(zoomLevel + 0.2);
    });

    zoomOutButton?.addEventListener('click', () => {
      setZoomLevel(zoomLevel - 0.2);
    });

    canvas.addEventListener(
      'wheel',
      (event) => {
        if (isControlTarget(event.target)) {
          return;
        }
        event.preventDefault();
        const direction = event.deltaY > 0 ? -0.12 : 0.12;
        setZoomLevel(zoomLevel + direction, {
          anchorClientX: event.clientX,
          anchorClientY: event.clientY
        });
      },
      { passive: false }
    );

    canvas.addEventListener('pointerdown', (event) => {
      if (
        isMarkerTarget(event.target) ||
        isControlTarget(event.target) ||
        zoomLevel <= 1
      ) {
        return;
      }
      panState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startPanX: panX,
        startPanY: panY,
        isDragging: false
      };
      canvas.setPointerCapture(event.pointerId);
      applyCanvasCursor();
    });

    canvas.addEventListener('pointermove', (event) => {
      if (!panState || panState.pointerId !== event.pointerId) {
        return;
      }
      const deltaX = event.clientX - panState.startX;
      const deltaY = event.clientY - panState.startY;
      panX = panState.startPanX + deltaX;
      panY = panState.startPanY + deltaY;
      if (Math.abs(deltaX) > 1 || Math.abs(deltaY) > 1) {
        panState.isDragging = true;
      }
      applyViewTransform();
    });

    const finishPan = (event) => {
      if (!panState || panState.pointerId !== event.pointerId) {
        return;
      }
      if (canvas.hasPointerCapture(event.pointerId)) {
        canvas.releasePointerCapture(event.pointerId);
      }
      panState = null;
      applyCanvasCursor();
    };

    canvas.addEventListener('pointerup', finishPan);
    canvas.addEventListener('pointercancel', finishPan);

    const handleResize = () => {
      applyViewTransform();
    };
    window.addEventListener('resize', handleResize);
    image.addEventListener('load', handleResize);
    requestAnimationFrame(handleResize);
  }

  const serializeHotspots = () =>
    items.map((item) => ({
      id: item.id,
      name: item.name,
      category: item.category,
      hotspot: {
        xPct: Number(item.hotspot.xPct.toFixed(2)),
        yPct: Number(item.hotspot.yPct.toFixed(2))
      },
      callout: {
        xPct: Number(item.callout.xPct.toFixed(2)),
        yPct: Number(item.callout.yPct.toFixed(2))
      }
    }));

  const updateHotspot = (id, xPct, yPct) => {
    const item = itemById.get(id);
    if (!item) {
      return;
    }
    item.hotspot.xPct = clampPct(xPct);
    item.hotspot.yPct = clampPct(yPct);
    const button = hotspotButtons.get(id);
    if (button) {
      button.style.left = `${item.hotspot.xPct}%`;
      button.style.top = `${item.hotspot.yPct}%`;
    }
  };

  const updateCallout = (id, xPct, yPct) => {
    const item = itemById.get(id);
    if (!item) {
      return;
    }
    item.callout.xPct = clampPct(xPct);
    item.callout.yPct = clampPct(yPct);
    const button = calloutButtons.get(id);
    if (button) {
      button.style.left = `${item.callout.xPct}%`;
      button.style.top = `${item.callout.yPct}%`;
    }
  };

  const refreshEditorReadout = () => {
    if (!editorOutput) {
      return;
    }
    editorOutput.value = JSON.stringify(serializeHotspots(), null, 2);
  };

  const persistHotspots = ({ announce = '' } = {}) => {
    const payload = serializeHotspots();
    if (editMode) {
      try {
        window.localStorage.setItem(hotspotStorageKey, JSON.stringify(payload));
      } catch (error) {
        if (editorStatus) {
          editorStatus.textContent = 'Unable to write local hotspot save in this browser.';
        }
      }
    }
    refreshEditorReadout();
    if (announce && editorStatus) {
      editorStatus.textContent = announce;
    }
    // eslint-disable-next-line no-console
    console.log('[STAF] Updated hotspot coordinates:\n', JSON.stringify(payload, null, 2));
  };

  const restoreSavedHotspots = () => {
    if (!editMode) {
      return false;
    }
    try {
      const raw = window.localStorage.getItem(hotspotStorageKey);
      if (!raw) {
        return false;
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        return false;
      }
      parsed.forEach((entry) => {
        if (!entry || !entry.id) {
          return;
        }
        if (entry.hotspot) {
          updateHotspot(entry.id, entry.hotspot.xPct, entry.hotspot.yPct);
        }
        if (entry.callout) {
          updateCallout(entry.id, entry.callout.xPct, entry.callout.yPct);
        }
      });
      return true;
    } catch (error) {
      if (editorStatus) {
        editorStatus.textContent = 'Saved hotspot data is invalid. Using defaults.';
      }
      return false;
    }
  };

  const loadFunctionDetails = async () => {
    try {
      const response = await fetch(`${baseUrl}/assets/data/functions.json`);
      if (!response.ok) {
        return;
      }
      const functionsData = await response.json();
      if (!Array.isArray(functionsData)) {
        return;
      }
      functionsData.forEach((entry) => {
        if (!entry || !entry.id) {
          return;
        }
        const primaryId = resolvePrimaryId(entry.id);
        if (!itemById.has(primaryId)) {
          return;
        }
        const current = readDetails(primaryId);
        const description =
          normalizeText(entry.impact_statement) ||
          normalizeText(entry.impactStatement) ||
          normalizeText(entry.short_description) ||
          current.description;
        const functionStatement =
          normalizeText(entry.function_statement) || normalizeText(entry.functionStatement);
        const context =
          normalizeText(entry.assessment_context) ||
          normalizeText(entry.assessmentContext) ||
          normalizeText(entry.long_description);
        detailsById.set(primaryId, {
          description,
          functionStatement: functionStatement || current.functionStatement,
          context: context || current.context
        });
      });
      renderSelection();
    } catch (error) {
      // Keep placeholder content when supplemental function details are unavailable.
    }
  };

  const hasSavedHotspots = restoreSavedHotspots();

  const currentItem = () => itemById.get(selectedId) || items[0];
  const withFallback = (value, fallback) => {
    const normalized = normalizeText(value);
    return normalized || fallback;
  };

  const renderSelection = () => {
    const selected = currentItem();
    const selectedDetails = readDetails(selected.id);
    detailsName.textContent = selected.name;
    detailsMeta.textContent = `Category: ${categoryLabel[selected.category] || selected.category}`;
    detailsDescriptionValue.textContent = withFallback(
      selectedDetails.description || selected.description,
      'Description coming soon.'
    );
    detailsStatementValue.textContent = withFallback(
      selectedDetails.functionStatement,
      'Function statement coming soon.'
    );
    detailsContextValue.textContent = withFallback(
      selectedDetails.context,
      'Context details coming soon.'
    );
    jumpButton.setAttribute('aria-label', `Jump to ${selected.name} row in stream functions table`);

    hotspotButtons.forEach((button, id) => {
      const isSelected = id === selected.id;
      button.classList.toggle('is-selected', isSelected);
      button.setAttribute('aria-pressed', String(isSelected));
    });
    calloutButtons.forEach((button, id) => {
      const isSelected = id === selected.id;
      button.classList.toggle('is-selected', isSelected);
      button.setAttribute('aria-pressed', String(isSelected));
    });
    if (editorSelect && editorSelect.value !== selected.id) {
      editorSelect.value = selected.id;
    }
  };

  const selectFunction = (id, { jump = false } = {}) => {
    if (!itemById.has(id)) {
      return;
    }
    selectedId = id;
    renderSelection();
    if (jump) {
      emitRowSelect(id, true);
    }
  };

  const handleArrowCycle = (event, id) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
      return false;
    }
    event.preventDefault();
    const index = items.findIndex((item) => item.id === id);
    if (index === -1) {
      return false;
    }
    let nextIndex = index;
    if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = items.length - 1;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (index - 1 + items.length) % items.length;
    } else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (index + 1) % items.length;
    }
    const next = items[nextIndex];
    selectFunction(next.id, { jump: false });
    const nextButton = hotspotButtons.get(next.id);
    if (nextButton) {
      nextButton.focus();
    }
    return true;
  };

  const startDrag = (event, item, button, target) => {
    if (!editMode) {
      return;
    }
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    dragState = {
      pointerId: event.pointerId,
      id: item.id,
      target: target || 'hotspot',
      moved: false
    };
  };

  const updateDrag = (event) => {
    if (!editMode || !dragState || dragState.pointerId !== event.pointerId) {
      return;
    }
    if (!itemById.get(dragState.id)) {
      return;
    }
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return;
    }
    const x = clampPct(((event.clientX - rect.left) / rect.width) * 100);
    const y = clampPct(((event.clientY - rect.top) / rect.height) * 100);
    if (dragState.target === 'callout') {
      updateCallout(dragState.id, x, y);
    } else {
      updateHotspot(dragState.id, x, y);
    }
    dragState.moved = true;
  };

  const endDrag = (event) => {
    if (!editMode || !dragState || dragState.pointerId !== event.pointerId) {
      return;
    }
    const button = hotspotButtons.get(dragState.id);
    const calloutButton = calloutButtons.get(dragState.id);
    if (button && button.hasPointerCapture(event.pointerId)) {
      button.releasePointerCapture(event.pointerId);
    }
    if (calloutButton && calloutButton.hasPointerCapture(event.pointerId)) {
      calloutButton.releasePointerCapture(event.pointerId);
    }
    if (dragState.moved) {
      const movedItem = itemById.get(dragState.id);
      const targetLabel = dragState.target === 'callout' ? 'callout' : 'hotspot';
      persistHotspots({
        announce: movedItem ? `Updated ${movedItem.name} ${targetLabel}.` : `Updated ${targetLabel}.`
      });
    }
    dragState = null;
  };

  items.forEach((item) => {
    const hotspotButton = document.createElement('button');
    hotspotButton.type = 'button';
    hotspotButton.className = 'functions-landscape-hotspot';
    hotspotButton.dataset.functionId = item.id;
    hotspotButton.style.left = `${item.hotspot.xPct}%`;
    hotspotButton.style.top = `${item.hotspot.yPct}%`;
    hotspotButton.setAttribute(
      'aria-label',
      `Select ${item.name} hotspot (${categoryLabel[item.category] || item.category})`
    );
    hotspotButton.setAttribute('aria-pressed', 'false');

    hotspotButton.addEventListener('keydown', (event) => {
      if (handleArrowCycle(event, item.id)) {
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectFunction(item.id, { jump: false });
      }
    });

    hotspotButton.addEventListener('click', () => {
      if (editMode && dragState && dragState.moved) {
        return;
      }
      selectFunction(item.id, { jump: false });
    });

    const calloutButton = document.createElement('button');
    calloutButton.type = 'button';
    calloutButton.className = 'functions-landscape-callout';
    calloutButton.dataset.functionId = item.id;
    calloutButton.style.left = `${item.callout.xPct}%`;
    calloutButton.style.top = `${item.callout.yPct}%`;
    calloutButton.setAttribute(
      'aria-label',
      `Select ${item.name} callout (${categoryLabel[item.category] || item.category})`
    );
    calloutButton.setAttribute('aria-pressed', 'false');

    calloutButton.addEventListener('keydown', (event) => {
      if (handleArrowCycle(event, item.id)) {
        return;
      }
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        selectFunction(item.id, { jump: false });
      }
    });

    calloutButton.addEventListener('click', () => {
      if (editMode && dragState && dragState.moved) {
        return;
      }
      selectFunction(item.id, { jump: false });
    });

    if (editMode) {
      hotspotButton.addEventListener('pointerdown', (event) =>
        startDrag(event, item, hotspotButton, 'hotspot')
      );
      hotspotButton.addEventListener('pointermove', updateDrag);
      hotspotButton.addEventListener('pointerup', endDrag);
      hotspotButton.addEventListener('pointercancel', endDrag);

      calloutButton.addEventListener('pointerdown', (event) =>
        startDrag(event, item, calloutButton, 'callout')
      );
      calloutButton.addEventListener('pointermove', updateDrag);
      calloutButton.addEventListener('pointerup', endDrag);
      calloutButton.addEventListener('pointercancel', endDrag);
    }

    hotspotButtons.set(item.id, hotspotButton);
    calloutButtons.set(item.id, calloutButton);
    hotspotLayer.appendChild(calloutButton);
    hotspotLayer.appendChild(hotspotButton);
  });

  if (editMode) {
    canvas.addEventListener('click', (event) => {
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest('.functions-landscape-hotspot, .functions-landscape-callout')
      ) {
        return;
      }
      const selected = currentItem();
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) {
        return;
      }
      const x = clampPct(((event.clientX - rect.left) / rect.width) * 100);
      const y = clampPct(((event.clientY - rect.top) / rect.height) * 100);
      const editTarget = editorTargetSelect?.value === 'callout' ? 'callout' : 'hotspot';
      if (editTarget === 'callout') {
        updateCallout(selected.id, x, y);
      } else {
        updateHotspot(selected.id, x, y);
      }
      persistHotspots({
        announce: `Placed ${selected.name} ${editTarget} at x=${x.toFixed(2)}%, y=${y.toFixed(2)}%.`
      });
    });
  }

  jumpButton.addEventListener('click', () => {
    const selected = currentItem();
    emitRowSelect(selected.id, true);
  });

  // Support cross-file row lookups when ids differ between widget config and table source data.
  window.addEventListener('staf:function-select', (event) => {
    const requestedId = event?.detail?.id;
    if (!requestedId) {
      return;
    }
    if (itemById.has(requestedId)) {
      return;
    }
    const mappedId = aliasToPrimary[requestedId];
    if (mappedId && itemById.has(mappedId)) {
      selectFunction(mappedId, { jump: false });
      return;
    }
    const aliasId = tableIdAliases[requestedId];
    if (aliasId && itemById.has(aliasId)) {
      selectFunction(aliasId, { jump: false });
    }
  });

  if (editMode) {
    refreshEditorReadout();
    if (editorStatus) {
      editorStatus.textContent = hasSavedHotspots
        ? 'Loaded saved hotspot and callout coordinates from this browser.'
        : 'Using default coordinates. Click image to place points.';
    }
  } else {
    applyViewTransform();
  }

  selectFunction(selectedId, { jump: false });
  loadFunctionDetails();
})();
