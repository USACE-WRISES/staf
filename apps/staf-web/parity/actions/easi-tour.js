// EASI's worksheet, function by function (capture.py --actions): the report closed, then each of the
// twenty functions shown with its Scoring panel open. Returns, as JSON, each function's panel and the
// rail as the page has them (the markup the compare reads), the Plotly widget of the cross-section
// cards left out (the static app draws its plot later).
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const x = document.getElementById(tool + '-close_modal_x');
  if (x) x.click();
  await wait(3000);
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const clean = (el) => {
    if (!el) return null;
    const c = el.cloneNode(true);
    c.querySelectorAll('.easi-xs-right, .shiny-ipywidget-output, .js-plotly-plot').forEach((n) => n.replaceWith(document.createComment('plot')));
    return c.outerHTML.replace(/ ?\bshiny-bound-(?:input|output)\b/g, '').replace(/ class=""/g, '').replace(/></g, '>\n<');
  };
  const out = [];
  const n = sec.querySelectorAll('.sfari-nav-fn').length;
  for (let i = 0; i < n; i++) {
    sec.querySelector('.sfari-nav-fn[data-idx="' + i + '"]').click();
    await wait(1500);
    const btn = sec.querySelector('#' + tool + '-fn_panel [data-staf-panel="scoring"]');
    if (btn) btn.click();
    await wait(700);
    out.push({ i, panel: clean(sec.querySelector('#' + tool + '-fn_panel')), nav: clean(sec.querySelector('#' + tool + '-fn_nav')) });
  }
  out.push({ rail: clean(sec.querySelector('#' + tool + '-rollup_rail')) });
  return JSON.stringify(out);
}
