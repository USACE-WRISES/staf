// EASI's report scrolled to its cross-section (capture.py --actions): the screenshot shows the block.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let t = 0; t < 40 && !document.querySelector('.modal.show #' + tool + '-report'); t++) await wait(250);
  const wrap = document.querySelector('.modal.show .easi-xsection-wrap');
  if (wrap) wrap.scrollIntoView({ block: 'center' });
  await wait(800);
  const img = wrap && wrap.querySelector('img');
  return JSON.stringify({ block: !!wrap, image: img ? [img.naturalWidth, img.naturalHeight] : null,
    panel: wrap ? wrap.querySelector('.easi-xs-panel').innerText : null });
}
