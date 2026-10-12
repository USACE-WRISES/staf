// The header's Save (capture.py --actions, with --download): its link pressed once the tool has
// registered it (a Shiny download link is off until then). A Shiny link opens its file in a tab of its
// own, which a headless browser blocks for a script's click: here it downloads in place.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const a = document.getElementById(tool + '-save_session');
  for (let i = 0; i < 100 && a.classList.contains('disabled'); i++) await wait(100);
  a.removeAttribute('target');
  a.click();
  await wait(2000);
  return 'save';
}
