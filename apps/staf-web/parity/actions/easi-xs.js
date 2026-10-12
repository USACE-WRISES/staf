// EASI's cross-section card (capture.py --actions): the report closed, High flow dynamics shown, then the
// next section, a bankfull edit and the switch to metres. Returns, as JSON, at each state: the card's
// right side (the plot widget's inside left out), the sizes of its boxes and of the plot, the plot's axes,
// the window range the page publishes, the summary, the switch, the reach table, the height boxes, and
// the shown metric's value and rating.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const id = (s) => document.getElementById(tool + '-' + s);
  const x = id('close_modal_x');
  if (x) x.click();
  await wait(3000);
  sec.querySelector('.sfari-nav-fn[data-idx="5"]').click();
  for (let t = 0; t < 60 && !sec.querySelector('.easi-xs-in-card .js-plotly-plot .main-svg'); t++) await wait(250);
  await wait(1500);
  const rect = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
  };
  const clean = (el) => {
    if (!el) return null;
    const c = el.cloneNode(true);
    c.querySelectorAll('.shiny-ipywidget-output, .js-plotly-plot').forEach((n) => { n.innerHTML = '<!--plot-->'; });
    return c.outerHTML.replace(/ ?\bshiny-bound-(?:input|output)\b/g, '').replace(/ class=""/g, '').replace(/></g, '>\n<');
  };
  const state = () => {
    const card = sec.querySelector('.easi-xs-in-card');
    const gd = card && card.querySelector('.js-plotly-plot');
    const fl = gd && gd._fullLayout;
    const chain = [];
    for (let n = gd; n && n !== card; n = n.parentElement) {
      chain.push({ tag: n.tagName.toLowerCase(), id: n.id || '', cls: n.getAttribute('class') || '', style: n.getAttribute('style') || '', rect: rect(n) });
    }
    return {
      right: clean(card && card.querySelector('.easi-xs-right')),
      rects: { wrap: rect(card), panel: rect(card && card.querySelector('.easi-xs-panel')), right: rect(card && card.querySelector('.easi-xs-right')),
        head: rect(card && card.querySelector('.easi-xs-plot-head')), box: rect(card && card.querySelector('.easi-xsection')),
        modebar: rect(card && card.querySelector('.modebar-container')) },
      chain,
      layout: fl ? { width: fl.width, height: fl.height, x: fl.xaxis.range, y: fl.yaxis.range, margin: fl.margin, n: gd.data.length,
        ys: gd.data.map((d) => (d.y || []).length) } : null,
      window: (id('xs_window_range') || {}).textContent || null,
      summary: clean(id('xs_summary')),
      selector: (id('xs_selector') || {}).textContent || null,
      reach: clean(id('xs_reach_table')),
      boxes: [(id('xs_bankfull') || {}).value, (id('xs_lowbank') || {}).value],
      unit: (sec.querySelector('input[name="' + tool + '-xs_unit"]:checked') || {}).value || null,
      live: (id('fn_metric_live') || {}).textContent || null,
      rate: (sec.querySelector('#' + tool + '-fn_metric_rate select') || {}).value || null,
    };
  };
  const out = { start: state() };
  id('xs_next').click();
  await wait(3000);
  out.next = state();
  const bf = id('xs_bankfull');
  bf.value = String(Math.round((Number(bf.value) + 0.5) * 100) / 100);
  bf.dispatchEvent(new Event('input', { bubbles: true }));
  bf.dispatchEvent(new Event('change', { bubbles: true }));
  await wait(3000);
  out.edited = state();
  const m = sec.querySelector('input[name="' + tool + '-xs_unit"][value="m"]');
  m.click();
  await wait(3000);
  out.metres = state();
  id('xs_prev').click();
  await wait(3000);
  out.back = state();
  return JSON.stringify(out);
}
