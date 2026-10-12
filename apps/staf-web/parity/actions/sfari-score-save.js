// SFARI's worksheet worked (sfari-score.js), then the header's Save (save.js): capture.py --actions with
// --download, for the assessment file both apps write.
async (tool) => {
  const said = await (async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const q = (sel) => sec.querySelector(sel);
  const done = [];
  q('.sfari-suggest-chip').click();
  done.push('use');
  await wait(1500);
  const sel = sec.querySelectorAll('.sfari-likert-select')[1];
  sel.value = 'Agree';
  sel.dispatchEvent(new Event('change', { bubbles: true }));
  done.push('rate');
  await wait(1500);
  const note = sec.querySelectorAll('.sfari-metric-note')[1];
  note.value = 'Roads along the valley floor.';
  note.dispatchEvent(new Event('input', { bubbles: true }));
  await wait(1500);
  q('.sfari-accept').click();
  done.push('accept');
  await wait(2000);
  q('[data-nav="1"]').click();
  done.push('next');
  await wait(2500);
  const slider = q('.sfari-fscore');
  slider.value = 4;
  slider.dispatchEvent(new Event('input', { bubbles: true }));
  slider.dispatchEvent(new Event('change', { bubbles: true }));
  done.push('score');
  await wait(2000);
  const fnote = q('.sfari-fn-note');
  fnote.value = 'Storage limited by the culverts.';
  fnote.dispatchEvent(new Event('input', { bubbles: true }));
  await wait(1500);
  q('.sfari-nav-fn[data-idx="3"]').click();
  done.push('jump');
  await wait(2500);
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
