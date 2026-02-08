import { promises as fs } from 'fs';
import path from 'path';
import { MetricDetailSchema } from '../src/lib/metricLibrary/schemas.ts';

const root = process.cwd();
const sourceTsvPath = path.join(
  root,
  'docs',
  'assets',
  'data',
  'screening-metrics-source-rich.tsv'
);
const screeningTsvPath = path.join(
  root,
  'docs',
  'assets',
  'data',
  'screening-metrics.tsv'
);
const metricsDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'metrics');
const curvesDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'curves');
const functionsPath = path.join(root, 'docs', 'assets', 'data', 'functions.json');

const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
const normalizeText = (value: string) =>
  (value || '')
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const normalizeHeader = (value: string) => value.toLowerCase().replace(/\s+/g, ' ').trim();
const compactWhitespace = (value: string) =>
  (value || '')
    .replace(/\r\n/g, '\n')
    .replace(/\t/g, ' ')
    .replace(/[ ]{2,}/g, ' ')
    .trim();
const flattenForTsv = (value: string) => compactWhitespace(value).replace(/\n+/g, ' ').trim();
const toYesNo = (value: string) =>
  /^(yes|true|1|y)$/i.test((value || '').trim()) ? 'Yes' : 'No';
const toBool = (value: string) => toYesNo(value) === 'Yes';
const formatScore = (value: number) => value.toFixed(2).replace(/\.00$/, '.0');

type ParsedRow = Record<string, string>;

type Range = { min: number; max: number };

type DerivedBin = {
  name: string;
  desc: string;
  range: Range;
};

type DerivedCriteria = {
  optimal: DerivedBin;
  suboptimal: DerivedBin;
  marginal: DerivedBin;
  poor: DerivedBin;
  originalBins: {
    bin1: DerivedBin;
    bin2: DerivedBin;
    bin3: DerivedBin;
  };
};

const parseQuotedTsv = (text: string): ParsedRow[] => {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let inQuotes = false;

  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === '"') {
      const next = text[i + 1];
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
      if (char === '\r' && text[i + 1] === '\n') {
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

const getValue = (row: ParsedRow, column: string) => row[normalizeHeader(column)] || '';

const parseRange = (value: string): Range | null => {
  // Range fields use hyphen separators (e.g., "0.70-1.0"), so parse only numeric tokens.
  const matches = (value || '')
    .replace(/[–—]/g, '-')
    .replace(/\s+/g, '')
    .match(/\d+(?:\.\d+)?/g);
  if (!matches || !matches.length) {
    return null;
  }
  if (matches.length === 1) {
    const parsed = Number(matches[0]);
    if (!Number.isFinite(parsed)) {
      return null;
    }
    return { min: parsed, max: parsed };
  }
  const first = Number(matches[0]);
  const second = Number(matches[1]);
  if (!Number.isFinite(first) || !Number.isFinite(second)) {
    return null;
  }
  return {
    min: Math.min(first, second),
    max: Math.max(first, second),
  };
};

const splitFairDescription = (value: string) => {
  const normalized = compactWhitespace(value);
  const parts = normalized.split(/\s+\/\s+/);
  if (parts.length <= 1) {
    return [normalized, normalized];
  }
  const first = compactWhitespace(parts.shift() || normalized);
  const second = compactWhitespace(parts.join(' / ') || first);
  return [first, second];
};

const parseMethodContext = (value: string) => {
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

const deriveCriteria = (row: ParsedRow): DerivedCriteria => {
  const bin1Name = compactWhitespace(getValue(row, 'Bin 1 Name') || 'Poor');
  const bin1Desc = compactWhitespace(getValue(row, 'Bin 1 Desc'));
  const bin1Range = parseRange(getValue(row, 'Bin 1 Recommended Index (0-1.0)')) || {
    min: 0,
    max: 0.29,
  };

  const bin2Name = compactWhitespace(getValue(row, 'Bin 2 Name') || 'Fair');
  const bin2Desc = compactWhitespace(getValue(row, 'Bin 2 Desc'));
  const bin2Range = parseRange(getValue(row, 'Bin 2 Recommended Index (0-1.0)')) || {
    min: 0.3,
    max: 0.69,
  };

  const bin3Name = compactWhitespace(getValue(row, 'Bin 3 Name') || 'Good');
  const bin3Desc = compactWhitespace(getValue(row, 'Bin 3 Desc'));
  const bin3Range = parseRange(getValue(row, 'Bin 3 Recommended Index (0-1.0)')) || {
    min: 0.7,
    max: 1,
  };

  const [suboptimalDesc, marginalDesc] = splitFairDescription(bin2Desc);
  const midpoint = Number(((bin2Range.min + bin2Range.max) / 2).toFixed(2));
  let marginalRange: Range;
  let suboptimalRange: Range;

  if (midpoint <= bin2Range.min || midpoint >= bin2Range.max) {
    marginalRange = { min: bin2Range.min, max: bin2Range.max };
    suboptimalRange = { min: bin2Range.min, max: bin2Range.max };
  } else {
    const upperStart = Number((midpoint + 0.01).toFixed(2));
    marginalRange = {
      min: bin2Range.min,
      max: Number((upperStart - 0.01).toFixed(2)),
    };
    suboptimalRange = {
      min: upperStart,
      max: bin2Range.max,
    };
  }

  return {
    optimal: {
      name: bin3Name,
      desc: bin3Desc,
      range: bin3Range,
    },
    suboptimal: {
      name: bin2Name,
      desc: suboptimalDesc,
      range: suboptimalRange,
    },
    marginal: {
      name: bin2Name,
      desc: marginalDesc,
      range: marginalRange,
    },
    poor: {
      name: bin1Name,
      desc: bin1Desc,
      range: bin1Range,
    },
    originalBins: {
      bin1: { name: bin1Name, desc: bin1Desc, range: bin1Range },
      bin2: { name: bin2Name, desc: bin2Desc, range: bin2Range },
      bin3: { name: bin3Name, desc: bin3Desc, range: bin3Range },
    },
  };
};

const formatCriteriaMarkdown = (bin: DerivedBin) => {
  const rangeText = `${formatScore(bin.range.min)}-${formatScore(bin.range.max)}`;
  const label = bin.name || 'Rating';
  const description = bin.desc || '';
  return `${label}: ${description} (Suggested index range ${rangeText})`.trim();
};

const buildCurveSet = (curveSetId: string, metricId: string, criteria: DerivedCriteria) => {
  const midpoint = (min: number, max: number) => Number(((min + max) / 2).toFixed(2));
  const points = [
    {
      x: 'Optimal',
      description: formatCriteriaMarkdown(criteria.optimal),
      yMax: criteria.optimal.range.max,
      yMin: criteria.optimal.range.min,
      y: midpoint(criteria.optimal.range.min, criteria.optimal.range.max),
    },
    {
      x: 'Suboptimal',
      description: formatCriteriaMarkdown(criteria.suboptimal),
      yMax: criteria.suboptimal.range.max,
      yMin: criteria.suboptimal.range.min,
      y: midpoint(criteria.suboptimal.range.min, criteria.suboptimal.range.max),
    },
    {
      x: 'Marginal',
      description: formatCriteriaMarkdown(criteria.marginal),
      yMax: criteria.marginal.range.max,
      yMin: criteria.marginal.range.min,
      y: midpoint(criteria.marginal.range.min, criteria.marginal.range.max),
    },
    {
      x: 'Poor',
      description: formatCriteriaMarkdown(criteria.poor),
      yMax: criteria.poor.range.max,
      yMin: criteria.poor.range.min,
      y: midpoint(criteria.poor.range.min, criteria.poor.range.max),
    },
  ];

  return {
    schemaVersion: 1,
    curveSetId,
    metricId,
    tier: 'screening',
    name: 'Screening default curve',
    axes: {
      xLabel: 'Metric category',
      yLabel: 'Index score',
    },
    curves: [
      {
        indexRange: true,
        layers: [
          {
            name: 'Screening',
            points,
            id: `${curveSetId}-layer`,
          },
        ],
        xType: 'categorical',
        curveId: `${curveSetId}-default`,
        name: 'Screening',
        units: '',
        activeLayerId: `${curveSetId}-layer`,
      },
    ],
  };
};

const main = async () => {
  await fs.mkdir(metricsDir, { recursive: true });
  await fs.mkdir(curvesDir, { recursive: true });

  const sourceRaw = await fs.readFile(sourceTsvPath, 'utf8');
  const rows = parseQuotedTsv(sourceRaw.replace(/^\ufeff/, ''));
  if (!rows.length) {
    throw new Error(`No rows parsed from ${sourceTsvPath}`);
  }
  const functionsRaw = JSON.parse((await fs.readFile(functionsPath, 'utf8')).replace(/^\ufeff/, ''));
  const functionsList = Array.isArray(functionsRaw) ? functionsRaw : [];

  const functionAliases = new Map<string, string>([
    ['bed composition and bedform dynamics', 'bed composition and large wood'],
  ]);

  const canonicalByName = new Map<
    string,
    { name: string; discipline: string; functionIndex: number; disciplineIndex: number }
  >();
  const disciplineOrder: string[] = [];
  const disciplineIndexByName = new Map<string, number>();
  functionsList.forEach((fn: any, index: number) => {
    const functionName = compactWhitespace(fn?.name || '');
    const disciplineName = compactWhitespace(fn?.category || '');
    if (!functionName) {
      return;
    }
    const disciplineKey = normalizeText(disciplineName);
    if (disciplineKey && !disciplineIndexByName.has(disciplineKey)) {
      disciplineIndexByName.set(disciplineKey, disciplineOrder.length);
      disciplineOrder.push(disciplineName);
    }
    const disciplineIndex = disciplineIndexByName.get(disciplineKey) ?? 9999;
    const record = { name: functionName, discipline: disciplineName, functionIndex: index, disciplineIndex };
    canonicalByName.set(normalizeText(functionName), record);
    canonicalByName.set(normalizeText(String(fn?.id || '').replace(/-/g, ' ')), record);
  });
  functionAliases.forEach((target, alias) => {
    const targetRecord = canonicalByName.get(normalizeText(target));
    if (targetRecord) {
      canonicalByName.set(normalizeText(alias), targetRecord);
    }
  });

  const sourceOrder = new Map<ParsedRow, number>(rows.map((row, index) => [row, index]));
  const resolveFunctionMeta = (row: ParsedRow) => {
    const rawFunctionName = compactWhitespace(getValue(row, 'Mapped Function'));
    const rawDisciplineName = compactWhitespace(getValue(row, 'Discipline'));
    const aliasTarget = functionAliases.get(rawFunctionName.toLowerCase()) || rawFunctionName;
    const canonical =
      canonicalByName.get(normalizeText(aliasTarget)) ||
      canonicalByName.get(normalizeText(rawFunctionName));
    if (canonical) {
      return {
        functionName: canonical.name,
        discipline: canonical.discipline,
        functionIndex: canonical.functionIndex,
        disciplineIndex: canonical.disciplineIndex,
      };
    }
    const normalizedDiscipline = normalizeText(rawDisciplineName);
    const fallbackDisciplineIndex = disciplineIndexByName.get(normalizedDiscipline);
    return {
      functionName: rawFunctionName,
      discipline: rawDisciplineName,
      functionIndex: 9999,
      disciplineIndex: fallbackDisciplineIndex ?? 9999,
    };
  };

  const orderedRows = rows.slice().sort((a, b) => {
    const aMeta = resolveFunctionMeta(a);
    const bMeta = resolveFunctionMeta(b);
    if (aMeta.disciplineIndex !== bMeta.disciplineIndex) {
      return aMeta.disciplineIndex - bMeta.disciplineIndex;
    }
    if (aMeta.functionIndex !== bMeta.functionIndex) {
      return aMeta.functionIndex - bMeta.functionIndex;
    }
    const sourceA = sourceOrder.get(a) ?? 0;
    const sourceB = sourceOrder.get(b) ?? 0;
    return sourceA - sourceB;
  });

  const existingMetricFiles = (await fs.readdir(metricsDir)).filter((name) =>
    name.endsWith('.json')
  );
  const reservedMetricIds = new Set<string>();
  const screeningCurveIdsToDelete = new Set<string>();

  for (const file of existingMetricFiles) {
    const fullPath = path.join(metricsDir, file);
    const parsed = JSON.parse((await fs.readFile(fullPath, 'utf8')).replace(/^\ufeff/, ''));
    const profiles = Array.isArray(parsed.profiles) ? parsed.profiles : [];
    const hasScreening = profiles.some((profile: any) => profile?.tier === 'screening');
    if (hasScreening) {
      profiles.forEach((profile: any) => {
        if (
          profile?.tier === 'screening' &&
          profile?.curveIntegration?.enabled &&
          Array.isArray(profile.curveIntegration.curveSetRefs)
        ) {
          profile.curveIntegration.curveSetRefs.forEach((ref: string) => {
            if (ref) {
              screeningCurveIdsToDelete.add(ref);
            }
          });
        }
      });
      await fs.unlink(fullPath);
      continue;
    }
    if (parsed.metricId) {
      reservedMetricIds.add(parsed.metricId);
    }
  }

  const existingCurveFiles = (await fs.readdir(curvesDir)).filter((name) => name.endsWith('.json'));
  for (const file of existingCurveFiles) {
    const fullPath = path.join(curvesDir, file);
    const parsed = JSON.parse((await fs.readFile(fullPath, 'utf8')).replace(/^\ufeff/, ''));
    if (parsed?.tier === 'screening') {
      await fs.unlink(fullPath);
      continue;
    }
    if (screeningCurveIdsToDelete.has(parsed?.curveSetId)) {
      await fs.unlink(fullPath);
    }
  }

  const allocatedIds = new Set<string>();
  const allocateMetricId = (functionName: string, metricName: string) => {
    const baseId = slugify(`${functionName}-${metricName}`) || slugify(metricName) || 'metric';
    let candidate = baseId;
    if (reservedMetricIds.has(candidate) || allocatedIds.has(candidate)) {
      candidate = `${baseId}-screening`;
    }
    let suffix = 2;
    let unique = candidate;
    while (reservedMetricIds.has(unique) || allocatedIds.has(unique)) {
      unique = `${candidate}-${suffix}`;
      suffix += 1;
    }
    allocatedIds.add(unique);
    return unique;
  };

  const screeningRows: string[][] = [];
  const screeningHeader = [
    'Metric ID',
    'Discipline',
    'Function',
    'Function statement',
    'Metric',
    'Metric statement',
    'Predefined SCS',
    'Context',
    'Method',
    'How to measure',
    'Optimal',
    'Suboptimal',
    'Marginal',
    'Poor',
    'References',
    'Source',
    'Metric Data Source',
    'Bin 1 Name',
    'Bin 1 Desc',
    'Bin 1 Recommended Index (0-1.0)',
    'Bin 2 Name',
    'Bin 2 Desc',
    'Bin 2 Recommended Index (0-1.0)',
    'Bin 3 Name',
    'Bin 3 Desc',
    'Bin 3 Recommended Index (0-1.0)',
    'Recommended Tiers',
    'Original Description',
  ];
  screeningRows.push(screeningHeader);

  for (const row of orderedRows) {
    const functionMeta = resolveFunctionMeta(row);
    const discipline = functionMeta.discipline;
    const functionName = functionMeta.functionName;
    const metricName = compactWhitespace(getValue(row, 'Metric Name'));
    if (!discipline || !functionName || !metricName) {
      continue;
    }

    const functionStatement = compactWhitespace(getValue(row, 'Metric Statement'));
    const metricStatement = compactWhitespace(getValue(row, 'Description'));
    const scsDefaultRaw =
      getValue(row, 'Stream Condition Screening (SCS) Has it On') ||
      getValue(row, 'Stream Condition Screening (SCS) Has it "On"');
    const isPredefined = toBool(scsDefaultRaw);
    const sourceCitation = compactWhitespace(getValue(row, 'Source'));
    const metricDataSource = compactWhitespace(getValue(row, 'Metric Data Source'));
    const recommendedTiersRaw = compactWhitespace(getValue(row, 'Recommended Tiers'));
    const methodContext = parseMethodContext(getValue(row, 'Method/Context'));
    const howToMeasure = compactWhitespace(getValue(row, 'How To Measure'));
    const referencesRaw = getValue(row, 'References');
    const references = referencesRaw
      .split(/;\s*/)
      .map((entry) => compactWhitespace(entry))
      .filter(Boolean);

    const metricId = allocateMetricId(functionName, metricName);
    const criteria = deriveCriteria(row);
    const curveSetId = `curve-${metricId}`;
    const tierTag = 'screening';

    const optimalText = formatCriteriaMarkdown(criteria.optimal);
    const suboptimalText = formatCriteriaMarkdown(criteria.suboptimal);
    const marginalText = formatCriteriaMarkdown(criteria.marginal);
    const poorText = formatCriteriaMarkdown(criteria.poor);

    const detail = {
      schemaVersion: 1,
      metricId,
      name: metricName,
      shortName: '',
      discipline,
      function: functionName,
      functionStatement,
      descriptionMarkdown: metricStatement,
      methodContextMarkdown: methodContext.markdown,
      howToMeasureMarkdown: howToMeasure,
      inputs: [
        {
          label: metricName,
          type: (methodContext.method || 'desktop').toLowerCase(),
          source: methodContext.context || 'Unspecified',
          required: true,
          helpMarkdown: metricDataSource || undefined,
        },
      ],
      profiles: [
        {
          profileId: 'screening-default',
          tier: 'screening',
          status: 'active',
          recommended: isPredefined,
          scoring: {
            type: 'categorical',
            ratingScaleId: 'fourBand',
            output: { kind: 'rating', ratingScaleId: 'fourBand' },
            rubric: {
              levels: [
                {
                  label: 'Optimal',
                  ratingId: 'optimal',
                  criteriaMarkdown: optimalText,
                },
                {
                  label: 'Suboptimal',
                  ratingId: 'suboptimal',
                  criteriaMarkdown: suboptimalText,
                },
                {
                  label: 'Marginal',
                  ratingId: 'marginal',
                  criteriaMarkdown: marginalText,
                },
                {
                  label: 'Poor',
                  ratingId: 'poor',
                  criteriaMarkdown: poorText,
                },
              ],
            },
          },
          curveIntegration: {
            enabled: true,
            curveSetRefs: [curveSetId],
          },
        },
      ],
      references,
      tags: [discipline, functionName, tierTag].filter(Boolean),
      sourceMetadata: {
        importSource: 'screening-metrics-source-rich.tsv',
        sourceCitation,
        scsDefaultOn: isPredefined,
        metricDataSource,
        recommendedTiersRaw,
        originalBins: criteria.originalBins,
      },
    };

    MetricDetailSchema.parse(detail);

    const metricPath = path.join(metricsDir, `${metricId}.json`);
    await fs.writeFile(metricPath, JSON.stringify(detail, null, 2));

    const curveSet = buildCurveSet(curveSetId, metricId, criteria);
    const curvePath = path.join(curvesDir, `${curveSetId}.json`);
    await fs.writeFile(curvePath, JSON.stringify(curveSet, null, 2));

    screeningRows.push(
      [
        metricId,
        discipline,
        functionName,
        functionStatement,
        metricName,
        metricStatement,
        isPredefined ? 'Yes' : 'No',
        methodContext.context,
        methodContext.method,
        howToMeasure,
        optimalText,
        suboptimalText,
        marginalText,
        poorText,
        references.join('; '),
        sourceCitation,
        metricDataSource,
        criteria.originalBins.bin1.name,
        criteria.originalBins.bin1.desc,
        `${formatScore(criteria.originalBins.bin1.range.min)}-${formatScore(
          criteria.originalBins.bin1.range.max
        )}`,
        criteria.originalBins.bin2.name,
        criteria.originalBins.bin2.desc,
        `${formatScore(criteria.originalBins.bin2.range.min)}-${formatScore(
          criteria.originalBins.bin2.range.max
        )}`,
        criteria.originalBins.bin3.name,
        criteria.originalBins.bin3.desc,
        `${formatScore(criteria.originalBins.bin3.range.min)}-${formatScore(
          criteria.originalBins.bin3.range.max
        )}`,
        recommendedTiersRaw,
        metricStatement,
      ].map(flattenForTsv)
    );
  }

  const screeningTsvText =
    screeningRows.map((cells) => cells.join('\t')).join('\n') + '\n';
  await fs.writeFile(screeningTsvPath, screeningTsvText, 'utf8');

  // eslint-disable-next-line no-console
  console.log(
    `Migrated ${screeningRows.length - 1} screening metrics from ${sourceTsvPath}.`
  );
};

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});
