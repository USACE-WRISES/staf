import { promises as fs } from 'fs';
import path from 'path';
import { CurveSetSchema, MetricDetailSchema } from '../src/lib/metricLibrary/schemas.ts';

const root = process.cwd();
const sourceTsvPath = path.join(
  root,
  'docs',
  'assets',
  'data',
  'detailed-metrics-source-rich.tsv'
);
const detailedTsvPath = path.join(root, 'docs', 'assets', 'data', 'detailed-metrics.tsv');
const siteSourceTsvPath = path.join(
  root,
  'docs',
  '_site',
  'assets',
  'data',
  'detailed-metrics-source-rich.tsv'
);
const siteDetailedTsvPath = path.join(
  root,
  'docs',
  '_site',
  'assets',
  'data',
  'detailed-metrics.tsv'
);
const metricsDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'metrics');
const curvesDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'curves');
const functionsPath = path.join(root, 'docs', 'assets', 'data', 'functions.json');

type ParsedRow = Record<string, string>;

type ParsedMethodContext = {
  context: string;
  method: string;
  markdown: string;
};

type CurvePoint = {
  x: number;
  y: number;
  description: string;
};

type StratificationLayer = {
  name: string;
  stratification: string;
  points: CurvePoint[];
  rawFieldValues: string[];
  rawIndexValues: string[];
  sourceRow: number;
  sourceRows: number[];
  sourceCitation: string;
  sourceCitations: string[];
  signature: string;
};

type FunctionMeta = {
  name: string;
  id: string;
  discipline: string;
  functionStatement: string;
  functionIndex: number;
  disciplineIndex: number;
};

type MetricGroup = {
  key: string;
  sourceOrder: number;
  functionName: string;
  functionId: string;
  functionIndex: number;
  discipline: string;
  disciplineIndex: number;
  functionStatement: string;
  metricName: string;
  metricTier: string;
  sources: string[];
  metricDataSource: string;
  metricStatement: string;
  description: string;
  methodContextRaw: string;
  methodContext: ParsedMethodContext;
  howToMeasure: string;
  references: string[];
  referencesRaw: string[];
  exampleOn: boolean;
  layers: StratificationLayer[];
  sourceRows: number[];
};

const defaultIndexValues = [0, 0.3, 0.7, 1];

const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 96);

const normalizeText = (value: string) =>
  (value || '')
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, '')
    .trim();

const normalizeHeader = (value: string) =>
  (value || '')
    .replace(/["']/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();

const compactWhitespace = (value: string) =>
  (value || '')
    .replace(/\r\n/g, '\n')
    .replace(/\t/g, ' ')
    .replace(/[ ]{2,}/g, ' ')
    .trim();

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const stripSourceSuffixFromMetricName = (metricName: string, sourceCitation: string) => {
  const base = compactWhitespace(metricName);
  const source = compactWhitespace(sourceCitation);
  if (!base || !source) {
    return base;
  }
  const pattern = new RegExp(`\\s*\\(${escapeRegExp(source)}\\)\\s*$`, 'i');
  const stripped = base.replace(pattern, '').trim();
  return stripped || base;
};

const buildLayerName = (sourceCitation: string, stratification: string, fallbackIndex: number) => {
  const source = compactWhitespace(sourceCitation);
  const strat = compactWhitespace(stratification);
  if (source && strat) {
    return normalizeText(source) === normalizeText(strat) ? source : `${source} - ${strat}`;
  }
  if (source) {
    return source;
  }
  if (strat) {
    return strat;
  }
  return fallbackIndex === 1 ? 'Default' : `Stratification ${fallbackIndex}`;
};

const toBooleanOn = (value: string) => /^(on|yes|true|1|y)$/i.test((value || '').trim());

const parseQuotedTsv = (text: string): ParsedRow[] => {
  const rows: string[][] = [];
  let row: string[] = [];
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

  const header = rows[0].map((value) => normalizeHeader(value.replace(/^\ufeff/, '')));
  return rows.slice(1).map((cells) => {
    const parsed: ParsedRow = {};
    header.forEach((key, index) => {
      parsed[key] = (cells[index] || '').trim();
    });
    return parsed;
  });
};

const getValue = (row: ParsedRow, columns: string[]) => {
  for (const column of columns) {
    const normalized = normalizeHeader(column);
    if (row[normalized] !== undefined) {
      return row[normalized];
    }
  }
  return '';
};

const parseMethodContextCell = (value: string): ParsedMethodContext => {
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

const parseIndexNumber = (value: string) => {
  const parsed = Number.parseFloat((value || '').trim());
  return Number.isFinite(parsed) ? parsed : null;
};

const parseFieldNumber = (value: string) => {
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
  if (raw.startsWith('>')) {
    return parsed + 0.0001;
  }
  if (raw.startsWith('<')) {
    return parsed - 0.0001;
  }
  return parsed;
};

const roundScore = (value: number, places = 6) => {
  const factor = 10 ** places;
  return Math.round(value * factor) / factor;
};

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const parseCurvePoints = (row: ParsedRow) => {
  const points: CurvePoint[] = [];
  const rawFieldValues: string[] = [];
  const rawIndexValues: string[] = [];
  const usedX = new Set<string>();

  for (let i = 1; i <= 7; i += 1) {
    const fieldRaw = compactWhitespace(getValue(row, [`Field Value ${i}`]));
    const indexRaw = compactWhitespace(getValue(row, [`Index Value ${i}`]));
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
      x: roundScore(adjustedX),
      y: clamp(y, 0, 1),
      description: fieldRaw && indexRaw ? `Field ${fieldRaw}, Index ${indexRaw}` : '',
    });
  }

  points.sort((a, b) => a.x - b.x);
  return {
    points,
    rawFieldValues,
    rawIndexValues,
  };
};

const ensureCurvePoints = (points: CurvePoint[], rawIndexValues: string[]) => {
  if (points.length >= 2) {
    return points;
  }

  const indexOnlyPoints = rawIndexValues
    .map((value, index) => {
      const parsed = parseIndexNumber(value);
      if (parsed === null) {
        return null;
      }
      return {
        x: index,
        y: clamp(parsed, 0, 1),
        description: '',
      };
    })
    .filter((value): value is CurvePoint => Boolean(value));

  if (indexOnlyPoints.length >= 2) {
    return indexOnlyPoints;
  }

  if (points.length === 1) {
    return [
      points[0],
      {
        x: roundScore(points[0].x + 1),
        y: points[0].y,
        description: '',
      },
    ];
  }

  return defaultIndexValues.map((value, index) => ({
    x: index,
    y: value,
    description: '',
  }));
};

const buildCurveSignature = (points: CurvePoint[]) =>
  points
    .map((point) => `${roundScore(point.x, 6)}:${roundScore(point.y, 6)}`)
    .join('|');

const collapseGroupLayers = (group: MetricGroup) => {
  const layers = Array.isArray(group.layers) ? group.layers : [];
  if (layers.length <= 1 || !group.sources.length) {
    return layers;
  }
  const allSourcesAreSqt =
    group.sources.length > 1 && group.sources.every((source) => /sqt/i.test(source));
  if (!allSourcesAreSqt) {
    return layers;
  }
  const uniqueSignatures = new Set(
    layers.map((layer) => layer.signature || buildCurveSignature(layer.points))
  );
  if (uniqueSignatures.size !== 1) {
    return layers;
  }
  const mergedRows = Array.from(
    new Set(
      layers.flatMap((layer) =>
        Array.isArray(layer.sourceRows) && layer.sourceRows.length
          ? layer.sourceRows
          : [layer.sourceRow]
      )
    )
  ).sort((a, b) => a - b);
  const mergedSources = Array.from(
    new Set(
      layers.flatMap((layer) =>
        Array.isArray(layer.sourceCitations) && layer.sourceCitations.length
          ? layer.sourceCitations
          : [layer.sourceCitation]
      )
    )
  )
    .map((source) => compactWhitespace(source))
    .filter(Boolean);
  const representative = layers[0];
  return [
    {
      ...representative,
      name: 'Multiple SQTs',
      stratification: 'Multiple SQTs',
      sourceRow: mergedRows[0] || representative.sourceRow,
      sourceRows: mergedRows.length ? mergedRows : [representative.sourceRow],
      sourceCitation: mergedSources[0] || representative.sourceCitation,
      sourceCitations: mergedSources.length
        ? mergedSources
        : representative.sourceCitation
          ? [representative.sourceCitation]
          : [],
      signature: representative.signature || buildCurveSignature(representative.points),
    },
  ];
};

const splitReferences = (value: string) =>
  (value || '')
    .split(/;\s*/)
    .map((entry) => compactWhitespace(entry))
    .filter(Boolean);

const pushUnique = (target: string[], values: string[]) => {
  values.forEach((value) => {
    if (value && !target.includes(value)) {
      target.push(value);
    }
  });
};

const extractUnitFromMetricName = (metricName: string) => {
  const match = (metricName || '').match(/\(([^)]+)\)\s*$/);
  return compactWhitespace(match?.[1] || '');
};

const writeIfParentExists = async (filePath: string, content: string) => {
  const dir = path.dirname(filePath);
  try {
    await fs.access(dir);
    await fs.writeFile(filePath, content, 'utf8');
  } catch {
    // Ignore optional mirror writes.
  }
};

const main = async () => {
  await fs.mkdir(metricsDir, { recursive: true });
  await fs.mkdir(curvesDir, { recursive: true });

  const sourceRaw = await fs.readFile(sourceTsvPath, 'utf8');
  const normalizedSourceText = sourceRaw.endsWith('\n') ? sourceRaw : `${sourceRaw}\n`;
  await fs.writeFile(detailedTsvPath, normalizedSourceText, 'utf8');
  await writeIfParentExists(siteSourceTsvPath, normalizedSourceText);
  await writeIfParentExists(siteDetailedTsvPath, normalizedSourceText);

  const rows = parseQuotedTsv(sourceRaw);
  if (!rows.length) {
    throw new Error(`No rows parsed from ${sourceTsvPath}`);
  }

  const functionsRaw = JSON.parse((await fs.readFile(functionsPath, 'utf8')).replace(/^\ufeff/, ''));
  const functionsList = Array.isArray(functionsRaw) ? functionsRaw : [];

  const disciplineOrder: string[] = [];
  const disciplineIndexByName = new Map<string, number>();
  const canonicalByName = new Map<string, FunctionMeta>();

  functionsList.forEach((fn: any, index: number) => {
    const functionName = compactWhitespace(String(fn?.name || ''));
    if (!functionName) {
      return;
    }
    const functionId = compactWhitespace(String(fn?.id || slugify(functionName)));
    const discipline = compactWhitespace(String(fn?.category || ''));
    const functionStatement = compactWhitespace(
      String(fn?.function_statement || fn?.impact_statement || '')
    );

    const disciplineKey = normalizeText(discipline);
    if (disciplineKey && !disciplineIndexByName.has(disciplineKey)) {
      disciplineIndexByName.set(disciplineKey, disciplineOrder.length);
      disciplineOrder.push(discipline);
    }
    const disciplineIndex = disciplineIndexByName.get(disciplineKey) ?? 9999;
    const meta: FunctionMeta = {
      name: functionName,
      id: functionId,
      discipline,
      functionStatement,
      functionIndex: index,
      disciplineIndex,
    };

    canonicalByName.set(normalizeText(functionName), meta);
    canonicalByName.set(normalizeText(functionId.replace(/-/g, ' ')), meta);
  });

  const functionAliases = new Map<string, string>([
    ['bed composition and bedform dynamics', 'bed composition and large wood'],
  ]);
  functionAliases.forEach((target, alias) => {
    const targetMeta = canonicalByName.get(normalizeText(target));
    if (targetMeta) {
      canonicalByName.set(normalizeText(alias), targetMeta);
    }
  });

  const resolveFunctionMeta = (mappedFunction: string, fallbackStatement: string) => {
    const aliasTarget = functionAliases.get(mappedFunction.toLowerCase()) || mappedFunction;
    const canonical =
      canonicalByName.get(normalizeText(aliasTarget)) ||
      canonicalByName.get(normalizeText(mappedFunction));
    if (canonical) {
      return canonical;
    }
    return {
      name: mappedFunction,
      id: slugify(mappedFunction) || 'unknown-function',
      discipline: 'Other',
      functionStatement: fallbackStatement || '',
      functionIndex: 9999,
      disciplineIndex: 9999,
    } satisfies FunctionMeta;
  };

  const reservedMetricIds = new Set<string>();
  const detailedCurveRefsToDelete = new Set<string>();
  let removedDetailedProfiles = 0;
  let removedDetailedOnlyMetrics = 0;
  let updatedMixedMetrics = 0;

  const existingMetricFiles = (await fs.readdir(metricsDir)).filter((name) =>
    name.endsWith('.json')
  );
  for (const file of existingMetricFiles) {
    const fullPath = path.join(metricsDir, file);
    const parsed = JSON.parse((await fs.readFile(fullPath, 'utf8')).replace(/^\ufeff/, ''));
    const profiles = Array.isArray(parsed.profiles) ? parsed.profiles : [];
    const detailedProfiles = profiles.filter((profile: any) => profile?.tier === 'detailed');

    if (!detailedProfiles.length) {
      if (parsed.metricId) {
        reservedMetricIds.add(parsed.metricId);
      }
      continue;
    }

    removedDetailedProfiles += detailedProfiles.length;
    detailedProfiles.forEach((profile: any) => {
      if (profile?.curveIntegration?.enabled && Array.isArray(profile.curveIntegration.curveSetRefs)) {
        profile.curveIntegration.curveSetRefs.forEach((ref: string) => {
          if (ref) {
            detailedCurveRefsToDelete.add(ref);
          }
        });
      }
      if (profile?.scoring?.type === 'curve' && Array.isArray(profile?.scoring?.rubric?.curveSetRefs)) {
        profile.scoring.rubric.curveSetRefs.forEach((ref: string) => {
          if (ref) {
            detailedCurveRefsToDelete.add(ref);
          }
        });
      }
    });

    const remainingProfiles = profiles.filter((profile: any) => profile?.tier !== 'detailed');
    if (!remainingProfiles.length) {
      await fs.unlink(fullPath);
      removedDetailedOnlyMetrics += 1;
      continue;
    }

    const updatedTags = Array.isArray(parsed.tags)
      ? parsed.tags.filter((tag: string) => normalizeText(tag) !== 'detailed')
      : parsed.tags;

    const nextDetail = {
      ...parsed,
      profiles: remainingProfiles,
      tags: updatedTags,
    };
    await fs.writeFile(fullPath, JSON.stringify(nextDetail, null, 2));
    if (nextDetail.metricId) {
      reservedMetricIds.add(nextDetail.metricId);
    }
    updatedMixedMetrics += 1;
  }

  let deletedDetailedCurveFiles = 0;
  const existingCurveFiles = (await fs.readdir(curvesDir)).filter((name) => name.endsWith('.json'));
  for (const file of existingCurveFiles) {
    const fullPath = path.join(curvesDir, file);
    const parsed = JSON.parse((await fs.readFile(fullPath, 'utf8')).replace(/^\ufeff/, ''));
    const curveSetId = String(parsed?.curveSetId || '');
    const isDetailedTierCurve = parsed?.tier === 'detailed';
    const isRemovedDetailedRef = curveSetId && detailedCurveRefsToDelete.has(curveSetId);
    if (!isDetailedTierCurve && !isRemovedDetailedRef) {
      continue;
    }
    await fs.unlink(fullPath);
    deletedDetailedCurveFiles += 1;
  }

  const groups = new Map<string, MetricGroup>();

  rows.forEach((row, index) => {
    const tierRaw = compactWhitespace(getValue(row, ['Metric Tier', 'Tier']));
    if (tierRaw && normalizeText(tierRaw) !== 'detailed') {
      return;
    }

    const metricName = compactWhitespace(getValue(row, ['Metric Name', 'Metric']));
    const mappedFunction = compactWhitespace(getValue(row, ['Mapped Function', 'Function']));
    if (!metricName || !mappedFunction) {
      return;
    }

    const sourceCitation = compactWhitespace(getValue(row, ['Source']));
    const canonicalMetricName = stripSourceSuffixFromMetricName(metricName, sourceCitation);
    const metricDataSource = compactWhitespace(getValue(row, ['Metric Data Source']));
    const metricStatement = compactWhitespace(getValue(row, ['Metric Statement']));
    const description = compactWhitespace(getValue(row, ['Description']));
    const methodContextRaw = getValue(row, ['Method/Context', 'Method']);
    const howToMeasure = compactWhitespace(getValue(row, ['How To Measure', 'How to measure']));
    const referencesRaw = compactWhitespace(getValue(row, ['References']));
    const references = splitReferences(referencesRaw);
    const stratification = compactWhitespace(getValue(row, ['Stratification']));
    const exampleOn = toBooleanOn(
      getValue(row, [
        'Example Detailed Assessment as it "On" (only 1 metric per function)',
        'Example Detailed Assessment as it On (only 1 metric per function)',
      ])
    );
    const parsedMethodContext = parseMethodContextCell(methodContextRaw);
    const parsedCurve = parseCurvePoints(row);
    const functionMeta = resolveFunctionMeta(mappedFunction, metricStatement);

    const groupKey = [
      normalizeText(functionMeta.name),
      normalizeText(canonicalMetricName || metricName),
    ].join('|');

    if (!groups.has(groupKey)) {
      groups.set(groupKey, {
        key: groupKey,
        sourceOrder: index,
        functionName: functionMeta.name,
        functionId: functionMeta.id,
        functionIndex: functionMeta.functionIndex,
        discipline: functionMeta.discipline,
        disciplineIndex: functionMeta.disciplineIndex,
        functionStatement: metricStatement || functionMeta.functionStatement,
        metricName: canonicalMetricName || metricName,
        metricTier: tierRaw || 'Detailed',
        sources: sourceCitation ? [sourceCitation] : [],
        metricDataSource,
        metricStatement,
        description,
        methodContextRaw: compactWhitespace(methodContextRaw),
        methodContext: parsedMethodContext,
        howToMeasure,
        references: [],
        referencesRaw: [],
        exampleOn,
        layers: [],
        sourceRows: [],
      });
    }

    const group = groups.get(groupKey) as MetricGroup;
    if (!group.functionStatement && (metricStatement || functionMeta.functionStatement)) {
      group.functionStatement = metricStatement || functionMeta.functionStatement;
    }
    if (sourceCitation && !group.sources.includes(sourceCitation)) {
      group.sources.push(sourceCitation);
    }
    if (!group.metricDataSource && metricDataSource) {
      group.metricDataSource = metricDataSource;
    }
    if (!group.metricStatement && metricStatement) {
      group.metricStatement = metricStatement;
    }
    if (!group.description && description) {
      group.description = description;
    }
    if (!group.methodContextRaw && methodContextRaw) {
      group.methodContextRaw = compactWhitespace(methodContextRaw);
    }
    if (!group.methodContext.context && parsedMethodContext.context) {
      group.methodContext.context = parsedMethodContext.context;
    }
    if (!group.methodContext.method && parsedMethodContext.method) {
      group.methodContext.method = parsedMethodContext.method;
    }
    if (!group.methodContext.markdown && parsedMethodContext.markdown) {
      group.methodContext.markdown = parsedMethodContext.markdown;
    }
    if (!group.howToMeasure && howToMeasure) {
      group.howToMeasure = howToMeasure;
    }
    if (exampleOn) {
      group.exampleOn = true;
    }

    pushUnique(group.references, references);
    if (referencesRaw && !group.referencesRaw.includes(referencesRaw)) {
      group.referencesRaw.push(referencesRaw);
    }

    const rowNumber = index + 2;
    const layerIndex = group.layers.length + 1;
    const layerName = buildLayerName(sourceCitation, stratification, layerIndex);
    const layerPoints = ensureCurvePoints(parsedCurve.points, parsedCurve.rawIndexValues);
    group.layers.push({
      name: layerName,
      stratification,
      points: layerPoints,
      rawFieldValues: parsedCurve.rawFieldValues,
      rawIndexValues: parsedCurve.rawIndexValues,
      sourceRow: rowNumber,
      sourceRows: [rowNumber],
      sourceCitation,
      sourceCitations: sourceCitation ? [sourceCitation] : [],
      signature: buildCurveSignature(layerPoints),
    });
    group.sourceRows.push(rowNumber);
  });

  const orderedGroups = Array.from(groups.values()).sort((a, b) => {
    if (a.disciplineIndex !== b.disciplineIndex) {
      return a.disciplineIndex - b.disciplineIndex;
    }
    if (a.functionIndex !== b.functionIndex) {
      return a.functionIndex - b.functionIndex;
    }
    if (a.functionName !== b.functionName) {
      return a.functionName.localeCompare(b.functionName);
    }
    if (a.metricName !== b.metricName) {
      return a.metricName.localeCompare(b.metricName);
    }
    return a.sourceOrder - b.sourceOrder;
  });

  const allocatedMetricIds = new Set<string>();
  const allocateMetricId = (group: MetricGroup) => {
    const baseId =
      slugify(`${group.functionName}-${group.metricName}-detailed`) ||
      slugify(`${group.metricName}-detailed`) ||
      `detailed-metric-${allocatedMetricIds.size + 1}`;

    let candidate = baseId;
    let suffix = 2;
    while (reservedMetricIds.has(candidate) || allocatedMetricIds.has(candidate)) {
      candidate = `${baseId}-${suffix}`;
      suffix += 1;
    }
    allocatedMetricIds.add(candidate);
    return candidate;
  };

  let createdDetailedMetrics = 0;
  let createdDetailedCurves = 0;
  let recommendedDetailedMetrics = 0;

  for (const group of orderedGroups) {
    const effectiveLayers = collapseGroupLayers(group);
    const metricId = allocateMetricId(group);
    const curveSetId = `curve-${metricId}`;
    const displayName = group.metricName;
    const unit = extractUnitFromMetricName(group.metricName);

    const layerIds = new Set<string>();
    const layerNames = new Set<string>();
    const layers = effectiveLayers.map((layer, index) => {
      const baseLayerId =
        slugify(`${curveSetId}-${layer.name || `layer-${index + 1}`}`) || `${curveSetId}-layer-${index + 1}`;
      let layerId = baseLayerId;
      let suffix = 2;
      while (layerIds.has(layerId)) {
        layerId = `${baseLayerId}-${suffix}`;
        suffix += 1;
      }
      layerIds.add(layerId);
      const baseLayerName = layer.name || `Layer ${index + 1}`;
      let layerName = baseLayerName;
      let nameSuffix = 2;
      while (layerNames.has(layerName)) {
        layerName = `${baseLayerName} (${nameSuffix})`;
        nameSuffix += 1;
      }
      layerNames.add(layerName);
      return {
        id: layerId,
        name: layerName,
        points: layer.points.map((point) => ({
          x: point.x,
          y: point.y,
          description: point.description,
        })),
        sourceMetadata: {
          stratification: layer.stratification,
          sourceRow: layer.sourceRow,
          sourceRows: layer.sourceRows,
          sourceCitation: layer.sourceCitation,
          sourceCitations: layer.sourceCitations,
          fieldValues: layer.rawFieldValues,
          indexValues: layer.rawIndexValues,
        },
      };
    });

    const detail = {
      schemaVersion: 1,
      metricId,
      name: displayName,
      shortName: group.metricName,
      discipline: group.discipline || 'Other',
      function: group.functionName,
      functionStatement: group.functionStatement || group.metricStatement || '',
      descriptionMarkdown: group.description || group.metricStatement || '',
      methodContextMarkdown: group.methodContext.markdown || group.methodContextRaw,
      howToMeasureMarkdown: group.howToMeasure,
      inputs: [
        {
          label: group.metricName,
          type: (group.methodContext.method || 'desktop').toLowerCase(),
          source: group.methodContext.context || 'Unspecified',
          required: true,
          helpMarkdown: group.metricDataSource || undefined,
        },
      ],
      profiles: [
        {
          profileId: 'detailed-default',
          tier: 'detailed',
          status: 'active',
          recommended: group.exampleOn,
          scoring: {
            type: 'curve',
            scoringMethod: 'reference-curve',
            ratingMapping: 'fourBand',
            rubric: {
              curveSetRefs: [curveSetId],
            },
          },
          curveIntegration: {
            enabled: true,
            curveSetRefs: [curveSetId],
          },
        },
      ],
      references: group.references,
      tags: [group.discipline, group.functionName, 'detailed'].filter(Boolean),
      sourceMetadata: {
        importSource: 'detailed-metrics-source-rich.tsv',
        sourceCitation: group.sources.join('; '),
        sourceCitations: group.sources,
        metricTier: group.metricTier || 'Detailed',
        exampleDetailedAssessmentOn: group.exampleOn,
        mappedFunction: group.functionName,
        metricDataSource: group.metricDataSource,
        metricStatement: group.metricStatement,
        description: group.description,
        methodContextRaw: group.methodContextRaw,
        howToMeasure: group.howToMeasure,
        referencesRaw: group.referencesRaw,
        sourceRows: group.sourceRows,
        stratifications: effectiveLayers.map((layer) => ({
          name: layer.name,
          stratification: layer.stratification,
          sourceRow: layer.sourceRow,
          sourceRows: layer.sourceRows,
          sourceCitation: layer.sourceCitation,
          sourceCitations: layer.sourceCitations,
          fieldValues: layer.rawFieldValues,
          indexValues: layer.rawIndexValues,
        })),
      },
    };

    MetricDetailSchema.parse(detail);

    const curveSet = {
      schemaVersion: 1,
      curveSetId,
      metricId,
      tier: 'detailed',
      name: `${displayName} reference curve`,
      axes: {
        xLabel: group.metricName,
        yLabel: 'Index score',
        xUnit: unit || undefined,
        yUnit: 'index',
      },
      curves: [
        {
          curveId: `${curveSetId}-default`,
          name: 'Detailed reference',
          xType: 'quantitative',
          units: unit,
          indexRange: false,
          layers,
          activeLayerId: layers[0]?.id || null,
        },
      ],
      sourceMetadata: {
        importSource: 'detailed-metrics-source-rich.tsv',
        sourceCitation: group.sources.join('; '),
        sourceCitations: group.sources,
        mappedFunction: group.functionName,
        sourceRows: group.sourceRows,
      },
    };

    CurveSetSchema.parse(curveSet);

    await fs.writeFile(path.join(metricsDir, `${metricId}.json`), JSON.stringify(detail, null, 2));
    await fs.writeFile(path.join(curvesDir, `${curveSetId}.json`), JSON.stringify(curveSet, null, 2));

    createdDetailedMetrics += 1;
    createdDetailedCurves += 1;
    if (group.exampleOn) {
      recommendedDetailedMetrics += 1;
    }
  }

  // eslint-disable-next-line no-console
  console.log(
    [
      `Detailed migration complete from ${sourceTsvPath}.`,
      `Removed detailed profiles: ${removedDetailedProfiles}.`,
      `Removed detailed-only metrics: ${removedDetailedOnlyMetrics}.`,
      `Updated mixed-tier metrics: ${updatedMixedMetrics}.`,
      `Deleted detailed curve files: ${deletedDetailedCurveFiles}.`,
      `Created detailed metrics: ${createdDetailedMetrics}.`,
      `Created detailed curve sets: ${createdDetailedCurves}.`,
      `Recommended (Example On) detailed metrics: ${recommendedDetailedMetrics}.`,
    ].join('\n')
  );
};

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});
