// app.core.js
// Core, UI-agnostic helpers for cross-section geometry & hydraulics.
// NOTE: These functions intentionally do not manipulate the DOM. They rely on
// global HCS state (ineffectiveAreas, obstructions, levees) where appropriate.
// Keep load order: include this file before app.ui.js.
function polylineBetween(pts,xA,xB){
  const seg=[];
  for(let i=0;i<pts.length-1;i++){
    const {x:x1,z:z1}=pts[i], {x:x2,z:z2}=pts[i+1];
    if(x2<=xA||x1>=xB) continue;
    const xa=Math.max(x1,xA), xb=Math.min(x2,xB);
    const dx=x2-x1; const m=(z2-z1)/dx;
    const za=z1+m*(xa-x1), zb=z1+m*(xb-x1);
    if(!seg.length) seg.push({x:xa,z:za});
    if(x1>xa&&x1<xb) seg.push({x:x1,z:z1});
    if(x2>xa&&x2<xb) seg.push({x:x2,z:z2});
    seg.push({x:xb,z:zb});
  }
  const out=[];
  for(const p of seg){
    if(!out.length||Math.abs(out[out.length-1].x-p.x)>1e-12) out.push(p);
    else out[out.length-1]=p;
  }
  return out;
}


function integrateOnPolyline(polyPts,stage){
  let A=0,P=0;
  const intervals=[];
  for(let i=0;i<polyPts.length-1;i++){
    const p1=polyPts[i], p2=polyPts[i+1];
    const x1=p1.x, z1=p1.z, x2=p2.x, z2=p2.z;
    const dx=x2-x1; if(dx<=0) continue;
    const rel1=z1-stage, rel2=z2-stage;
    const m=(z2-z1)/dx;
    if(rel1<=0 && rel2<=0){
      const d1=-rel1,d2=-rel2; A+=0.5*(d1+d2)*dx; P+=Math.hypot(dx,z2-z1); intervals.push([x1,x2]);
    }else if((rel1<=0&&rel2>0)||(rel1>0&&rel2<=0)){
      const t=(stage - z1)/(z2 - z1); const xi=x1 + t*dx; const slopeLen=Math.sqrt(1+m*m);
      if(rel1<=0&&rel2>0){ const dxw=xi-x1, d1=-rel1; A+=0.5*d1*Math.abs(dxw); P+=slopeLen*Math.abs(dxw); intervals.push([x1,xi]); }
      else { const dxw=x2-xi, d2=-rel2; A+=0.5*d2*Math.abs(dxw); P+=slopeLen*Math.abs(dxw); intervals.push([xi,x2]); }
    }
  }
  intervals.sort((a,b)=>a[0]-b[0]);
  const merged=[]; const eps=1e-12;
  for(const [a,b] of intervals){
    if(!merged.length) merged.push([a,b]);
    else { const L=merged[merged.length-1]; if(a<=L[1]+eps) L[1]=Math.max(L[1],b); else merged.push([a,b]); }
  }
  let T=0; for(const [a,b] of merged) T+=(b-a);
  return {A,P,T,wetIntervals:merged};
}


function integrateSectionInRangeOnBed(bedPts,stage,xL,xR){
  if(!(xR>xL)) return {A:0,P:0,T:0,wetIntervals:[]};
  const seg=polylineBetween(bedPts, xL, xR);
  if(seg.length<2) return {A:0,P:0,T:0,wetIntervals:[]};
  return integrateOnPolyline(seg,stage);
}


function totalWetWidth(intervals){ return intervals.reduce((s,[a,b])=>s+(b-a),0); }


function buildNSegments(rows){
  const rowsSorted = rows.slice().sort((a,b)=>a.x-b.x);
  if(!rowsSorted.length) return {ok:false, err:'No rows', segments:[]};
  const firstN = rowsSorted[0].n;
  if(!Number.isFinite(firstN)) return {ok:false, err:'HVn is ON: first (leftmost) row must have an n value.', segments:[]};
  const segments=[]; let currentN=firstN;
  for(let i=0;i<rowsSorted.length-1;i++){
    const a=rowsSorted[i], b=rowsSorted[i+1];
    segments.push({xL:a.x, xR:b.x, n:currentN});
    if(Number.isFinite(b.n)) currentN=b.n;
  }
  return {ok:true, segments};
}


function clipSegments(segments, L, R){
  const out=[];
  for(const s of segments){
    const xL=Math.max(L, s.xL), xR=Math.min(R, s.xR);
    if(xR>xL) out.push({xL, xR, n:s.n});
  }
  return out;
}


function bankSlopeRatios(pts, LBx, RBx){
  const idxLB = pts.findIndex(p=>p.x===LBx);
  const idxRB = pts.findIndex(p=>p.x===RBx);
  let SL=Infinity, SR=Infinity;
  if(idxLB>=0 && idxLB+1<pts.length){
    const dx=pts[idxLB+1].x - pts[idxLB].x; const dz=pts[idxLB+1].z - pts[idxLB].z;
    SL = (Math.abs(dz)>0)? Math.abs(dx/dz) : Infinity;
  }
  if(idxRB-1>=0 && idxRB<pts.length){
    const dx=pts[idxRB].x - pts[idxRB-1].x; const dz=pts[idxRB].z - pts[idxRB-1].z;
    SR = (Math.abs(dz)>0)? Math.abs(dx/dz) : Infinity;
  }
  return {SL, SR};
}


function buildPlotNSegmentsFromExplicit(rowsSorted, leftX, rightX){
  const explicit = rowsSorted
    .filter(r => Number.isFinite(r.n))
    .map(r => ({ x: r.x, n: r.n }));
  if (!explicit.length) return [];
  explicit.sort((a,b)=>a.x-b.x);
  const segs = [];
  let curX = explicit[0].x;
  let curN = explicit[0].n;
  for (let i=1; i<explicit.length; i++){
    const e = explicit[i];
    if (Math.abs(e.n - curN) > 1e-12) {
      segs.push({ xL: curX, xR: e.x, n: curN });
      curX = e.x; curN = e.n;
    }
  }
  segs.push({ xL: curX, xR: rightX, n: curN });
  if (explicit[0].x > leftX) segs.unshift({ xL: leftX, xR: explicit[0].x, n: explicit[0].n });
  return segs
    .map(s => ({ xL: Math.max(leftX, s.xL), xR: Math.min(rightX, s.xR), n: s.n }))
    .filter(s => s.xR > s.xL + 1e-9);
}


function floorAtX(x, stage){
  let f = -Infinity;
  for(const ob of obstructions){
    if(ob && Number.isFinite(ob.l) && Number.isFinite(ob.r) && Number.isFinite(ob.top) && ob.r>ob.l){
      if(x>=Math.min(ob.l,ob.r) && x<=Math.max(ob.l,ob.r)) f = Math.max(f, ob.top);
    }
  }
  for(const ia of ineffectiveAreas){
    if(!ia || !Number.isFinite(ia.l) || !Number.isFinite(ia.r) || !Number.isFinite(ia.elev)) continue;
    const xl=Math.min(ia.l, ia.r), xr=Math.max(ia.l, ia.r);
    if(x>=xl && x<=xr){
      if(ia.permanent || stage <= ia.elev){ f = Math.max(f, ia.elev); }
    }
  }
  return f;
}


function buildConveyanceBed(bedPts, stage){
  const xs = bedPts.map(p=>p.x);
  const xMin = Math.min(...xs), xMax=Math.max(...xs);
  const cuts = new Set(xs);
  for(const ob of obstructions){ if(!ob) continue; if(Number.isFinite(ob.l)) cuts.add(ob.l); if(Number.isFinite(ob.r)) cuts.add(ob.r); }
  for(const ia of ineffectiveAreas){ if(!ia) continue; if(Number.isFinite(ia.l)) cuts.add(ia.l); if(Number.isFinite(ia.r)) cuts.add(ia.r); }
  const X=[...cuts].sort((a,b)=>a-b);
  const eff=[];
  const pushPt=(x,z)=>{ if(!eff.length || Math.abs(eff[eff.length-1].x - x) > 1e-12 || Math.abs(eff[eff.length-1].z - z) > 1e-12){ eff.push({x,z}); } };
  for(let i=0;i<X.length-1;i++){
    const xa=X[i], xb=X[i+1]; if(!(xb>xa)) continue;
    const seg=polylineBetween(bedPts, xa, xb); if(seg.length<2) continue;
    const F = floorAtX(0.5*(xa+xb), stage);
    for(let j=0;j<seg.length-1;j++){
      const p1=seg[j], p2=seg[j+1]; const x1=p1.x, z1=p1.z, x2=p2.x, z2=p2.z; if(!(x2>x1)) continue;
      const m=(z2-z1)/(x2-x1);
      const z1c = Math.max(z1, F), z2c=Math.max(z2, F);
      if(z1>=F && z2>=F){ if(!eff.length) pushPt(x1,z1c); pushPt(x2,z2c); }
      else if(z1<=F && z2<=F){ if(!eff.length) pushPt(x1,F); pushPt(x2,F); }
      else{
        const xi = x1 + (F - z1)/m;
        if(z1<F && z2>F){ if(!eff.length) pushPt(x1,F); pushPt(xi,F); pushPt(x2,z2); }
        else{ if(!eff.length) pushPt(x1,z1); pushPt(xi,F); pushPt(x2,F); }
      }
    }
  }
  if(!eff.length){
    const seg=polylineBetween(bedPts, xMin, xMax);
    return seg.length? seg : bedPts.slice();
  }
  return eff;
}


function leveeClip(stage, xMin, xMax){
  let L = xMin, R = xMax;
  if(levees.left.enabled && Number.isFinite(levees.left.station) && Number.isFinite(levees.left.crest) && stage < levees.left.crest){ L = Math.max(L, levees.left.station); }
  if(levees.right.enabled && Number.isFinite(levees.right.station) && Number.isFinite(levees.right.crest) && stage < levees.right.crest){ R = Math.min(R, levees.right.station); }
  return {L, R};
}


function computeDistribution(convBed, stage, parts, nVals, phys, Nvals, Qtot){
  const {left, main, right}=parts;
  const {nLOB, nMC, nROB}=nVals;
  const {S, kConst, gamma, sqrtS}=phys;
  const {N_LOB, N_CH, N_ROB}=Nvals;

  const boundsLOB=partitionByWidth(left.wetIntervals, N_LOB);
  const boundsCH =partitionByWidth(main.wetIntervals, N_CH);
  const boundsROB=partitionByWidth(right.wetIntervals, N_ROB);

  const slices=[];
  function push(bounds,label,nVal){
    if(!bounds || bounds.length<2) return;
    for(let i=0;i<bounds.length-1;i++){
      const sL=bounds[i], sR=bounds[i+1];
      const geom=integrateSectionInRangeOnBed(convBed, stage, sL, sR);
      const A=geom.A, P=geom.P, Tw=geom.T;
      const R=(A>0&&P>0)?A/P:0; const Dh=(Tw>0)?A/Tw:0;
      const n_use = (Number.isFinite(nVal) && nVal>0)? nVal : 0.035;
      const AR23=(A>0&&R>0)? A*Math.pow(R,2/3):0;
      const Kraw=(AR23>0)?(kConst/n_use)*AR23:0;
      const Qraw=Kraw*sqrtS;
      slices.push({
        label: `${label} ${i+1}`, side:label, sL,sR,A,P,Tw,R,Dh,
        n:n_use, Kraw,Qraw, K:0,Q:0,V:0, tau:gamma*R*S, Power:0, mid:0, pctK:0
      });
    }
  }
  function partitionByWidth(intervals,N){
    const T=totalWetWidth(intervals);
    if(!(N>=1) || !(T>0)) return [];
    const step=T/N; const bounds=[];
    let acc=0,target=0,idx=0;
    if(intervals.length) bounds.push(intervals[0][0]);
    while(bounds.length<N){
      target+=step;
      while(idx<intervals.length && acc+(intervals[idx][1]-intervals[idx][0])<target-1e-12){
        acc+=(intervals[idx][1]-intervals[idx][0]); idx++;
      }
      if(idx>=intervals.length) break;
      const [a,b]=intervals[idx]; const within=target-acc; bounds.push(a+within);
    }
    if(intervals.length) bounds.push(intervals[intervals.length-1][1]);
    return bounds;
  }

  push(boundsLOB,'LOB', nLOB);
  push(boundsCH, 'Chan', nMC);
  push(boundsROB,'ROB', nROB);

  const SumKraw=slices.reduce((s,o)=>s+o.Kraw,0);
  const Korig=(sqrtS>0)? (Qtot/sqrtS) : 0;
  const r=(SumKraw>0)? (Korig/SumKraw) : 0;

  for(const sl of slices){
    sl.K=r*sl.Kraw; sl.Q=sl.K*sqrtS;
    sl.V=(sl.A>0)? sl.Q/sl.A : 0;
    sl.Power=sl.tau*sl.V;
    sl.mid=0.5*(sl.sL+sl.sR);
    sl.pctK=(Korig>0)? 100*sl.K/Korig : 0;
  }

  const boundaries=[]; const addB=a=>{ if(a&&a.length) for(const x of a) boundaries.push(x); };
  addB(boundsLOB); addB(boundsCH); addB(boundsROB);
  return {slices, boundaries, SumKraw, r, Korig};
}
