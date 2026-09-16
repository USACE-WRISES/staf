"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const www = path.join(__dirname,"..","www");
const tick = () => new Promise(resolve => setImmediate(resolve));
function load() {
  const window = {Shiny:{addCustomMessageHandler(){}},location:{href:"http://localhost/"}};
  const context = vm.createContext({window, document:{getElementById(){return null;}}, URL, AbortController, Uint8Array, setTimeout,clearTimeout,console});
  for(const name of ["viewer-compatibility.js","viewer.js"]) vm.runInContext(fs.readFileSync(path.join(www,name),"utf8"),context);
  return {window,context,api:window.EASIViewerCompatibility,shared:window.EASIViewer.shared};
}
test("decoded tile cache obeys both budgets, recency and oversized insertion", () => {
  const {api} = load(), cache = new api.Cache(2,10);
  cache.set("a",[1],4); cache.set("b",[2],4); cache.get("a"); cache.set("c",[3],4);
  assert.equal(cache.get("b"),undefined); assert.deepEqual(cache.get("a"),[1]); assert.equal(cache.bytes,8);
  cache.set("a",[4],8); assert.equal(cache.get("c"),undefined); assert.equal(cache.bytes,8);
  cache.set("huge",[],11); assert.equal(cache.get("huge"),undefined);
  cache.clear(); assert.equal(cache.bytes,0); assert.equal(cache.entries.size,0);
});
test("request queue caps concurrency and cancels queued and active requests", async () => {
  const {api}=load(), queue=new api.Queue(2), controllers=[], resolvers=[]; let running=0, peak=0, started=0;
  const promises=Array.from({length:6},()=>{
    const controller=new AbortController(); controllers.push(controller);
    return queue.run(()=>new Promise((resolve,reject)=>{
      running++; started++; peak=Math.max(peak,running);
      const finish=()=>{running--;resolve();}; resolvers.push(finish);
      controller.signal.addEventListener("abort",()=>{running--;reject(Object.assign(new Error("abort"),{name:"AbortError"}));},{once:true});
    }),controller.signal).catch(error=>error.name);
  });
  await tick(); assert.equal(peak,2); assert.equal(started,2); assert.equal(queue.waiting.length,4);
  controllers[5].abort(); controllers[0].abort(); await tick(); assert.equal(started,3);
  for(let n=1;n<5;n++) controllers[n].abort();
  const results=await Promise.all(promises); assert.ok(results.every(value=>value==="AbortError"));
  await tick(); assert.equal(queue.active,0); assert.equal(queue.waiting.length,0); assert.equal(peak,2);
});
test("512-pixel tile coordinates and VPU bounds use logical zoom one below Leaflet", () => {
  const {api}=load();
  assert.deepEqual(Array.from(api.boundsAt({x:0,y:0,z:1})).map(n=>Math.round(n)),[-180,-85,180,85]);
  const config={vpus:["west","east","unknown"],vpuBounds:{west:[-125,30,-100,50],east:[-80,30,-60,50]}};
  assert.deepEqual(Array.from(api.selectedVpus(config,{x:2,y:5,z:5})),["west","unknown"]);
  assert.equal(api.CACHE_ENTRIES,128); assert.equal(api.CACHE_BYTES,32*1024*1024); assert.equal(api.CONCURRENCY,6);
});
function feature(comid, y, extra={}) { return {properties:{comid,band:"Functioning",...extra},extent:4096,lines:[[[0,y],[4096,y]]]}; }
test("sibling display tiles share one parent request and one child abort preserves the other", async () => {
  const {api}=load(), cache=new api.Cache(2,10000), calls=[];
  const pool=new api.SourcePool(new api.Queue(1),cache,(key,signal)=>new Promise(resolve=>calls.push({key,signal,resolve})));
  const a=new AbortController(), b=new AbortController();
  const first=pool.acquire("parent",a.signal).catch(error=>error.name), second=pool.acquire("parent",b.signal);
  await tick();assert.equal(calls.length,1);assert.equal(pool.pending.size,1);
  a.abort();assert.equal(await first,"AbortError");assert.equal(calls[0].signal.aborted,false);
  const original=api.prepare([feature(7,2048)]);calls[0].resolve(original);
  assert.equal(await second,original);assert.equal(pool.pending.size,0);assert.equal(cache.entries.size,1);
  assert.equal(await pool.acquire("parent",new AbortController().signal),original);assert.equal(calls.length,1);
});
test("last subscriber abort cancels the source and late results cannot poison a replacement", async () => {
  const {api}=load(), cache=new api.Cache(2,10000), calls=[];
  const pool=new api.SourcePool(new api.Queue(2),cache,(key,signal)=>new Promise(resolve=>calls.push({key,signal,resolve})));
  const a=new AbortController(), b=new AbortController();
  const first=pool.acquire("parent",a.signal).catch(error=>error.name);
  const second=pool.acquire("parent",b.signal).catch(error=>error.name);
  await tick();a.abort();b.abort();assert.deepEqual(await Promise.all([first,second]),["AbortError","AbortError"]);
  assert.equal(calls[0].signal.aborted,true);
  const replacement=pool.acquire("parent",new AbortController().signal);await tick();assert.equal(calls.length,2);
  calls[0].resolve(api.prepare([feature(1,2048)]));await tick();assert.equal(cache.entries.size,0);assert.equal(pool.pending.size,1);
  const fresh=api.prepare([feature(2,2048)]);calls[1].resolve(fresh);assert.equal(await replacement,fresh);
  assert.equal(cache.get("parent")[0].properties.comid,2);pool.clear();assert.equal(cache.entries.size,0);
});
test("overzoom geometry is clipped into display pixels and remains continuous across sibling seams", () => {
  const {api,shared}=load(), parent={x:3,y:5,z:13}, left={x:55,y:88,z:17}, right={x:56,y:88,z:17};
  assert.equal(JSON.stringify(api.sourceCoords(left,12)),JSON.stringify(parent));
  assert.equal(JSON.stringify(api.sourceCoords(parent,12)),JSON.stringify(parent));
  const original=api.prepare([feature(7,2176),feature(8,0)]);
  assert.equal(api.displayFeatures(original,parent,parent,9),original);
  const a=api.displayFeatures(original,parent,left,9), b=api.displayFeatures(original,parent,right,9);
  assert.equal(a.length,1);assert.equal(b.length,1);assert.equal(a[0].extent,512);
  assert.equal(JSON.stringify(a[0].lines),JSON.stringify([[[-9,256],[521,256]]]));
  assert.equal(JSON.stringify(b[0].lines),JSON.stringify(a[0].lines));
  assert.equal(a[0].properties,original[0].properties);assert.equal(original[0].extent,4096);
  const entries=new Set([{coords:left,features:a,loaded:true},{coords:right,features:b,loaded:true}]);
  const at=y=>()=>({x:56*512,y:88*512+y});
  assert.equal(api.nearestInTiles(entries,at(264),17,8,shared.segmentDistance).properties.comid,7);
  assert.equal(api.nearestInTiles(entries,at(264.01),17,8,shared.segmentDistance),null);
  // Clipped segment endpoints in either image extend beyond their common edge,
  // so neither raster adds a round line cap inside the neighboring image.
  assert.ok(left.x*512+a[0].lines[0][1][0]>right.x*512);
  assert.ok(right.x*512+b[0].lines[0][0][0]<right.x*512);
  assert.equal(api.clipSegment([-20,30],[-10,40],-9,521),null);
  assert.equal(JSON.stringify(api.clipSegment([256,-20],[256,600],-9,521)),JSON.stringify([[256,-9],[256,521]]));
});
test("buffered source-parent seams preserve geometry and the eight-pixel hit at native and display zoom", () => {
  const {api,shared}=load(), parents=[{x:3,y:5,z:13},{x:4,y:5,z:13}];
  const left=feature(7,2176), right=feature(7,2176);
  left.lines=[[[4000,2176],[4128,2176]]];right.lines=[[[-96,2176],[32,2176]]];
  const sources=[api.prepare([left]),api.prepare([right])];
  for(const zoom of [13,17]){
    const coords=zoom===13?parents:[{x:63,y:88,z:17},{x:64,y:88,z:17}];
    const entries=new Set(coords.map((c,i)=>({coords:c,loaded:true,features:api.displayFeatures(sources[i],parents[i],c,9)})));
    const x=coords[1].x*512, y=zoom===13?5*512+272:88*512+256;
    for(const dx of [-1,0,1]){
      const at=dy=>()=>({x:x+dx,y:y+dy});
      assert.equal(api.nearestInTiles(entries,at(8),zoom,8,shared.segmentDistance).properties.comid,7);
      assert.equal(api.nearestInTiles(entries,at(8.01),zoom,8,shared.segmentDistance),null);
    }
    for(const entry of entries){
      const f=entry.features[0], start=entry.coords.x*512+f.lines[0][0][0]*512/f.extent;
      const end=entry.coords.x*512+f.lines[0][1][0]*512/f.extent;
      assert.ok(start<x&&end>x); // Both adjacent images paint through the shared edge.
    }
  }
});
test("nearest hit uses eight screen pixels, true segment distance, COMID dedup and overzoom", () => {
  const {api,shared}=load();
  const features=api.prepare([feature(1,2048),feature(1,2048),feature(2,2080)]);
  const entries=new Set([{coords:{x:2,y:3,z:10},features,loaded:true}]);
  const at=y=>()=>({x:2*512+256,y:3*512+y});
  let found=api.nearestInTiles(entries,at(257),10,8,shared.segmentDistance); assert.equal(found.properties.comid,1);
  found=api.nearestInTiles(entries,at(260),10,8,shared.segmentDistance); assert.equal(found.properties.comid,2);
  assert.equal(api.nearestInTiles(entries,at(269),10,8,shared.segmentDistance),null);
  assert.ok(api.nearestInTiles(entries,at(262),12,8,shared.segmentDistance));
  assert.equal(api.nearestInTiles(entries,at(262.1),12,8,shared.segmentDistance),null);
  entries.forEach(entry=>entry.loaded=false); assert.equal(api.nearestInTiles(entries,at(256),10,8,shared.segmentDistance),null);
});
test("line widths match the Standard expression at every stop and between stops", () => {
  const {shared}=load();
  assert.equal(shared.widthAt(4,null),.55);
  for(const order of [1,3,7]) for(const [zoom,base,factor] of [[4,.4,.15],[8,.8,.35],[12,1.6,.6],[16,3,.9]])
    assert.ok(Math.abs(shared.widthAt(zoom,order)-(base+factor*order))<1e-12);
  assert.ok(Math.abs(shared.widthAt(10,3)-2.625)<1e-12);
});
// Minimal independent protobuf writer makes one line in a flowlines MVT layer.
function vari(n){const b=[];do{let v=n&127;n=Math.floor(n/128);b.push(v|(n?128:0));}while(n);return b;}
function field(tag,value){return [...vari(tag*8+2),...vari(value.length),...value];}
function scalar(tag,n){return [...vari(tag*8),...vari(n)];}
function str(s){return [...Buffer.from(s)];}
function tileFixture(){
  const tags=[0,0,1,1,2,2];
  const f=[...scalar(1,7),...field(2,tags),...scalar(3,2),...field(4,[9,...vari(0),...vari(4096),10,...vari(8192),0])];
  const layer=[...field(1,str("flowlines")),...field(2,f),...field(3,str("comid")),...field(3,str("band")),...field(3,str("order")),
    ...field(4,scalar(5,123)),...field(4,field(1,str("Functioning"))),...field(4,scalar(5,3)),...scalar(5,4096),...scalar(15,2)];
  return new Uint8Array(field(3,layer));
}
test("locally bundled decoder reads actual MVT properties and exact line coordinates", () => {
  const {context}=load(); vm.runInContext(fs.readFileSync(path.join(www,"vendor","easi-vector-tile.js"),"utf8"),context);
  const result=context.EASIVectorTile.decode(tileFixture());
  assert.equal(result.length,1); assert.equal(result[0].properties.comid,123); assert.equal(result[0].properties.band,"Functioning");
  assert.equal(result[0].properties.order,3); assert.equal(result[0].extent,4096);
  assert.equal(JSON.stringify(result[0].lines),JSON.stringify([[[0,2048],[4096,2048]]]));
});

test("generated assets match their manifest and Leaflet selectors are scoped", () => {
  const crypto=require("node:crypto"), root=path.join(www,"vendor");
  const manifest=JSON.parse(fs.readFileSync(path.join(root,"nationwide-assets.json"),"utf8"));
  for(const item of manifest.files) assert.equal(crypto.createHash("sha256").update(fs.readFileSync(path.join(root,item.path))).digest("hex"),item.sha256,item.path);
  const css=fs.readFileSync(path.join(root,"leaflet","leaflet.css"),"utf8").replace(/\/\*[\s\S]*?\*\//g,"");
  for(const rule of css.matchAll(/([^{}]+)\{/g)) {
    if(rule[1].trim().startsWith("@"))continue;
    for(const selector of rule[1].split(",")) assert.ok(selector.trim().startsWith("#easi-viewer-map"),selector);
  }
});

function rendererHarness(options={}) {
  const {window,context,shared}=load(), network=[], images=[], canvases=[], blobs=[], layers=[], created=[], revoked=[];
  const handlers={}, classes=new Set(["original"]), attrs=new Map(), panes={};
  classes.remove=name=>classes.delete(name);
  const container={style:{},classList:classes,getAttribute:name=>attrs.get(name)??null,
    setAttribute(name,value){attrs.set(name,value);},removeAttribute(name){attrs.delete(name);}};
  let decoded=0;
  const map={
    on(name,fn){(handlers[name] ||= []).push(fn); return this;},
    createPane(name){return panes[name]={style:{}};},getPane(name){return panes[name];},
    getContainer(){return container;},getZoom(){return options.zoom??5;},getCenter(){return{lng:-96,lat:38.5};},
    removeLayer(layer){(layer.images||[]).forEach(image=>layer.handlers.tileunload?.({tile:image}));},
    remove(){layers.forEach(layer=>map.removeLayer(layer));},invalidateSize(){}
  };
  context.document.getElementById=()=>container;
  function simple(){return {addTo(){layers.push(this);return this;},setStyle(){},setContent(){return this;},setLatLng(){return this;},openOn(){return this;}};}
  const L={map(){classes.add("leaflet-container");classes.add("leaflet-touch");attrs.set("tabindex","0");return map;},control:{zoom:simple},
    tileLayer(url,options){return Object.assign(simple(),{url,options});},geoJSON:simple,layerGroup:simple,polyline:simple,popup:simple,
    GridLayer:{extend(def){return class {
      constructor(options){this.options=options;this.handlers={};}
      on(name,fn){this.handlers[name]=fn;return this;}
      addTo(){layers.push(this);this.images=(options.coords||[{x:3,y:5,z:5}]).map(coords=>def.createTile(coords,()=>{}));this.image=this.images[0];return this;}
      redraw(){}
    };}}
  };
  context.document.createElement=name=>{
    if(name==="img"){const image={setAttribute(){}}; images.push(image);return image;}
    assert.equal(name,"canvas");
    const pen={points:[],strokes:[],scale(){},stroke(){this.strokes.push({width:this.lineWidth,color:this.strokeStyle});},beginPath(){},
      moveTo(x,y){this.points.push([x,y]);},lineTo(x,y){this.points.push([x,y]);}};
    const canvas={pen,getContext:()=>pen,toBlob(fn){this.rasterWidth=this.width;this.rasterHeight=this.height;blobs.push(fn);}};
    canvases.push(canvas);return canvas;
  };
  class LocalURL extends URL {}
  LocalURL.createObjectURL=()=>{const url="blob:test-"+created.length;created.push(url);return url;};
  LocalURL.revokeObjectURL=url=>revoked.push(url);context.URL=LocalURL;
  Object.assign(window,{L,devicePixelRatio:options.dpr||1,requestAnimationFrame(){return 1;},cancelAnimationFrame(){},
    EASIVectorTile:{decode(){decoded++;return options.features||[feature(123,2048)];}},
    fetch(url,options){return new Promise(resolve=>network.push({url,signal:options.signal,resolve}));}
  });
  const state={}, notes={loading:0,idle:0,failed:0};
  const engine=window.EASIViewerRenderers.compatibility({shared,state,current:()=>true,
    loading(){notes.loading++;},idle(){notes.idle++;},failed(){notes.failed++;},pick(){}},
    {routeBase:"tiles",vpus:["02"],datasetKey:"old",generation:1},null);
  function resolve(job){job.resolve({ok:true,status:200,json:async()=>({type:"FeatureCollection",features:[]}),arrayBuffer:async()=>new ArrayBuffer(1)});}
  return {engine,network,images,canvases,blobs,created,revoked,notes,resolve,container,layers,decoded:()=>decoded};
}

test("logical zoom 16 draws full-width strokes on 512-pixel child images from one zoom-12 parent", async () => {
  const coords=[{x:55,y:88,z:17},{x:56,y:88,z:17}];
  const h=rendererHarness({coords,zoom:17,features:[feature(123,2176,{order:3})]});
  await tick();assert.equal(h.network.length,2); // One coverage request plus one shared source tile.
  const tile=h.network.find(job=>job.url.includes("vpu=02"));
  assert.ok(tile,tile?.url);const url=new URL(tile.url);
  assert.equal(url.searchParams.get("z"),"12");assert.equal(url.searchParams.get("x"),"3");assert.equal(url.searchParams.get("y"),"5");
  h.network.forEach(h.resolve);await tick();await tick();
  assert.equal(h.decoded(),1);assert.equal(h.canvases.length,2);
  assert.equal(h.layers.find(layer=>layer.options?.pane==="easiLines").options.maxNativeZoom,17);
  assert.equal(h.layers.find(layer=>layer.url?.includes("USGSTopo")).options.maxNativeZoom,16);
  for(let i=0;i<2;i++){
    const image=h.images[i],canvas=h.canvases[i];
    assert.equal(canvas.rasterWidth,512);assert.equal(canvas.rasterHeight,512);
    assert.ok(Math.abs(canvas.pen.strokes[0].width-5.7)<1e-12);
    assert.equal(JSON.stringify(canvas.pen.points),JSON.stringify([[-9,256],[521,256]]));
    assert.equal(JSON.stringify(image._easiEntry.coords),JSON.stringify(coords[i]));
    assert.equal(JSON.stringify(image._easiEntry.sourceCoords),JSON.stringify({x:3,y:5,z:13}));
    h.blobs[i]({});image.onload();assert.equal(canvas.width,0);
  }
  assert.equal(h.engine.diagnostics().cacheEntries,1);h.engine.destroy();assert.equal(h.revoked.length,2);
});
test("unloading one overzoom image keeps its sibling request and raster alive", async () => {
  const h=rendererHarness({coords:[{x:55,y:88,z:17},{x:56,y:88,z:17}],zoom:17,dpr:3,features:[feature(123,2176,{order:1})]});
  await tick();const grid=h.layers.find(layer=>layer.options?.pane==="easiLines"), tile=h.network.find(job=>job.url.includes("vpu=02"));
  grid.handlers.tileunload({tile:h.images[0]});assert.equal(tile.signal.aborted,false);
  h.network.forEach(h.resolve);await tick();await tick();
  assert.equal(h.canvases.length,1);assert.equal(h.images[0].src,undefined);
  assert.equal(h.canvases[0].rasterWidth,1024); // Device-pixel ratio stays capped at 2.
  assert.ok(Math.abs(h.canvases[0].pen.strokes[0].width-3.9)<1e-12);
  h.blobs[0]({});h.images[1].onload();assert.equal(h.images[1]._easiEntry.loaded,true);
  assert.equal(h.engine.diagnostics().tiles,1);h.engine.destroy();
});
test("late old-generation network results cannot populate images, coverage or decoded cache", async () => {
  const h=rendererHarness();await tick();assert.equal(h.network.length,2);
  const old=[...h.network];
  h.engine.refresh({routeBase:"tiles",vpus:["02"],datasetKey:"new",generation:2});await tick();
  assert.ok(old.every(job=>job.signal.aborted));
  old.forEach(h.resolve);await tick();await tick();
  assert.equal(h.decoded(),0);assert.equal(h.engine.diagnostics().cacheEntries,0);assert.equal(h.images[0].src,undefined);
  h.network.slice(2).forEach(h.resolve);await tick();await tick();
  assert.equal(h.decoded(),1);assert.equal(h.engine.diagnostics().cacheEntries,1);assert.equal(h.blobs.length,1);
  h.blobs[0]({});assert.equal(h.images[1].src,"blob:test-0");h.images[1].onload();
  h.engine.destroy();assert.deepEqual(h.revoked,["blob:test-0"]);assert.equal(h.engine.diagnostics().tiles,0);
  assert.deepEqual([...h.container.classList],["original"]);assert.equal(h.container.getAttribute("tabindex"),null);
});
test("late image encoding is discarded and unavailable data makes no requests", async () => {
  const h=rendererHarness();await tick();h.network.forEach(h.resolve);await tick();await tick();
  assert.equal(h.blobs.length,1);assert.equal(h.canvases[0].width,512);
  h.engine.refresh({routeBase:"tiles",vpus:[],datasetKey:"new",generation:2,available:false});
  h.blobs[0]({});
  assert.equal(h.canvases[0].width,0);assert.equal(h.created.length,0);assert.equal(h.images[0].src,undefined);
  assert.equal(h.network.length,2);assert.equal(h.engine.diagnostics().cacheEntries,0);
  assert.equal(h.engine.diagnostics().tiles,0);h.engine.destroy();
});
