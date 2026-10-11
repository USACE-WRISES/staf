// SFARI's scenarios as a user works them (capture.py --actions): score Existing Conditions, add a
// scenario (a copy, named and described), change its scores, then open Compare. Returns what it did.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const q = (sel) => sec.querySelector(sel);
  const set = (el, value) => {
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const done = [];
  q('.sfari-suggest-chip').click();
  await wait(1500);
  q('.sfari-accept').click();
  done.push('scored');
  await wait(2500);
  q('[data-sc-action="new"]').click();
  await wait(2500);
  set(document.getElementById(tool + '-staf_sc_name'), 'Restored buffer');
  set(document.getElementById(tool + '-staf_sc_desc'), 'Plant 100 ft of riparian forest.');
  await wait(800);
  document.getElementById(tool + '-staf_sc_save').click();
  done.push('added');
  await wait(3000);
  const sel = sec.querySelectorAll('.sfari-likert-select')[1];
  set(sel, 'Strongly Disagree');
  await wait(1500);
  const slider = q('.sfari-fscore');
  set(slider, 6);
  done.push('rescored');
  await wait(2500);
  q('[data-sc-action="compare"]').click();
  done.push('compare');
  await wait(3000);
  return done.join(',') + ' | ' + (document.querySelector('#shiny-modal .modal-title') || {}).textContent;
}
