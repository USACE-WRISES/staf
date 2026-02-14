// app.scenarios.js
// Scenario manager with scenario-scoped HCS + Designer, immediate autosave on edits,
// and safe apply (autosave suspended while switching).
//
// - "Add" creates a NEW scenario from a clean template (not a copy)
// - Rename uses pencil; duplicate uses overlapping squares; delete uses "X"
// - Session persistence via sessionStorage (clears when tab/window closes)
// - Robust autosave hooks on Setup, XS grid, HCS, and Designer panels
//
// Depends on: app.core.js + app.ui.js + designer calculator wiring in app.ui.js.
// Uses HCS globals defined in app.ui.js: ineffectiveAreas, obstructions, levees,
// and their renderers: renderIFATable(), renderObsTable(), renderLevTable().

(function () {
  // ---------- Persistence (session only) ----------
  const SS_KEY = 'xsScenarios.session.v1';
  const STORAGE = window.sessionStorage;

  // ---------- Styles (dock) ----------
  const CSS = `
    :root { --dockW: 252px; }
    @media (min-width: 1080px){
      main{ margin-left: calc(var(--dockW) + 24px) !important; }
      #scenarioDock{
        position: fixed; left: 20px; top: 68px; bottom: 20px; width: var(--dockW);
        overflow: auto; background: var(--card); border:1px solid var(--border);
        border-radius:12px; padding:12px; display:flex; flex-direction:column; gap:10px;
      }
    }
    #scenarioDock .hdr{ display:flex; align-items:center; justify-content:space-between; }
    #scenarioDock .hdr h3{ margin:0; font-size:1rem; }
    #scenarioDock .hdr .btns{ display:flex; gap:8px; }
    #scenarioDock button{ padding:8px 10px; border:1px solid var(--border); border-radius:10px; background:#fff; cursor:pointer; }
    #scenarioDock button:hover{ background:#f9f9f9; }
    #scenarioDock ul{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }
    #scenarioDock li{
      display:flex; align-items:center; justify-content:space-between; gap:8px;
      padding:8px 10px; border:1px solid var(--border); border-radius:10px; background:#fff; cursor:pointer;
    }
    #scenarioDock li.active{ border-color:#0b57d0; box-shadow:0 0 0 2px rgba(11,87,208,.10) inset; }
    #scenarioDock .name{ flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    #scenarioDock .rowBtns{ display:flex; gap:6px; }
    #scenarioDock .iconTiny{
      width:28px; height:28px; padding:0; display:inline-flex; align-items:center; justify-content:center;
      border:1px solid var(--border); border-radius:8px; background:#fff; line-height:1;
      font-weight:500; color:#444; user-select:none;
    }
    #scenarioDock .iconTiny:hover{ background:#f5f7ff; }
    #scenarioDock .iconTiny svg{ width:18px; height:18px; display:block; }
    #scenarioDock .iconTiny svg *{
      fill:none; stroke:currentColor; stroke-width:1.7; stroke-linecap:round; stroke-linejoin:round;
    }
    #scenarioDock .ghost{ color:#999; font-size:.92rem; }
  `;

  // ---------- Utilities ----------
  const $ = (sel) => document.querySelector(sel);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const nowISO = () => new Date().toISOString();
  function freshLevees() {
    return { left:{enabled:false, station:null, crest:null}, right:{enabled:false, station:null, crest:null} };
  }

  // Guard to avoid autosaving while applying a scenario to the UI
  let AUTOSAVE_SUSPENDED = false;
  function withAutosaveSuspended(fn){
    const prev = AUTOSAVE_SUSPENDED;
    AUTOSAVE_SUSPENDED = true;
    try { fn(); } finally { AUTOSAVE_SUSPENDED = prev; }
  }

  // ---------- DEFAULTS (for "Add") ----------
  function templateScenario(name) {
    const id = crypto.randomUUID();

    // Setup defaults align with Reset Example in app.ui.js. :contentReference[oaicite:2]{index=2}
    const setup = {
      units: 'US',
      slope: 0.001,
      nLOB: 0.100,
      nMC:  0.035,
      nROB: 0.100,
      depth: 2.5,
      specifyQ: false,
      discharge: 0,
      hvnOn: false,
      N_LOB: 1, N_CHAN: 1, N_ROB: 1,
      plotMode: 'off'
    };

    // Reset Example cross-section (stations/elevations). :contentReference[oaicite:3]{index=3}
    const xs = [
      {x:-40, z:3,   tag:'',   n:null},
      {x:-10, z:2,   tag:'LB', n:null},
      {x:-5.5,z:0.5, tag:'',   n:null},
      {x:0,   z:0,   tag:'',   n:null},
      {x:5.5, z:0.5, tag:'',   n:null},
      {x:10,  z:2,   tag:'RB', n:null},
      {x:40,  z:3,   tag:'',   n:null}
    ];

    // HCS starts empty. (Arrays/objects are per-scenario snapshots.)
    const hcs = {
      ineffectiveAreas: [],
      obstructions: [],
      levees: freshLevees()
    };

    // Designer panel defaults (matches your panel’s initial state/IDs). :contentReference[oaicite:4]{index=4}
    const designer = {
      mergeMode: 'replace',
      autoApply: false,
      numStages: 1,
      stage1: {
        Width: 20, Depth: 2, mbanks: 2, ybed: 0.5, thalweg_shift: 0,
        Mtieout_L: 3, Mtieout_R: 3, Roundness: 0
      },
      stage2: {
        D2stg: 2, Mbanks_2stg: 2,
        Wbench_L: 10, Wbench_R: 10,
        Ybench_L: 0.2, Ybench_R: 0.2,
        Mtieout_2stg_L: 3, Mtieout_2stg_R: 3
      },
      stage3: {
        D3stg: 0.5, Mbanks_3stg: 2,
        W3rdstage_L: 10, W3rdstage_R: 10,
        y3rdStage_L: 0.2, y3rdStage_R: 0.2,
        Mtieout_3stg_L: 3, Mtieout_3stg_R: 3
      },
      advanced: {
        Left_BKF_Height_Multiplier: 1,
        Right_BKF_Height_Multiplier: 1,
        Left_Mbanks_BKF_Multiplier: 1,
        Right_Mbanks_BKF_Multiplier: 1,
        Left_BKF_Bottom_Slope_Multiplier: 1,
        Right_BKF_Bottom_Slope_Multiplier: 1,
        X_datum: 0, Y_datum: 0
      },
      isInnerBerm: !!document.getElementById('des-isInnerBerm')?.checked,
      innerBerm: { WIB: null, DmaxIB: null }
    };

    return { id, name: name || 'Scenario', setup, xs, hcs, designer, updatedAt: nowISO() };
  }

  // ---------- UI <-> Scenario bridges ----------
  function readSetupFromUI() {
    const val = (id) => document.getElementById(id)?.value ?? '';
    const num = (id) => { const v = parseFloat(val(id)); return Number.isFinite(v) ? v : null; };
    const bool = (id) => !!document.getElementById(id)?.checked;
    const plotMode = (document.querySelector('input[name="plotMode"]:checked') || {}).value || 'off';
    return {
      units: val('units'),
      slope: num('slope'),
      nLOB: num('nLOB'),
      nMC:  num('nMC'),
      nROB: num('nROB'),
      depth: num('depth'),
      specifyQ: bool('specifyQ'),
      discharge: num('discharge'),
      hvnOn: bool('hvnToggle'),
      N_LOB: parseInt(document.getElementById('N_LOB')?.value || '1', 10),
      N_CHAN: parseInt(document.getElementById('N_CHAN')?.value || '1', 10),
      N_ROB: parseInt(document.getElementById('N_ROB')?.value || '1', 10),
      plotMode
    };
  }
  function applySetupToUI(s) {
    if (!s) return;
    const setVal = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
    const setChk = (id, on) => { const el = document.getElementById(id); if (el) el.checked = !!on; };
    setVal('units', s.units);
    setVal('slope', s.slope ?? '');
    setVal('nLOB', s.nLOB ?? '');
    setVal('nMC',  s.nMC  ?? '');
    setVal('nROB', s.nROB ?? '');
    setChk('specifyQ', !!s.specifyQ);
    if (typeof window.updateQModeUI === 'function') window.updateQModeUI(!!s.specifyQ);
    setVal('depth', s.depth ?? '');
    setVal('discharge', s.discharge ?? '');
    setChk('hvnToggle', !!s.hvnOn);
    if (typeof window.updateHVnUI === 'function') window.updateHVnUI(!!s.hvnOn);
    setVal('N_LOB', String(s.N_LOB ?? 1));
    setVal('N_CHAN', String(s.N_CHAN ?? 1));
    setVal('N_ROB', String(s.N_ROB ?? 1));
    const pm = String(s.plotMode || 'off');
    const pmRadio = document.querySelector(`input[name="plotMode"][value="${pm}"]`);
    if (pmRadio) pmRadio.checked = true;
    if (typeof window.updateSliceOptionStates === 'function') window.updateSliceOptionStates();
    if (typeof window.updateXsLockFromAutoToggle === 'function') window.updateXsLockFromAutoToggle();
  }

  function readXsFromUI() {
    const rows = (typeof window.getPointsWithStagesRaw === 'function') ? window.getPointsWithStagesRaw() : [];
    return rows.map(r => ({ x: r.x, z: r.z, tag: r.tag || '', n: Number.isFinite(r.n) ? r.n : null }));
  }
  function applyXsToUI(rows) {
    const tbody = document.querySelector('#xsTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (Array.isArray(rows)) {
      rows.forEach(r => {
        const st  = Number.isFinite(+r.x) ? r.x : '';
        const el  = Number.isFinite(+r.z) ? r.z : '';
        const tag = (r.tag === 'LB' || r.tag === 'RB') ? r.tag : '';
        const nv  = Number.isFinite(+r.n) ? r.n : '';
        if (typeof window.addRow === 'function') window.addRow(st, el, tag, nv);
      });
    }
    if (typeof window.ensureTrailingBlankRow === 'function') window.ensureTrailingBlankRow();
    if (typeof window.renumberIDs === 'function') window.renumberIDs();
    if (typeof window.clearCellSelection === 'function') window.clearCellSelection();
  }

  // ---------- HCS (scenario-scoped) ----------
  // NOTE: these map exactly to app.ui.js globals/renderers. :contentReference[oaicite:5]{index=5}
  function readHcsFromUI() {
    if (typeof window.getHcsState === 'function') {
      const state = window.getHcsState() || {};
      return {
        ineffectiveAreas: Array.isArray(state.ineffectiveAreas) ? clone(state.ineffectiveAreas) : [],
        obstructions: Array.isArray(state.obstructions) ? clone(state.obstructions) : [],
        levees: state.levees && typeof state.levees === 'object' ? clone(state.levees) : freshLevees()
      };
    }
    return {
      ineffectiveAreas: (window.ineffectiveAreas ? clone(window.ineffectiveAreas) : []),
      obstructions:     (window.obstructions     ? clone(window.obstructions)     : []),
      levees:           (window.levees           ? clone(window.levees)           : freshLevees())
    };
  }
  function hardResetHcsGlobals(){
    if (typeof window.setHcsState === 'function') {
      window.setHcsState({ ineffectiveAreas: [], obstructions: [], levees: freshLevees() });
    } else {
      window.ineffectiveAreas = [];
      window.obstructions = [];
      window.levees = freshLevees();
    }
  }
  function applyHcsToUI(hcs) {
    const src = hcs || { ineffectiveAreas: [], obstructions: [], levees: freshLevees() };
    if (typeof window.setHcsState === 'function') {
      window.setHcsState(src);
      return;
    }
    // Replace the UI globals with deep clones to avoid shared references across scenarios.
    window.ineffectiveAreas = Array.isArray(src.ineffectiveAreas) ? clone(src.ineffectiveAreas) : [];
    window.obstructions     = Array.isArray(src.obstructions)     ? clone(src.obstructions)     : [];
    window.levees           = src.levees && typeof src.levees==='object' ? clone(src.levees)    : freshLevees();

    if (typeof window.renderIFATable === 'function') window.renderIFATable();
    if (typeof window.renderObsTable === 'function') window.renderObsTable();
    if (typeof window.renderLevTable === 'function') window.renderLevTable();
  }

  // ---------- Designer (scenario-scoped) ----------
  function readDesignerFromUI() {
    const val = (id) => document.getElementById(id)?.value ?? '';
    const num = (id) => {
      const el = document.getElementById(id); if (!el) return null;
      const v = parseFloat(el.value); return Number.isFinite(v) ? v : null;
    };
    const bool = (id) => !!document.getElementById(id)?.checked;

    return {
      mergeMode: (document.getElementById('des-mergeMode')?.value || 'replace'),
      autoApply: bool('designerAutoApply'),
      numStages: parseInt(val('des-numStages') || '1', 10),

      stage1: {
        Width:            num('des-s1-Width'),
        Depth:            num('des-s1-Depth'),
        mbanks:           num('des-s1-mbanks'),
        ybed:             num('des-s1-ybed'),
        thalweg_shift:    num('des-s1-thalweg_shift'),
        Mtieout_L:        num('des-s1-Mtieout_L'),
        Mtieout_R:        num('des-s1-Mtieout_R'),
        Roundness:        num('des-s1-Roundness')
      },

      stage2: {
        D2stg:            num('des-s2-D2stg'),
        Mbanks_2stg:      num('des-s2-Mbanks_2stg'),
        Wbench_L:         num('des-s2-Wbench_L'),
        Wbench_R:         num('des-s2-Wbench_R'),
        Ybench_L:         num('des-s2-Ybench_L'),
        Ybench_R:         num('des-s2-Ybench_R'),
        Mtieout_2stg_L:   num('des-s2-Mtieout_2stg_L'),
        Mtieout_2stg_R:   num('des-s2-Mtieout_2stg_R')
      },

      stage3: {
        D3stg:            num('des-s3-D3stg'),
        Mbanks_3stg:      num('des-s3-Mbanks_3stg'),
        W3rdstage_L:      num('des-s3-W3rdstage_L'),
        W3rdstage_R:      num('des-s3-W3rdstage_R'),
        y3rdStage_L:      num('des-s3-y3rdStage_L'),
        y3rdStage_R:      num('des-s3-y3rdStage_R'),
        Mtieout_3stg_L:   num('des-s3-Mtieout_3stg_L'),
        Mtieout_3stg_R:   num('des-s3-Mtieout_3stg_R')
      },

      advanced: {
        Left_BKF_Height_Multiplier:          num('des-adv-Left_BKF_Height_Multiplier'),
        Right_BKF_Height_Multiplier:         num('des-adv-Right_BKF_Height_Multiplier'),
        Left_Mbanks_BKF_Multiplier:          num('des-adv-Left_Mbanks_BKF_Multiplier'),
        Right_Mbanks_BKF_Multiplier:         num('des-adv-Right_Mbanks_BKF_Multiplier'),
        Left_BKF_Bottom_Slope_Multiplier:    num('des-adv-Left_BKF_Bottom_Slope_Multiplier'),
        Right_BKF_Bottom_Slope_Multiplier:   num('des-adv-Right_BKF_Bottom_Slope_Multiplier'),
        X_datum:                              num('des-adv-X_datum'),
        Y_datum:                              num('des-adv-Y_datum')
      },

      isInnerBerm: !!document.getElementById('des-isInnerBerm')?.checked,
      innerBerm: {
        WIB:    num('des-ib-WIB'),
        DmaxIB: num('des-ib-DmaxIB')
      }
    };
  }

  function applyDesignerToUI(d) {
    if (!d) return;
    const setVal = (id, v) => { const el = document.getElementById(id); if (el != null && v != null) el.value = v; };
    const setValNum = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v == null ? '' : v); };
    const setChk = (id, on) => { const el = document.getElementById(id); if (el) el.checked = !!on; };

    setVal('des-numStages', String(d.numStages || 1));

    // Stage 1
    setValNum('des-s1-Width', d.stage1?.Width);
    setValNum('des-s1-Depth', d.stage1?.Depth);
    setValNum('des-s1-mbanks', d.stage1?.mbanks);
    setValNum('des-s1-ybed', d.stage1?.ybed);
    setValNum('des-s1-thalweg_shift', d.stage1?.thalweg_shift);
    setValNum('des-s1-Mtieout_L', d.stage1?.Mtieout_L);
    setValNum('des-s1-Mtieout_R', d.stage1?.Mtieout_R);
    setValNum('des-s1-Roundness', d.stage1?.Roundness);

    // Stage 2
    setValNum('des-s2-D2stg', d.stage2?.D2stg);
    setValNum('des-s2-Mbanks_2stg', d.stage2?.Mbanks_2stg);
    setValNum('des-s2-Wbench_L', d.stage2?.Wbench_L);
    setValNum('des-s2-Wbench_R', d.stage2?.Wbench_R);
    setValNum('des-s2-Ybench_L', d.stage2?.Ybench_L);
    setValNum('des-s2-Ybench_R', d.stage2?.Ybench_R);
    setValNum('des-s2-Mtieout_2stg_L', d.stage2?.Mtieout_2stg_L);
    setValNum('des-s2-Mtieout_2stg_R', d.stage2?.Mtieout_2stg_R);

    // Stage 3
    setValNum('des-s3-D3stg', d.stage3?.D3stg);
    setValNum('des-s3-Mbanks_3stg', d.stage3?.Mbanks_3stg);
    setValNum('des-s3-W3rdstage_L', d.stage3?.W3rdstage_L);
    setValNum('des-s3-W3rdstage_R', d.stage3?.W3rdstage_R);
    setValNum('des-s3-y3rdStage_L', d.stage3?.y3rdStage_L);
    setValNum('des-s3-y3rdStage_R', d.stage3?.y3rdStage_R);
    setValNum('des-s3-Mtieout_3stg_L', d.stage3?.Mtieout_3stg_L);
    setValNum('des-s3-Mtieout_3stg_R', d.stage3?.Mtieout_3stg_R);

    // Advanced
    setValNum('des-adv-Left_BKF_Height_Multiplier', d.advanced?.Left_BKF_Height_Multiplier);
    setValNum('des-adv-Right_BKF_Height_Multiplier', d.advanced?.Right_BKF_Height_Multiplier);
    setValNum('des-adv-Left_Mbanks_BKF_Multiplier', d.advanced?.Left_Mbanks_BKF_Multiplier);
    setValNum('des-adv-Right_Mbanks_BKF_Multiplier', d.advanced?.Right_Mbanks_BKF_Multiplier);
    setValNum('des-adv-Left_BKF_Bottom_Slope_Multiplier', d.advanced?.Left_BKF_Bottom_Slope_Multiplier);
    setValNum('des-adv-Right_BKF_Bottom_Slope_Multiplier', d.advanced?.Right_BKF_Bottom_Slope_Multiplier);
    setValNum('des-adv-X_datum', d.advanced?.X_datum);
    setValNum('des-adv-Y_datum', d.advanced?.Y_datum);

    // Optional Inner Berm
    if (document.getElementById('des-isInnerBerm')) setChk('des-isInnerBerm', !!d.isInnerBerm);
    if (document.getElementById('des-ib-WIB'))     setValNum('des-ib-WIB', d.innerBerm?.WIB);
    if (document.getElementById('des-ib-DmaxIB'))  setValNum('des-ib-DmaxIB', d.innerBerm?.DmaxIB);

    // Merge/Replace UI + hidden value
    const mode = (d.mergeMode === 'merge') ? 'merge' : 'replace';
    const rReplace = document.getElementById('mergeReplace');
    const rMerge   = document.getElementById('mergeMerge');
    const hidden   = document.getElementById('des-mergeMode');
    if (hidden) hidden.value = mode;
    if (rReplace && rMerge) {
      rReplace.checked = (mode === 'replace');
      rMerge.checked   = (mode === 'merge');
    }
    if (typeof window !== 'undefined') {
      window.__designerMergeMode = mode;
    }

    // Auto-apply checkbox
    setChk('designerAutoApply', !!d.autoApply);
    if (typeof window.updateXsLockFromAutoToggle === 'function') window.updateXsLockFromAutoToggle();

    if (typeof window.refreshDesignPreview === 'function') window.refreshDesignPreview();
  }

  // ---------- Snapshot & Apply a whole scenario ----------
  function captureScenarioFromUI(scen) {
    return {
      id: scen?.id || crypto.randomUUID(),
      name: scen?.name || 'Scenario',
      setup: readSetupFromUI(),
      xs: readXsFromUI(),
      hcs: readHcsFromUI(),
      designer: readDesignerFromUI(),
      updatedAt: nowISO()
    };
  }
  function applyScenarioToUI(scen) {
    if (!scen) return;
    withAutosaveSuspended(() => {
      // Setup first
      applySetupToUI(scen.setup);
      // Designer BEFORE XS so “Auto-apply” locks are consistent
      applyDesignerToUI(scen.designer);
      // HCS: reset then apply
      hardResetHcsGlobals();
      applyHcsToUI(scen.hcs);
      // XS last
      applyXsToUI(scen.xs);
    });
    if (typeof window.compute === 'function') window.compute();
  }

  // ---------- Store (in-memory + sessionStorage) ----------
  const Store = {
    scenarios: [],
    activeId: null,

    load() {
      try {
        const json = STORAGE.getItem(SS_KEY);
        if (!json) return false;
        const data = JSON.parse(json);
        if (!data || !Array.isArray(data.scenarios) || !data.scenarios.length) return false;
        this.scenarios = data.scenarios;
        this.activeId = data.activeId || this.scenarios[0].id;
        return true;
      } catch {
        return false;
      }
    },
    save() {
      try { STORAGE.setItem(SS_KEY, JSON.stringify({ scenarios: this.scenarios, activeId: this.activeId })); }
      catch { /* ignore */ }
    },
    active() { return this.scenarios.find(s => s.id === this.activeId) || null; },
    setActive(id) { this.activeId = id; this.save(); renderDock(); },

    upsert(s) {
      const i = this.scenarios.findIndex(x => x.id === s.id);
      if (i >= 0) this.scenarios[i] = s; else this.scenarios.push(s);
      this.save();
    },

    addFromTemplate() {
      // Persist the current one first
      if (this.activeId) {
        const snap = captureScenarioFromUI(this.active());
        this.upsert(snap);
      }
      const snap = templateScenario(nextScenarioName());
      this.scenarios.push(snap);
      this.activeId = snap.id;
      this.save();
      renderDock();
      applyScenarioToUI(snap);
    },

    duplicateFromId(id) {
      const src = this.scenarios.find(it => it.id === id);
      if (!src) return;

      if (this.activeId === id) {
        const snapActive = captureScenarioFromUI(src);
        this.upsert(snapActive);
      }

      const copy = clone(src);
      copy.id = crypto.randomUUID();
      copy.name = nextScenarioName();
      copy.updatedAt = nowISO();

      this.scenarios.push(copy);
      this.activeId = copy.id;
      this.save();
      renderDock();
      applyScenarioToUI(copy);
    },

    removeById(id) {
      const idx = this.scenarios.findIndex(s => s.id === id);
      if (idx < 0) return;
      const wasActive = (this.activeId === id);
      this.scenarios.splice(idx, 1);

      if (!this.scenarios.length) {
        const fresh = templateScenario('Scenario 1');
        this.scenarios.push(fresh);
        this.activeId = fresh.id;
        applyScenarioToUI(fresh);
      } else if (wasActive) {
        const next = this.scenarios[Math.max(0, idx - 1)];
        this.activeId = next.id;
        applyScenarioToUI(next);
      }
      this.save();
      renderDock();
    },

    rename(id, newName) {
      const s = this.scenarios.find(it => it.id === id);
      if (s) { s.name = String(newName || '').trim() || s.name; this.save(); renderDock(); }
    }
  };

  function nextScenarioName() {
    const base = 'Scenario ';
    let n = Store.scenarios.length + 1;
    const names = new Set(Store.scenarios.map(s => s.name));
    while (names.has(base + n)) n++;
    return base + n;
  }

  // ---------- Icons (dock) ----------
  function pencilSvg() {
    const el = document.createElement('span');
    el.innerHTML = `
      <svg class="ico-pencil" viewBox="0 0 24 24" aria-hidden="true" focusable="false" role="img">
        <g transform="translate(12,12) scale(0.88) translate(-12,-12)">
          <path d="M3 17.5V21h3.5L19.5 8c.8-.8.8-2.1 0-2.9l-.6-.6c-.8-.8-2.1-.8-2.9 0L3 17.5Z"></path>
          <path d="M14.5 5.5l4 4"></path>
        </g>
      </svg>`;
    return el.firstElementChild;
  }
  function duplicateSvg(){
    const el = document.createElement('span');
    el.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" role="img">
        <rect x="5" y="5" width="10" height="10" rx="2" ry="2"></rect>
        <rect x="9" y="9" width="10" height="10" rx="2" ry="2"></rect>
      </svg>`;
    return el.firstElementChild;
  }
  function xSvg() {
    const el = document.createElement('span');
    el.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false" role="img">
        <line x1="6" y1="6" x2="18" y2="18"></line>
        <line x1="18" y1="6" x2="6" y2="18"></line>
      </svg>`;
    return el.firstElementChild;
  }

  // ---------- Dock UI ----------
  function renderDock() {
    const list = document.getElementById('scenarioList');
    if (!list) return;
    list.innerHTML = '';
    if (!Store.scenarios.length) return;

    for (const s of Store.scenarios) {
      const li = document.createElement('li');
      li.dataset.id = s.id;
      if (s.id === Store.activeId) li.classList.add('active');

      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = s.name;

      const btns = document.createElement('div');
      btns.className = 'rowBtns';

      const btnRen = document.createElement('button');
      btnRen.className = 'iconTiny';
      btnRen.title = 'Rename scenario';
      btnRen.setAttribute('aria-label', 'Rename scenario');
      btnRen.appendChild(pencilSvg());

      const btnDup = document.createElement('button');
      btnDup.className = 'iconTiny';
      btnDup.title = 'Duplicate scenario';
      btnDup.setAttribute('aria-label', 'Duplicate scenario');
      btnDup.appendChild(duplicateSvg());

      const btnDel = document.createElement('button');
      btnDel.className = 'iconTiny';
      btnDel.title = 'Delete scenario';
      btnDel.setAttribute('aria-label', 'Delete scenario');
      btnDel.appendChild(xSvg());

      btns.appendChild(btnRen);
      btns.appendChild(btnDup);
      btns.appendChild(btnDel);

      li.appendChild(name);
      li.appendChild(btns);
      list.appendChild(li);

      function doRename() {
        const nn = prompt('Rename scenario', s.name);
        if (nn != null) Store.rename(s.id, nn);
      }
      name.addEventListener('dblclick', doRename);
      btnRen.addEventListener('click', (e) => { e.stopPropagation(); doRename(); });

      btnDup.addEventListener('click', (e) => {
        e.stopPropagation();
        Store.duplicateFromId(s.id);
      });

      btnDel.addEventListener('click', (e) => {
        e.stopPropagation();
        if (confirm(`Delete "${s.name}"?`)) Store.removeById(s.id);
      });

      // Activate on row click (save current first)
      li.addEventListener('click', (e) => {
        if (e.target.closest('button')) return;
        if (s.id === Store.activeId) return;
        if (Store.activeId) {
          const snap = captureScenarioFromUI(Store.active());
          Store.upsert(snap);
        }
        Store.setActive(s.id);
        applyScenarioToUI(Store.active());
      });
    }
  }

  function ensureDock() {
    if (document.getElementById('scenarioDock')) return;
    const st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);

    const dock = document.createElement('aside');
    dock.id = 'scenarioDock';
    dock.innerHTML = `
      <div class="hdr">
        <h3>Scenarios</h3>
        <div class="btns">
          <button id="scenarioAdd">Add</button>
        </div>
      </div>
      <ul id="scenarioList" role="listbox" aria-label="Scenarios"></ul>
      <div class="ghost">Changes are auto‑saved to each scenario.</div>
    `;
    document.body.appendChild(dock);

    // "Add" => fresh template
    document.getElementById('scenarioAdd').addEventListener('click', () => Store.addFromTemplate());
  }

  // ---------- Autosave (debounced + immediate) ----------
  let saveTimer = null;
  function debouncedAutosave() {
    if (AUTOSAVE_SUSPENDED) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      if (!Store.activeId) return;
      const snap = captureScenarioFromUI(Store.active());
      Store.upsert(snap);
    }, 160);
  }
  function immediateAutosave() {
    if (AUTOSAVE_SUSPENDED) return;
    if (!Store.activeId) return;
    const snap = captureScenarioFromUI(Store.active());
    Store.upsert(snap);
  }

  // Wrap compute() so any path that calls compute triggers a save. :contentReference[oaicite:6]{index=6}
  function wrapComputeForAutosave() {
    const orig = window.compute;
    if (typeof orig === 'function') {
      window.compute = function () {
        const r = orig.apply(this, arguments);
        debouncedAutosave();
        return r;
      };
    }
  }

  // Extra: listen on HCS panel for all mutating actions, not just input/change.
  let autosaveHooksInstalled = false;
  function installAutosaveHooksOnce() {
    if (autosaveHooksInstalled) return;
    autosaveHooksInstalled = true;

    function attach(sel, types){
      const el = document.querySelector(sel);
      if (!el) return;
      types.forEach(type => el.addEventListener(type, () => {
        // Save fast for HCS to keep each scenario isolated
        if (sel === '#tab-hcs') {
          immediateAutosave();
        } else {
          debouncedAutosave();
        }
      }, true /* capture - survive re-renders */));
    }

    // Setup / XS / Designer still use debounce; HCS uses immediate too on clicks.
    attach('#tab-setup',     ['input','change']);
    attach('#xsTable',       ['input','change']);
    attach('#tab-designer',  ['input','change']);
    attach('#tab-hcs',       ['input','change','click']); // <- includes Add/Del buttons in HCS
  }

  // ---------- Boot ----------
  function initScenarios() {
    ensureDock();

    // Start clean each page refresh (session-only app)
    try { STORAGE.removeItem(SS_KEY); } catch (_) { /* ignore */ }

    // Seed from current UI as "Scenario 1"
    if (!Store.load()) {
      const first = captureScenarioFromUI({ name: 'Scenario 1' });
      Store.scenarios = [first];
      Store.activeId = first.id;
      Store.save();
    }
    renderDock();
    applyScenarioToUI(Store.active());

    wrapComputeForAutosave();
    installAutosaveHooksOnce();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initScenarios);
  } else {
    initScenarios();
  }

  // Expose for debugging if needed
  window.__Scenarios = Store;
})();
