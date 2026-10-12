// DEEP's worksheet worked (deep-measure.js), then the header's Save (save.js): capture.py --actions with
// --download, for the assessment file both apps write.
async (tool) => {
  const said = await (async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const q = (sel) => sec.querySelector(sel);
  const set = (el, value, kinds) => {
    el.value = value;
    (kinds || ['input', 'change']).forEach((k) => el.dispatchEvent(new Event(k, { bubbles: true })));
  };
  const done = [];
  const inputs = sec.querySelectorAll('.deep-metric-input');
  if (inputs[0]) { set(inputs[0], '12.5', ['input']); done.push('value'); }
  await wait(2000);
  const na = sec.querySelectorAll('.deep-na')[1];
  if (na) { na.checked = true; na.dispatchEvent(new Event('change', { bubbles: true })); done.push('na'); }
  await wait(2000);
  const note = sec.querySelector('textarea[data-mid-note]');
  if (note) { set(note, 'Measured at the riffle.', ['input']); done.push('note'); }
  await wait(1500);
  q('[data-nav="1"]').click();
  done.push('next');
  await wait(2500);
  const second = sec.querySelector('.deep-metric-input');
  if (second) { set(second, '3', ['input']); done.push('value2'); }
  await wait(2000);
  const fns = sec.querySelectorAll('.sfari-nav-fn');
  const far = fns[Math.min(fns.length - 1, 9)];
  if (far) { far.click(); done.push('jump'); }
  await wait(2500);
  q('[data-sc-action="new"]').click();
  await wait(2500);
  set(document.getElementById(tool + '-staf_sc_name'), 'Restored riffle');
  await wait(800);
  document.getElementById(tool + '-staf_sc_save').click();
  done.push('added');
  await wait(3000);
  const again = sec.querySelector('.deep-metric-input');
  if (again) { set(again, '0', ['input']); done.push('value3'); }
  await wait(2000);
  q('[data-sc-action="compare"]').click();
  done.push('compare');
  await wait(3000);
  return done.join(',');
})()
  const saved = await (async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const a = document.getElementById(tool + '-save_session');
  for (let i = 0; i < 100 && a.classList.contains('disabled'); i++) await wait(100);
  a.removeAttribute('target');
  a.click();
  await wait(2000);
  return 'save';
})()
  return said + ',' + saved
}
