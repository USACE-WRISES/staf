// The Get Forms preview (capture.py --actions, after --assess or --open): opens the dialog, shows the
// "Field forms preview" pill and waits for its frame; returns the frame's address kind and the size and
// first bytes of the document it shows, so the capture says whether the worksheet loaded.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const open = sec.querySelector('[data-desktop-metrics], [data-forms], [data-field-forms]');
  if (!open) return 'no Get Forms button';
  open.click();
  const tabs = '#shiny-modal.show #' + tool + '-ff_tabs';
  for (let i = 0; i < 120 && !document.querySelector(tabs); i++) await wait(250);
  const pill = [...document.querySelectorAll(tabs + ' a.nav-link')].find((a) => /preview/i.test(a.textContent));
  if (pill) pill.click();
  let frame = null;
  for (let i = 0; i < 80; i++) {
    frame = document.querySelector('#' + tool + '-ff_preview iframe');
    if (frame && frame.getAttribute('src')) break;
    await wait(250);
  }
  if (!frame) return 'no preview frame';
  const src = frame.getAttribute('src');
  let info = '';
  try {
    const buf = new Uint8Array(await (await fetch(src)).arrayBuffer());
    info = buf.length + ' bytes, ' + String.fromCharCode(...buf.slice(0, 8));
  } catch (e) {
    info = 'fetch failed: ' + e;
  }
  await wait(1500);
  return 'preview ' + src.split(':')[0] + ' ' + info;
}
