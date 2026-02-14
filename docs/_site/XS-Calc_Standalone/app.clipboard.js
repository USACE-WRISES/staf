// app.clipboard.js (RE-REVISED)
// Excel-compatible copy/cut/paste for the Cross-Section table + Copy/Paste buttons.
// Major fixes:
//  • Smarter delimiter detection (tab, comma, semicolon, or whitespace)
//  • Paste button now ingests all rows. If no cell is selected, it REPLACES the table
//    with the pasted rows. If a cell is selected, it pastes starting at that cell.
//  • Keeps header auto-skip and LB/RB recognition.
//
// Requires: app.ui.js loaded first (selection helpers).

(function(){
  const XS_TABLE_ID = 'xsTable';

  // ---------- helpers ----------

  function isGridContext(){
    const grid = document.getElementById(XS_TABLE_ID);
    if(!grid) return false;
    if(typeof selectedCells !== 'undefined' && selectedCells && selectedCells.size > 0) return true;
    const el = document.activeElement;
    return !!(el && (el.id === XS_TABLE_ID || grid.contains(el)));
  }
  function getActiveEditingInputSafe(){
    try { return (typeof getActiveEditingInput === 'function') ? getActiveEditingInput() : null; }
    catch { return null; }
  }
  function looksStage(s){
    const t = String(s||'').trim().toUpperCase();
    return t === 'LB' || t === 'RB';
  }
  function looksNumber(s){
    const t = String(s||'').trim();
    if(!t) return false;
    const s1 = t.replace(/,/g,''); // allow thousands separators
    return Number.isFinite(parseFloat(s1));
  }
  // Preserve decimal places exactly as typed, but sanitize for <input type="number">
  function normalizeNum(s){
    let t = String(s ?? '').trim();
    if(!t) return '';
    // normalize unicode minus and non-breaking/thin spaces
    t = t.replace(/\u2212/g, '-').replace(/[\u00A0\u2007\u202F]/g, ' ');
    // remove standard thousands separators (US-style)
    t = t.replace(/,/g, '');
    // allow forms like "12", "12.", "12.0", ".5", "-.25", "1.23e-4"
    const valid = /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(t);
    if(valid) return t;
    // fallback: try to parse; if ok, return minimal string (no padding)
    const v = parseFloat(t);
    return Number.isFinite(v) ? String(v) : '';
  }


  function getSelectionBounds(){
    if(!selectedCells || selectedCells.size===0) return null;
    const rows = getRows();
    let rMin=Infinity,rMax=-1,cMin=Infinity,cMax=-1;
    selectedCells.forEach(td=>{
      if(!isEditableCell(td)) return;
      const tr = td.closest('tr');
      const r = rows.indexOf(tr);
      const c = cellColIndex(td);
      if(r>=0 && c>=0){
        if(r<rMin) rMin=r; if(r>rMax) rMax=r;
        if(c<cMin) cMin=c; if(c>cMax) cMax=c;
      }
    });
    return (rMin===Infinity) ? null : {rMin,rMax,cMin,cMax};
  }

  function readCellValue(td){
    const inp = td?.querySelector('input');
    return inp ? String(inp.value ?? '') : '';
  }

  function buildCopyMatrix(){
    const rows = getRows();
    if(!rows.length) return [];
    const b = getSelectionBounds();
    const mat = [];
    if(b){
      for(let r=b.rMin; r<=b.rMax; r++){
        const row=[];
        for(let c=b.cMin; c<=b.cMax; c++){
          const td = getCellByRowCol(r,c);
          row.push(readCellValue(td));
        }
        mat.push(row);
      }
    }else{
      // Copy all used rows; visible editable columns only
      const used = rows.filter(tr=>{
        const s = tr.querySelector('.station')?.value?.trim() || '';
        const e = tr.querySelector('.elevation')?.value?.trim() || '';
        const n = tr.querySelector('.nval')?.value?.trim() || '';
        return s!=='' || e!=='' || n!=='';
      });
      if(!used.length) return [];
      const colCount = editableCellsInRow(used[0]).length; // station, elevation, (n if visible)
      used.forEach(tr=>{
        const cols = editableCellsInRow(tr);
        const row=[];
        for(let i=0;i<colCount;i++) row.push(readCellValue(cols[i] || null));
        mat.push(row);
      });
    }
    return mat;
  }

  function matrixToTSV(mat){
    return mat.map(row => row.map(v => String(v ?? '')).join('\t')).join('\n');
  }

  // --- NEW: smarter delimiter detection & parsing ---
  function detectDelimiter(lines){
    // Examine up to first 50 non-empty lines
    const sample = lines.filter(Boolean).slice(0, 50);
    const candidates = [
      { delim: '\t', score: 0 },
      { delim: ',',  score: 0 },
      { delim: ';',  score: 0 },
      { delim: /\s+/, score: 0 } // one-or-more whitespace as a fallback
    ];
    for(const c of candidates){
      let totalCols = 0, rows = 0;
      for(const ln of sample){
        const parts = (c.delim instanceof RegExp) ? ln.trim().split(c.delim) : ln.split(c.delim);
        const nonEmptyCols = parts.filter(p => p !== '').length;
        if(nonEmptyCols>0){ totalCols += nonEmptyCols; rows++; }
      }
      c.score = rows ? totalCols / rows : 0;
    }
    candidates.sort((a,b)=> b.score - a.score);
    return candidates[0].delim;
  }

  function parseClipboard(text){
    const t = String(text||'').replace(/\r\n/g,'\n').replace(/\r/g,'\n');
    const rawLines = t.split('\n');
    const lines = rawLines.filter((ln,i)=> !(i===rawLines.length-1 && ln.trim()==='')); // drop trailing blank
    if(!lines.length) return [];

    const delim = detectDelimiter(lines);
    const rows = lines.map(line => {
      const parts = (delim instanceof RegExp) ? line.trim().split(delim) : line.split(delim);
      return parts.map(s => s == null ? '' : String(s));
    });

    return rows;
  }

  function maybeDropHeader(mat){
    if(!mat || !mat.length) return mat;
    const first = mat[0] || [];
    const a = first[0] ?? '', b = first[1] ?? '';
    // If first two tokens are neither numbers nor LB/RB, treat first row as header.
    if(!looksNumber(a) && !looksStage(a) && !looksNumber(b) && !looksStage(b)){
      return mat.slice(1);
    }
    return mat;
  }

  function writeIntoCell(rowIdx, colIdx, raw){
    const td = getCellByRowCol(rowIdx, colIdx);
    if(td){
      const inp = td.querySelector('input');
      if(inp){ inp.value = normalizeNum(raw); }
    }else{
      // If 'n' is hidden and target is the 3rd editable column (index 2), write to .nval directly.
      if(colIdx === 2){
        const tr = getRows()[rowIdx];
        const nv = tr?.querySelector('input.nval');
        if(nv) nv.value = normalizeNum(raw);
      }
    }
  }

  function ensureRowsUpTo(targetRow){
    while(targetRow >= getRows().length - 1){
      addRow('','','','');
    }
  }

  function applyMatrixAt(startTd, mat){
    if(!startTd || !mat || !mat.length) return;

    const rowsArr = getRows();
    const rStart = rowsArr.indexOf(startTd.closest('tr'));
    const cStart = cellColIndex(startTd);
    if(rStart < 0 || cStart < 0) return;

    mat = maybeDropHeader(mat);

    for(let i=0;i<mat.length;i++){
      const targetRow = rStart + i;
      ensureRowsUpTo(targetRow);
      const tr = getRows()[targetRow];
      let stageTag = null;

      const rowTokens = mat[i] || [];
      for(let j=0;j<rowTokens.length;j++){
        const token = rowTokens[j];
        if(token == null) continue;
        const t = String(token).trim();
        if(looksStage(t)){ stageTag = t.toUpperCase(); continue; }
        writeIntoCell(targetRow, cStart + j, t);
      }

      if(stageTag){
        const sel = tr.querySelector('.stagetag');
        if(sel){ sel.value = stageTag; enforceUniqueStageTag(sel); }
      }
    }

    ensureTrailingBlankRow();
    renumberIDs();
    clearCellSelection();
    compute();
  }

  // NEW: replace entire XS table with matrix (used when pasting with no active selection)
  function applyMatrixReplacingTable(mat){
    if(!Array.isArray(mat) || !mat.length) return;
    mat = maybeDropHeader(mat);

    const tbody = document.querySelector('#xsTable tbody');
    if(!tbody) return;
    tbody.innerHTML = '';

    // Accept rows as: [station, elevation, (optional LB/RB), (optional n)]
    const toNumStr = (s)=> {
      const t = normalizeNum(s);
      return t === '' ? null : t;
    };
    function asStageToken(toks){
      // Find LB/RB anywhere in the row
      for(const tok of toks){
        const T = String(tok||'').trim().toUpperCase();
        if(T==='LB' || T==='RB') return T;
      }
      return '';
    }
    function maybeNStr(toks){
      for(let i=2;i<toks.length;i++){
        const t = toNumStr(toks[i]);
        if(t != null) return t; // first valid numeric-looking token after station/elev
      }
      return '';
    }

    let rowsAdded = 0;
    for(const rawRow of mat){
      if(!rawRow || !rawRow.length) continue;
      // Prefer first two numeric-looking fields for station & elevation
      const st = toNumStr(rawRow[0]);
      const el = toNumStr(rawRow[1]);
      if(st == null || el == null) continue;
      const stage = asStageToken(rawRow);
      const nVal = maybeNStr(rawRow);
      addRow(st, el, stage, nVal);
      rowsAdded++;
    }

    ensureTrailingBlankRow();
    renumberIDs();
    clearCellSelection();
    compute();

    const msgEl = document.getElementById('messages');
    if(msgEl) msgEl.textContent = rowsAdded ? `Pasted ${rowsAdded} row(s).` : 'Nothing pasted (no valid rows detected).';
  }

  function isXsLocked(){
    const auto = document.getElementById('designerAutoApply');
    return !!(auto && auto.checked);
  }

  // ---------- COPY (keyboard) ----------
  document.addEventListener('copy', (e)=>{
    if(getActiveEditingInputSafe()) return; // allow copying text inside an input normally
    if(!isGridContext()) return;
    const mat = buildCopyMatrix();
    if(!mat.length) return;
    const tsv = matrixToTSV(mat);
    try { e.clipboardData.setData('text/plain', tsv); } catch(_){}
    try { e.clipboardData.setData('text/tab-separated-values', tsv); } catch(_){}
    e.preventDefault();
  });

  // ---------- CUT (keyboard) ----------
  document.addEventListener('cut', (e)=>{
    if(getActiveEditingInputSafe()) return;
    if(!isGridContext()) return;
    const mat = buildCopyMatrix();
    if(!mat.length) return;
    const tsv = matrixToTSV(mat);
    try { e.clipboardData.setData('text/plain', tsv); } catch(_){}
    try { e.clipboardData.setData('text/tab-separated-values', tsv); } catch(_){}
    e.preventDefault();
    if(typeof clearSelectedCells === 'function') clearSelectedCells();
  });

  // ---------- PASTE (keyboard) ----------
  document.addEventListener('paste', (e)=>{
    if(getActiveEditingInputSafe()) return; // let browser paste into the focused input
    if(!isGridContext()) return;

    if(isXsLocked()){
      alert('Turn off “Update XS Data” in the Designer tab before pasting into the Cross‑Section table.');
      e.preventDefault();
      return;
    }

    const txt = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
    if(!txt) return;

    const mat = parseClipboard(txt);
    const haveSelection = (typeof selectedCells !== 'undefined' && selectedCells && selectedCells.size > 0);
    const start = haveSelection
      ? ((typeof singleSelectedCell === 'function' && singleSelectedCell())
          || (typeof firstSelectedCell === 'function' && firstSelectedCell())
          || getCellByRowCol(0,0))
      : null;

    e.preventDefault();
    if(start){ applyMatrixAt(start, mat); }
    else { applyMatrixReplacingTable(mat); }

    if(typeof focusGrid === 'function') focusGrid();
  });

  // ---------- Buttons: Copy / Paste ----------
  function flash(btn){
    try{
      btn.classList.add('clicked');
      setTimeout(()=>btn.classList.remove('clicked'),160);
    }catch(_){}
  }

  async function copySelectionToClipboard(btn){
    const mat = buildCopyMatrix();
    if(!mat.length){ alert('Nothing to copy. Select cells or ensure the table has data.'); return; }
    const tsv = matrixToTSV(mat);
    try{
      if(navigator.clipboard && navigator.clipboard.writeText){
        await navigator.clipboard.writeText(tsv);
      }else{
        // Fallback: hidden textarea + execCommand
        const ta = document.createElement('textarea');
        ta.value = tsv; ta.style.position='fixed'; ta.style.opacity='0'; ta.style.pointerEvents='none';
        document.body.appendChild(ta); ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      flash(btn);
    }catch(err){
      console.error(err);
      alert('Copy failed. Try Ctrl/Cmd+C.');
    }
  }

    // ---- Manual multi-line paste dialog (replaces window.prompt fallback) ----
  function manualPasteDialog(){
    return new Promise((resolve)=>{
      let backdrop = document.getElementById('pasteModal');
      if(!backdrop){
        backdrop = document.createElement('div');
        backdrop.id = 'pasteModal';
        backdrop.className = 'modalBackdrop';
        backdrop.innerHTML = `
          <div class="modal" role="dialog" aria-modal="true" aria-labelledby="pasteTitle">
            <header style="display:flex;align-items:center;justify-content:space-between;padding:0;border-bottom:none">
              <h3 id="pasteTitle" style="margin:0;font-size:1.05rem;">Paste data</h3>
              <div class="rightBtns">
                <button id="pasteCancel" class="btnSecondary" aria-label="Cancel">Cancel</button>
                <button id="pasteOk" class="btnPrimary" aria-label="OK">OK</button>
              </div>
            </header>
            <div style="display:grid;gap:8px">
              <p class="small" style="margin:0 0 6px;">
                Paste tab-, comma-, semicolon-, or whitespace-separated data below. Click <b>OK</b> or Press <b>Ctrl/Cmd+Enter</b> to apply.
              </p>
              <textarea id="pasteText" rows="12" spellcheck="false"
                        style="width:100%;min-height:260px;border:1px solid #e0e0e0;border-radius:10px;padding:10px;font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace;"></textarea>
            </div>
          </div>`;
        document.body.appendChild(backdrop);
      }

      const ta = backdrop.querySelector('#pasteText');
      const btnOk = backdrop.querySelector('#pasteOk');
      const btnCancel = backdrop.querySelector('#pasteCancel');

      function close(result){
        // hide and cleanup handlers
        backdrop.style.display = 'none';
        document.body.classList.remove('modal-open');
        window.removeEventListener('keydown', onKeydown, true);
        resolve(result);
      }
      function onKeydown(e){
        if(e.key === 'Escape'){ e.preventDefault(); close(null); }
        if((e.key === 'Enter') && (e.metaKey || e.ctrlKey)){ e.preventDefault(); close(ta.value); }
      }

      // show
      backdrop.style.display = 'flex';
      document.body.classList.add('modal-open');
      ta.value = '';
      setTimeout(()=>{ ta.focus(); }, 0);

      // wire
      window.addEventListener('keydown', onKeydown, true);
      btnOk.onclick = ()=> close(ta.value);
      btnCancel.onclick = ()=> close(null);
    });
  }



  async function pasteFromClipboard(btn){
    if(isXsLocked()){
      alert('Turn off “Update XS Data” in the Designer tab before pasting into the Cross‑Section table.');
      return;
    }
    let txt = '';
    try{
      if(navigator.clipboard && navigator.clipboard.readText){
        txt = await navigator.clipboard.readText();
      }else{
        throw new Error('Clipboard API unavailable');
      }
    }catch(err){
      // Multi-line fallback dialog (works when Clipboard API is blocked)
      const manual = await manualPasteDialog();
      if(manual == null) return; // canceled
      txt = manual;
    }
    if(!txt || !txt.trim()){ alert('Clipboard is empty.'); return; }

    const mat = parseClipboard(txt);
    const haveSelection = (typeof selectedCells !== 'undefined' && selectedCells && selectedCells.size > 0);
    const start = haveSelection
      ? ((typeof singleSelectedCell === 'function' && singleSelectedCell())
          || (typeof firstSelectedCell === 'function' && firstSelectedCell())
          || getCellByRowCol(0,0))
      : null;

    if(start){ applyMatrixAt(start, mat); }
    else { applyMatrixReplacingTable(mat); }

    if(typeof focusGrid === 'function') focusGrid();
    flash(btn);
  }

  // Wire up buttons if present
  window.addEventListener('DOMContentLoaded', ()=>{
    const btnCopy = document.getElementById('btnCopyXS');
    const btnPaste = document.getElementById('btnPasteXS');
    if(btnCopy) btnCopy.addEventListener('click', ()=> copySelectionToClipboard(btnCopy));
    if(btnPaste) btnPaste.addEventListener('click', ()=> pasteFromClipboard(btnPaste));
  });
})();
