// SFARI's worksheet as a user works it (capture.py --actions): the first metric's suggested rating
// used, the second rated by hand, a note, the suggested function score accepted, the next function
// scored on the slider with a note, then a jump to Streamflow regime. Returns what it did.
async (tool) => {
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
}
