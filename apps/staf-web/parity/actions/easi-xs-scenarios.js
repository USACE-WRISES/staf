// EASI's cross-section across scenarios (capture.py --actions): on High flow dynamics the heights edited,
// a copy added (Alternative 1, its heights kept), the heights edited again there, then Existing Conditions
// shown again (its own heights back) and the copy once more. Returns each state's boxes, the shown metric's
// rating and value, the rail's index and the scenario shown.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const id = (s) => document.getElementById(tool + '-' + s);
  const x = id('close_modal_x');
  if (x) x.click();
  await wait(2500);
  sec.querySelector('.sfari-nav-fn[data-idx="5"]').click();
  await wait(2500);
  const state = (name) => ({
    name,
    scenario: ((sec.querySelector('.staf-sc-chip') || {}).textContent || '').replace(/\s+/g, ' ').trim(),
    boxes: [(id('xs_bankfull') || {}).value, (id('xs_lowbank') || {}).value],
    rate: (sec.querySelector('#' + tool + '-fn_metric_rate select') || {}).value || null,
    live: ((id('fn_metric_live') || {}).textContent || '').replace(/\s+/g, ' ').trim(),
    eci: ((sec.querySelector('#' + tool + '-rollup_rail .easi-eci-num, #' + tool + '-rollup_rail .sfari-eci-num') || {}).textContent || '').trim(),
    selector: ((id('xs_selector') || {}).textContent || '').trim(),
  });
  const setBox = async (box, v) => {
    const el = id(box);
    el.value = String(v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    await wait(1500);
  };
  const out = [state('start')];
  const bf0 = Number(id('xs_bankfull').value);
  await setBox('xs_bankfull', Math.round((bf0 + 0.6) * 100) / 100);
  out.push(state('edited'));
  // Add a scenario: a copy of the shown one (the dialog's default start)
  const add = sec.querySelector('[data-sc-action="new"]');
  if (add) add.click();
  await wait(1500);
  const save = id('staf_sc_save');
  if (save) save.click();
  await wait(2500);
  out.push(state('copy'));
  await setBox('xs_lowbank', Math.round((Number(id('xs_lowbank').value) + 0.4) * 100) / 100);
  out.push(state('copy edited'));
  const pick = (sid) => { const b = sec.querySelector('[data-sc-action="select"][data-sc-id="' + sid + '"]'); if (b) b.click(); };
  pick('existing');
  await wait(2500);
  out.push(state('existing again'));
  pick('s2');
  await wait(2500);
  out.push(state('copy again'));
  return JSON.stringify(out);
}
