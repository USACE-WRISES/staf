(() => {
  const getBaseUrl = () => {
    const el = document.querySelector('[data-baseurl]');
    return el ? el.dataset.baseurl || '' : '';
  };

  const buildUrl = (path) => {
    const base = getBaseUrl();
    return base ? `${base}${path}` : path;
  };

  const fetchJson = async (url) => {
    const base = getBaseUrl();
    const stripped = base && url.startsWith(base) ? url.slice(base.length) || '/' : url;

    try {
      const response = await fetch(url);
      if (response.ok) {
        return response.json();
      }
    } catch (error) {
      // fall through
    }

    if (stripped && stripped !== url) {
      const response = await fetch(stripped);
      if (response.ok) {
        return response.json();
      }
    }

    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Failed to load ${url} (${response.status})`);
    }
    return response.json();
  };

  const cache = {
    index: null,
    ratingScales: null,
    indexPromise: null,
    ratingPromise: null,
    details: new Map(),
    curves: new Map(),
  };

  const loadMetricIndex = async () => {
    if (cache.index) {
      return cache.index;
    }
    if (!cache.indexPromise) {
      cache.indexPromise = fetchJson(buildUrl('/assets/data/metric-library/index.json')).then(
        (data) => {
          cache.index = data;
          return data;
        }
      );
    }
    return cache.indexPromise;
  };

  const loadRatingScales = async () => {
    if (cache.ratingScales) {
      return cache.ratingScales;
    }
    if (!cache.ratingPromise) {
      cache.ratingPromise = fetchJson(
        buildUrl('/assets/data/metric-library/rating-scales.json')
      ).then((data) => {
        cache.ratingScales = data;
        return data;
      });
    }
    return cache.ratingPromise;
  };

  const loadMetricDetail = async (metricId, detailsRef) => {
    const key = metricId || detailsRef;
    if (cache.details.has(key)) {
      return cache.details.get(key);
    }
    const ref = detailsRef || `metrics/${metricId}.json`;
    const promise = fetchJson(buildUrl(`/assets/data/metric-library/${ref}`)).then((data) => {
      cache.details.set(key, data);
      return data;
    });
    cache.details.set(key, promise);
    return promise;
  };

  const loadCurveSet = async (curveSetId) => {
    if (cache.curves.has(curveSetId)) {
      return cache.curves.get(curveSetId);
    }
    const promise = fetchJson(
      buildUrl(`/assets/data/metric-library/curves/${curveSetId}.json`)
    ).then((data) => {
      cache.curves.set(curveSetId, data);
      return data;
    });
    cache.curves.set(curveSetId, promise);
    return promise;
  };

  const clearCache = () => {
    cache.index = null;
    cache.ratingScales = null;
    cache.indexPromise = null;
    cache.ratingPromise = null;
    cache.details.clear();
    cache.curves.clear();
  };

  window.STAFMetricLibraryStore = {
    loadMetricIndex,
    loadRatingScales,
    loadMetricDetail,
    loadCurveSet,
    buildUrl,
    clearCache,
  };
})();

