// STAF Metric Toolbox download (standalone, used on the Tools page).
// Exports the STAF metric library as an .xlsx by reusing the site-wide metric
// store (window.STAFMetricLibraryStore). It does not depend on the assessment
// widgets, so it works on any page that includes a [data-metric-toolbox-download]
// trigger. The store is read at click time, so script load order does not matter.
(() => {
  const toList = (value) =>
    Array.isArray(value)
      ? value.filter((item) => item !== null && item !== undefined && item !== '').join('; ')
      : '';

  let excelPromise = null;
  const ensureExcelJs = (store) => {
    if (window.ExcelJS) {
      return Promise.resolve(window.ExcelJS);
    }
    if (excelPromise) {
      return excelPromise;
    }
    excelPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = store.buildUrl('/assets/vendor/exceljs.min.js');
      script.async = true;
      script.onload = () =>
        window.ExcelJS ? resolve(window.ExcelJS) : reject(new Error('ExcelJS not available.'));
      script.onerror = () => reject(new Error('Failed to load ExcelJS.'));
      document.head.appendChild(script);
    }).catch((error) => {
      excelPromise = null;
      throw error;
    });
    return excelPromise;
  };

  const buildMetricRows = (entries, detailMap) =>
    entries.map((entry) => {
      const detail = detailMap.get(entry.metricId) || {};
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

  const exportToolbox = async (button) => {
    const store = window.STAFMetricLibraryStore;
    if (!store) {
      window.alert('The metric library is still loading. Please try again in a moment.');
      return;
    }
    if (button.dataset.busy === 'true') {
      return;
    }
    button.dataset.busy = 'true';
    button.setAttribute('aria-busy', 'true');
    const originalText = button.textContent;
    button.textContent = 'Preparing download...';
    try {
      const ExcelJS = await ensureExcelJs(store);
      const index = await store.loadMetricIndex();
      const entries = index.metrics || [];
      const results = await Promise.allSettled(
        entries.map(async (entry) => ({
          entry,
          detail: await store.loadMetricDetail(entry.metricId, entry.detailsRef),
        }))
      );
      const detailMap = new Map();
      const validEntries = [];
      results.forEach((result, i) => {
        if (result.status === 'fulfilled' && result.value && result.value.detail) {
          detailMap.set(result.value.entry.metricId, result.value.detail);
          validEntries.push(result.value.entry);
        } else {
          console.warn('Skipping metric during export.', entries[i] && entries[i].metricId);
        }
      });
      if (!validEntries.length) {
        throw new Error('No metric details available for export.');
      }

      const workbook = new ExcelJS.Workbook();
      workbook.creator = 'STAF Metric Library';
      const sheet = workbook.addWorksheet('Metrics');
      sheet.columns = [
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
      sheet.addRows(buildMetricRows(validEntries, detailMap));
      sheet.views = [{ state: 'frozen', ySplit: 1 }];

      const buffer = await workbook.xlsx.writeBuffer();
      const blob = new Blob([buffer], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      const stamp = new Date().toISOString().slice(0, 10);
      link.href = url;
      link.download = `STAF_Metric_Library_${stamp}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      console.error('Metric library export failed.', error);
      window.alert('Metric library export failed. Please try again.');
    } finally {
      button.dataset.busy = 'false';
      button.removeAttribute('aria-busy');
      button.textContent = originalText;
    }
  };

  const init = () => {
    document.querySelectorAll('[data-metric-toolbox-download]').forEach((button) => {
      button.addEventListener('click', () => exportToolbox(button));
    });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
