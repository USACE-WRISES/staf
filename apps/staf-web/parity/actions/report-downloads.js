// The report and its downloads (capture.py --open FILE --actions, with --download): Open report, then
// each of the footer's links that the tool has registered (PDF, CSV and GeoJSON unless STAF_DOWNLOADS names others),
// clicked in place. The report stays open for the capture. Returns what it clicked.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const done = [];
  const open = sec.querySelector('[data-report]');
  if (open) { open.click(); done.push('report'); }
  for (let i = 0; i < 120 && !document.querySelector('#shiny-modal.show #' + tool + '-report'); i++) await wait(250);
  await wait(1500);
  for (const id of (window.STAF_DOWNLOADS || ['dl_pdf', 'dl_csv', 'dl_geojson'])) {
    const a = document.getElementById(tool + '-' + id);
    if (!a) continue;
    // Shiny binds each link's address after the report shows; an unbound one would reload the page
    const shiny = !!document.querySelector('script[src*="shiny"]');
    for (let i = 0; i < 40 && (a.classList.contains('disabled') || (shiny && !a.getAttribute('href'))); i++) await wait(250);
    a.removeAttribute('target');
    a.click();
    done.push(id);
    await wait(2500);
  }
  return done.join(',');
}
