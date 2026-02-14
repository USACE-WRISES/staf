import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { MetricDetailSchema } from '../src/lib/metricLibrary/schemas.ts';

const loadJson = async (filePath: string) => {
  const raw = await fs.readFile(filePath, 'utf8');
  return JSON.parse(raw.replace(/^\ufeff/, ''));
};

const run = async () => {
  const fixturesDir = path.join(
    process.cwd(),
    'docs',
    'assets',
    'data',
    'metric-library',
    'metrics'
  );

  // Valid sample metric detail parses
  const fixtureFiles = (await fs.readdir(fixturesDir))
    .filter((fileName) => fileName.endsWith('.json'))
    .sort();
  assert.ok(fixtureFiles.length > 0, 'Expected at least one metric fixture.');
  const samplePath = path.join(fixturesDir, fixtureFiles[0]);
  const sample = await loadJson(samplePath);
  const parsed = MetricDetailSchema.parse(sample);
  assert.equal(parsed.metricId, sample.metricId);

  // Invalid scoring.type fails
  const invalid = { ...sample };
  invalid.profiles = JSON.parse(JSON.stringify(sample.profiles));
  invalid.profiles[0].scoring.type = 'bogus';
  assert.throws(() => MetricDetailSchema.parse(invalid));

  // Missing curveSetRefs when curveIntegration.enabled=true fails
  const missingCurves = {
    metricId: 'dummy',
    name: 'Dummy metric',
    profiles: [
      {
        profileId: 'screening-default',
        tier: 'screening',
        status: 'active',
        scoring: {
          type: 'categorical',
          ratingScaleId: 'fourBand',
          rubric: { levels: [{ label: 'Optimal' }] },
        },
        curveIntegration: {
          enabled: true,
        },
      },
    ],
  };
  assert.throws(() => MetricDetailSchema.parse(missingCurves));

  // eslint-disable-next-line no-console
  console.log('Metric library schema tests passed.');
};

run().catch((error) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});

