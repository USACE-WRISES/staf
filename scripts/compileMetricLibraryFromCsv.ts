import { promises as fs } from 'fs';
import path from 'path';
import { CurveSetSchema, MetricDetailSchema } from '../src/lib/metricLibrary/schemas.ts';

type Tier = 'screening' | 'rapid' | 'detailed';

type CsvRow = Record<string, string>;

type IndexRange = {
  min: number;
  max: number;
};

type ParsedMethodContext = {
  context: string;
  method: string;
  markdown: string;
};

type StratificationBin = {
  binIndex: number;
  name: string;
  desc: string;
  indexRaw: string;
  range: IndexRange | null;
  midpoint: number | null;
};

type Stratification = {
  stratIndex: number;
  rawName: string;
  type: string;
  indexAsRange: boolean;
  unit: string;
  bins: StratificationBin[];
  sourceName: string;
  stratLabel: string;
  layerName: string;
};

type TierRow = {
  tier: Tier;
  sourceRow: number;
  sourceCitation: string;
  sourceList: string[];
  metricDataSource: string;
  metricStatement: string;
  description: string;
  methodContextRaw: string;
  methodContext: ParsedMethodContext;
  howToMeasure: string;
  references: string[];
  recommended: boolean;
  stratifications: Stratification[];
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
  metricName: string;
  functionName: string;
  functionId: string;
  discipline: string;
  functionStatement: string;
  functionIndex: number;
  disciplineIndex: number;
  sourceOrder: number;
  sourceRows: number[];
  tierRows: Partial<Record<Tier, TierRow>>;
  sourceCitations: Set<string>;
  references: Set<string>;
};

type ScreeningBand = {
  label: string;
  desc: string;
  range: IndexRange;
};

type ScreeningCriteria = {
  good: ScreeningBand;
  fair: ScreeningBand;
  poor: ScreeningBand;
};

type RapidLevelKey = 'sa' | 'a' | 'n' | 'd' | 'sd';

const root = process.cwd();
const metricLibraryDataDir = path.join(root, 'docs', 'assets', 'data', 'metric-library');
const siteDataDir = path.join(root, 'docs', '_site', 'assets', 'data');
const outputDataDir = path.join(root, 'docs', 'assets', 'data');
const metricsDir = path.join(metricLibraryDataDir, 'metrics');
const curvesDir = path.join(metricLibraryDataDir, 'curves');
const indexPath = path.join(metricLibraryDataDir, 'index.json');
const functionsPath = path.join(outputDataDir, 'functions.json');
const ratingScalesPath = path.join(metricLibraryDataDir, 'rating-scales.json');

const screeningTsvPath = path.join(outputDataDir, 'screening-metrics.tsv');
const screeningReferenceCurvesPath = path.join(outputDataDir, 'screening-reference-curves.json');
const rapidIndicatorsPath = path.join(outputDataDir, 'rapid-indicators.tsv');
const rapidCriteriaPath = path.join(outputDataDir, 'rapid-criteria.tsv');
const detailedMetricsPath = path.join(outputDataDir, 'detailed-metrics.tsv');
const screeningSourceRichPath = path.join(outputDataDir, 'screening-metrics-source-rich.tsv');
const detailedSourceRichPath = path.join(outputDataDir, 'detailed-metrics-source-rich.tsv');

const normalizeHeader = (value: string) =>
  (value || '')
    .replace(/^\ufeff/, '')
    .replace(/["']/g, '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();

const normalizeText = (value: string) =>
  (value || '')
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const compactWhitespace = (value: string) =>
  (value || '')
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/[ \t]*\n[ \t]*/g, '\n')
    .trim();

const flattenForSimpleTsv = (value: string) =>
  compactWhitespace(value).replace(/\n+/g, ' ').trim();

const slugify = (value: string) =>
  (value || '')
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 120);

const toBooleanOn = (value: string) => /^(on|yes|true|1|y)$/i.test((value || '').trim());

const formatScore = (value: number) => {
  if (!Number.isFinite(value)) {
    return '';
  }
  const rounded = Number(value.toFixed(2));
  return rounded.toFixed(2).replace(/\.00$/, '.0');
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const parseQuotedDelimited = (text: string, delimiter: string): CsvRow[] => {
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
    if (char === delimiter && !inQuotes) {
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

  if (cell.length > 0 || row.length > 0) {
    row.push(cell);
    if (row.some((entry) => entry.length > 0)) {
      rows.push(row);
    }
  }

  if (!rows.length) {
    return [];
  }

  const header = rows[0].map((value) => normalizeHeader(value));
  return rows.slice(1).map((cells) => {
    const parsed: CsvRow = {};
    header.forEach((key, index) => {
      parsed[key] = (cells[index] || '').trim();
    });
    return parsed;
  });
};

const getRowValue = (row: CsvRow, ...keys: string[]) => {
  for (const key of keys) {
    const value = row[normalizeHeader(key)];
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return String(value).trim();
    }
  }
  return '';
};

const splitList = (value: string) =>
  (value || '')
    .split(';')
    .map((entry) => compactWhitespace(entry))
    .filter(Boolean);

const parseTier = (value: string): Tier | null => {
  const normalized = normalizeText(value || '');
  if (normalized.includes('screening')) {
    return 'screening';
  }
  if (normalized.includes('rapid')) {
    return 'rapid';
  }
  if (normalized.includes('detailed')) {
    return 'detailed';
  }
  return null;
};

const parseNumberToken = (value: string): number | null => {
  const match = (value || '').match(/-?\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }
  const parsed = Number.parseFloat(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
};

const parseIndexRange = (value: string): IndexRange | null => {
  const trimmed = (value || '').trim();
  if (!trimmed) {
    return null;
  }
  const normalized = trimmed.replace(/[–—]/g, '-').replace(/\s+to\s+/gi, '-');

  // Prefer explicit "a-b" parsing first so ranges like "0-0.39" stay positive.
  const explicitRange = normalized.match(
    /^\s*([+-]?\d+(?:\.\d+)?)\s*-\s*([+-]?\d+(?:\.\d+)?)\s*$/
  );
  if (explicitRange) {
    const first = Number.parseFloat(explicitRange[1]);
    const second = Number.parseFloat(explicitRange[2]);
    if (Number.isFinite(first) && Number.isFinite(second)) {
      return { min: Math.min(first, second), max: Math.max(first, second) };
    }
  }

  const matches = normalized.match(/[+-]?\d+(?:\.\d+)?/g);
  if (!matches || !matches.length) {
    return null;
  }
  const numbers = matches
    .map((token) => Number.parseFloat(token))
    .filter((num) => Number.isFinite(num));
  if (!numbers.length) {
    return null;
  }
  if (numbers.length === 1) {
    return { min: numbers[0], max: numbers[0] };
  }
  const first = numbers[0];
  const second = numbers[1];
  return { min: Math.min(first, second), max: Math.max(first, second) };
};

const midpoint = (range: IndexRange | null): number | null => {
  if (!range) {
    return null;
  }
  return Number(((range.min + range.max) / 2).toFixed(3));
};

const parseMethodContextCell = (value: string): ParsedMethodContext => {
  const source = (value || '')
    .replace(/\r\n/g, '\n')
    .replace(/^"+|"+$/g, '')
    .replace(/""/g, '"');
  const contexts = Array.from(
    new Set(
      [...source.matchAll(/Context:\s*([\s\S]*?)(?=\bMethod:|$)/gi)]
        .map((match) => compactWhitespace(match[1] || ''))
        .filter(Boolean)
    )
  );
  const methods = Array.from(
    new Set(
      [...source.matchAll(/Method:\s*([\s\S]*?)(?=\bContext:|$)/gi)]
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

const splitObservationCriteria = (value: string) => {
  const text = compactWhitespace(value);
  if (!text) {
    return { observation: '', criteria: '' };
  }
  const observationMatch = text.match(/Observation:\s*(.+?)(?:\s+Criteria:|$)/i);
  const criteriaMatch = text.match(/Criteria:\s*(.+)$/i);
  return {
    observation: compactWhitespace(observationMatch ? observationMatch[1] : ''),
    criteria: compactWhitespace(criteriaMatch ? criteriaMatch[1] : text),
  };
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

const deriveSourceAndStrat = (
  rawStratification: string,
  sourceList: string[],
  stratificationCount: number
) => {
  const raw = compactWhitespace(rawStratification);
  const sortedSources = sourceList
    .map((source) => compactWhitespace(source))
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);

  let sourceName = '';
  let stratLabel = raw;

  for (const source of sortedSources) {
    if (normalizeText(raw) === normalizeText(source)) {
      sourceName = source;
      stratLabel = '';
      break;
    }
    const suffixPattern = new RegExp(`\\s*-\\s*${escapeRegExp(source)}$`, 'i');
    if (suffixPattern.test(raw)) {
      sourceName = source;
      stratLabel = raw.replace(suffixPattern, '').trim();
      break;
    }
  }

  if (!sourceName) {
    if (sortedSources.length === 1) {
      sourceName = sortedSources[0];
    } else if (sortedSources.length > 1) {
      sourceName = sortedSources.every((entry) => /sqt/i.test(entry))
        ? 'Multiple SQTs'
        : 'Multiple Sources';
    }
  }

  stratLabel = stratLabel.replace(/^default\s*-\s*/i, '').replace(/^default$/i, '').trim();
  if (sourceName && normalizeText(stratLabel) === normalizeText(sourceName)) {
    stratLabel = '';
  }
  if (!stratLabel && !sourceName && stratificationCount > 1) {
    stratLabel = raw || `Stratification ${stratificationCount}`;
  }
  return { sourceName, stratLabel };
};

const parseStratifications = (
  row: CsvRow,
  sourceList: string[],
  tier: Tier
): Stratification[] => {
  const stratifications: Stratification[] = [];

  for (let i = 1; i <= 28; i += 1) {
    const rawName = getRowValue(row, `Stratification ${i}`);
    const type = compactWhitespace(getRowValue(row, `Stratification ${i} Type`));
    const unit = compactWhitespace(getRowValue(row, `Stratification ${i} metric unit`));
    const indexAsRange = toBooleanOn(getRowValue(row, `Stratification ${i} Index as range?`));
    const bins: StratificationBin[] = [];

    for (let binIndex = 1; binIndex <= 6; binIndex += 1) {
      const name = compactWhitespace(
        getRowValue(row, `Stratification ${i} Bin ${binIndex} Name`)
      );
      const desc = compactWhitespace(
        getRowValue(row, `Stratification ${i} Bin ${binIndex} Desc`)
      );
      const indexRaw = compactWhitespace(
        getRowValue(row, `Stratification ${i} Bin ${binIndex} Recommended Index (0-1.0)`)
      );
      if (!name && !desc && !indexRaw) {
        continue;
      }
      const range = parseIndexRange(indexRaw);
      bins.push({
        binIndex,
        name,
        desc,
        indexRaw,
        range,
        midpoint: midpoint(range),
      });
    }

    if (!rawName && !type && !unit && !bins.length) {
      continue;
    }

    const derived = deriveSourceAndStrat(rawName, sourceList, stratifications.length + 1);
    const layerName = buildLayerName(
      derived.sourceName,
      derived.stratLabel,
      stratifications.length + 1
    );

    stratifications.push({
      stratIndex: i,
      rawName,
      type: type || tier,
      indexAsRange,
      unit,
      bins,
      sourceName: derived.sourceName,
      stratLabel: derived.stratLabel,
      layerName,
    });
  }

  if (stratifications.length > 0) {
    return stratifications;
  }

  if (tier === 'screening') {
    const fallbackBins: StratificationBin[] = [];
    for (let binIndex = 1; binIndex <= 3; binIndex += 1) {
      const name = compactWhitespace(getRowValue(row, `Bin ${binIndex} Name`));
      const desc = compactWhitespace(getRowValue(row, `Bin ${binIndex} Desc`));
      const indexRaw = compactWhitespace(
        getRowValue(row, `Bin ${binIndex} Recommended Index (0-1.0)`)
      );
      if (!name && !desc && !indexRaw) {
        continue;
      }
      const range = parseIndexRange(indexRaw);
      fallbackBins.push({
        binIndex,
        name,
        desc,
        indexRaw,
        range,
        midpoint: midpoint(range),
      });
    }
    if (fallbackBins.length > 0) {
      const sourceName =
        sourceList.length > 1 && sourceList.every((entry) => /sqt/i.test(entry))
          ? 'Multiple SQTs'
          : sourceList[0] || '';
      return [
        {
          stratIndex: 1,
          rawName: 'Default',
          type: 'categorical',
          indexAsRange: true,
          unit: '',
          bins: fallbackBins,
          sourceName,
          stratLabel: '',
          layerName: buildLayerName(sourceName, '', 1),
        },
      ];
    }
  }

  return [];
};

const deriveScreeningCriteria = (stratification: Stratification): ScreeningCriteria => {
  const defaultPoor: ScreeningBand = {
    label: 'Poor',
    desc: '',
    range: { min: 0, max: 0.39 },
  };
  const defaultFair: ScreeningBand = {
    label: 'Fair',
    desc: '',
    range: { min: 0.4, max: 0.69 },
  };
  const defaultGood: ScreeningBand = {
    label: 'Good',
    desc: '',
    range: { min: 0.7, max: 1 },
  };

  const bins = stratification.bins
    .filter((bin) => bin.range !== null || bin.name || bin.desc)
    .map((bin) => ({
      ...bin,
      range:
        bin.range ||
        (bin.binIndex === 1
          ? defaultPoor.range
          : bin.binIndex === 2
          ? defaultFair.range
          : defaultGood.range),
    }));

  if (!bins.length) {
    return {
      good: defaultGood,
      fair: defaultFair,
      poor: defaultPoor,
    };
  }

  const sortedByMidpoint = bins
    .slice()
    .sort((a, b) => (b.midpoint ?? 0) - (a.midpoint ?? 0));

  const findByPattern = (pattern: RegExp) =>
    bins.find((bin) => pattern.test(normalizeText(bin.name)));

  const goodBin =
    findByPattern(/\bgood\b|\boptimal\b/) ||
    sortedByMidpoint[0] ||
    bins[bins.length - 1];
  const poorBin =
    findByPattern(/\bpoor\b|\bnon ?functioning\b/) ||
    sortedByMidpoint[sortedByMidpoint.length - 1] ||
    bins[0];
  const fairCandidates = sortedByMidpoint.filter(
    (bin) => bin !== goodBin && bin !== poorBin
  );
  const fairBin =
    findByPattern(/\bfair\b|\bsuboptimal\b|\bmarginal\b|\bat[- ]?risk\b/) ||
    fairCandidates[0] ||
    (goodBin === bins[0] ? bins[1] : bins[0]) ||
    goodBin;

  return {
    good: {
      label: goodBin.name || 'Good',
      desc: goodBin.desc,
      range: goodBin.range,
    },
    fair: {
      label: fairBin.name || 'Fair',
      desc: fairBin.desc,
      range: fairBin.range,
    },
    poor: {
      label: poorBin.name || 'Poor',
      desc: poorBin.desc,
      range: poorBin.range,
    },
  };
};

const defaultRapidRanges: Record<RapidLevelKey, IndexRange> = {
  sa: { min: 0.84, max: 1 },
  a: { min: 0.64, max: 0.83 },
  n: { min: 0.44, max: 0.63 },
  d: { min: 0.24, max: 0.43 },
  sd: { min: 0, max: 0.23 },
};

const defaultRapidLabels: Record<RapidLevelKey, string> = {
  sa: 'Strongly Agree',
  a: 'Agree',
  n: 'Neutral',
  d: 'Disagree',
  sd: 'Strongly Disagree',
};

const classifyRapidLabel = (label: string): RapidLevelKey | null => {
  const normalized = normalizeText(label);
  if (!normalized) {
    return null;
  }
  if (normalized.includes('strongly agree')) {
    return 'sa';
  }
  if (normalized.includes('strongly disagree')) {
    return 'sd';
  }
  if (normalized === 'agree' || normalized.endsWith(' agree')) {
    return 'a';
  }
  if (normalized === 'disagree' || normalized.endsWith(' disagree')) {
    return 'd';
  }
  if (normalized.includes('neutral')) {
    return 'n';
  }
  return null;
};

const deriveRapidLevels = (stratification: Stratification) => {
  const levels: Record<RapidLevelKey, StratificationBin | null> = {
    sa: null,
    a: null,
    n: null,
    d: null,
    sd: null,
  };

  const bins = stratification.bins.filter(
    (bin) => bin.name || bin.desc || bin.range !== null
  );

  bins.forEach((bin) => {
    const key = classifyRapidLabel(bin.name);
    if (key && !levels[key]) {
      levels[key] = bin;
    }
  });

  const remainingBins = bins.filter((bin) => !Object.values(levels).includes(bin));
  if (remainingBins.length > 0) {
    const sorted = remainingBins
      .slice()
      .sort((a, b) => (b.midpoint ?? 0) - (a.midpoint ?? 0));
    const keysByCount: RapidLevelKey[] =
      sorted.length >= 5
        ? ['sa', 'a', 'n', 'd', 'sd']
        : sorted.length === 4
        ? ['sa', 'a', 'd', 'sd']
        : sorted.length === 3
        ? ['sa', 'n', 'sd']
        : sorted.length === 2
        ? ['sa', 'sd']
        : ['n'];
    keysByCount.forEach((key, index) => {
      if (!levels[key] && sorted[index]) {
        levels[key] = sorted[index];
      }
    });
  }

  return levels;
};

const ratingIdToTierOrder: Tier[] = ['screening', 'rapid', 'detailed'];

const parseFunctionList = async () => {
  const raw = await fs.readFile(functionsPath, 'utf8');
  const list = JSON.parse(raw.replace(/^\ufeff/, '')) as Array<Record<string, unknown>>;
  const disciplineOrder: string[] = [];
  const disciplineIndexByName = new Map<string, number>();
  const byLookup = new Map<string, FunctionMeta>();

  list.forEach((item, index) => {
    const name = compactWhitespace(String(item.name || ''));
    if (!name) {
      return;
    }
    const discipline = compactWhitespace(String(item.category || ''));
    const disciplineKey = normalizeText(discipline);
    if (disciplineKey && !disciplineIndexByName.has(disciplineKey)) {
      disciplineIndexByName.set(disciplineKey, disciplineOrder.length);
      disciplineOrder.push(discipline);
    }
    const meta: FunctionMeta = {
      name,
      id: compactWhitespace(String(item.id || '')),
      discipline,
      functionStatement: compactWhitespace(String(item.function_statement || '')),
      functionIndex: index,
      disciplineIndex: disciplineIndexByName.get(disciplineKey) ?? 9999,
    };
    byLookup.set(normalizeText(name), meta);
    if (meta.id) {
      byLookup.set(normalizeText(meta.id.replace(/-/g, ' ')), meta);
    }
  });

  const aliases = new Map<string, string>([
    [
      normalizeText('Bed composition and bedform dynamics'),
      normalizeText('Bed composition and large wood'),
    ],
  ]);

  aliases.forEach((target, alias) => {
    const targetMatch = byLookup.get(target);
    if (targetMatch) {
      byLookup.set(alias, targetMatch);
    }
  });

  return { byLookup, disciplineIndexByName };
};

const sanitizeTierStratifications = (
  existing: Stratification[],
  incoming: Stratification[]
): Stratification[] => {
  const signature = (strat: Stratification) =>
    [
      normalizeText(strat.layerName),
      normalizeText(strat.sourceName),
      normalizeText(strat.stratLabel),
      ...strat.bins.map((bin) =>
        [
          normalizeText(bin.name),
          normalizeText(bin.desc),
          compactWhitespace(bin.indexRaw),
          bin.range ? `${bin.range.min}:${bin.range.max}` : '',
        ].join('|')
      ),
    ].join('||');

  const merged = [...existing];
  const seen = new Set(existing.map((strat) => signature(strat)));
  incoming.forEach((strat) => {
    const key = signature(strat);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    merged.push(strat);
  });
  return merged;
};

const extractTierRow = (
  row: CsvRow,
  sourceRow: number,
  tier: Tier,
  functionStatement: string
): TierRow => {
  const sourceCitation = compactWhitespace(getRowValue(row, 'Source'));
  const sourceList = splitList(sourceCitation);
  const metricDataSource = compactWhitespace(getRowValue(row, 'Metric Data Source'));
  const metricStatement = compactWhitespace(getRowValue(row, 'Metric Statement'));
  const description = compactWhitespace(getRowValue(row, 'Description'));
  const methodContextRaw = getRowValue(row, 'Method/Context');
  const methodContext = parseMethodContextCell(methodContextRaw);
  const howToMeasure = compactWhitespace(getRowValue(row, 'How To Measure', 'How to Measure'));
  const references = splitList(getRowValue(row, 'References'));
  const recommended =
    tier === 'screening'
      ? toBooleanOn(getRowValue(row, 'Stream Condition Screening (SCS) Has it On'))
      : tier === 'rapid'
      ? toBooleanOn(getRowValue(row, 'SFARI Rapid Assessment has it On'))
      : toBooleanOn(
          getRowValue(
            row,
            'Example Detailed Assessment as it On (only 1 metric per function)',
            'Example Detailed Assessment as it "On" (only 1 metric per function)'
          )
        );
  const stratifications = parseStratifications(row, sourceList, tier);

  return {
    tier,
    sourceRow,
    sourceCitation,
    sourceList,
    metricDataSource,
    metricStatement: metricStatement || functionStatement,
    description: description || metricStatement || '',
    methodContextRaw,
    methodContext,
    howToMeasure,
    references,
    recommended,
    stratifications,
  };
};

const asTsvLine = (values: string[]) =>
  values
    .map((value) => {
      const cell = value ?? '';
      if (/[\"\t\r\n]/.test(cell)) {
        return `"${cell.replace(/"/g, '""')}"`;
      }
      return cell;
    })
    .join('\t');

const buildScreeningCriteriaMarkdown = (band: ScreeningBand) =>
  `${band.label}: ${band.desc}${band.desc ? ' ' : ''}(Suggested index range ${formatScore(
    band.range.min
  )}-${formatScore(band.range.max)})`.trim();

const ensureRange = (range: IndexRange | null, fallback: IndexRange) => range || fallback;

const buildMetricLibraryFromCsv = async () => {
  const csvPath = await resolveCsvPath();
  const rawCsv = await fs.readFile(csvPath, 'utf8');
  const rows = parseQuotedDelimited(rawCsv.replace(/^\ufeff/, ''), ',');
  if (!rows.length) {
    throw new Error(`No rows parsed from ${csvPath}`);
  }

  const { byLookup, disciplineIndexByName } = await parseFunctionList();

  const metricGroups = new Map<string, MetricGroup>();

  rows.forEach((row, rowIndex) => {
    const tier = parseTier(getRowValue(row, 'Recommended Tiers'));
    if (!tier) {
      return;
    }
    const metricName = compactWhitespace(getRowValue(row, 'Metric Name'));
    const rawFunctionName = compactWhitespace(getRowValue(row, 'Mapped Function'));
    if (!metricName || !rawFunctionName) {
      return;
    }

    const resolvedFunction = byLookup.get(normalizeText(rawFunctionName));
    const disciplineFallback = compactWhitespace(getRowValue(row, 'Discipline'));
    const functionName = resolvedFunction?.name || rawFunctionName;
    const functionId = resolvedFunction?.id || slugify(functionName);
    const discipline = resolvedFunction?.discipline || disciplineFallback || 'General';
    const functionStatement =
      resolvedFunction?.functionStatement ||
      compactWhitespace(getRowValue(row, 'Metric Statement'));
    const functionIndex = resolvedFunction?.functionIndex ?? 9999;
    const disciplineIndex =
      resolvedFunction?.disciplineIndex ??
      disciplineIndexByName.get(normalizeText(discipline)) ??
      9999;

    const key = `${normalizeText(functionName)}||${normalizeText(metricName)}`;
    const tierRow = extractTierRow(row, rowIndex + 2, tier, functionStatement);

    if (!metricGroups.has(key)) {
      metricGroups.set(key, {
        key,
        metricName,
        functionName,
        functionId,
        discipline,
        functionStatement,
        functionIndex,
        disciplineIndex,
        sourceOrder: rowIndex,
        sourceRows: [rowIndex + 2],
        tierRows: { [tier]: tierRow },
        sourceCitations: new Set(splitList(tierRow.sourceCitation)),
        references: new Set(tierRow.references),
      });
      return;
    }

    const group = metricGroups.get(key);
    if (!group) {
      return;
    }
    group.sourceRows.push(rowIndex + 2);
    splitList(tierRow.sourceCitation).forEach((source) => group.sourceCitations.add(source));
    tierRow.references.forEach((reference) => group.references.add(reference));
    group.sourceOrder = Math.min(group.sourceOrder, rowIndex);
    group.functionIndex = Math.min(group.functionIndex, functionIndex);
    group.disciplineIndex = Math.min(group.disciplineIndex, disciplineIndex);

    if (!group.tierRows[tier]) {
      group.tierRows[tier] = tierRow;
      return;
    }

    const existing = group.tierRows[tier];
    if (!existing) {
      group.tierRows[tier] = tierRow;
      return;
    }
    existing.sourceRow = Math.min(existing.sourceRow, tierRow.sourceRow);
    existing.sourceCitation = existing.sourceCitation || tierRow.sourceCitation;
    existing.sourceList = Array.from(new Set([...existing.sourceList, ...tierRow.sourceList]));
    existing.metricDataSource = existing.metricDataSource || tierRow.metricDataSource;
    existing.metricStatement = existing.metricStatement || tierRow.metricStatement;
    existing.description = existing.description || tierRow.description;
    existing.methodContextRaw = existing.methodContextRaw || tierRow.methodContextRaw;
    existing.methodContext = existing.methodContextRaw
      ? existing.methodContext
      : tierRow.methodContext;
    existing.howToMeasure = existing.howToMeasure || tierRow.howToMeasure;
    existing.references = Array.from(new Set([...existing.references, ...tierRow.references]));
    existing.recommended = existing.recommended || tierRow.recommended;
    existing.stratifications = sanitizeTierStratifications(
      existing.stratifications,
      tierRow.stratifications
    );
  });

  const sortedGroups = Array.from(metricGroups.values()).sort((a, b) => {
    if (a.disciplineIndex !== b.disciplineIndex) {
      return a.disciplineIndex - b.disciplineIndex;
    }
    if (a.functionIndex !== b.functionIndex) {
      return a.functionIndex - b.functionIndex;
    }
    if (a.sourceOrder !== b.sourceOrder) {
      return a.sourceOrder - b.sourceOrder;
    }
    return a.metricName.localeCompare(b.metricName);
  });

  await fs.mkdir(metricsDir, { recursive: true });
  await fs.mkdir(curvesDir, { recursive: true });
  await cleanJsonDirectory(metricsDir);
  await cleanJsonDirectory(curvesDir);

  const usedMetricIds = new Set<string>();
  const curveSetsById = new Map<string, Record<string, unknown>>();
  const metricDetailsForIndex: Array<Record<string, unknown>> = [];
  const screeningCurveOverrides: Record<string, unknown> = {};

  const screeningRows: string[][] = [
    [
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
      'Good',
      'Fair',
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
    ],
  ];

  const rapidIndicatorRows: string[][] = [
    [
      'Discipline',
      'Functions',
      'Function statement',
      'Metric',
      'Metric statements',
      'Context',
      'Method',
      'How to measure',
      'Criteria key',
    ],
  ];

  const rapidCriteriaRows: string[][] = [['Indicator', 'Observation', 'Criteria', 'Sentiment']];

  const detailedRows: string[][] = [
    [
      'Metric Name',
      'Metric Tier',
      'Mapped Function',
      'Metric Data Source',
      'Example Detailed Assessment as it On (only 1 metric per function)',
      'Source',
      'Metric Statement',
      'Description',
      'Method/Context',
      'How To Measure',
      'References',
      'Stratification',
      'Field Value 1',
      'Index Value 1',
      'Field Value 2',
      'Index Value 2',
      'Field Value 3',
      'Index Value 3',
      'Field Value 4',
      'Index Value 4',
      'Field Value 5',
      'Index Value 5',
      'Field Value 6',
      'Index Value 6',
      'Field Value 7',
      'Index Value 7',
    ],
  ];

  for (const group of sortedGroups) {
    const metricId = allocateMetricId(group, usedMetricIds);
    const profiles: Array<Record<string, unknown>> = [];
    const profileAvailability = {
      screening: false,
      rapid: false,
      detailed: false,
    };

    const baseTierRow =
      group.tierRows.screening || group.tierRows.rapid || group.tierRows.detailed;
    if (!baseTierRow) {
      continue;
    }

    if (group.tierRows.screening) {
      profileAvailability.screening = true;
      const screeningTier = group.tierRows.screening;
      const curveSetId = `curve-${metricId}-screening`;
      const layers = screeningTier.stratifications.length
        ? screeningTier.stratifications
        : [
            {
              stratIndex: 1,
              rawName: 'Default',
              type: 'categorical',
              indexAsRange: true,
              unit: '',
              bins: [],
              sourceName:
                screeningTier.sourceList.length > 1 &&
                screeningTier.sourceList.every((entry) => /sqt/i.test(entry))
                  ? 'Multiple SQTs'
                  : screeningTier.sourceList[0] || '',
              stratLabel: '',
              layerName:
                screeningTier.sourceList.length > 1 &&
                screeningTier.sourceList.every((entry) => /sqt/i.test(entry))
                  ? 'Multiple SQTs'
                  : screeningTier.sourceList[0] || 'Default',
            },
          ];

      const firstCriteria = deriveScreeningCriteria(layers[0]);
      const rubricLevels = [
        {
          label: 'Good',
          ratingId: 'good',
          criteriaMarkdown: buildScreeningCriteriaMarkdown(firstCriteria.good),
        },
        {
          label: 'Fair',
          ratingId: 'fair',
          criteriaMarkdown: buildScreeningCriteriaMarkdown(firstCriteria.fair),
        },
        {
          label: 'Poor',
          ratingId: 'poor',
          criteriaMarkdown: buildScreeningCriteriaMarkdown(firstCriteria.poor),
        },
      ];

      profiles.push({
        profileId: 'screening-default',
        tier: 'screening',
        status: 'active',
        recommended: screeningTier.recommended,
        scoring: {
          type: 'categorical',
          ratingScaleId: 'threeBand',
          output: {
            kind: 'rating',
            ratingScaleId: 'threeBand',
          },
          rubric: {
            levels: rubricLevels,
          },
        },
        curveIntegration: {
          enabled: true,
          curveSetRefs: [curveSetId],
        },
      });

      const curveLayers = layers.map((strat, layerIndex) => {
        const criteria = deriveScreeningCriteria(strat);
        const layerId =
          slugify(`${curveSetId}-${strat.layerName}`) || `${curveSetId}-layer-${layerIndex + 1}`;
        const points = [
          {
            x: 'Good',
            y: Number(((criteria.good.range.min + criteria.good.range.max) / 2).toFixed(3)),
            yMin: criteria.good.range.min,
            yMax: criteria.good.range.max,
            description: buildScreeningCriteriaMarkdown(criteria.good),
          },
          {
            x: 'Fair',
            y: Number(((criteria.fair.range.min + criteria.fair.range.max) / 2).toFixed(3)),
            yMin: criteria.fair.range.min,
            yMax: criteria.fair.range.max,
            description: buildScreeningCriteriaMarkdown(criteria.fair),
          },
          {
            x: 'Poor',
            y: Number(((criteria.poor.range.min + criteria.poor.range.max) / 2).toFixed(3)),
            yMin: criteria.poor.range.min,
            yMax: criteria.poor.range.max,
            description: buildScreeningCriteriaMarkdown(criteria.poor),
          },
        ];
        return {
          id: layerId,
          name: strat.layerName || `Stratification ${layerIndex + 1}`,
          points,
        };
      });

      const curveSet: Record<string, unknown> = {
        schemaVersion: 1,
        curveSetId,
        metricId,
        tier: 'screening',
        name: `${group.metricName} screening curve`,
        axes: {
          xLabel: 'Metric category',
          yLabel: 'Index score',
        },
        curves: [
          {
            curveId: `${curveSetId}-default`,
            name: 'Screening criteria',
            xType: 'qualitative',
            units: '',
            layers: curveLayers,
            activeLayerId: curveLayers[0]?.id || null,
            indexRange: true,
          },
        ],
      };
      CurveSetSchema.parse(curveSet);
      curveSetsById.set(curveSetId, curveSet);

      const activeCriteria = deriveScreeningCriteria(layers[0]);
      screeningRows.push(
        [
          metricId,
          group.discipline,
          group.functionName,
          group.functionStatement,
          group.metricName,
          screeningTier.description || screeningTier.metricStatement,
          screeningTier.recommended ? 'Yes' : 'No',
          screeningTier.methodContext.context,
          screeningTier.methodContext.method,
          screeningTier.howToMeasure,
          buildScreeningCriteriaMarkdown(activeCriteria.good),
          buildScreeningCriteriaMarkdown(activeCriteria.fair),
          buildScreeningCriteriaMarkdown(activeCriteria.poor),
          screeningTier.references.join('; '),
          screeningTier.sourceCitation,
          screeningTier.metricDataSource,
          activeCriteria.poor.label,
          activeCriteria.poor.desc,
          `${formatScore(activeCriteria.poor.range.min)}-${formatScore(activeCriteria.poor.range.max)}`,
          activeCriteria.fair.label,
          activeCriteria.fair.desc,
          `${formatScore(activeCriteria.fair.range.min)}-${formatScore(activeCriteria.fair.range.max)}`,
          activeCriteria.good.label,
          activeCriteria.good.desc,
          `${formatScore(activeCriteria.good.range.min)}-${formatScore(activeCriteria.good.range.max)}`,
          'screening',
          screeningTier.description || screeningTier.metricStatement,
        ].map(flattenForSimpleTsv)
      );

      screeningCurveOverrides[metricId] = {
        name: 'Screening criteria',
        xType: 'qualitative',
        indexRange: true,
        units: '',
        layers: curveLayers,
        activeLayerId: curveLayers[0]?.id || null,
      };
    }

    if (group.tierRows.rapid) {
      profileAvailability.rapid = true;
      const rapidTier = group.tierRows.rapid;
      const curveSetId = `curve-${metricId}-rapid`;
      const layers = rapidTier.stratifications.length
        ? rapidTier.stratifications
        : [
            {
              stratIndex: 1,
              rawName: 'Default',
              type: 'categorical',
              indexAsRange: true,
              unit: '',
              bins: [],
              sourceName: rapidTier.sourceList[0] || 'SFARI Rapid',
              stratLabel: '',
              layerName: rapidTier.sourceList[0] || 'SFARI Rapid',
            },
          ];

      const firstLevels = deriveRapidLevels(layers[0]);
      const rubricLevels = (['sd', 'd', 'n', 'a', 'sa'] as RapidLevelKey[]).map((key) => {
        const bin = firstLevels[key];
        const parts: string[] = [];
        if (bin?.desc) {
          const parsed = splitObservationCriteria(bin.desc);
          if (parsed.observation) {
            parts.push(`Observation: ${parsed.observation}`);
          }
          if (parsed.criteria) {
            parts.push(`Criteria: ${parsed.criteria}`);
          }
        }
        return {
          label: defaultRapidLabels[key],
          ratingId: key,
          criteriaMarkdown: parts.join('<br>') || 'Neutral / mixed evidence.',
        };
      });

      profiles.push({
        profileId: 'rapid-sfari-likert',
        tier: 'rapid',
        status: 'active',
        recommended: rapidTier.recommended,
        scoring: {
          type: 'categorical',
          ratingScaleId: 'sfariLikert',
          output: {
            kind: 'rating',
            ratingScaleId: 'sfariLikert',
          },
          rubric: {
            levels: rubricLevels,
          },
        },
        curveIntegration: {
          enabled: true,
          curveSetRefs: [curveSetId],
        },
      });

      const rapidCurveLayers = layers.map((strat, layerIndex) => {
        const levelMap = deriveRapidLevels(strat);
        const layerId =
          slugify(`${curveSetId}-${strat.layerName}`) || `${curveSetId}-layer-${layerIndex + 1}`;
        const points = (['sa', 'a', 'n', 'd', 'sd'] as RapidLevelKey[]).map((key) => {
          const bin = levelMap[key];
          const range = ensureRange(bin?.range || null, defaultRapidRanges[key]);
          const parsed = splitObservationCriteria(bin?.desc || '');
          const descriptionParts: string[] = [];
          if (parsed.observation) {
            descriptionParts.push(`Observation: ${parsed.observation}`);
          }
          if (parsed.criteria) {
            descriptionParts.push(`Criteria: ${parsed.criteria}`);
          }
          return {
            x: defaultRapidLabels[key],
            y: Number(((range.min + range.max) / 2).toFixed(3)),
            yMin: range.min,
            yMax: range.max,
            description: descriptionParts.join(' '),
          };
        });
        return {
          id: layerId,
          name: strat.layerName || `Stratification ${layerIndex + 1}`,
          points,
        };
      });

      const rapidCurveSet: Record<string, unknown> = {
        schemaVersion: 1,
        curveSetId,
        metricId,
        tier: 'rapid',
        name: `${group.metricName} rapid curve`,
        axes: {
          xLabel: 'Metric category',
          yLabel: 'Index score',
        },
        curves: [
          {
            curveId: `${curveSetId}-default`,
            name: 'SFARI rapid scoring',
            xType: 'qualitative',
            units: '',
            layers: rapidCurveLayers,
            activeLayerId: rapidCurveLayers[0]?.id || null,
            indexRange: true,
          },
        ],
      };
      CurveSetSchema.parse(rapidCurveSet);
      curveSetsById.set(curveSetId, rapidCurveSet);

      rapidIndicatorRows.push(
        [
          group.discipline,
          group.functionName,
          group.functionStatement,
          group.metricName,
          rapidTier.description || rapidTier.metricStatement,
          rapidTier.methodContext.context,
          rapidTier.methodContext.method,
          rapidTier.howToMeasure,
          metricId,
        ].map(flattenForSimpleTsv)
      );

      const levelMap = deriveRapidLevels(layers[0]);
      const sentimentByLevel: Record<RapidLevelKey, string | null> = {
        sa: '++',
        a: '+',
        n: null,
        d: '-',
        sd: '--',
      };
      (['sa', 'a', 'n', 'd', 'sd'] as RapidLevelKey[]).forEach((levelKey) => {
        const sentiment = sentimentByLevel[levelKey];
        if (!sentiment) {
          return;
        }
        const bin = levelMap[levelKey];
        const parsed = splitObservationCriteria(bin?.desc || '');
        if (!parsed.observation && !parsed.criteria) {
          return;
        }
        rapidCriteriaRows.push([
          metricId,
          flattenForSimpleTsv(parsed.observation),
          flattenForSimpleTsv(parsed.criteria),
          sentiment,
        ]);
      });
    }

    if (group.tierRows.detailed) {
      profileAvailability.detailed = true;
      const detailedTier = group.tierRows.detailed;
      const curveSetId = `curve-${metricId}-detailed`;
      const layers = detailedTier.stratifications;
      const curveLayers = layers.map((strat, layerIndex) => {
        const points = strat.bins
          .map((bin) => {
            const x = parseNumberToken(bin.name);
            const y = parseNumberToken(bin.indexRaw);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
              return null;
            }
            return {
              x,
              y: Math.max(0, Math.min(1, y)),
              description:
                bin.name || bin.indexRaw ? `Field ${bin.name}, Index ${bin.indexRaw}` : '',
            };
          })
          .filter((point): point is { x: number; y: number; description: string } => point !== null)
          .sort((a, b) => a.x - b.x);
        const normalizedPoints =
          points.length >= 2
            ? points
            : [
                { x: 0, y: 0, description: '' },
                { x: 1, y: 0.4, description: '' },
                { x: 2, y: 0.7, description: '' },
                { x: 3, y: 1, description: '' },
              ];
        const layerId =
          slugify(`${curveSetId}-${strat.layerName}`) || `${curveSetId}-layer-${layerIndex + 1}`;
        return {
          id: layerId,
          name: strat.layerName || `Stratification ${layerIndex + 1}`,
          points: normalizedPoints,
          sourceMetadata: {
            stratification: strat.stratLabel,
            sourceCitation: strat.sourceName,
            sourceRow: detailedTier.sourceRow,
            fieldValues: strat.bins.map((bin) => bin.name),
            indexValues: strat.bins.map((bin) => bin.indexRaw),
          },
        };
      });

      const detailedCurveSet: Record<string, unknown> = {
        schemaVersion: 1,
        curveSetId,
        metricId,
        tier: 'detailed',
        name: `${group.metricName} reference curve`,
        axes: {
          xLabel: group.metricName,
          yLabel: 'Index score',
          yUnit: 'index',
        },
        curves: [
          {
            curveId: `${curveSetId}-default`,
            name: 'Detailed reference',
            xType: 'quantitative',
            units: '',
            indexRange: false,
            layers: curveLayers,
            activeLayerId: curveLayers[0]?.id || null,
          },
        ],
      };
      CurveSetSchema.parse(detailedCurveSet);
      curveSetsById.set(curveSetId, detailedCurveSet);

      profiles.push({
        profileId: 'detailed-default',
        tier: 'detailed',
        status: 'active',
        recommended: detailedTier.recommended,
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
      });

      layers.forEach((strat) => {
        const fieldValues = new Array<string>(7).fill('');
        const indexValues = new Array<string>(7).fill('');
        strat.bins.slice(0, 7).forEach((bin, index) => {
          fieldValues[index] = bin.name;
          indexValues[index] = bin.indexRaw;
        });
        detailedRows.push([
          group.metricName,
          'Detailed',
          group.functionName,
          detailedTier.metricDataSource,
          detailedTier.recommended ? 'On' : '',
          strat.sourceName || detailedTier.sourceCitation,
          detailedTier.metricStatement || group.functionStatement,
          detailedTier.description || detailedTier.metricStatement || '',
          detailedTier.methodContextRaw,
          detailedTier.howToMeasure,
          detailedTier.references.join('; '),
          strat.stratLabel,
          fieldValues[0],
          indexValues[0],
          fieldValues[1],
          indexValues[1],
          fieldValues[2],
          indexValues[2],
          fieldValues[3],
          indexValues[3],
          fieldValues[4],
          indexValues[4],
          fieldValues[5],
          indexValues[5],
          fieldValues[6],
          indexValues[6],
        ]);
      });
    }

    if (!profiles.length) {
      continue;
    }

    const detail: Record<string, unknown> = {
      schemaVersion: 1,
      metricId,
      name: group.metricName,
      shortName: group.metricName,
      discipline: group.discipline,
      function: group.functionName,
      functionStatement: group.functionStatement,
      descriptionMarkdown: baseTierRow.description || baseTierRow.metricStatement,
      methodContextMarkdown: baseTierRow.methodContext.markdown,
      howToMeasureMarkdown: baseTierRow.howToMeasure,
      inputs: [
        {
          label: group.metricName,
          type: (baseTierRow.methodContext.method || 'desktop').toLowerCase(),
          source: baseTierRow.methodContext.context || 'Unspecified',
          required: true,
          helpMarkdown: baseTierRow.metricDataSource || undefined,
        },
      ],
      profiles,
      references: Array.from(group.references),
      tags: [
        group.discipline,
        group.functionName,
        ...ratingIdToTierOrder.filter((tier) => profileAvailability[tier]),
      ],
      status: 'active',
      sourceMetadata: {
        importSource: path.basename(csvPath),
        sourceRows: Array.from(new Set(group.sourceRows)).sort((a, b) => a - b),
        sourceCitations: Array.from(group.sourceCitations),
        profileAvailability,
      },
    };

    MetricDetailSchema.parse(detail);
    metricDetailsForIndex.push(detail);
    await fs.writeFile(
      path.join(metricsDir, `${metricId}.json`),
      `${JSON.stringify(detail, null, 2)}\n`,
      'utf8'
    );
  }

  for (const [curveSetId, curveSet] of curveSetsById.entries()) {
    await fs.writeFile(
      path.join(curvesDir, `${curveSetId}.json`),
      `${JSON.stringify(curveSet, null, 2)}\n`,
      'utf8'
    );
  }

  await fs.writeFile(
    screeningTsvPath,
    `${screeningRows.map((row) => asTsvLine(row)).join('\n')}\n`,
    'utf8'
  );
  await fs.writeFile(
    screeningSourceRichPath,
    `${screeningRows.map((row) => asTsvLine(row)).join('\n')}\n`,
    'utf8'
  );
  await fs.writeFile(
    rapidIndicatorsPath,
    `${rapidIndicatorRows.map((row) => asTsvLine(row)).join('\n')}\n`,
    'utf8'
  );
  await fs.writeFile(
    rapidCriteriaPath,
    `${rapidCriteriaRows.map((row) => asTsvLine(row)).join('\n')}\n`,
    'utf8'
  );
  await fs.writeFile(
    detailedMetricsPath,
    `${detailedRows.map((row) => asTsvLine(row)).join('\n')}\n`,
    'utf8'
  );
  await fs.writeFile(
    detailedSourceRichPath,
    `${detailedRows.map((row) => asTsvLine(row)).join('\n')}\n`,
    'utf8'
  );
  await fs.writeFile(
    screeningReferenceCurvesPath,
    `${JSON.stringify({ curves: screeningCurveOverrides }, null, 2)}\n`,
    'utf8'
  );

  const index = buildMetricIndex(metricDetailsForIndex, curveSetsById);
  await fs.writeFile(indexPath, `${JSON.stringify(index, null, 2)}\n`, 'utf8');

  await mirrorToSite();

  // eslint-disable-next-line no-console
  console.log(
    `Built metric library from ${path.basename(csvPath)}: ${metricDetailsForIndex.length} metrics, ${curveSetsById.size} curve sets.`
  );
  // eslint-disable-next-line no-console
  console.log(
    `Screening rows: ${Math.max(0, screeningRows.length - 1)}, Rapid rows: ${Math.max(
      0,
      rapidIndicatorRows.length - 1
    )}, Detailed rows: ${Math.max(0, detailedRows.length - 1)}`
  );
};

const buildMetricIndex = (
  metricDetails: Array<Record<string, unknown>>,
  curveSetsById: Map<string, Record<string, unknown>>
) => {
  const tierOrder: Record<Tier, number> = { screening: 1, rapid: 2, detailed: 3 };
  const scoringShapeFromProfile = (profile: Record<string, unknown>) => {
    const scoring = profile.scoring as Record<string, unknown> | undefined;
    if (!scoring) {
      return '';
    }
    const scoringType = String(scoring.type || '');
    if (scoringType === 'categorical') {
      const rubric = scoring.rubric as Record<string, unknown> | undefined;
      const levels = Array.isArray(rubric?.levels) ? rubric?.levels : [];
      return `${levels.length} levels`;
    }
    if (scoringType === 'thresholds') {
      const rubric = scoring.rubric as Record<string, unknown> | undefined;
      const bands = Array.isArray(rubric?.bands) ? rubric?.bands : [];
      return `${bands.length} bands`;
    }
    if (scoringType === 'curve') {
      const rubric = scoring.rubric as Record<string, unknown> | undefined;
      const refs = Array.isArray(rubric?.curveSetRefs) ? rubric?.curveSetRefs : [];
      return `${refs.length} curve sets`;
    }
    return scoringType || '';
  };

  const indexEntries = metricDetails.map((detail) => {
    const profiles = Array.isArray(detail.profiles) ? detail.profiles : [];
    const profileAvailability = {
      screening: profiles.some((profile) => profile.tier === 'screening'),
      rapid: profiles.some((profile) => profile.tier === 'rapid'),
      detailed: profiles.some((profile) => profile.tier === 'detailed'),
    };
    const profileSummaries: Record<string, unknown> = {};
    const curvesByTier: Record<Tier, number> = {
      screening: 0,
      rapid: 0,
      detailed: 0,
    };

    profiles.forEach((profile) => {
      const tier = profile.tier as Tier;
      const curveIntegration = profile.curveIntegration as Record<string, unknown> | undefined;
      const refs = Array.isArray(curveIntegration?.curveSetRefs)
        ? (curveIntegration?.curveSetRefs as string[])
        : [];
      const curveCount = refs.reduce((sum, ref) => {
        const curveSet = curveSetsById.get(ref);
        const curves = Array.isArray(curveSet?.curves) ? (curveSet?.curves as unknown[]) : [];
        return sum + curves.length;
      }, 0);
      curvesByTier[tier] += curveCount;
      profileSummaries[tier] = {
        profileId: profile.profileId,
        scoringType: profile.scoring?.type,
        scoringShape: scoringShapeFromProfile(profile),
        rawOutput: profile.scoring?.output?.kind || 'rating',
        normalizedOutput: profile.scoring?.output?.kind || 'rating',
        curveSetCount: curveCount,
      };
    });

    const recommendedTiers = profiles
      .filter((profile) => Boolean(profile.recommended))
      .map((profile) => profile.tier as Tier);
    const tiers = recommendedTiers.length
      ? recommendedTiers
      : profiles.map((profile) => profile.tier as Tier);
    const minimumTier = profiles.length
      ? profiles
          .slice()
          .sort((a, b) => tierOrder[a.tier as Tier] - tierOrder[b.tier as Tier])[0].tier
      : undefined;
    const inputs = Array.isArray(detail.inputs) ? detail.inputs : [];
    const sources = Array.from(
      new Set(
        inputs
          .map((input) => String(input?.source || '').trim())
          .filter((value) => value.length > 0)
      )
    );
    const totalCurveSetCount =
      curvesByTier.screening + curvesByTier.rapid + curvesByTier.detailed;

    return {
      metricId: detail.metricId,
      name: detail.name,
      shortName: detail.shortName || '',
      discipline: detail.discipline || '',
      function: detail.function || '',
      category: detail.discipline || detail.function || 'General',
      tags: Array.isArray(detail.tags) ? detail.tags : [],
      status: detail.status || 'active',
      minimumTier,
      profileAvailability,
      recommendedTiers: tiers,
      inputsSummary: {
        sources: sources.length ? sources : ['unknown'],
        effort: inputs.length ? 'field + desktop' : 'unknown',
        primaryUnit: inputs[0]?.unit || '',
      },
      profileSummaries,
      curvesSummary: {
        totalCurveSetCount,
        byTier: curvesByTier,
      },
      detailsRef: `metrics/${detail.metricId}.json`,
    };
  });

  indexEntries.sort((a, b) => {
    const categoryCompare = String(a.category || '').localeCompare(String(b.category || ''));
    if (categoryCompare !== 0) {
      return categoryCompare;
    }
    return String(a.name || '').localeCompare(String(b.name || ''));
  });

  return {
    schemaVersion: 1,
    metrics: indexEntries,
  };
};

const allocateMetricId = (group: MetricGroup, usedIds: Set<string>) => {
  const baseId =
    slugify(`${group.functionName}-${group.metricName}`) || slugify(group.metricName) || 'metric';
  let candidate = baseId;
  let suffix = 2;
  while (usedIds.has(candidate)) {
    candidate = `${baseId}-${suffix}`;
    suffix += 1;
  }
  usedIds.add(candidate);
  return candidate;
};

const cleanJsonDirectory = async (directoryPath: string) => {
  await fs.mkdir(directoryPath, { recursive: true });
  const entries = await fs.readdir(directoryPath);
  await Promise.all(
    entries
      .filter((entry) => entry.toLowerCase().endsWith('.json'))
      .map((entry) => fs.unlink(path.join(directoryPath, entry)))
  );
};

const pathExists = async (targetPath: string) => {
  try {
    await fs.access(targetPath);
    return true;
  } catch {
    return false;
  }
};

const resolveCsvPath = async () => {
  const configured = process.env.METRIC_LIBRARY_CSV_PATH || '';
  if (configured) {
    const absolute = path.isAbsolute(configured) ? configured : path.join(root, configured);
    if (await pathExists(absolute)) {
      return absolute;
    }
    throw new Error(`Configured METRIC_LIBRARY_CSV_PATH not found: ${absolute}`);
  }

  const files = await fs.readdir(metricLibraryDataDir);
  const matches = files
    .filter((name) => /^Metric Library Complete .*\.csv$/i.test(name))
    .map((name) => path.join(metricLibraryDataDir, name));
  if (!matches.length) {
    throw new Error(
      `No source CSV found in ${metricLibraryDataDir}. Expected "Metric Library Complete *.csv".`
    );
  }
  const withStats = await Promise.all(
    matches.map(async (fullPath) => ({
      fullPath,
      stat: await fs.stat(fullPath),
    }))
  );
  withStats.sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs);
  return withStats[0].fullPath;
};

const mirrorToSite = async () => {
  if (!(await pathExists(siteDataDir))) {
    return;
  }
  const siteMetricLibraryDir = path.join(siteDataDir, 'metric-library');
  const siteMetricsDir = path.join(siteMetricLibraryDir, 'metrics');
  const siteCurvesDir = path.join(siteMetricLibraryDir, 'curves');
  await fs.mkdir(siteMetricLibraryDir, { recursive: true });
  await fs.mkdir(siteMetricsDir, { recursive: true });
  await fs.mkdir(siteCurvesDir, { recursive: true });
  await cleanJsonDirectory(siteMetricsDir);
  await cleanJsonDirectory(siteCurvesDir);

  const sourceFiles = await fs.readdir(metricsDir);
  await Promise.all(
    sourceFiles
      .filter((name) => name.toLowerCase().endsWith('.json'))
      .map((name) => fs.copyFile(path.join(metricsDir, name), path.join(siteMetricsDir, name)))
  );
  const curveFiles = await fs.readdir(curvesDir);
  await Promise.all(
    curveFiles
      .filter((name) => name.toLowerCase().endsWith('.json'))
      .map((name) => fs.copyFile(path.join(curvesDir, name), path.join(siteCurvesDir, name)))
  );

  await fs.copyFile(indexPath, path.join(siteMetricLibraryDir, 'index.json'));
  if (await pathExists(ratingScalesPath)) {
    await fs.copyFile(ratingScalesPath, path.join(siteMetricLibraryDir, 'rating-scales.json'));
  }

  await fs.copyFile(screeningTsvPath, path.join(siteDataDir, 'screening-metrics.tsv'));
  await fs.copyFile(
    screeningReferenceCurvesPath,
    path.join(siteDataDir, 'screening-reference-curves.json')
  );
  await fs.copyFile(rapidIndicatorsPath, path.join(siteDataDir, 'rapid-indicators.tsv'));
  await fs.copyFile(rapidCriteriaPath, path.join(siteDataDir, 'rapid-criteria.tsv'));
  await fs.copyFile(detailedMetricsPath, path.join(siteDataDir, 'detailed-metrics.tsv'));
  await fs.copyFile(
    screeningSourceRichPath,
    path.join(siteDataDir, 'screening-metrics-source-rich.tsv')
  );
  await fs.copyFile(
    detailedSourceRichPath,
    path.join(siteDataDir, 'detailed-metrics-source-rich.tsv')
  );
};

buildMetricLibraryFromCsv().catch((error: unknown) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});
