/* filter_logic.js
   Runs inside filter.html. Receives XS from parent, previews filtering, and returns filtered XS.
   Unified messaging:
     Filter -> Main:  'filter-ready'
                      'filter-apply' { payload:{ points, LBStation, RBStation } }
                      'filter-close-request'
     Main   -> Filter: 'filter-xs-data' { payload:{ points, LBStation, RBStation, units } }
   Back-compat accepted: 'xs-filter-open', 'filter-init-data'
*/
(function(){
  'use strict';

  // ---------- State ----------
  let UNITS = 'US';               // 'US' or 'SI'
  let unitLen = 'ft';
  let orig = [];                  // [{x,z,tag?,n?}] ascending x
  let filtered = [];              // working copy for preview
  let lbX = null, rbX = null;     // bank stations (x)
  const plot = document.getElementById('plot');
  const ctx = plot.getContext('2d');

  // ---------- Utilities ----------
  function setCanvasDPR(){
    const dpr = window.devicePixelRatio || 1;
    const rect = plot.getBoundingClientRect();
    ctx.setTransform(1,0,0,1,0,0);
    plot.width = Math.max(1, Math.floor(rect.width * dpr));
    plot.height= Math.max(1, Math.floor(rect.height* dpr));
    ctx.scale(dpr, dpr);
  }
  function clonePts(a){ return a.map(p=>({x:+p.x, z:+p.z, tag:p.tag||'', n: (Number.isFinite(p.n)? p.n : undefined)})); }
  function nearestIndexToX(points, x){
    if(!Number.isFinite(x) || !Array.isArray(points) || !points.length) return -1;
    let best=-1, bestD=Infinity;
    for(let i=0;i<points.length;i++){
      const d = Math.abs(points[i].x - x);
      if(d < bestD){ best = i; bestD = d; }
    }
    return best;
  }
  function bankGuardSet(points){
    const set = new Set();
    points.forEach((p,i)=>{ if(p.tag==='LB' || p.tag==='RB') set.add(i); });
    // Include nearest indices to supplied LB/RB stations if tags not present
    const iLB = nearestIndexToX(points, lbX);
    const iRB = nearestIndexToX(points, rbX);
    if(iLB >= 0) set.add(iLB);
    if(iRB >= 0) set.add(iRB);
    // Also guard endpoints
    if(points.length){ set.add(0); set.add(points.length-1); }
    return set;
  }
  function findBanksFromTags(points){
    const lb = points.find(p=>p.tag==='LB');
    const rb = points.find(p=>p.tag==='RB');
    return { LB: lb ? lb.x : null, RB: rb ? rb.x : null };
  }
  function inRegion(p, region){
    if(region==='all' || lbX==null || rbX==null) return true;
    if(region==='chan') return p.x >= lbX && p.x <= rbX;
    if(region==='lob')  return p.x < lbX;
    if(region==='rob')  return p.x > rbX;
    if(region==='ob')   return (p.x < lbX || p.x > rbX);
    return true;
  }
  function triArea(a,b,c){ return Math.abs( (a.x*(b.z-c.z) + b.x*(c.z-a.z) + c.x*(a.z-b.z)) / 2 ); }
  function slope(a,b){ const dx=b.x-a.x; return dx!==0 ? ((b.z-a.z)/dx) : Number.POSITIVE_INFINITY; }
  function perpDistPointToLine(p, a, b){
    const dx=b.x-a.x, dz=b.z-a.z;
    const den = Math.hypot(dx,dz);
    if(den===0) return Math.hypot(p.x-a.x, p.z-a.z);
    return Math.abs( dz*p.x - dx*p.z + (b.x*a.z) - (b.z*a.x) ) / den;
  }

  // ---------- Drawing ----------
  function draw(){
    setCanvasDPR();
    const rect = plot.getBoundingClientRect();
    const W = rect.width, H = rect.height;
    ctx.clearRect(0,0,W,H);
    if(!orig.length) return;

    const xs = (filtered.length? filtered : orig);
    const minX = Math.min(xs[0].x, ...orig.map(p=>p.x), ...xs.map(p=>p.x));
    const maxX = Math.max(xs[xs.length-1].x, ...orig.map(p=>p.x), ...xs.map(p=>p.x));
    const minZ = Math.min(...orig.map(p=>p.z), ...xs.map(p=>p.z));
    const maxZ = Math.max(...orig.map(p=>p.z), ...xs.map(p=>p.z));
    const padZ = (maxZ-minZ)*0.08 || 1;
    const y0 = minZ - padZ, y1 = maxZ + padZ;

    const pad = { l: 56, r: 12, t: 12, b: 36 };
    const w = Math.max(10, W - pad.l - pad.r);
    const h = Math.max(10, H - pad.t - pad.b);
    const xToPx = x => pad.l + ( (x - minX) / (maxX - minX || 1) ) * w;
    const zToPx = z => pad.t + h - ( (z - y0) / (y1 - y0 || 1) ) * h;

    // Axes
    ctx.strokeStyle = '#888'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l, pad.t+h); ctx.lineTo(pad.l+w, pad.t+h); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(pad.l, pad.t);   ctx.lineTo(pad.l, pad.t+h);   ctx.stroke();

    ctx.fillStyle = '#666'; ctx.font = '12px system-ui,-apple-system,Segoe UI,Roboto,Arial';
    ctx.textAlign='center'; ctx.fillText(`Station (${unitLen})`, pad.l + w/2, H-8);
    ctx.save(); ctx.translate(12, pad.t + h/2); ctx.rotate(-Math.PI/2);
    ctx.textAlign='center'; ctx.fillText(`Elevation (${unitLen})`, 0, 0); ctx.restore();

    // Grid ticks
    const xt = 6, yt = 5;
    for(let i=0;i<=xt;i++){
      const x = minX + (i/xt)*(maxX-minX), px = xToPx(x);
      ctx.strokeStyle='#eee'; ctx.beginPath(); ctx.moveTo(px, pad.t); ctx.lineTo(px, pad.t+h); ctx.stroke();
      ctx.fillStyle='#666'; ctx.textAlign='center'; ctx.fillText(formatTick(x), px, pad.t+h+14);
    }
    for(let i=0;i<=yt;i++){
      const z = y0 + (i/yt)*(y1-y0), pz = zToPx(z);
      ctx.strokeStyle='#eee'; ctx.beginPath(); ctx.moveTo(pad.l, pz); ctx.lineTo(pad.l+w, pz); ctx.stroke();
      ctx.fillStyle='#666'; ctx.textAlign='right'; ctx.fillText(formatTick(z), pad.l-8, pz+4);
    }

    // Original line
    ctx.strokeStyle = '#888'; ctx.lineWidth = 1.75;
    ctx.beginPath();
    orig.forEach((p,i)=>{ const X=xToPx(p.x), Y=zToPx(p.z); if(i===0) ctx.moveTo(X,Y); else ctx.lineTo(X,Y); });
    ctx.stroke();

    // Filtered line
    if(filtered.length){
      ctx.strokeStyle = '#0b57d0'; ctx.lineWidth = 2.2;
      ctx.beginPath();
      filtered.forEach((p,i)=>{ const X=xToPx(p.x), Y=zToPx(p.z); if(i===0) ctx.moveTo(X,Y); else ctx.lineTo(X,Y); });
      ctx.stroke();
    }

    // Bank indices (by tag or nearest station hints)
    const pts = filtered.length ? filtered : orig;
    const idxLB = nearestIndexToX(pts, lbX);
    const idxRB = nearestIndexToX(pts, rbX);

    // Points
    for(let i=0;i<pts.length;i++){
      const p = pts[i];
      const X=xToPx(p.x), Y=zToPx(p.z);
      const isBank = (p.tag==='LB' || p.tag==='RB' || i===idxLB || i===idxRB);
      const r = isBank ? 4.4 : 3.8;
      ctx.fillStyle = isBank ? '#d32f2f' : '#222';
      ctx.beginPath(); ctx.arc(X, Y, r, 0, Math.PI*2); ctx.fill();
    }
  }

  function formatTick(v){
    const av = Math.abs(v);
    const d = (av>=1000)?0:(av>=100)?0:(av>=10)?1:2;
    return v.toFixed(d);
  }

  // ---------- Tabs ----------
  document.querySelectorAll('.tab').forEach(tab=>{
    tab.addEventListener('click', ()=>{
      document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('panel-'+tab.dataset.tab).classList.add('active');
    });
  });

  // ---------- Controls ----------
  const tolX   = document.getElementById('tolX');
  const tolZ   = document.getElementById('tolZ');
  const colTol = document.getElementById('colTol');
  const minSlope = document.getElementById('minSlope');
  const regionSel= document.getElementById('near-region');
  const onlyIfOver = document.getElementById('onlyIfOver');
  const overN  = document.getElementById('overN');
  const targetN= document.getElementById('targetN');
  const unitsTag = document.getElementById('unitsTag');

  function setUnits(u){
    UNITS = (u==='SI') ? 'SI' : 'US';
    unitLen = (UNITS==='SI') ? 'm' : 'ft';
    document.getElementById('tolXUnit').textContent = `(${unitLen})`;
    document.getElementById('tolZUnit').textContent = `(${unitLen})`;
    document.getElementById('colZUnit').textContent = `(${unitLen})`;
    unitsTag.textContent = `Units: ${UNITS==='US' ? 'US (ft)' : 'SI (m)'}`;

    const defLen = (UNITS==='SI') ? 0.05*0.3048 : 0.05;
    if(!tolXTouched) tolX.value = defLen.toFixed( (UNITS==='SI')? 4 : 3 );
    if(!tolZTouched) tolZ.value = defLen.toFixed( (UNITS==='SI')? 4 : 3 );
    if(!colTolTouched) colTol.value = defLen.toFixed( (UNITS==='SI')? 4 : 3 );
  }
  let tolXTouched=false, tolZTouched=false, colTolTouched=false;
  tolX.addEventListener('input', ()=> tolXTouched=true);
  tolZ.addEventListener('input', ()=> tolZTouched=true);
  colTol.addEventListener('input', ()=> colTolTouched=true);

  // ---------- Near + Colinear filter ----------
  function filterNearAndColinear(){
    const pts = filtered.length ? filtered : orig;
    let work = clonePts(pts);
    const guard = bankGuardSet(work);
    const region = regionSel.value;

    // Only filter if > N points (optional)
    const mustExceed = !!onlyIfOver.checked;
    const over = parseInt(overN.value, 10) || 500;
    if(mustExceed && work.length <= over) return work;

    // Pass 1: Near filter (sequential)
    const tX = Number(tolX.value)||0, tZ = Number(tolZ.value)||0;
    if(tX<0 || tZ<0) return work;
    let out = [];
    out.push(work[0]);
    for(let i=0;i<work.length-1;i++){
      const a = work[i], b = work[i+1];
      if(inRegion(a,region) && inRegion(b,region) &&
         Math.abs(b.x - a.x) <= tX && Math.abs(b.z - a.z) <= tZ){
        const idxB = i+1;
        if(!guard.has(idxB)) continue; // drop b
      }
      out.push(b);
    }
    work = out;

    // Pass 2: Colinear filter (iterative)
    const vt = Number(colTol.value)||0;
    const minChange = Math.abs(Number(minSlope.value)||0);
    if(vt<0 || minChange<0) return work;

    let changed = true;
    while(changed){
      changed = false;
      const g2 = bankGuardSet(work);
      for(let i=0;i<work.length-2;i++){
        const a = work[i], b = work[i+1], c = work[i+2];
        if(!(inRegion(a,region) && inRegion(b,region) && inRegion(c,region))) continue;
        const midIdx = i+1;
        if(g2.has(midIdx)) continue; // guard LB/RB + endpoints
        const d = perpDistPointToLine(b, a, c);
        if(d <= vt + 1e-12){
          const m12 = slope(a,b), m13 = slope(a,c);
          if(!isFinite(m12) && !isFinite(m13)){
            work.splice(midIdx,1); changed = true; break;
          }
          const dSlope = Math.abs(m12 - m13);
          if(dSlope <= minChange + 1e-12){
            work.splice(midIdx,1); changed = true; break;
          }
        }
      }
    }
    return work;
  }

  // ---------- Minimize Area Change (Visvalingam) ----------
  function filterTrimToN(){
    const pts = filtered.length ? filtered : orig;
    let work = clonePts(pts);

    let N = parseInt(targetN.value, 10);
    if(!Number.isFinite(N) || N < 2) N = 2;
    if(N >= work.length) return work;

    const guards = bankGuardSet(work);

    const nodes = work.map((p,idx)=>({
      i: idx, p, prev: idx-1, next: idx+1,
      area: Infinity, bank: guards.has(idx)
    }));

    function triAreaLocal(i){
      const n = nodes[i]; if(!n) return;
      if(n.prev<0 || n.next>=nodes.length){ n.area = Infinity; return; }
      const a = nodes[n.prev].p, b = n.p, c = nodes[n.next].p;
      n.area = triArea(a,b,c);
    }

    for(let i=0;i<nodes.length;i++){ if(!nodes[i].bank) triAreaLocal(i); }

    let remaining = nodes.length;
    while(remaining > N){
      let bestIdx = -1, bestArea = Infinity;
      for(let i=0;i<nodes.length;i++){
        const n = nodes[i];
        if(!n || n.bank) continue;
        if(n.area < bestArea){ bestArea = n.area; bestIdx = i; }
      }
      if(bestIdx<0) break;
      const cur = nodes[bestIdx];
      if(cur.prev>=0) nodes[cur.prev].next = cur.next;
      if(cur.next<nodes.length) nodes[cur.next].prev = cur.prev;
      nodes[bestIdx] = null; remaining--;
      if(cur.prev>=0 && nodes[cur.prev] && !nodes[cur.prev].bank) triAreaLocal(cur.prev);
      if(cur.next<nodes.length && nodes[cur.next] && !nodes[cur.next].bank) triAreaLocal(cur.next);
    }

    const out = []; for(const n of nodes){ if(n) out.push(n.p); }
    out.sort((a,b)=>a.x-b.x);
    return out;
  }

  // ---------- Buttons ----------
  document.getElementById('restoreBtn').addEventListener('click', ()=>{
    filtered = clonePts(orig);
    draw();
  });
  document.getElementById('applyBtn').addEventListener('click', ()=>{
    const nearTabActive = document.querySelector('.tab.active')?.dataset?.tab === 'near';
    filtered = nearTabActive ? filterNearAndColinear() : filterTrimToN();
    draw();
  });
  document.getElementById('okBtn').addEventListener('click', ()=>{
    const out = filtered.length ? filtered : orig;
    const payload = {
      points: out.map(p=>({x:+p.x, z:+p.z})),
      LBStation: lbX,
      RBStation: rbX
    };
    try { window.parent.postMessage({ type:'filter-apply', payload }, '*'); } catch {}
    // legacy emit (best effort)
    try { window.parent.postMessage({ type:'xs-filter-apply', points: payload.points }, '*'); } catch {}
    try { window.parent.postMessage({ type:'filter-close-request' }, '*'); } catch {}
  });
  document.getElementById('cancelBtn').addEventListener('click', ()=>{
    try { window.parent.postMessage({ type:'filter-close-request' }, '*'); } catch {}
  });

  // ---------- Messaging ----------
  function acceptInitFromPayload(pl){
    if(!pl) return;
    setUnits(pl.units === 'SI' ? 'SI' : 'US');

    // Normalize incoming points: [{x,z}] or [{x,z,tag,n}]
    const arr = Array.isArray(pl.points) ? pl.points : [];
    const pts = arr
      .map(p => ({ x: Number(p.x), z: Number(p.z), tag: p.tag || '', n: Number.isFinite(p.n) ? Number(p.n) : undefined }))
      .filter(p => Number.isFinite(p.x) && Number.isFinite(p.z))
      .sort((a,b)=>a.x-b.x);

    orig = clonePts(pts);

    // Banks: prefer explicit, else incoming stations
    const tags = findBanksFromTags(orig);
    lbX = Number.isFinite(tags.LB) ? tags.LB : (Number.isFinite(pl.LBStation) ? +pl.LBStation : null);
    rbX = Number.isFinite(tags.RB) ? tags.RB : (Number.isFinite(pl.RBStation) ? +pl.RBStation : null);

    filtered = clonePts(orig);
    // Suggest a default target for trim
    const targetN = document.getElementById('targetN');
    if(targetN) targetN.value = Math.max(2, Math.min(300, Math.floor(orig.length/2)));
    draw();
  }

  window.addEventListener('message', (evt)=>{
    const data = evt.data || {};
    // New protocol
    if(data.type === 'filter-xs-data'){
      acceptInitFromPayload(data.payload || {});
      return;
    }
    // Back-compat: older wire used xs-filter-open {units, points}
    if(data.type === 'xs-filter-open'){
      acceptInitFromPayload({ units:data.units, points:data.points });
      return;
    }
    // Back-compat: very early iteration
    if(data.type === 'filter-init-data'){
      acceptInitFromPayload({ units:data.units, points:data.points });
      return;
    }
  });

  // Tell parent we're ready to receive data
  try{ window.parent.postMessage({ type:'filter-ready' }, '*'); }catch{}

  // If parent didn’t respond (rare), keep UI responsive; user can still interact.

  // Handle resize crisply
  window.addEventListener('resize', draw);

})();
