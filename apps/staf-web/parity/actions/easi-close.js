// EASI's worksheet after the screening (capture.py --actions): the report the Shiny EASI opens on its
// own is closed with its ✕ (the static app shows its notice instead), and the notice has gone.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const x = document.getElementById(tool + '-close_modal_x');
  if (x) x.click();
  await wait(5500);
  return x ? 'closed the report' : 'no report open';
}
