// EASI's worksheet on High flow dynamics (capture.py --actions): the report closed, function 6 shown.
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const x = document.getElementById(tool + '-close_modal_x');
  if (x) x.click();
  await wait(3000);
  document.querySelector('.staf-section[data-staf-tool="' + tool + '"] .sfari-nav-fn[data-idx="5"]').click();
  await wait(3000);
  return 'ok';
}
