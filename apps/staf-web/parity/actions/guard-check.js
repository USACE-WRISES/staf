// The leave-page guard (capture.py --open FILE --actions): the page warns before it is left only while
// the tool's work differs from the last Save or Open. After Open nothing is unsaved; an edit makes it
// so; Save clears it. Returns each state, "warns" or "quiet".
async (tool) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const state = () => {
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented ? 'warns' : 'quiet';
  };
  const out = ['opened:' + state()];
  const sec = document.querySelector('.staf-section[data-staf-tool="' + tool + '"]');
  const field = sec.querySelector('.sfari-likert-select, .deep-metric-input, .easi-note-ta');
  if (field) {
    if (field.tagName === 'SELECT') field.value = field.options[field.options.length - 1].value;
    else field.value = field.classList.contains('easi-note-ta') ? 'An edit.' : '7';
    ['input', 'change', 'blur'].forEach((k) => field.dispatchEvent(new Event(k, { bubbles: true })));
  }
  await wait(1500);
  out.push('edited:' + state());
  const a = document.getElementById(tool + '-save_session');
  a.removeAttribute('target');
  a.click();
  await wait(2500);
  out.push('saved:' + state());
  return out.join(',');
}
