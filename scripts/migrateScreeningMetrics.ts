import { promises as fs } from 'fs';
import path from 'path';
import { MetricDetailSchema } from '../src/lib/metricLibrary/schemas.ts';

const root = process.cwd();
const tsvPath = path.join(root, 'docs', 'assets', 'data', 'screening-metrics.tsv');
const metricsDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'metrics');
const curvesDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'curves');

const slugify = (value: string) =>
  value
    .toLowerCase()
    .replace(/&/g, 'and')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);

const parseTSV = (text: string) => {
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
    const row: Record<string, string> = {};
    header.forEach((key, index) => {
      row[key] = cells[index] ? cells[index].trim() : '';
    });
    return row;
  });
};

const buildCurveSet = (curveSetId: string, metricId: string, criteria: any) => {
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
        curveId: `${curveSetId}-default`,
        name: 'Screening',
        xType: 'qualitative',
        units: '',
        layers: [
          {
            id: `${curveSetId}-layer`,
            name: 'Screening',
            points: [
              { x: 'Optimal', y: 1, description: criteria.optimal || '' },
              { x: 'Suboptimal', y: 0.69, description: criteria.suboptimal || '' },
              { x: 'Marginal', y: 0.3, description: criteria.marginal || '' },
              { x: 'Poor', y: 0, description: criteria.poor || '' },
            ],
          },
        ],
        activeLayerId: `${curveSetId}-layer`,
      },
    ],
  };
};

const main = async () => {
  await fs.mkdir(metricsDir, { recursive: true });
  await fs.mkdir(curvesDir, { recursive: true });

  const raw = await fs.readFile(tsvPath, 'utf8');
  const rows = parseTSV(raw);
  const usedIds = new Map<string, number>();

  for (const row of rows) {
    const discipline = row.Discipline || '';
    const functionName = row.Function || '';
    const metricName = row.Metric || '';

    const baseId = slugify(`${functionName}-${metricName}`) || slugify(metricName) || 'metric';
    const count = usedIds.get(baseId) || 0;
    usedIds.set(baseId, count + 1);
    const metricId = count ? `${baseId}-${count + 1}` : baseId;

    const criteria = {
      optimal: row.Optimal || '',
      suboptimal: row.Suboptimal || '',
      marginal: row.Marginal || '',
      poor: row.Poor || '',
    };

    const curveSetId = `curve-${metricId}`;

    const detail = {
      schemaVersion: 1,
      metricId,
      name: metricName || metricId,
      shortName: row['Metric ID'] || '',
      discipline,
      function: functionName,
      functionStatement: row['Function statement'] || '',
      descriptionMarkdown: row['Metric statement'] || '',
      methodContextMarkdown: row.Context
        ? `Context: ${row.Context}\n\nMethod: ${row.Method || ''}`.trim()
        : row.Method || '',
      howToMeasureMarkdown: row['How to measure'] || '',
      inputs: [
        {
          label: metricName || 'Metric value',
          type: row.Method ? row.Method.toLowerCase() : 'desktop',
          source: row.Context || '',
          required: true,
        },
      ],
      profiles: [
        {
          profileId: 'screening-default',
          tier: 'screening',
          status: 'active',
          recommended: (row['Predefined SCS'] || '').toLowerCase() === 'yes',
          scoring: {
            type: 'categorical',
            ratingScaleId: 'fourBand',
            output: { kind: 'rating', ratingScaleId: 'fourBand' },
            rubric: {
              levels: [
                { label: 'Optimal', ratingId: 'optimal', criteriaMarkdown: criteria.optimal },
                {
                  label: 'Suboptimal',
                  ratingId: 'suboptimal',
                  criteriaMarkdown: criteria.suboptimal,
                },
                { label: 'Marginal', ratingId: 'marginal', criteriaMarkdown: criteria.marginal },
                { label: 'Poor', ratingId: 'poor', criteriaMarkdown: criteria.poor },
              ],
            },
          },
          curveIntegration: {
            enabled: true,
            curveSetRefs: [curveSetId],
          },
        },
      ],
      references: row.References ? row.References.split(/;\s*/).filter(Boolean) : [],
      tags: [discipline, functionName].filter(Boolean),
    };

    // Validate to keep data consistent
    MetricDetailSchema.parse(detail);

    const metricPath = path.join(metricsDir, `${metricId}.json`);
    await fs.writeFile(metricPath, JSON.stringify(detail, null, 2));

    const curveSet = buildCurveSet(curveSetId, metricId, criteria);
    const curvePath = path.join(curvesDir, `${curveSetId}.json`);
    await fs.writeFile(curvePath, JSON.stringify(curveSet, null, 2));
  }

  // eslint-disable-next-line no-console
  console.log(`Migrated ${rows.length} screening metrics.`);
};

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});

