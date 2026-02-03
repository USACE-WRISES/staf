import path from 'path';
import { promises as fs } from 'fs';
import {
  parseCurveSet,
  parseMetricDetail,
  parseMetricIndex,
  parseRatingScales,
} from './schemas.ts';

const defaultBaseDir = path.join(
  process.cwd(),
  'docs',
  'assets',
  'data',
  'metric-library'
);

const cache = {
  index: null,
  ratingScales: null,
  details: new Map(),
  curves: new Map(),
};

const readJson = async (filePath) => {
  const raw = await fs.readFile(filePath, 'utf8');
  return JSON.parse(raw.replace(/^\ufeff/, ''));
};

export const loadMetricIndex = async (baseDir = defaultBaseDir) => {
  if (cache.index) {
    return cache.index;
  }
  const filePath = path.join(baseDir, 'index.json');
  const data = await readJson(filePath);
  cache.index = parseMetricIndex(data);
  return cache.index;
};

export const loadRatingScales = async (baseDir = defaultBaseDir) => {
  if (cache.ratingScales) {
    return cache.ratingScales;
  }
  const filePath = path.join(baseDir, 'rating-scales.json');
  const data = await readJson(filePath);
  cache.ratingScales = parseRatingScales(data);
  return cache.ratingScales;
};

export const loadMetricDetail = async (metricId, detailsRef, baseDir = defaultBaseDir) => {
  const key = detailsRef || metricId;
  if (cache.details.has(key)) {
    return cache.details.get(key);
  }
  const fileName = detailsRef || `metrics/${metricId}.json`;
  const filePath = path.join(baseDir, fileName);
  const data = await readJson(filePath);
  const parsed = parseMetricDetail(data);
  cache.details.set(key, parsed);
  return parsed;
};

export const loadCurveSet = async (curveSetId, baseDir = defaultBaseDir) => {
  if (cache.curves.has(curveSetId)) {
    return cache.curves.get(curveSetId);
  }
  const filePath = path.join(baseDir, 'curves', `${curveSetId}.json`);
  const data = await readJson(filePath);
  const parsed = parseCurveSet(data);
  cache.curves.set(curveSetId, parsed);
  return parsed;
};

export const clearMetricLibraryCache = () => {
  cache.index = null;
  cache.ratingScales = null;
  cache.details.clear();
  cache.curves.clear();
};

