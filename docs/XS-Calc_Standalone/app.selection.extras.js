// app.selection.extras.js (RE-REVISED)
// Enhances selection on #xsTable with:
//  • Crossing window (drag-rectangle) that selects all Station/Elevation cells the box touches
//  • Shift + click on row headers (ID col) selects all rows between
//  • Shift + click on any cells selects all cells in rows between
//
// This revision ensures crossing-window drags never place a text caret in any input.
// Caret appears only on explicit edit (typing/backspace) or on double-click.
//
// Requires app.ui.js (selection utilities) to be loaded first.

(function(){
  const table = document.getElementById('xsTable');
  if(!table) return;
  const tbody = table.querySelector('tbody');

  // ---- Utilities from app.ui.js (used defensively) ----
  const hasFn = (f)=> typeof window[f] === 'function';
  const _getRows = hasFn('getRows') ? window.getRows : ()=>[...tbody.querySelectorAll('tr')];
  const _addRowCellsToSelection = hasFn('addRowCellsToSelection') ? window.addRowCellsToSelection : (tr)=>{
    const tdS = tr.querySelector('td.cell-station'); if(tdS) markSelected(tdS);
    const tdE = tr.querySelector('td.cell-elev');   if(tdE) markSelected(tdE);
    const tdN = tr.querySelector('td.cell-n');      if(tdN) markSelected(tdN);
  };
  const _addCellToSelection = hasFn('addCellToSelection') ? window.addCellToSelection : markSelected;
  const _clearCellSelection = hasFn('clearCellSelection') ? window.clearCellSelection : clearSelectedFallback;
  const _editableCellsInRow = hasFn('editableCellsInRow') ? window.editableCellsInRow : (tr)=>[
    tr.querySelector('td.cell-station'), tr.querySelector('td.cell-elev')
  ].filter(Boolean);
  const _focusGrid = hasFn('focusGrid') ? window.focusGrid : ()=>{ try{ table.focus(); }catch(_){} };

  function markSelected(td){ if(!td) return; td.classList.add('cell-selected'); }
  function unmarkSelected(td){ if(!td) return; td.classList.remove('cell-selected'); }
  function clearSelectedFallback(){ tbody.querySelectorAll('td.cell-selected').forEach(unmarkSelected); }

  // Ensure row-header (ID) selection visual is also cleared when the app clears selection.
  (function wrapClear(){
    if(!hasFn('clearCellSelection')) return;
    const orig = window.clearCellSelection;
    window.clearCellSelection = function(){
      try{ orig(); }catch(_){}
      try{
        tbody.querySelectorAll('td.cell-id.cell-selected, td.cell-id.rowhdr-selected')
             .forEach(td=> td.classList.remove('cell-selected','rowhdr-selected'));
      }catch(_){}
    };
  })();

  // ---- Shift anchor (row index) ----
  let anchorRowIdx = null;
  function setAnchorToRowIdx(idx){ anchorRowIdx = Number.isInteger(idx) ? idx : null; }

  function rowIndexOfEl(tdOrTr){
    const tr = tdOrTr?.closest('tr');
    if(!tr) return -1;
    const rows = _getRows();
    return rows.indexOf(tr);
  }

  function selectRowsRange(i1, i2, keepExisting=false){
    if(!keepExisting) _clearCellSelection();
    tbody.querySelectorAll('td.cell-id.cell-selected, td.cell-id.rowhdr-selected')
         .forEach(td=> td.classList.remove('cell-selected','rowhdr-selected'));
    const rows = _getRows();
    if(!rows.length) return;
    const a = Math.max(0, Math.min(i1, i2));
    const b = Math.min(rows.length-1, Math.max(i1, i2));
    for(let i=a;i<=b;i++){
      const tr = rows[i];
      _addRowCellsToSelection(tr);
      const hdr = tr.querySelector('td.cell-id');
      if(hdr){ hdr.classList.add('rowhdr-selected','cell-selected'); }
    }
    _focusGrid();
  }

  // ---- Shift + click behaviors ----
  tbody.addEventListener('click', (e)=>{
    const td = e.target.closest('td');
    if(!td) return;

    if(suppressNextClick){ // from rectangle drag completion
      e.stopImmediatePropagation(); e.preventDefault(); suppressNextClick=false; return;
    }

    // Shift + row header → select rows range
    if(e.shiftKey && td.classList.contains('cell-id')){
      e.stopImmediatePropagation(); e.preventDefault();
      const idx = rowIndexOfEl(td);
      if(idx < 0){ setAnchorToRowIdx(null); return; }
      if(anchorRowIdx==null) selectRowsRange(idx, idx, false);
      else selectRowsRange(anchorRowIdx, idx, false);
      setAnchorToRowIdx(idx);
      return;
    }

    // Shift + any cell → select all cells in rows between
    if(e.shiftKey && (td.classList.contains('cell-station') || td.classList.contains('cell-elev') || td.classList.contains('cell-n'))){
      e.stopImmediatePropagation(); e.preventDefault();
      const idx = rowIndexOfEl(td);
      if(idx < 0){ setAnchorToRowIdx(null); return; }
      if(anchorRowIdx==null) selectRowsRange(idx, idx, false);
      else selectRowsRange(anchorRowIdx, idx, false);
      setAnchorToRowIdx(idx);
      return;
    }

    // No shift → set new anchor row
    if(!e.shiftKey){
      const idx = rowIndexOfEl(td);
      if(idx >= 0) setAnchorToRowIdx(idx);
    }
  }, true); // capture

  // ---- Crossing window selection (drag rectangle) ----
  let draggingPossibly = false;   // we pressed mouse down inside the table
  let rectActive = false;         // we converted into a rectangle drag
  let startX = 0, startY = 0;
  let startTarget = null;
  let rectDiv = null;
  let candidates = null;
  let additive = false;           // ctrl/cmd held at drag start?
  let suppressNextClick = false;
  let prevBodyUserSelect = '';

  // Expose a tiny flag so other handlers can know a rect-selection is live (optional use)
  window.__xsRectSelecting = false;

  function ensureRectDiv(){
    if(rectDiv) return rectDiv;
    rectDiv = document.createElement('div');
    rectDiv.id = 'xsCrossRect';
    rectDiv.style.position = 'fixed';
    rectDiv.style.zIndex = '9999';
    rectDiv.style.pointerEvents = 'none';
    rectDiv.style.border = '1.5px dashed #0b57d0';
    rectDiv.style.background = 'rgba(11,87,208,0.15)';
    rectDiv.style.borderRadius = '4px';
    rectDiv.style.display = 'none';
    document.body.appendChild(rectDiv);
    return rectDiv;
  }

  function rectFromPoints(x1,y1,x2,y2){
    const left = Math.min(x1,x2), top = Math.min(y1,y2);
    const right = Math.max(x1,x2), bottom = Math.max(y1,y2);
    return { left, top, right, bottom, width:right-left, height:bottom-top };
  }
  function intersects(a,b){ return !(b.left > a.right || b.right < a.left || b.top > a.bottom || b.bottom < a.top); }
  function getRect(el){ return el.getBoundingClientRect(); }
  function updateRectUI(r){
    ensureRectDiv();
    rectDiv.style.display = 'block';
    rectDiv.style.left = r.left + 'px';
    rectDiv.style.top = r.top + 'px';
    rectDiv.style.width = Math.max(0, r.width) + 'px';
    rectDiv.style.height = Math.max(0, r.height) + 'px';
  }
  function hideRectUI(){ if(rectDiv){ rectDiv.style.display = 'none'; } }

  function beginRectangle(e){
    // include Station + Elevation cells (per requirement). Add ', td.cell-n' to include n.
    candidates = Array.from(tbody.querySelectorAll('td.cell-station, td.cell-elev'));
    additive = (e.ctrlKey || e.metaKey);

    // Blur input if drag started on an input so caret doesn't fight the drag
    if(startTarget && startTarget.tagName === 'INPUT'){ try{ startTarget.blur(); }catch(_){} }

    // Fresh selection unless additive
    if(!additive) _clearCellSelection();

    // Disable text selection while dragging
    prevBodyUserSelect = document.body.style.userSelect;
    document.body.style.userSelect = 'none';

    rectActive = true;
    window.__xsRectSelecting = true;
    const r = rectFromPoints(startX,startY,e.clientX,e.clientY);
    updateRectUI(r);
    updateSelectionUnderRect(r, /*live=*/true);
  }

  function endRectangle(){
    hideRectUI();
    rectActive = false;
    window.__xsRectSelecting = false;
    draggingPossibly = false;
    candidates = null;
    additive = false;
    // restore text selection
    document.body.style.userSelect = prevBodyUserSelect || '';
    prevBodyUserSelect = '';
    suppressNextClick = true; // swallow synthetic click
    _focusGrid();
  }

  function updateSelectionUnderRect(r, live){
    if(!candidates) return;

    // When additive, do not remove previous selection. When not additive, live preview clears & rebuilds.
    if(!additive){
      _clearCellSelection();
      // Also clear row header visuals (we're selecting individual cells)
      tbody.querySelectorAll('td.cell-id.cell-selected, td.cell-id.rowhdr-selected')
           .forEach(td=> td.classList.remove('cell-selected','rowhdr-selected'));
    }

    for(const td of candidates){
      const cr = getRect(td);
      if(intersects(r, cr)){ _addCellToSelection(td); }
    }
  }

  // Prevent the browser from focusing inputs on any left-button down inside the table.
  // This guarantees no caret appears unless the user explicitly starts editing.
  document.addEventListener('mousedown', (e)=>{
    const inTable = e.target.closest('#xsTable');
    if(!inTable) return;
    if(e.button !== 0) return; // left button only

    // Preempt default focus & caret placement
    e.preventDefault();

    // Initialize drag state (we'll decide later if it's a rectangle)
    draggingPossibly = true;
    rectActive = false;
    startX = e.clientX;
    startY = e.clientY;
    startTarget = e.target;
    suppressNextClick = false;
  }, true); // capture

  document.addEventListener('mousemove', (e)=>{
    if(!draggingPossibly) return;

    const dx = Math.abs(e.clientX - startX), dy = Math.abs(e.clientY - startY);
    const threshold = 6; // px until we treat it as a rectangle drag
    if(!rectActive && (dx>threshold || dy>threshold)){
      beginRectangle(e);
    }

    if(rectActive){
      // While the rectangle is active, take control: prevent table's own drag handling
      e.preventDefault();
      e.stopPropagation();
      const r = rectFromPoints(startX,startY,e.clientX,e.clientY);
      updateRectUI(r);
      updateSelectionUnderRect(r, /*live=*/true);
    }
  }, true);

  document.addEventListener('mouseup', (e)=>{
    if(!draggingPossibly) return;

    if(rectActive){
      e.preventDefault();
      e.stopPropagation();
      endRectangle();
    }else{
      // Not enough movement: treat as a normal click; let app.ui.js handle it.
      draggingPossibly = false;
      rectActive = false;
      window.__xsRectSelecting = false;
      startTarget = null;
    }
  }, true);

  // Swallow the click that follows a rectangle drag so it doesn’t override selection
  document.addEventListener('click', (e)=>{
    if(!suppressNextClick) return;
    suppressNextClick = false;
    e.stopImmediatePropagation();
    e.preventDefault();
  }, true);
})();
