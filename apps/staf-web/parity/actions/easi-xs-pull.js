// The static app's Pull from 3DEP (capture.py --actions): the report closed, High flow dynamics shown, the
// sections stepped through until one has no profile, then its profile pulled. Returns, as JSON, the
// card before and after the pull (the plot's inside left out) with the time the pull took.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const id = (s) => document.getElementById(tool + '-' + s);
  const x = id('close_modal_x');
  if (x) x.click();
  await wait(3000);
  sec.querySelector('.sfari-nav-fn[data-idx="5"]').click();
  await wait(2500);
  const clean = (el) => {
    if (!el) return null;
    const c = el.cloneNode(true);
    c.querySelectorAll('.js-plotly-plot').forEach((n) => { n.innerHTML = '<!--plot-->'; });
    return c.outerHTML.replace(/></g, '>\n<');
  };
  const state = () => ({
    selector: (id('xs_selector') || {}).textContent || null,
    plot: clean(id('xsection_plot')),
    boxes: [(id('xs_bankfull') || {}).value, (id('xs_lowbank') || {}).value, (id('xs_bankfull') || {}).disabled],
    summary: ((id('xs_summary') || {}).textContent || '').replace(/\s+/g, ' '),
    window: (id('xs_window_range') || {}).textContent || null,
    live: ((id('fn_metric_live') || {}).textContent || '').replace(/\s+/g, ' '),
  });
  const out = { steps: [] };
  for (let i = 0; i < 9 && !sec.querySelector('[data-xs-pull]'); i++) {
    id('xs_prev').click();
    await wait(1200);
    out.steps.push(state().selector);
  }
  out.before = state();
  const btn = sec.querySelector('[data-xs-pull]');
  if (!btn) return JSON.stringify(out);
  const t0 = performance.now();
  btn.click();
  await wait(50);
  out.pulling = state();
  for (let t = 0; t < 240 && !sec.querySelector('#' + tool + '-xsection_plot .main-svg'); t++) {
    await wait(250);
    if (sec.querySelector('[data-xs-pull]:not([disabled])')) break;
  }
  out.ms = Math.round(performance.now() - t0);
  await wait(800);
  out.after = state();
  return JSON.stringify(out);
}
