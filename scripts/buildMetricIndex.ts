import { promises as fs } from 'fs';
import path from 'path';
import { MetricDetailSchema } from '../src/lib/metricLibrary/schemas.ts';

const root = process.cwd();
const metricsDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'metrics');
const curvesDir = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'curves');
const indexPath = path.join(root, 'docs', 'assets', 'data', 'metric-library', 'index.json');

const tierOrder = { screening: 1, rapid: 2, detailed: 3 } as const;

const readJson = async (filePath: string) => {
  const raw = await fs.readFile(filePath, 'utf8');
  const clean = raw.replace(/^\ufeff/, '');
  return JSON.parse(clean);
};

const curveCountCache = new Map<string, number>();
const getCurveCount = async (curveSetId: string) => {
  if (curveCountCache.has(curveSetId)) {
    return curveCountCache.get(curveSetId) || 0;
  }
  try {
    const filePath = path.join(curvesDir, `${curveSetId}.json`);
    const data = await readJson(filePath);
    const count = Array.isArray(data.curves) ? data.curves.length : 0;
    curveCountCache.set(curveSetId, count);
    return count;
  } catch (error) {
    curveCountCache.set(curveSetId, 0);
    return 0;
  }
};

const scoringShapeFromProfile = (profile: any) => {
  switch (profile.scoring?.type) {
    case 'categorical':
      return `${profile.scoring?.rubric?.levels?.length || 0} levels`;
    case 'thresholds':
      return `${profile.scoring?.rubric?.bands?.length || 0} bands`;
    case 'curve':
      return `${profile.scoring?.rubric?.curveSetRefs?.length || 0} curve sets`;
    case 'formula':
      return 'formula';
    case 'binary':
      return 'binary';
    case 'lookup':
      return 'lookup';
    default:
      return '';
  }
};

const main = async () => {
  const files = await fs.readdir(metricsDir);
  const entries: any[] = [];

  for (const file of files) {
    if (!file.endsWith('.json')) {
      continue;
    }
    const fullPath = path.join(metricsDir, file);
    const detailRaw = await readJson(fullPath);
    const detail = MetricDetailSchema.parse(detailRaw);

    const profiles = detail.profiles || [];
    const profileAvailability = {
      screening: profiles.some((p: any) => p.tier === 'screening'),
      rapid: profiles.some((p: any) => p.tier === 'rapid'),
      detailed: profiles.some((p: any) => p.tier === 'detailed'),
    };

    const profileSummaries: any = {};
    const curvesByTier: Record<'screening' | 'rapid' | 'detailed', number> = {
      screening: 0,
      rapid: 0,
      detailed: 0,
    };

    for (const profile of profiles) {
      let curveCount = 0;
      if (profile.curveIntegration?.enabled) {
        const refs = profile.curveIntegration.curveSetRefs || [];
        const counts = await Promise.all(refs.map((ref: string) => getCurveCount(ref)));
        curveCount = counts.reduce((sum, value) => sum + value, 0);
      }
      curvesByTier[profile.tier] += curveCount;
      profileSummaries[profile.tier] = {
        profileId: profile.profileId,
        scoringType: profile.scoring?.type,
        scoringShape: scoringShapeFromProfile(profile),
        rawOutput: profile.scoring?.output?.kind || 'rating',
        normalizedOutput: profile.scoring?.output?.kind || 'rating',
        curveSetCount: curveCount,
      };
    }

    const totalCurveSetCount = Object.values(curvesByTier).reduce(
      (sum, value) => sum + value,
      0
    );

    const recommendedTiers = profiles
      .filter((profile: any) => profile.recommended)
      .map((profile: any) => profile.tier);

    const tierList = recommendedTiers.length
      ? recommendedTiers
      : profiles.map((profile: any) => profile.tier);

    const inputs = detail.inputs || [];
    const sources = Array.from(
      new Set(
        inputs
          .map((input: any) => input.source)
          .filter((value: string) => Boolean(value))
      )
    );

    const minimumTier = profiles.length
      ? profiles
          .slice()
          .sort((a: any, b: any) => tierOrder[a.tier] - tierOrder[b.tier])[0].tier
      : undefined;

    entries.push({
      metricId: detail.metricId,
      name: detail.name,
      shortName: detail.shortName,
      discipline: detail.discipline,
      function: detail.function,
      category: detail.discipline || detail.function || 'General',
      tags: detail.tags || [],
      status: detail.status || 'active',
      minimumTier,
      profileAvailability,
      recommendedTiers: tierList,
      inputsSummary: {
        sources: sources.length ? sources : ['unknown'],
        effort: inputs.length ? 'field + desktop' : 'unknown',
        primaryUnit: inputs[0]?.unit,
      },
      profileSummaries,
      curvesSummary: {
        totalCurveSetCount,
        byTier: curvesByTier,
      },
      detailsRef: `metrics/${file}`,
    });
  }

  entries.sort((a, b) => {
    const catCompare = (a.category || '').localeCompare(b.category || '');
    if (catCompare !== 0) {
      return catCompare;
    }
    return (a.name || '').localeCompare(b.name || '');
  });

  const index = {
    schemaVersion: 1,
    metrics: entries,
  };

  await fs.writeFile(indexPath, JSON.stringify(index, null, 2));
  // eslint-disable-next-line no-console
  console.log(`Wrote ${entries.length} metrics to ${indexPath}`);
};

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});

