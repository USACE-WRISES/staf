// SFARI's cross-section hydraulics tool (capture.py --actions): the first hydraulics function, its
// Cross-section hydraulics button, then a target discharge typed in. The dialog stays open for the
// capture. Returns what it did.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const done = [];
  const fns = [...sec.querySelectorAll('.sfari-nav-fn')];
  const hyd = fns.find((a) => /Low-flow|High-flow|Floodplain/i.test(a.textContent));
  if (hyd) { hyd.click(); done.push('fn'); }
  await wait(2500);
  const open = sec.querySelector('[data-xs]');
  if (open) { open.click(); done.push('open'); }
  await wait(3000);
  const q = document.getElementById(tool + '-xs_targetq');
  if (q) {
    q.value = '40';
    q.dispatchEvent(new Event('input', { bubbles: true }));
    q.dispatchEvent(new Event('change', { bubbles: true }));
    done.push('q');
  }
  await wait(3000);
  return done.join(',');
}
