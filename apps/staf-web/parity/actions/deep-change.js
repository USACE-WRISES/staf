// DEEP's Change (capture.py --actions after --delineate): the Basin pane's Change link, the dialog's
// first card set to its last version, then its Select. Returns what it did and the pane's assessment.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const done = [];
  for (let i = 0; i < 60 && !document.getElementById(tool + '-change_assessment'); i++) await wait(500);
  const link = document.getElementById(tool + '-change_assessment');
  if (!link) return 'no change link';
  link.click();
  done.push('open');
  await wait(2500);
  const sel = document.getElementById(tool + '-assess_ver_0');
  if (sel) {
    sel.value = sel.options[sel.options.length - 1].value;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    done.push('version ' + sel.value);
  }
  await wait(1500);
  const dialog = (document.querySelector('#shiny-modal .modal-body') || {}).innerText || '';
  document.getElementById(tool + '-assess_pick_0').click();
  done.push('pick');
  await wait(3500);
  const notes = [...document.querySelectorAll('.shiny-notification-content-text')].map((e) => e.textContent.trim());
  const pane = (document.querySelector('.deep-pane-assess') || {}).innerText || '';
  return done.join(',') + ' | ' + notes.join(' / ') + ' | ' + pane.replace(/\s+/g, ' ') + ' | ' + dialog.replace(/\s+/g, ' ').slice(0, 300);
}
