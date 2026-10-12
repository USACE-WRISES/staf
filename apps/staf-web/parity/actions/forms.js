// Get Forms (capture.py --actions, after --assess or --open; with --download): the worksheet's Get Forms
// button (SFARI's data-desktop-metrics, EASI's data-forms, DEEP's data-field-forms), then, once the
// dialog's status has settled, each of its links named in window.STAF_DOWNLOADS (all of them when unset)
// clicked in place. The dialog stays open for the capture. Returns what it did.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const done = [];
  const open = sec.querySelector('[data-desktop-metrics], [data-forms], [data-field-forms]');
  if (open) { open.click(); done.push('forms'); }
  const tabs = '#shiny-modal.show #' + tool + '-ff_tabs, #shiny-modal.show #' + tool + '-gf_tabs';
  for (let i = 0; i < 120 && !document.querySelector(tabs); i++) await wait(250);
  // the status line says what the forms wait on until the pull and the engine are done
  for (let i = 0; i < 240; i++) {
    const st = document.getElementById(tool + '-ff_status');
    if (!st || !/Preparing field forms/.test(st.textContent)) break;
    await wait(250);
  }
  await wait(1500);
  const ids = window.STAF_DOWNLOADS
    || [...document.querySelectorAll('#shiny-modal.show a.shiny-download-link')].map((a) => a.id.slice(tool.length + 1));
  for (const id of ids) {
    const a = document.getElementById(tool + '-' + id);
    if (!a) continue;
    // Shiny binds each link's address after the dialog shows; an unbound one would reload the page
    const shiny = !!document.querySelector('script[src*="shiny"]');
    for (let i = 0; i < 40 && (a.classList.contains('disabled') || (shiny && !a.getAttribute('href'))); i++) await wait(250);
    a.removeAttribute('target');
    a.click();
    done.push(id);
    await wait(2500);
  }
  return done.join(',');
}
