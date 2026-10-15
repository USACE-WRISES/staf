// The Metric Toolbox workbook (Apply STAF's "Metric Toolbox (Excel)" link): the metric library as a
// spreadsheet with a Metrics sheet and a Reference Curves sheet, built from the generated JSON
// (index.json, metrics/*.json, curves/*.json). scripts/buildMetricToolbox.ts writes it, and
// scripts/runMetricLibraryTests.ts checks the committed copy still matches the library.
import path from 'path';
import { promises as fs } from 'fs';
import ExcelJS from 'exceljs';

type Json = Record<string, any>;

export const metricLibraryDir = path.join(
  process.cwd(),
  'docs',
  'assets',
  'data',
  'metric-library'
);

export const toolboxPath = path.join(metricLibraryDir, 'STAF_Metric_Toolbox.xlsx');

const readJson = async (filePath: string) => {
  const raw = await fs.readFile(filePath, 'utf8');
  return JSON.parse(raw.replace(/^﻿/, ''));
};

const toList = (value: unknown) =>
  Array.isArray(value)
    ? value.filter((item) => item !== null && item !== undefined && item !== '').join('; ')
    : '';

const parseScore = (value: unknown) => {
  const parsed = Number.parseFloat(String(value));
  return Number.isFinite(parsed) ? parsed : null;
};

const toNumberMaybe = (value: unknown) => {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  const parsed = Number.parseFloat(String(value));
  if (Number.isFinite(parsed) && String(value).trim() === String(parsed)) {
    return parsed;
  }
  return value;
};

// A point's index as one cell (a value, or "min-max" for a range) plus its min and max.
const resolvePointIndex = (point: Json) => {
  const yMin = parseScore(point?.yMin);
  const yMax = parseScore(point?.yMax);
  const yValue = parseScore(point?.y);
  const minValue = yMin !== null ? yMin : point?.yMin ?? '';
  const maxValue = yMax !== null ? yMax : point?.yMax ?? '';
  const value = yValue !== null ? yValue : point?.y ?? '';
  let range: unknown = '';
  if (minValue !== '' || maxValue !== '') {
    if (minValue !== '' && maxValue !== '') {
      range = `${minValue}-${maxValue}`;
    } else {
      range = minValue !== '' ? String(minValue) : String(maxValue);
    }
  } else if (value !== '') {
    range = value;
  }
  return { range, min: minValue, max: maxValue };
};

// Empty strings become blank cells.
const blankEmpty = (row: Json) =>
  Object.fromEntries(Object.entries(row).map(([key, value]) => [key, value === '' ? null : value]));

const boldFrozenHeader = (sheet: ExcelJS.Worksheet) => {
  sheet.getRow(1).font = { bold: true };
  sheet.views = [{ state: 'frozen', ySplit: 1 }];
};

const loadLibrary = async () => {
  const index = await readJson(path.join(metricLibraryDir, 'index.json'));
  const entries: Json[] = index.metrics || [];
  const details = new Map<string, Json>();
  for (const entry of entries) {
    const ref = entry.detailsRef || `metrics/${entry.metricId}.json`;
    details.set(entry.metricId, await readJson(path.join(metricLibraryDir, ref)));
  }
  return { entries, details };
};

const buildMetricRows = (entries: Json[], details: Map<string, Json>) =>
  entries.map((entry) => {
    const detail = details.get(entry.metricId) || {};
    return {
      metricId: entry.metricId || '',
      name: detail.name || entry.name || '',
      function: detail.function || entry.function || '',
      category: detail.category || entry.category || '',
      recommendedTiers: toList(entry.recommendedTiers || []),
      functionStatement: detail.functionStatement || '',
      description: detail.descriptionMarkdown || '',
      methodContext: detail.methodContextMarkdown || '',
      howToMeasure: detail.howToMeasureMarkdown || '',
      references: toList(detail.references || []),
    };
  });

// One row per curve layer of every curve set a profile scores with.
const buildCurveRows = async (entries: Json[], details: Map<string, Json>) => {
  const rows: Json[] = [];
  let maxPoints = 0;
  for (const entry of entries) {
    const detail = details.get(entry.metricId) || {};
    const profiles: Json[] = Array.isArray(detail.profiles) ? detail.profiles : [];
    for (const profile of profiles) {
      if (!profile?.curveIntegration?.enabled) {
        continue;
      }
      for (const ref of profile.curveIntegration.curveSetRefs || []) {
        const curveSet = await readJson(path.join(metricLibraryDir, 'curves', `${ref}.json`));
        const curves: Json[] = Array.isArray(curveSet.curves) ? curveSet.curves : [];
        for (const curve of curves) {
          const curveData = curve?.data ? curve.data : curve || {};
          const layers: Json[] =
            Array.isArray(curveData.layers) && curveData.layers.length
              ? curveData.layers
              : [
                  {
                    id: curveData.activeLayerId || '',
                    name: curveData.name || '',
                    points: curveData.points || [],
                  },
                ];
          for (const layer of layers) {
            const points: Json[] = Array.isArray(layer.points) ? layer.points : [];
            maxPoints = Math.max(maxPoints, points.length);
            const row: Json = {
              metricId: entry.metricId || '',
              metricName: detail.name || entry.name || '',
              profileTier: profile.tier || '',
              curveSetName: curveSet.name || '',
              curveId: curveData.curveId || curve.curveId || '',
              units: curveData.units || '',
              xType: curveData.xType || '',
              indexRange: !!curveData.indexRange,
              axesXLabel: curveSet.axes?.xLabel || '',
              axesYLabel: curveSet.axes?.yLabel || '',
              layerName: layer.name || '',
            };
            points.forEach((point, i) => {
              const n = i + 1;
              const { range, min, max } = resolvePointIndex(point);
              row[`value_${n}`] = toNumberMaybe(point?.x ?? '');
              row[`value_desc_${n}`] = point?.description ?? '';
              row[`index_${n}`] = range;
              row[`index_min_${n}`] = toNumberMaybe(min);
              row[`index_max_${n}`] = toNumberMaybe(max);
            });
            rows.push(row);
          }
        }
      }
    }
  }
  return { rows, maxPoints };
};

export const buildToolbox = async () => {
  const { entries, details } = await loadLibrary();
  const metricRows = buildMetricRows(entries, details);
  const { rows: curveRows, maxPoints } = await buildCurveRows(entries, details);

  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'STAF Metric Library';

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
  metricsSheet.addRows(metricRows.map(blankEmpty));
  boldFrozenHeader(metricsSheet);

  const curveColumns: Partial<ExcelJS.Column>[] = [
    { header: 'Metric ID', key: 'metricId', width: 24 },
    { header: 'Metric Name', key: 'metricName', width: 36 },
    { header: 'Profile Tier', key: 'profileTier', width: 14 },
    { header: 'Curve Set Name', key: 'curveSetName', width: 28 },
    { header: 'Curve ID', key: 'curveId', width: 28 },
    { header: 'Units', key: 'units', width: 16 },
    { header: 'Curve Type', key: 'xType', width: 14 },
    { header: 'Index Scores As Range', key: 'indexRange', width: 18 },
    { header: 'Axis X Label', key: 'axesXLabel', width: 18 },
    { header: 'Axis Y Label', key: 'axesYLabel', width: 18 },
    { header: 'Layer Name', key: 'layerName', width: 24 },
  ];
  for (let n = 1; n <= maxPoints; n += 1) {
    curveColumns.push(
      { header: `Metric Value ${n}`, key: `value_${n}`, width: 18 },
      { header: `Metric Value ${n} Desc`, key: `value_desc_${n}`, width: 36 },
      { header: `Metric Index ${n}`, key: `index_${n}`, width: 16 },
      { header: `Metric Index ${n} Min`, key: `index_min_${n}`, width: 16 },
      { header: `Metric Index ${n} Max`, key: `index_max_${n}`, width: 16 }
    );
  }
  const curvesSheet = workbook.addWorksheet('Reference Curves');
  curvesSheet.columns = curveColumns;
  curvesSheet.addRows(curveRows.map(blankEmpty));
  boldFrozenHeader(curvesSheet);

  const buffer = Buffer.from((await workbook.xlsx.writeBuffer()) as ArrayBuffer);
  return { buffer, metricCount: metricRows.length, curveRowCount: curveRows.length };
};

// What a workbook says, as one string: each sheet's name, column widths, frozen panes and cell
// values. Two files with the same snapshot differ only in their timestamps.
export const workbookSnapshot = async (source: Buffer | string) => {
  const workbook = new ExcelJS.Workbook();
  if (typeof source === 'string') {
    await workbook.xlsx.readFile(source);
  } else {
    await workbook.xlsx.load(source as any);
  }
  return JSON.stringify(
    workbook.worksheets.map((sheet) => {
      const rows: unknown[][] = [];
      for (let r = 1; r <= sheet.rowCount; r += 1) {
        const row = sheet.getRow(r);
        const cells: unknown[] = [];
        for (let c = 1; c <= sheet.columnCount; c += 1) {
          const value = row.getCell(c).value;
          cells.push(value === null || value === undefined ? '' : value);
        }
        rows.push(cells);
      }
      return {
        name: sheet.name,
        widths: (sheet.columns || []).map((column) => column.width ?? null),
        views: sheet.views,
        rows,
      };
    })
  );
};
