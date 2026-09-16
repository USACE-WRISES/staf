import { VectorTile } from '@mapbox/vector-tile';
import Pbf from 'pbf';

/** Decode only the existing flowlines layer; retain native tile coordinates. */
export function decode(bytes) {
  const tile = new VectorTile(new Pbf(bytes));
  const layer = tile.layers.flowlines;
  if (!layer) return [];
  const features = [];
  for (let i = 0; i < layer.length; i++) {
    const feature = layer.feature(i);
    if (feature.type !== 2) continue;
    features.push({
      properties: feature.properties,
      extent: feature.extent,
      lines: feature.loadGeometry().map(line => line.map(point => [point.x, point.y]))
    });
  }
  return features;
}
