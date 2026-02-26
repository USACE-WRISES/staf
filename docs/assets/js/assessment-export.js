/**
 * STAF Assessment Export Engine
 *
 * Shared Excel export for screening, rapid, and detailed assessment widgets.
 * Produces an .xlsx workbook with data tables, roll-up computations, and
 * native bar charts (injected via OOXML post-processing with JSZip).
 *
 * Exposed as window.STAFAssessmentExport.downloadAssessmentWorkbook(config).
 */
(() => {
  'use strict';

  // ---------------------------------------------------------------------------
  // Color utilities
  // ---------------------------------------------------------------------------

  const COLORS = {
    functioning:    { argb: 'FFC8D9F2', rgb: 'C8D9F2' },
    atRisk:         { argb: 'FFF5E7A6', rgb: 'F5E7A6' },
    nonFunctioning: { argb: 'FFF5B5B5', rgb: 'F5B5B5' },
    headerBg:       { argb: 'FF1F3F6E' },
    headerFont:     { argb: 'FFFFFFFF' },
    rollupHeaderBg: { argb: 'FFD6DCE4' },
    white:          { argb: 'FFFFFFFF' },
  };

  const DISCIPLINE_COLORS = {
    hydrology:        { argb: 'FFD9E2F3' },
    hydraulics:       { argb: 'FFB9CBE6' },
    geomorphology:    { argb: 'FFF7E1D1' },
    physicochemistry: { argb: 'FFF3E9C4' },
    biology:          { argb: 'FFDBEADE' },
  };

  const disciplineFill = (discipline) => {
    const key = (discipline || '').toLowerCase().trim();
    const color = DISCIPLINE_COLORS[key];
    return color ? { type: 'pattern', pattern: 'solid', fgColor: color } : null;
  };

  const functionScoreColor = (value) => {
    if (value <= 5) return COLORS.nonFunctioning;
    if (value <= 10) return COLORS.atRisk;
    return COLORS.functioning;
  };

  const indexColor = (value) => {
    if (value <= 0.39) return COLORS.nonFunctioning;
    if (value <= 0.69) return COLORS.atRisk;
    return COLORS.functioning;
  };

  const labelForFunctionScore = (value) => {
    if (value <= 5) return 'Non-Functioning';
    if (value <= 10) return 'At-Risk';
    return 'Functioning';
  };

  // ---------------------------------------------------------------------------
  // Lazy loaders for ExcelJS and JSZip
  // ---------------------------------------------------------------------------

  let excelPromise = null;
  let jszipPromise = null;

  const resolveBaseUrl = () => {
    const meta = document.querySelector('[data-baseurl]');
    return meta ? meta.dataset.baseurl : '';
  };

  const loadScript = (src) =>
    new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(script);
    });

  const ensureExcelJs = () => {
    if (window.ExcelJS) return Promise.resolve(window.ExcelJS);
    if (excelPromise) return excelPromise;
    excelPromise = loadScript(`${resolveBaseUrl()}/assets/vendor/exceljs.min.js`)
      .then(() => {
        if (!window.ExcelJS) throw new Error('ExcelJS not available.');
        return window.ExcelJS;
      })
      .catch((err) => { excelPromise = null; throw err; });
    return excelPromise;
  };

  const ensureJSZip = () => {
    if (window.JSZip) return Promise.resolve(window.JSZip);
    if (jszipPromise) return jszipPromise;
    jszipPromise = loadScript(`${resolveBaseUrl()}/assets/vendor/jszip.min.js`)
      .then(() => {
        if (!window.JSZip) throw new Error('JSZip not available.');
        return window.JSZip;
      })
      .catch((err) => { jszipPromise = null; throw err; });
    return jszipPromise;
  };

  // ---------------------------------------------------------------------------
  // ExcelJS workbook builder
  // ---------------------------------------------------------------------------

  const THIN_BORDER = { style: 'thin', color: { argb: 'FFB0B0B0' } };
  const ALL_BORDERS = {
    top: THIN_BORDER,
    left: THIN_BORDER,
    bottom: THIN_BORDER,
    right: THIN_BORDER,
  };

  const HEADER_FILL = {
    type: 'pattern',
    pattern: 'solid',
    fgColor: COLORS.headerBg,
  };

  const HEADER_FONT = { bold: true, color: COLORS.headerFont, size: 10 };

  const tierLabel = (tier) => {
    if (tier === 'screening') return 'Screening';
    if (tier === 'rapid') return 'Rapid';
    if (tier === 'detailed') return 'Detailed';
    return tier;
  };

  const clampNumber = (value, min, max, fallback = min) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(max, Math.max(min, parsed));
  };

  const parseSuggestedRange = (row) => {
    const explicitMin = Number(row?.suggestedMin);
    const explicitMax = Number(row?.suggestedMax);
    if (Number.isFinite(explicitMin) && Number.isFinite(explicitMax)) {
      return {
        min: Math.min(explicitMin, explicitMax),
        max: Math.max(explicitMin, explicitMax),
      };
    }

    const raw = String(row?.suggestedRange || '').trim();
    const match = raw.match(/(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)/);
    if (!match) return null;
    const first = Number.parseFloat(match[1]);
    const second = Number.parseFloat(match[2]);
    if (!Number.isFinite(first) || !Number.isFinite(second)) return null;
    return {
      min: Math.min(first, second),
      max: Math.max(first, second),
    };
  };

  const weightForMappingCode = (code) => {
    const normalized = String(code || '').trim();
    if (normalized === 'D') return 1;
    if (normalized.toLowerCase() === 'i') return 0.1;
    return 0;
  };

  const functionScoreBandLabel = (value) => {
    if (!Number.isFinite(value)) return '';
    if (value <= 5) return 'NF';
    if (value <= 10) return 'AR';
    return 'F';
  };

  const addFunctionScoreConditionalFormatting = (worksheet, ref, scoreCellRef) => {
    worksheet.addConditionalFormatting({
      ref,
      rules: [
        {
          type: 'expression',
          formulae: [`AND(${scoreCellRef}<>"",${scoreCellRef}<=5)`],
          style: {
            fill: { type: 'pattern', pattern: 'solid', fgColor: COLORS.nonFunctioning },
            font: { bold: true },
          },
        },
        {
          type: 'expression',
          formulae: [`AND(${scoreCellRef}>=6,${scoreCellRef}<=10)`],
          style: {
            fill: { type: 'pattern', pattern: 'solid', fgColor: COLORS.atRisk },
            font: { bold: true },
          },
        },
        {
          type: 'expression',
          formulae: [`AND(${scoreCellRef}<>"",${scoreCellRef}>=11)`],
          style: {
            fill: { type: 'pattern', pattern: 'solid', fgColor: COLORS.functioning },
            font: { bold: true },
          },
        },
      ],
    });
  };

  const addIndexConditionalFormatting = (worksheet, ref, valueCellRef) => {
    worksheet.addConditionalFormatting({
      ref,
      rules: [
        {
          type: 'expression',
          formulae: [`AND(${valueCellRef}<>"",${valueCellRef}<=0.39)`],
          style: {
            fill: { type: 'pattern', pattern: 'solid', fgColor: COLORS.nonFunctioning },
            font: { bold: true },
          },
        },
        {
          type: 'expression',
          formulae: [`AND(${valueCellRef}>=0.4,${valueCellRef}<=0.69)`],
          style: {
            fill: { type: 'pattern', pattern: 'solid', fgColor: COLORS.atRisk },
            font: { bold: true },
          },
        },
        {
          type: 'expression',
          formulae: [`AND(${valueCellRef}<>"",${valueCellRef}>=0.7)`],
          style: {
            fill: { type: 'pattern', pattern: 'solid', fgColor: COLORS.functioning },
            font: { bold: true },
          },
        },
      ],
    });
  };

  const normalizeExportText = (value) =>
    String(value || '')
      .toLowerCase()
      .replace(/&/g, 'and')
      .replace(/[^a-z0-9]+/g, '')
      .trim();

  const toListString = (value) => {
    if (Array.isArray(value)) {
      return value
        .map((item) => String(item || '').trim())
        .filter(Boolean)
        .join('; ');
    }
    return String(value || '').trim();
  };

  const toNumberMaybe = (value) => {
    if (value === null || value === undefined || value === '') {
      return '';
    }
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    const parsed = Number.parseFloat(String(value).trim());
    return Number.isFinite(parsed) ? parsed : value;
  };

  const toScoreNumber = (value) => {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    const parsed = Number.parseFloat(String(value || '').trim());
    return Number.isFinite(parsed) ? parsed : null;
  };

  const normalizePoint = (point, curveType) => {
    const y = toScoreNumber(point?.y);
    const yMin = toScoreNumber(point?.yMin);
    const yMax = toScoreNumber(point?.yMax);
    const avg =
      y !== null
        ? y
        : yMin !== null && yMax !== null
        ? (yMin + yMax) / 2
        : null;
    return {
      x:
        curveType === 'quantitative'
          ? toNumberMaybe(point?.x)
          : String(point?.x ?? '').trim(),
      description: String(point?.description || '').trim(),
      y: avg,
      yMin,
      yMax,
    };
  };

  const deriveCurveType = (meta, row) => {
    const rawCurveType = String(meta?.curveType || '').trim().toLowerCase();
    if (rawCurveType === 'categorical' || rawCurveType === 'quantitative') {
      return rawCurveType;
    }
    const metricValue = row?.metricValue;
    const numericMetricValue = Number.parseFloat(String(metricValue || '').trim());
    return Number.isFinite(numericMetricValue) ? 'quantitative' : 'categorical';
  };

  const collectSheetExportMeta = (config) => {
    const dataRows = Array.isArray(config.rows) ? config.rows : [];
    const metricMap = new Map();
    const scoringMap = new Map();
    let maxPoints = 0;

    dataRows.forEach((row, rowIndex) => {
      const rowMeta = row && typeof row._exportMeta === 'object' ? row._exportMeta : {};
      const metricId = String(
        rowMeta.metricId ||
          row.metricId ||
          row.metric ||
          `metric-${rowIndex + 1}`
      ).trim();
      const metricName = String(rowMeta.metricName || row.metric || metricId).trim();
      const curveType = deriveCurveType(rowMeta, row);
      const layerName = String(
        rowMeta.layerName || row.scoringCriteria || 'Default'
      ).trim();
      const profileTier = String(rowMeta.profileTier || config.tier || '').trim();
      const curveSetName = String(
        rowMeta.curveSetName || row.scoringCriteria || layerName || ''
      ).trim();
      const curveId = String(
        rowMeta.curveId || `${metricId}-${normalizeExportText(layerName || 'curve')}`
      ).trim();
      const indexRange = Boolean(rowMeta.indexRange);

      const rawPoints = Array.isArray(rowMeta.points) ? rowMeta.points : [];
      let points = rawPoints
        .map((point) => normalizePoint(point, curveType))
        .filter((point) => {
          if (curveType === 'quantitative') {
            return (
              typeof point.x === 'number' &&
              Number.isFinite(point.x) &&
              Number.isFinite(point.y)
            );
          }
          return Boolean(String(point.x || '').trim());
        });

      if (curveType === 'quantitative') {
        points = points.sort((a, b) => a.x - b.x);
      }

      if (!points.length) {
        const fallbackIndex = toScoreNumber(row.metricIndex);
        points = [
          {
            x:
              curveType === 'quantitative'
                ? toNumberMaybe(row.metricValue)
                : String(row.metricValue || '').trim(),
            description: '',
            y: fallbackIndex,
            yMin: fallbackIndex,
            yMax: fallbackIndex,
          },
        ];
      }

      maxPoints = Math.max(maxPoints, points.length);

      if (!metricMap.has(metricId)) {
        metricMap.set(metricId, {
          metricId,
          name: metricName,
          function: String(rowMeta.functionName || row.function || '').trim(),
          category: String(rowMeta.category || row.discipline || '').trim(),
          recommendedTiers: toListString(
            rowMeta.recommendedTiers || [tierLabel(config.tier)]
          ),
          functionStatement: String(
            rowMeta.functionStatement || rowMeta.metricStatement || ''
          ).trim(),
          description: String(rowMeta.description || rowMeta.metricDescription || '').trim(),
          methodContext: String(
            rowMeta.methodContext || rowMeta.context || rowMeta.method || ''
          ).trim(),
          howToMeasure: String(rowMeta.howToMeasure || '').trim(),
          references: toListString(rowMeta.references || ''),
        });
      }

      const scoringKey = metricId;
      if (!scoringMap.has(scoringKey)) {
        scoringMap.set(scoringKey, {
          scoringKey,
          metricId,
          metricName,
          profileTier,
          curveSetName,
          curveId,
          units: String(rowMeta.units || '').trim(),
          curveType,
          indexRange,
          axesXLabel: String(rowMeta.axesXLabel || '').trim(),
          axesYLabel: String(rowMeta.axesYLabel || '').trim(),
          layerName,
          points,
        });
      }
    });

    return {
      metricsRows: Array.from(metricMap.values()),
      scoringRows: Array.from(scoringMap.values()),
      maxPoints: Math.max(1, maxPoints),
    };
  };

  const addMetricsAndScoringSheets = (workbook, config) => {
    const collected = collectSheetExportMeta(config);
    const metricsRows = collected.metricsRows;
    const scoringRows = collected.scoringRows;
    const maxPoints = collected.maxPoints;

    const metricsSheet = workbook.addWorksheet('Metrics');
    metricsSheet.columns = [
      { header: 'Metric ID', key: 'metricId', width: 28 },
      { header: 'Metric Name', key: 'name', width: 40 },
      { header: 'Function', key: 'function', width: 26 },
      { header: 'Category', key: 'category', width: 18 },
      { header: 'Recommended Tiers', key: 'recommendedTiers', width: 18 },
      { header: 'Metric Statement', key: 'functionStatement', width: 50 },
      { header: 'Description', key: 'description', width: 60 },
      { header: 'Method/Context', key: 'methodContext', width: 60 },
      { header: 'How To Measure', key: 'howToMeasure', width: 60 },
      { header: 'References', key: 'references', width: 40 },
    ];
    metricsSheet.addRows(metricsRows);
    metricsSheet.views = [{ state: 'frozen', ySplit: 1 }];

    const scoringSheet = workbook.addWorksheet('Metric Scoring');
    const scoringColumns = [
      { header: 'Metric ID Lookup', key: 'lookupKey', width: 24 },
      { header: 'Metric ID', key: 'metricId', width: 24 },
      { header: 'Metric Name', key: 'metricName', width: 36 },
      { header: 'Profile Tier', key: 'profileTier', width: 14 },
      { header: 'Curve Set Name', key: 'curveSetName', width: 28 },
      { header: 'Curve ID', key: 'curveId', width: 28 },
      { header: 'Units', key: 'units', width: 16 },
      { header: 'Curve Type', key: 'curveType', width: 14 },
      { header: 'Index Scores As Range', key: 'indexRange', width: 18 },
      { header: 'Axis X Label', key: 'axesXLabel', width: 18 },
      { header: 'Axis Y Label', key: 'axesYLabel', width: 18 },
      { header: 'Layer Name', key: 'layerName', width: 24 },
      { header: 'Point Count', key: 'pointCount', width: 12 },
    ];

    for (let i = 1; i <= maxPoints; i += 1) {
      scoringColumns.push({ header: `Metric Value ${i}`, key: `value_${i}`, width: 16 });
    }
    for (let i = 1; i <= maxPoints; i += 1) {
      scoringColumns.push({ header: `Metric Value ${i} Desc`, key: `value_desc_${i}`, width: 28 });
    }
    for (let i = 1; i <= maxPoints; i += 1) {
      scoringColumns.push({ header: `Metric Index ${i}`, key: `index_${i}`, width: 14 });
    }
    for (let i = 1; i <= maxPoints; i += 1) {
      scoringColumns.push({ header: `Metric Index ${i} Min`, key: `index_min_${i}`, width: 14 });
    }
    for (let i = 1; i <= maxPoints; i += 1) {
      scoringColumns.push({ header: `Metric Index ${i} Max`, key: `index_max_${i}`, width: 14 });
    }
    scoringSheet.columns = scoringColumns;

    const scoringRowsNormalized = scoringRows.map((row) => {
      const nextRow = {
        lookupKey: row.metricId,
        metricId: row.metricId,
        metricName: row.metricName,
        profileTier: row.profileTier,
        curveSetName: row.curveSetName,
        curveId: row.curveId,
        units: row.units,
        curveType: row.curveType,
        indexRange: row.indexRange,
        axesXLabel: row.axesXLabel,
        axesYLabel: row.axesYLabel,
        layerName: row.layerName,
        pointCount: row.points.length,
      };

      const lastPoint = row.points[row.points.length - 1] || null;
      for (let i = 1; i <= maxPoints; i += 1) {
        const point = row.points[i - 1] || null;
        if (point) {
          nextRow[`value_${i}`] = toNumberMaybe(point.x);
          nextRow[`value_desc_${i}`] = point.description || '';
          nextRow[`index_${i}`] = toNumberMaybe(point.y);
          nextRow[`index_min_${i}`] = toNumberMaybe(
            point.yMin !== null && point.yMin !== undefined ? point.yMin : ''
          );
          nextRow[`index_max_${i}`] = toNumberMaybe(
            point.yMax !== null && point.yMax !== undefined ? point.yMax : ''
          );
        } else if (
          row.curveType === 'quantitative' &&
          lastPoint &&
          typeof lastPoint.x === 'number'
        ) {
          // Keep trailing quantitative points monotonic so MATCH(...,1) works.
          nextRow[`value_${i}`] = toNumberMaybe(lastPoint.x);
          nextRow[`value_desc_${i}`] = '';
          nextRow[`index_${i}`] = toNumberMaybe(lastPoint.y);
          nextRow[`index_min_${i}`] = toNumberMaybe(
            lastPoint.yMin !== null && lastPoint.yMin !== undefined ? lastPoint.yMin : ''
          );
          nextRow[`index_max_${i}`] = toNumberMaybe(
            lastPoint.yMax !== null && lastPoint.yMax !== undefined ? lastPoint.yMax : ''
          );
        } else {
          nextRow[`value_${i}`] = '';
          nextRow[`value_desc_${i}`] = '';
          nextRow[`index_${i}`] = '';
          nextRow[`index_min_${i}`] = '';
          nextRow[`index_max_${i}`] = '';
        }
      }
      return nextRow;
    });
    scoringSheet.addRows(scoringRowsNormalized);
    scoringSheet.views = [{ state: 'frozen', ySplit: 1 }];
    scoringSheet.getColumn(1).hidden = true;

    const valueStartCol = 14;
    const valueEndCol = valueStartCol + maxPoints - 1;
    const indexStartCol = valueEndCol + maxPoints + 1;
    const indexEndCol = indexStartCol + maxPoints - 1;
    const lastColLetter = scoringSheet.getColumn(scoringColumns.length).letter;

    return {
      lookupRange: `'Metric Scoring'!$A:$${lastColLetter}`,
      curveTypeColIndex: 8,
      pointCountColIndex: 13,
      valueStartColIndex: valueStartCol,
      valueEndColIndex: valueEndCol,
      indexStartColIndex: indexStartCol,
      indexEndColIndex: indexEndCol,
      maxPoints,
    };
  };

  const buildMetricIndexFormula = ({
    metricValueRef,
    metricIdRef,
    scoringMeta,
  }) => {
    const lookupCol = (index) =>
      `VLOOKUP(${metricIdRef},${scoringMeta.lookupRange},${index},FALSE)`;
    const curveTypeExpr = lookupCol(scoringMeta.curveTypeColIndex);
    const pointCountExpr = lookupCol(scoringMeta.pointCountColIndex);

    const valueExprs = [];
    const indexExprs = [];
    for (let i = 0; i < scoringMeta.maxPoints; i += 1) {
      valueExprs.push(lookupCol(scoringMeta.valueStartColIndex + i));
      indexExprs.push(lookupCol(scoringMeta.indexStartColIndex + i));
    }

    let categoricalExpr = '""';
    for (let i = scoringMeta.maxPoints - 1; i >= 0; i -= 1) {
      const pointNumber = i + 1;
      categoricalExpr =
        `IF(AND(${pointCountExpr}>=${pointNumber},${metricValueRef}=${valueExprs[i]}),` +
        `${indexExprs[i]},${categoricalExpr})`;
    }

    const lastValueExpr = `CHOOSE(${pointCountExpr},${valueExprs.join(',')})`;
    const lastIndexExpr = `CHOOSE(${pointCountExpr},${indexExprs.join(',')})`;
    let segmentExpr = lastIndexExpr;
    for (let i = scoringMeta.maxPoints - 2; i >= 0; i -= 1) {
      const leftValue = valueExprs[i];
      const rightValue = valueExprs[i + 1];
      const leftIndex = indexExprs[i];
      const rightIndex = indexExprs[i + 1];
      const interpolation =
        `IF(${rightValue}=${leftValue},${leftIndex},` +
        `${leftIndex}+((x-${leftValue})*(${rightIndex}-${leftIndex})/(${rightValue}-${leftValue})))`;
      segmentExpr =
        `IF(AND(${pointCountExpr}>${i + 1},x<=${rightValue}),` +
        `${interpolation},${segmentExpr})`;
    }
    const quantitativeExpr =
      `LET(x,VALUE(${metricValueRef}),` +
      `IF(${pointCountExpr}<2,${indexExprs[0]},` +
      `IF(x<=${valueExprs[0]},${indexExprs[0]},` +
      `IF(x>=${lastValueExpr},${lastIndexExpr},${segmentExpr}` +
      `)))` +
      `)`;

    return (
      `IFERROR(` +
      `IF(OR(${metricIdRef}="",${metricValueRef}=""),"",` +
      `IF(LOWER(${curveTypeExpr})="categorical",` +
      `${categoricalExpr},` +
      `${quantitativeExpr}` +
      `)` +
      `),` +
      `""` +
      `)`
    );
  };

  /**
   * Build the ExcelJS workbook and return { buffer, chartMeta }.
   * chartMeta contains the cell references needed by the OOXML chart injection.
   */
  const buildWorkbook = async (ExcelJS, config) => {
    const workbook = new ExcelJS.Workbook();
    workbook.creator = 'STAF Assessment Export';
    workbook.created = new Date();

    const ws = workbook.addWorksheet('Assessment');
    const scoringMeta = addMetricsAndScoringSheets(workbook, config);
    const visibleColumnCount = config.columns.length;
    const helperHeaders = [
      'Function Key',
      'Function Anchor',
      'Suggested Min',
      'Suggested Max',
      'Physical Weight',
      'Chemical Weight',
      'Biological Weight',
      'Metric ID',
    ];
    const helperColumnIndexes = {
      functionKey: visibleColumnCount + 1,
      functionAnchor: visibleColumnCount + 2,
      suggestedMin: visibleColumnCount + 3,
      suggestedMax: visibleColumnCount + 4,
      physicalWeight: visibleColumnCount + 5,
      chemicalWeight: visibleColumnCount + 6,
      biologicalWeight: visibleColumnCount + 7,
      metricId: visibleColumnCount + 8,
    };

    // ---- Title area (rows 1-3) ----
    const titleRow = ws.addRow([config.assessmentName || 'Assessment']);
    titleRow.getCell(1).font = { bold: true, size: 14 };
    ws.mergeCells(1, 1, 1, visibleColumnCount);

    const tierRow = ws.addRow([`${tierLabel(config.tier)} Tier Assessment`]);
    tierRow.getCell(1).font = { bold: true, size: 11, color: { argb: 'FF555555' } };
    ws.mergeCells(2, 1, 2, visibleColumnCount);

    const dateRow = ws.addRow([`Exported: ${new Date().toLocaleDateString()}`]);
    dateRow.getCell(1).font = { italic: true, size: 10, color: { argb: 'FF888888' } };
    ws.mergeCells(3, 1, 3, visibleColumnCount);

    // ---- Blank row 4 ----
    ws.addRow([]);

    // ---- Header row (row 5) ----
    const headers = config.columns.map((column) => column.header);
    const headerRow = ws.addRow([...headers, ...helperHeaders]);
    for (let col = 1; col <= visibleColumnCount; col += 1) {
      const cell = headerRow.getCell(col);
      cell.fill = HEADER_FILL;
      cell.font = HEADER_FONT;
      cell.alignment = { horizontal: 'center', vertical: 'middle', wrapText: true };
      cell.border = ALL_BORDERS;
    }

    // Set column widths
    config.columns.forEach((column, index) => {
      ws.getColumn(index + 1).width = column.width || 14;
    });
    Object.values(helperColumnIndexes).forEach((columnNumber) => {
      const column = ws.getColumn(columnNumber);
      column.width = 4;
      column.hidden = true;
    });

    // Freeze panes
    ws.views = [{ state: 'frozen', ySplit: 5, xSplit: 0 }];

    // ---- Data rows (rows 6+) ----
    const dataStartRow = 6;
    const discColIndex = config.columns.findIndex((column) => column.key === 'discipline');
    const funcColIndex = config.columns.findIndex((column) => column.key === 'function');
    const metricColIndex = config.columns.findIndex((column) => column.key === 'metric');
    const metricValueColIndex = config.columns.findIndex((column) => column.key === 'metricValue');
    const metricIndexColIndex = config.columns.findIndex((column) => column.key === 'metricIndex');
    const functionEstimateColIndex = config.columns.findIndex(
      (column) => column.key === 'functionEstimate'
    );
    const functionScoreColIndex = config.columns.findIndex(
      (column) => column.key === 'functionScore'
    );
    const suggestedRangeColIndex = config.columns.findIndex(
      (column) => column.key === 'suggestedRange'
    );
    const labelColIndex = config.columns.findIndex(
      (column) => column.key === 'functionScoreLabel'
    );
    const physicalColIndex = config.columns.findIndex((column) => column.key === 'physical');
    const chemicalColIndex = config.columns.findIndex((column) => column.key === 'chemical');
    const biologicalColIndex = config.columns.findIndex((column) => column.key === 'biological');

    const dataRows = Array.isArray(config.rows) ? config.rows : [];
    const groupsByRowIndex = new Array(dataRows.length);
    const functionGroups = [];

    let currentGroup = null;
    let previousGroupKey = null;
    dataRows.forEach((row, rowIndex) => {
      const groupKey = `${String(row?.discipline || '').trim()}||${String(row?.function || '').trim()}`;
      if (!currentGroup || groupKey !== previousGroupKey) {
        currentGroup = {
          key: groupKey,
          startIndex: rowIndex,
          endIndex: rowIndex,
          anchorRow: dataStartRow + rowIndex,
          discipline: row?.discipline || '',
          functionName: row?.function || '',
          initialScore: clampNumber(row?.functionScore, 0, 15, 0),
          suggestedRange: parseSuggestedRange(row),
          physicalCode: row?.physical || '',
          chemicalCode: row?.chemical || '',
          biologicalCode: row?.biological || '',
        };
        functionGroups.push(currentGroup);
        previousGroupKey = groupKey;
      } else {
        currentGroup.endIndex = rowIndex;
        if (!currentGroup.suggestedRange) {
          currentGroup.suggestedRange = parseSuggestedRange(row);
        }
      }
      groupsByRowIndex[rowIndex] = currentGroup;
    });

    const fsColLetter =
      functionScoreColIndex >= 0 ? ws.getColumn(functionScoreColIndex + 1).letter : null;
    const suggestedMinColLetter = ws.getColumn(helperColumnIndexes.suggestedMin).letter;
    const suggestedMaxColLetter = ws.getColumn(helperColumnIndexes.suggestedMax).letter;

    dataRows.forEach((row, rowIndex) => {
      const group = groupsByRowIndex[rowIndex];
      const rowNumber = dataStartRow + rowIndex;
      const isAnchor = Boolean(group && rowIndex === group.startIndex);
      const range = group?.suggestedRange || null;

      const visibleValues = config.columns.map((column) => {
        const value = row[column.key];
        return value !== undefined && value !== null ? value : '';
      });
      if (functionScoreColIndex >= 0) {
        visibleValues[functionScoreColIndex] = '';
      }
      if (suggestedRangeColIndex >= 0) {
        visibleValues[suggestedRangeColIndex] = '';
      }
      if (labelColIndex >= 0) {
        visibleValues[labelColIndex] = '';
      }

      const helperValues = [
        group?.key || '',
        isAnchor ? 1 : 0,
        range ? Math.round(range.min) : '',
        range ? Math.round(range.max) : '',
        isAnchor ? weightForMappingCode(group?.physicalCode) : 0,
        isAnchor ? weightForMappingCode(group?.chemicalCode) : 0,
        isAnchor ? weightForMappingCode(group?.biologicalCode) : 0,
        '',
      ];

      const excelRow = ws.addRow([...visibleValues, ...helperValues]);
      const disciplineCellFill = disciplineFill(row.discipline);
      for (let col = 1; col <= visibleColumnCount; col += 1) {
        const cell = excelRow.getCell(col);
        cell.border = ALL_BORDERS;
        cell.alignment = { vertical: 'middle', wrapText: true };
        if (disciplineCellFill) {
          cell.fill = disciplineCellFill;
        }
      }

      // Bold discipline and function cells
      if (discColIndex >= 0) {
        excelRow.getCell(discColIndex + 1).font = { bold: true };
      }
      if (funcColIndex >= 0) {
        excelRow.getCell(funcColIndex + 1).font = { bold: true };
      }

      // Hidden Metric ID helper formula resolved from metric name on the Metrics sheet.
      const metricIdCell = excelRow.getCell(helperColumnIndexes.metricId);
      const metricNameColLetter =
        metricColIndex >= 0 ? ws.getColumn(metricColIndex + 1).letter : null;
      const fallbackMetricId = String(
        row?._exportMeta?.metricId || row?.metricId || ''
      ).trim();
      if (metricNameColLetter) {
        const metricIdFormula =
          `IFERROR(VLOOKUP(${metricNameColLetter}${rowNumber},'Metrics'!$B:$A,2,FALSE),"")`;
        metricIdCell.value = {
          formula: metricIdFormula,
          result: fallbackMetricId,
        };
      } else {
        metricIdCell.value = fallbackMetricId;
      }
      metricIdCell.alignment = { horizontal: 'center', vertical: 'middle' };

      // Number format for metric index
      if (metricIndexColIndex >= 0) {
        const metricIndexCell = excelRow.getCell(metricIndexColIndex + 1);
        const staticMetricIndex = toScoreNumber(row?.metricIndex);
        const metricValueColLetter =
          metricValueColIndex >= 0 ? ws.getColumn(metricValueColIndex + 1).letter : null;
        const metricIdColLetter = ws.getColumn(helperColumnIndexes.metricId).letter;
        if (metricValueColLetter) {
          const metricValueRef = `${metricValueColLetter}${rowNumber}`;
          const metricIdRef = `${metricIdColLetter}${rowNumber}`;
          metricIndexCell.value = {
            formula: buildMetricIndexFormula({
              metricValueRef,
              metricIdRef,
              scoringMeta,
            }),
            result: staticMetricIndex !== null ? staticMetricIndex : '',
          };
        } else if (staticMetricIndex !== null) {
          metricIndexCell.value = staticMetricIndex;
        }
        metricIndexCell.numFmt = '0.00';
        metricIndexCell.alignment = { horizontal: 'center', vertical: 'middle' };
      }

      if (functionEstimateColIndex >= 0) {
        excelRow.getCell(functionEstimateColIndex + 1).alignment = {
          horizontal: 'center',
          vertical: 'middle',
          wrapText: true,
        };
      }
      [physicalColIndex, chemicalColIndex, biologicalColIndex].forEach((columnIndex) => {
        if (columnIndex >= 0) {
          excelRow.getCell(columnIndex + 1).alignment = {
            horizontal: 'center',
            vertical: 'middle',
            wrapText: true,
          };
        }
      });

      // Function score input is editable for anchor rows only.
      if (functionScoreColIndex >= 0) {
        const scoreCell = excelRow.getCell(functionScoreColIndex + 1);
        scoreCell.fill = { type: 'pattern', pattern: 'solid', fgColor: COLORS.white };
        scoreCell.font = { bold: true };
        scoreCell.alignment = { horizontal: 'center', vertical: 'middle' };
        if (isAnchor) {
          const scoreValue = clampNumber(group?.initialScore, 0, 15, 0);
          scoreCell.value = scoreValue;
          scoreCell.numFmt = '0';
          scoreCell.dataValidation = {
            type: 'whole',
            operator: 'between',
            allowBlank: false,
            formulae: [0, 15],
            showErrorMessage: true,
            errorTitle: 'Invalid Function Score',
            error: 'Enter an integer from 0 to 15.',
          };
        } else {
          scoreCell.value = '';
        }
      }

      if (suggestedRangeColIndex >= 0) {
        const rangeCell = excelRow.getCell(suggestedRangeColIndex + 1);
        rangeCell.fill = { type: 'pattern', pattern: 'solid', fgColor: COLORS.white };
        rangeCell.alignment = { horizontal: 'center', vertical: 'middle' };
        if (isAnchor) {
          const suggestedRangeFormula = `IF(OR(${suggestedMinColLetter}${rowNumber}="",${suggestedMaxColLetter}${rowNumber}=""),"",TEXT(${suggestedMinColLetter}${rowNumber},"0")&"-"&TEXT(${suggestedMaxColLetter}${rowNumber},"0"))`;
          rangeCell.value = {
            formula: suggestedRangeFormula,
            result: range ? `${Math.round(range.min)}-${Math.round(range.max)}` : '',
          };
        } else {
          rangeCell.value = '';
        }
      }

      if (labelColIndex >= 0) {
        const labelCell = excelRow.getCell(labelColIndex + 1);
        labelCell.fill = { type: 'pattern', pattern: 'solid', fgColor: COLORS.white };
        labelCell.alignment = { horizontal: 'center', vertical: 'middle' };
        labelCell.font = { bold: true };
        if (isAnchor && fsColLetter) {
          const labelFormula =
            `IF(${fsColLetter}${rowNumber}="","",` +
            `IF(${fsColLetter}${rowNumber}<=5,"NF",` +
            `IF(${fsColLetter}${rowNumber}<=10,"AR","F")))`;
          labelCell.value = {
            formula: labelFormula,
            result: functionScoreBandLabel(clampNumber(group?.initialScore, 0, 15, 0)),
          };
        } else {
          labelCell.value = '';
        }
      }
    });

    const dataEndRow = dataRows.length > 0 ? dataStartRow + dataRows.length - 1 : dataStartRow;

    // Merge consecutive discipline cells vertically.
    if (discColIndex >= 0 && dataRows.length > 1) {
      let mergeStartRow = dataStartRow;
      for (let i = 1; i <= dataRows.length; i += 1) {
        const previousDiscipline = dataRows[i - 1]?.discipline;
        const currentDiscipline = i < dataRows.length ? dataRows[i]?.discipline : null;
        if (currentDiscipline !== previousDiscipline) {
          const mergeEndRow = dataStartRow + i - 1;
          if (mergeEndRow > mergeStartRow) {
            ws.mergeCells(mergeStartRow, discColIndex + 1, mergeEndRow, discColIndex + 1);
            ws.getCell(mergeStartRow, discColIndex + 1).alignment = {
              vertical: 'middle',
              horizontal: 'center',
              wrapText: true,
            };
          }
          mergeStartRow = dataStartRow + i;
        }
      }
    }

    // Merge function-related columns to match the row-spanned widget behavior.
    functionGroups.forEach((group) => {
      const startRow = group.anchorRow;
      const endRow = dataStartRow + group.endIndex;
      if (endRow <= startRow) return;
      [
        funcColIndex,
        functionScoreColIndex,
        suggestedRangeColIndex,
        labelColIndex,
      ].forEach((columnIndex) => {
        if (columnIndex < 0) return;
        ws.mergeCells(startRow, columnIndex + 1, endRow, columnIndex + 1);
        ws.getCell(startRow, columnIndex + 1).alignment = {
          horizontal: 'center',
          vertical: 'middle',
          wrapText: true,
        };
      });
    });

    if (dataRows.length > 0 && fsColLetter) {
      const scoreRangeRef = `${fsColLetter}${dataStartRow}:${fsColLetter}${dataEndRow}`;
      addFunctionScoreConditionalFormatting(
        ws,
        scoreRangeRef,
        `$${fsColLetter}${dataStartRow}`
      );
      if (labelColIndex >= 0) {
        const labelColLetter = ws.getColumn(labelColIndex + 1).letter;
        addFunctionScoreConditionalFormatting(
          ws,
          `${labelColLetter}${dataStartRow}:${labelColLetter}${dataEndRow}`,
          `$${fsColLetter}${dataStartRow}`
        );
      }
    }

    // ---- Roll-up section ----
    ws.addRow([]);
    let rollupRows = null;
    if (config.rollupData) {
      const physicalCol = physicalColIndex >= 0 ? physicalColIndex + 1 : 11;
      const chemicalCol = chemicalColIndex >= 0 ? chemicalColIndex + 1 : 12;
      const biologicalCol = biologicalColIndex >= 0 ? biologicalColIndex + 1 : 13;
      const outcomeCols = [physicalCol, chemicalCol, biologicalCol];
      const outcomeLetters = outcomeCols.map((col) => ws.getColumn(col).letter);
      const rollupLabelEndCol = Math.max(1, Math.min(...outcomeCols) - 1);
      const anchorColLetter = ws.getColumn(helperColumnIndexes.functionAnchor).letter;
      const physicalWeightColLetter = ws.getColumn(helperColumnIndexes.physicalWeight).letter;
      const chemicalWeightColLetter = ws.getColumn(helperColumnIndexes.chemicalWeight).letter;
      const biologicalWeightColLetter = ws.getColumn(helperColumnIndexes.biologicalWeight).letter;
      const physicalCodeLetter = ws.getColumn(physicalCol).letter;
      const chemicalCodeLetter = ws.getColumn(chemicalCol).letter;
      const biologicalCodeLetter = ws.getColumn(biologicalCol).letter;

      const dataHasRows = dataRows.length > 0;
      const fsDataRange = dataHasRows
        ? `$${fsColLetter}$${dataStartRow}:$${fsColLetter}$${dataEndRow}`
        : null;
      const anchorDataRange = dataHasRows
        ? `$${anchorColLetter}$${dataStartRow}:$${anchorColLetter}$${dataEndRow}`
        : null;
      const physicalCodeRange = dataHasRows
        ? `$${physicalCodeLetter}$${dataStartRow}:$${physicalCodeLetter}$${dataEndRow}`
        : null;
      const chemicalCodeRange = dataHasRows
        ? `$${chemicalCodeLetter}$${dataStartRow}:$${chemicalCodeLetter}$${dataEndRow}`
        : null;
      const biologicalCodeRange = dataHasRows
        ? `$${biologicalCodeLetter}$${dataStartRow}:$${biologicalCodeLetter}$${dataEndRow}`
        : null;
      const physicalWeightRange = dataHasRows
        ? `$${physicalWeightColLetter}$${dataStartRow}:$${physicalWeightColLetter}$${dataEndRow}`
        : null;
      const chemicalWeightRange = dataHasRows
        ? `$${chemicalWeightColLetter}$${dataStartRow}:$${chemicalWeightColLetter}$${dataEndRow}`
        : null;
      const biologicalWeightRange = dataHasRows
        ? `$${biologicalWeightColLetter}$${dataStartRow}:$${biologicalWeightColLetter}$${dataEndRow}`
        : null;

      const rollupHeaderValues = new Array(visibleColumnCount).fill('');
      rollupHeaderValues[physicalCol - 1] = 'Physical';
      rollupHeaderValues[chemicalCol - 1] = 'Chemical';
      rollupHeaderValues[biologicalCol - 1] = 'Biological';
      const rollupHeaderRow = ws.addRow(rollupHeaderValues);
      outcomeCols.forEach((columnNumber) => {
        const cell = rollupHeaderRow.getCell(columnNumber);
        cell.font = { bold: true, color: COLORS.headerFont };
        cell.fill = HEADER_FILL;
        cell.alignment = { horizontal: 'center', vertical: 'middle' };
        cell.border = ALL_BORDERS;
      });

      const createRollupRow = (label, formulas, results, numFmt) => {
        const values = new Array(visibleColumnCount).fill('');
        values[0] = label;
        const row = ws.addRow(values);
        row.getCell(1).font = { bold: true };
        row.getCell(1).alignment = { horizontal: 'right', vertical: 'middle' };
        if (rollupLabelEndCol >= 2) {
          ws.mergeCells(row.number, 1, row.number, rollupLabelEndCol);
        }
        outcomeCols.forEach((columnNumber, outcomeIndex) => {
          const cell = row.getCell(columnNumber);
          const formula = formulas[outcomeIndex];
          const result = results[outcomeIndex];
          if (formula) {
            cell.value = { formula, result };
          } else {
            cell.value = result;
          }
          cell.border = ALL_BORDERS;
          cell.alignment = { horizontal: 'center', vertical: 'middle' };
          if (numFmt) {
            cell.numFmt = numFmt;
          }
        });
        return row.number;
      };

      const directRowNumber = createRollupRow(
        'Direct Effect Functions',
        dataHasRows
          ? [
            `COUNTIFS(${physicalCodeRange},"D",${anchorDataRange},1)`,
            `COUNTIFS(${chemicalCodeRange},"D",${anchorDataRange},1)`,
            `COUNTIFS(${biologicalCodeRange},"D",${anchorDataRange},1)`,
          ]
          : [null, null, null],
        [
          config.rollupData.physical?.direct ?? 0,
          config.rollupData.chemical?.direct ?? 0,
          config.rollupData.biological?.direct ?? 0,
        ],
        '0'
      );

      const indirectRowNumber = createRollupRow(
        'Indirect Effect Functions',
        dataHasRows
          ? [
            `COUNTIFS(${physicalCodeRange},"i",${anchorDataRange},1)`,
            `COUNTIFS(${chemicalCodeRange},"i",${anchorDataRange},1)`,
            `COUNTIFS(${biologicalCodeRange},"i",${anchorDataRange},1)`,
          ]
          : [null, null, null],
        [
          config.rollupData.physical?.indirect ?? 0,
          config.rollupData.chemical?.indirect ?? 0,
          config.rollupData.biological?.indirect ?? 0,
        ],
        '0'
      );

      const weightedRowNumber = createRollupRow(
        'Weighted Score Total',
        dataHasRows
          ? [
            `SUMPRODUCT(${fsDataRange},${physicalWeightRange})`,
            `SUMPRODUCT(${fsDataRange},${chemicalWeightRange})`,
            `SUMPRODUCT(${fsDataRange},${biologicalWeightRange})`,
          ]
          : [null, null, null],
        [
          config.rollupData.physical?.weighted ?? 0,
          config.rollupData.chemical?.weighted ?? 0,
          config.rollupData.biological?.weighted ?? 0,
        ],
        '0.00'
      );

      const maxRowNumber = createRollupRow(
        'Max Weighted Score',
        dataHasRows
          ? [
            `15*SUM(${physicalWeightRange})`,
            `15*SUM(${chemicalWeightRange})`,
            `15*SUM(${biologicalWeightRange})`,
          ]
          : [null, null, null],
        [
          config.rollupData.physical?.max ?? 0,
          config.rollupData.chemical?.max ?? 0,
          config.rollupData.biological?.max ?? 0,
        ],
        '0.00'
      );

      const subIndexRowNumber = createRollupRow(
        'Outcome Sub-index',
        outcomeLetters.map(
          (columnLetter) =>
            `IF(${columnLetter}${maxRowNumber}>0,${columnLetter}${weightedRowNumber}/${columnLetter}${maxRowNumber},0)`
        ),
        [
          config.rollupData.physical?.subIndex ?? 0,
          config.rollupData.chemical?.subIndex ?? 0,
          config.rollupData.biological?.subIndex ?? 0,
        ],
        '0.00'
      );

      const ecosystemValues = new Array(visibleColumnCount).fill('');
      ecosystemValues[0] = 'Ecosystem Condition Index';
      const ecosystemRow = ws.addRow(ecosystemValues);
      ecosystemRow.getCell(1).font = { bold: true };
      ecosystemRow.getCell(1).alignment = { horizontal: 'right', vertical: 'middle' };
      if (rollupLabelEndCol >= 2) {
        ws.mergeCells(ecosystemRow.number, 1, ecosystemRow.number, rollupLabelEndCol);
      }
      const firstOutcomeCol = outcomeCols[0];
      const lastOutcomeCol = outcomeCols[outcomeCols.length - 1];
      ws.mergeCells(ecosystemRow.number, firstOutcomeCol, ecosystemRow.number, lastOutcomeCol);
      const firstOutcomeLetter = ws.getColumn(firstOutcomeCol).letter;
      const lastOutcomeLetter = ws.getColumn(lastOutcomeCol).letter;
      ecosystemRow.getCell(firstOutcomeCol).value = {
        formula: `AVERAGE(${firstOutcomeLetter}${subIndexRowNumber}:${lastOutcomeLetter}${subIndexRowNumber})`,
        result: config.rollupData.ecosystemConditionIndex ?? 0,
      };
      ecosystemRow.getCell(firstOutcomeCol).numFmt = '0.00';
      ecosystemRow.getCell(firstOutcomeCol).alignment = {
        horizontal: 'center',
        vertical: 'middle',
      };
      ecosystemRow.getCell(firstOutcomeCol).border = ALL_BORDERS;

      rollupRows = {
        direct: directRowNumber,
        indirect: indirectRowNumber,
        weighted: weightedRowNumber,
        max: maxRowNumber,
        subIndex: subIndexRowNumber,
        ecosystem: ecosystemRow.number,
        outcomeCols,
      };

      addIndexConditionalFormatting(
        ws,
        `${firstOutcomeLetter}${subIndexRowNumber}:${lastOutcomeLetter}${subIndexRowNumber}`,
        `${firstOutcomeLetter}${subIndexRowNumber}`
      );
      addIndexConditionalFormatting(
        ws,
        `${firstOutcomeLetter}${ecosystemRow.number}:${firstOutcomeLetter}${ecosystemRow.number}`,
        `${firstOutcomeLetter}${ecosystemRow.number}`
      );
    }

    // ---- Chart data area (Function Scores) ----
    ws.addRow([]);
    ws.addRow([]);
    const chartTitleRow = ws.addRow(['Function Scores']);
    chartTitleRow.getCell(1).font = { bold: true, size: 11 };

    const fnChartHeaderRow = ws.addRow(['Function', 'Score']);
    fnChartHeaderRow.getCell(1).font = { bold: true };
    fnChartHeaderRow.getCell(2).font = { bold: true };
    fnChartHeaderRow.getCell(1).border = ALL_BORDERS;
    fnChartHeaderRow.getCell(2).border = ALL_BORDERS;

    const functionScoreData = functionGroups.map((group) => ({
      name: group.functionName || 'Function',
      discipline: group.discipline || '',
      score: clampNumber(group.initialScore, 0, 15, 0),
      anchorRow: group.anchorRow,
    }));
    if (!functionScoreData.length) {
      functionScoreData.push({
        name: 'No function scores',
        discipline: '',
        score: 0,
        anchorRow: dataStartRow,
      });
    }

    const fnChartDataStart = fnChartHeaderRow.number + 1;
    functionScoreData.forEach((item) => {
      const row = ws.addRow([
        item.name,
        fsColLetter
          ? { formula: `${fsColLetter}${item.anchorRow}`, result: item.score }
          : item.score,
      ]);
      row.getCell(1).border = ALL_BORDERS;
      row.getCell(2).border = ALL_BORDERS;
      row.getCell(2).alignment = { horizontal: 'center', vertical: 'middle' };
      row.getCell(2).numFmt = '0';
    });
    const fnChartDataEnd = fnChartDataStart + functionScoreData.length - 1;
    addFunctionScoreConditionalFormatting(
      ws,
      `B${fnChartDataStart}:B${fnChartDataEnd}`,
      `B${fnChartDataStart}`
    );

    // ---- Chart data area (Condition Indices) ----
    ws.addRow([]);
    const idxTitleRow = ws.addRow(['Condition Indices']);
    idxTitleRow.getCell(1).font = { bold: true, size: 11 };

    const idxHeaderRow = ws.addRow(['Index', 'Value']);
    idxHeaderRow.getCell(1).font = { bold: true };
    idxHeaderRow.getCell(2).font = { bold: true };
    idxHeaderRow.getCell(1).border = ALL_BORDERS;
    idxHeaderRow.getCell(2).border = ALL_BORDERS;

    const summaryFallback = Array.isArray(config.summaryData) ? config.summaryData : [];
    const summaryFallbackByLabel = new Map(
      summaryFallback.map((item) => [item.label, item.value])
    );
    const rollupOutcomeLetters = rollupRows
      ? rollupRows.outcomeCols.map((col) => ws.getColumn(col).letter)
      : ['K', 'L', 'M'];

    const summaryData = [
      {
        label: 'Physical Outcome Sub-index',
        formula: rollupRows
          ? `${rollupOutcomeLetters[0]}${rollupRows.subIndex}`
          : null,
        value:
          config.rollupData?.physical?.subIndex ??
          summaryFallbackByLabel.get('Physical Outcome Sub-index') ??
          summaryFallbackByLabel.get('Physical Sub-index') ??
          0,
      },
      {
        label: 'Chemical Outcome Sub-index',
        formula: rollupRows
          ? `${rollupOutcomeLetters[1]}${rollupRows.subIndex}`
          : null,
        value:
          config.rollupData?.chemical?.subIndex ??
          summaryFallbackByLabel.get('Chemical Outcome Sub-index') ??
          summaryFallbackByLabel.get('Chemical Sub-index') ??
          0,
      },
      {
        label: 'Biological Outcome Sub-index',
        formula: rollupRows
          ? `${rollupOutcomeLetters[2]}${rollupRows.subIndex}`
          : null,
        value:
          config.rollupData?.biological?.subIndex ??
          summaryFallbackByLabel.get('Biological Outcome Sub-index') ??
          summaryFallbackByLabel.get('Biological Sub-index') ??
          0,
      },
      {
        label: 'Ecosystem Condition Index',
        formula: rollupRows
          ? `${rollupOutcomeLetters[0]}${rollupRows.ecosystem}`
          : null,
        value:
          config.rollupData?.ecosystemConditionIndex ??
          summaryFallbackByLabel.get('Ecosystem Condition Index') ??
          0,
      },
    ];

    const idxChartDataStart = idxHeaderRow.number + 1;
    summaryData.forEach((item) => {
      const row = ws.addRow([
        item.label,
        item.formula ? { formula: item.formula, result: item.value } : item.value,
      ]);
      row.getCell(1).border = ALL_BORDERS;
      row.getCell(2).border = ALL_BORDERS;
      row.getCell(2).alignment = { horizontal: 'center', vertical: 'middle' };
      row.getCell(2).numFmt = '0.00';
    });
    const idxChartDataEnd = idxChartDataStart + summaryData.length - 1;
    addIndexConditionalFormatting(
      ws,
      `B${idxChartDataStart}:B${idxChartDataEnd}`,
      `B${idxChartDataStart}`
    );

    const buffer = await workbook.xlsx.writeBuffer();

    return {
      buffer,
      chartMeta: {
        fnCatRange: `Assessment!$A$${fnChartDataStart}:$A$${fnChartDataEnd}`,
        fnValRange: `Assessment!$B$${fnChartDataStart}:$B$${fnChartDataEnd}`,
        fnCount: functionScoreData.length,
        fnItems: functionScoreData.map((item) => ({
          name: item.name,
          score: item.score,
        })),
        idxCatRange: `Assessment!$A$${idxChartDataStart}:$A$${idxChartDataEnd}`,
        idxValRange: `Assessment!$B$${idxChartDataStart}:$B$${idxChartDataEnd}`,
        idxCount: summaryData.length,
        idxItems: summaryData.map((item) => ({
          label: item.label,
          value: item.value,
        })),
        chartAnchorRow: idxChartDataEnd + 2,
      },
    };
  };

  // ---------------------------------------------------------------------------
  // OOXML chart XML generators
  // ---------------------------------------------------------------------------

  const xmlEscape = (str) =>
    String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  const buildDptElements = (items, colorFn) =>
    items
      .map(
        (item, i) =>
          `<c:dPt><c:idx val="${i}"/><c:spPr><a:solidFill><a:srgbClr val="${colorFn(item).rgb}"/></a:solidFill><a:ln><a:noFill/></a:ln></c:spPr></c:dPt>`
      )
      .join('');

  const buildBarChartXml = ({
    title,
    catRange,
    valRange,
    items,
    colorFn,
    maxScale,
    labelKey,
    valueKey,
  }) => {
    const dpts = buildDptElements(items, colorFn);

    // Inline string cache for category labels (renders without "refresh data")
    const strCachePts = items
      .map((item, i) => `<c:pt idx="${i}"><c:v>${xmlEscape(item[labelKey])}</c:v></c:pt>`)
      .join('');

    // Inline numeric cache for values
    const numFmt = maxScale <= 1 ? '0.00' : 'General';
    const numCachePts = items
      .map((item, i) => `<c:pt idx="${i}"><c:v>${item[valueKey]}</c:v></c:pt>`)
      .join('');

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <c:chart>
    <c:title>
      <c:tx>
        <c:rich>
          <a:bodyPr/>
          <a:lstStyle/>
          <a:p>
            <a:pPr><a:defRPr sz="1100" b="1"/></a:pPr>
            <a:r><a:rPr lang="en-US" sz="1100" b="1"/><a:t>${xmlEscape(title)}</a:t></a:r>
          </a:p>
        </c:rich>
      </c:tx>
      <c:overlay val="0"/>
    </c:title>
    <c:autoTitleDeleted val="0"/>
    <c:plotArea>
      <c:layout/>
      <c:barChart>
        <c:barDir val="bar"/>
        <c:grouping val="clustered"/>
        <c:gapWidth val="75"/>
        <c:varyColors val="1"/>
        <c:ser>
          <c:idx val="0"/>
          <c:order val="0"/>
          <c:tx><c:v>${xmlEscape(title)}</c:v></c:tx>
          ${dpts}
          <c:dLbls><c:showLegendKey val="0"/><c:showVal val="1"/><c:showCatName val="0"/><c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/></c:dLbls>
          <c:cat><c:strRef><c:f>${xmlEscape(catRange)}</c:f><c:strCache><c:ptCount val="${items.length}"/>${strCachePts}</c:strCache></c:strRef></c:cat>
          <c:val><c:numRef><c:f>${xmlEscape(valRange)}</c:f><c:numCache><c:formatCode>${numFmt}</c:formatCode><c:ptCount val="${items.length}"/>${numCachePts}</c:numCache></c:numRef></c:val>
        </c:ser>
        <c:axId val="111111111"/>
        <c:axId val="222222222"/>
      </c:barChart>
      <c:catAx>
        <c:axId val="111111111"/>
        <c:scaling><c:orientation val="maxMin"/></c:scaling>
        <c:delete val="0"/>
        <c:axPos val="l"/>
        <c:crossAx val="222222222"/>
        <c:crosses val="autoZero"/>
        <c:auto val="1"/>
        <c:lblAlgn val="ctr"/>
        <c:lblOffset val="100"/>
      </c:catAx>
      <c:valAx>
        <c:axId val="222222222"/>
        <c:scaling><c:orientation val="minMax"/><c:max val="${maxScale}"/><c:min val="0"/></c:scaling>
        <c:delete val="0"/>
        <c:axPos val="b"/>
        <c:numFmt formatCode="${numFmt}" sourceLinked="0"/>
        <c:crossAx val="111111111"/>
        <c:crosses val="autoZero"/>
        <c:crossBetween val="between"/>
      </c:valAx>
    </c:plotArea>
    <c:dispBlanksAs val="gap"/>
    <c:plotVisOnly val="1"/>
  </c:chart>
</c:chartSpace>`;
  };

  const buildDrawingXml = (anchorRow, fnCount, idxCount) => {
    const chart1Top = anchorRow - 1;
    const chart1Bottom = chart1Top + Math.max(fnCount + 2, 10);
    const chart2Top = chart1Bottom + 1;
    const chart2Bottom = chart2Top + Math.max(idxCount + 2, 8);

    return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
           xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
           xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <xdr:twoCellAnchor>
    <xdr:from><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${chart1Top}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
    <xdr:to><xdr:col>9</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${chart1Bottom}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
    <xdr:graphicFrame macro="">
      <xdr:nvGraphicFramePr>
        <xdr:cNvPr id="2" name="Chart 1"/>
        <xdr:cNvGraphicFramePr/>
      </xdr:nvGraphicFramePr>
      <xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>
      <a:graphic>
        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">
          <c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId1"/>
        </a:graphicData>
      </a:graphic>
    </xdr:graphicFrame>
    <xdr:clientData/>
  </xdr:twoCellAnchor>
  <xdr:twoCellAnchor>
    <xdr:from><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${chart2Top}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
    <xdr:to><xdr:col>9</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>${chart2Bottom}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
    <xdr:graphicFrame macro="">
      <xdr:nvGraphicFramePr>
        <xdr:cNvPr id="3" name="Chart 2"/>
        <xdr:cNvGraphicFramePr/>
      </xdr:nvGraphicFramePr>
      <xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>
      <a:graphic>
        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">
          <c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rId2"/>
        </a:graphicData>
      </a:graphic>
    </xdr:graphicFrame>
    <xdr:clientData/>
  </xdr:twoCellAnchor>
</xdr:wsDr>`;
  };

  const DRAWING_RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart2.xml"/>
</Relationships>`;

  const CHART_RELS_TEMPLATE = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>`;

  // ---------------------------------------------------------------------------
  // Post-processing: inject charts into xlsx via JSZip
  // ---------------------------------------------------------------------------

  const injectCharts = async (JSZip, buffer, chartMeta) => {
    const zip = await JSZip.loadAsync(buffer);

    // 1. Create chart XML files
    const chart1Xml = buildBarChartXml({
      title: 'Function Scores',
      catRange: chartMeta.fnCatRange,
      valRange: chartMeta.fnValRange,
      items: chartMeta.fnItems,
      colorFn: (item) => functionScoreColor(item.score),
      maxScale: 15,
      labelKey: 'name',
      valueKey: 'score',
    });

    const chart2Xml = buildBarChartXml({
      title: 'Condition Indices',
      catRange: chartMeta.idxCatRange,
      valRange: chartMeta.idxValRange,
      items: chartMeta.idxItems,
      colorFn: (item) => indexColor(item.value),
      maxScale: 1,
      labelKey: 'label',
      valueKey: 'value',
    });

    zip.file('xl/charts/chart1.xml', chart1Xml);
    zip.file('xl/charts/chart2.xml', chart2Xml);

    // 2. Create drawing XML
    const drawingXml = buildDrawingXml(
      chartMeta.chartAnchorRow,
      chartMeta.fnCount,
      chartMeta.idxCount
    );
    zip.file('xl/drawings/drawing1.xml', drawingXml);

    // 3. Create relationship files
    zip.file('xl/charts/_rels/chart1.xml.rels', CHART_RELS_TEMPLATE);
    zip.file('xl/charts/_rels/chart2.xml.rels', CHART_RELS_TEMPLATE);
    zip.file('xl/drawings/_rels/drawing1.xml.rels', DRAWING_RELS);

    const ensureXmlOverride = (xml, partName, contentType) => {
      if (xml.includes(`PartName="${partName}"`)) {
        return xml;
      }
      const override = `<Override PartName="${partName}" ContentType="${contentType}"/>`;
      return xml.replace('</Types>', `${override}</Types>`);
    };

    const appendRelationship = (relsXml, relationshipXml) => {
      if (relsXml.includes('</Relationships>')) {
        return relsXml.replace('</Relationships>', `${relationshipXml}</Relationships>`);
      }
      return relsXml.replace(
        /<Relationships([^>]*)\/>/,
        `<Relationships$1>${relationshipXml}</Relationships>`
      );
    };

    // 4. Update sheet1 rels to reference drawing1.xml
    const sheet1RelsPath = 'xl/worksheets/_rels/sheet1.xml.rels';
    let sheet1Rels = '';
    if (zip.file(sheet1RelsPath)) {
      sheet1Rels = await zip.file(sheet1RelsPath).async('string');
    }
    if (!sheet1Rels || !sheet1Rels.includes('<Relationships')) {
      sheet1Rels = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>`;
    }
    let drawingRId = null;
    if (!sheet1Rels.includes('drawing1.xml')) {
      const rIdMatches = sheet1Rels.match(/rId(\d+)/g) || [];
      const maxId = rIdMatches.reduce((max, m) => {
        const n = parseInt(m.replace('rId', ''), 10);
        return n > max ? n : max;
      }, 0);
      drawingRId = `rId${maxId + 1}`;
      const drawingRel = `<Relationship Id="${drawingRId}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>`;
      sheet1Rels = appendRelationship(sheet1Rels, drawingRel);
    } else {
      const m = sheet1Rels.match(/Id="(rId\d+)"[^>]*drawing1\.xml/);
      if (m) drawingRId = m[1];
    }
    zip.file(sheet1RelsPath, sheet1Rels);

    // 4b. Add <drawing> element to sheet1.xml so Excel can discover drawing1.xml
    if (drawingRId) {
      let sheet1Xml = await zip.file('xl/worksheets/sheet1.xml').async('string');
      if (!sheet1Xml.includes('<drawing ')) {
        sheet1Xml = sheet1Xml.replace(
          '</worksheet>',
          `<drawing r:id="${drawingRId}"/></worksheet>`
        );
        zip.file('xl/worksheets/sheet1.xml', sheet1Xml);
      }
    }

    // 5. Update [Content_Types].xml with explicit overrides for chart and drawing parts
    const contentTypesPath = '[Content_Types].xml';
    let contentTypes = await zip.file(contentTypesPath).async('string');
    contentTypes = ensureXmlOverride(
      contentTypes,
      '/xl/charts/chart1.xml',
      'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'
    );
    contentTypes = ensureXmlOverride(
      contentTypes,
      '/xl/charts/chart2.xml',
      'application/vnd.openxmlformats-officedocument.drawingml.chart+xml'
    );
    contentTypes = ensureXmlOverride(
      contentTypes,
      '/xl/drawings/drawing1.xml',
      'application/vnd.openxmlformats-officedocument.drawing+xml'
    );
    zip.file(contentTypesPath, contentTypes);

    // 6. Re-zip
    return zip.generateAsync({
      type: 'arraybuffer',
      mimeType: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      compression: 'DEFLATE',
      compressionOptions: { level: 6 },
    });
  };

  // ---------------------------------------------------------------------------
  // Main export entry point
  // ---------------------------------------------------------------------------

  const downloadAssessmentWorkbook = async (config) => {
    const [ExcelJS, JSZip] = await Promise.all([ensureExcelJs(), ensureJSZip()]);

    const { buffer, chartMeta } = await buildWorkbook(ExcelJS, config);

    const finalBuffer = await injectCharts(JSZip, buffer, chartMeta);

    // Trigger download
    const blob = new Blob([finalBuffer], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const stamp = new Date().toISOString().slice(0, 10);
    const tierPart = tierLabel(config.tier);
    const namePart = (config.assessmentName || 'Assessment')
      .replace(/[^a-zA-Z0-9]+/g, '_')
      .replace(/(^_|_$)/g, '');
    link.href = url;
    link.download = `STAF_${tierPart}_Assessment_${namePart}_${stamp}.xlsx`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return finalBuffer;
  };

  // ---------------------------------------------------------------------------
  // Expose on window
  // ---------------------------------------------------------------------------

  window.STAFAssessmentExport = {
    downloadAssessmentWorkbook,
    // Utility re-exports for data collectors
    labelForFunctionScore,
    functionScoreColor,
    indexColor,
  };
})();
