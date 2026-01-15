/* Cross-Section Designer calculator (extracted)
   Provides global `calculateCrossSection(params)` that returns 19 rows. */
(function(global){
  'use strict';

  // Utilities used by the calculator
  const round2 = (v) => {
    const n = Math.round((Number(v) + Number.EPSILON) * 100) / 100;
    return Object.is(n, -0) ? 0 : n;
  };
  const safeDiv = (n, d) => (d === 0 ? 0 : n / d);

  // Core calculator (ported from designer.html)
  function calculateCrossSection(params){
    const unitSystem = (params && params.unitSystem) ? String(params.unitSystem) : 'US';
    // Small offset used in layout; unit-agnostic and small in both systems
    const EPS = 0.001;
    const names = ["L9","L8","L7","L6","L5","L4","L3","L2","L1","CL","R1","R2","R3","R4","R5","R6","R7","R8","R9"];
    const desc = {
      "L9":"3rd Stage Top Bank","L8":"End of 3rd stage bench","L7":"3rd Stage","L6":"End of bankfull/design bench",
      "L5":"Bankfull/Design","L4":"Bottom of Bank","L3":"Top Innerberm (Point Bar)","L2":"Baseflow","L1":"CL ThalwegShift Left",
      "CL":"Centerline","R1":"CL ThalwegShift Right","R2":"Baseflow","R3":"Top Innerberm (Point Bar)","R4":"Bottom of Bank",
      "R5":"Bankfull/Design","R6":"End of bankfull/design bench","R7":"3rd Stage","R8":"End of 3rd stage bench","R9":"3rd Stage Top Bank"
    };

    const s1 = params.stage1, s2 = params.stage2, s3 = params.stage3, ib = {...params.innerBerm}, adv = params.advanced;
    const isIB = !!params.isInnerBerm;
    const ns = String(params.numStages ?? "3");

    // Init coord vars
    let L1x=0,L2x=0,L3x=0,L4x=0,L5x=0,L6x=0,L7x=0,L8x=0,L9x=0;
    let R1x=0,R2x=0,R3x=0,R4x=0,R5x=0,R6x=0,R7x=0,R8x=0,R9x=0;
    let CLx=0;

    let L1y=0,L2y=0,L3y=0,L4y=0,L5y=0,L6y=0,L7y=0,L8y=0,L9y=0;
    let R1y=0,R2y=0,R3y=0,R4y=0,R5y=0,R6y=0,R7y=0,R8y=0,R9y=0;
    let CLy=0;

    if (isIB){
      // --- WITH INNER BERM ---
      let YibArea = (2 * ((0.5 * ib.WIB) * ib.DmaxIB));
      let Ybedtotal = s1.ybed + ib.DmaxIB;
      let YibMeanDepth = ib.DmaxIB + s1.ybed;
      YibMeanDepth = safeDiv(YibArea, s1.Width);

      const dYbanks = s1.Depth - s1.ybed - YibMeanDepth;
      const Y2 = dYbanks;
      const Y1 = s1.ybed;
      const W2 = s1.mbanks * dYbanks;
      const W1 = (s1.Width * 0.5) - W2;

      const Abkf = (2 * (W1 * Y2) + (W1 * Y1) + (W2 * Y2)) + YibArea;
      const Dmean = safeDiv(Abkf, s1.Width);
      const WDRatio = safeDiv(s1.Width, Dmean);

      const Sib_trns = safeDiv(ib.DmaxIB, (0.5 * ib.WIB));
      const Wbed = s1.Width - (2 * (s1.mbanks * dYbanks));
      const halfWbed = 0.5 * Wbed;
      const Sbed_transverse = safeDiv((s1.ybed - ib.DmaxIB), (halfWbed - (0.5 * ib.WIB)));

      const S2stg_bench_R = safeDiv(s2.Ybench_R, s2.Wbench_R);
      const S2stg_bench_L = safeDiv(s2.Ybench_L, s2.Wbench_L);
      const D2s_bk_R = s2.D2stg - s2.Ybench_R;
      const D2s_bk_L = s2.D2stg - s2.Ybench_R; // (matches C#)
      const W2s_bk_R = s2.Mbanks_2stg * D2s_bk_R;
      const W2s_bk_L = s2.Mbanks_2stg * D2s_bk_L;

      const D3s_bk_R = s3.D3stg - s3.y3rdStage_R;
      const D3s_bk_L = s3.D3stg - s3.y3rdStage_L;
      const W3s_bk_R = s3.Mbanks_3stg * D3s_bk_R;
      const W3s_bk_L = s3.Mbanks_3stg * D3s_bk_L;
      const S3stg_bench_R = safeDiv(s3.y3rdStage_R, s3.W3rdstage_R);
      const S3stg_bench_L = safeDiv(s3.y3rdStage_L, s3.W3rdstage_L);

      // X (Station)
      CLx = s1.thalweg_shift + adv.X_datum;

      // Left side
      L1x = -EPS + CLx;
      L2x = (-0.25 * ib.WIB) + L1x;
      L3x = (-0.25 * ib.WIB) + L2x;

      if (s1.thalweg_shift !== 0){
        L4x = ((-0.5 * Wbed) - ((-0.25 * ib.WIB) + (-0.25 * ib.WIB)) - s1.thalweg_shift) + L3x;
      }else{
        L4x = ((-0.5 * Wbed) - ((-0.25 * ib.WIB) + (-0.25 * ib.WIB))) + L3x;
      }
      L5x = -((s1.mbanks * adv.Left_Mbanks_BKF_Multiplier) * (dYbanks * adv.Left_BKF_Height_Multiplier)) + L4x;
      L6x = -s2.Wbench_L + L5x;
      L7x = -W2s_bk_L + L6x;
      L8x = -s3.W3rdstage_L + L7x;
      L9x = -W3s_bk_L + L8x;

      // Right side
      R1x = EPS + CLx;
      R2x = (0.25 * ib.WIB) + R1x;
      R3x = (0.25 * ib.WIB) + R2x;

      if (s1.thalweg_shift !== 0){
        R4x = ((0.5 * Wbed) - ((0.25 * ib.WIB) + (0.25 * ib.WIB)) - s1.thalweg_shift) + R3x;
      }else{
        R4x = ((0.5 * Wbed) - ((0.25 * ib.WIB) + (0.25 * ib.WIB))) + R3x;
      }
      R5x = ((s1.mbanks * adv.Right_Mbanks_BKF_Multiplier) * (dYbanks * adv.Right_BKF_Height_Multiplier)) + R4x;
      R6x = s2.Wbench_R + R5x;
      R7x = W2s_bk_R + R6x;
      R8x = s3.W3rdstage_R + R7x;
      R9x = W3s_bk_R + R8x;

      // Y (Elevation)
      CLy = 0 + adv.Y_datum;
      const temphalfDibPrevH = (ib.DmaxIB / 2) - EPS;
      const tempDmaxib = ib.DmaxIB - temphalfDibPrevH;

      L1y = EPS + CLy;
      L2y = temphalfDibPrevH + L1y;
      L3y = tempDmaxib + L2y;
      L4y = (s1.Depth * adv.Left_BKF_Bottom_Slope_Multiplier) - (tempDmaxib + temphalfDibPrevH + EPS) - (dYbanks * adv.Left_BKF_Bottom_Slope_Multiplier) + L3y;
      L5y = (dYbanks * adv.Left_BKF_Height_Multiplier) + L4y;
      L6y = (S2stg_bench_L * s2.Wbench_L) + L5y;
      L7y = (s2.D2stg - s2.Ybench_L) + L6y;
      L8y = (S3stg_bench_L * s3.W3rdstage_L) + L7y;
      L9y = (s3.D3stg - s3.y3rdStage_L) + L8y;

      R1y = EPS + CLy;
      R2y = temphalfDibPrevH + R1y;
      R3y = tempDmaxib + R2y;
      R4y = (s1.Depth * adv.Right_BKF_Bottom_Slope_Multiplier) - (tempDmaxib + temphalfDibPrevH + EPS) - (dYbanks * adv.Right_BKF_Bottom_Slope_Multiplier) + R3y;
      R5y = (dYbanks * adv.Right_BKF_Height_Multiplier) + R4y;
      R6y = (S2stg_bench_R * s2.Wbench_R) + R5y;
      R7y = (s2.D2stg - s2.Ybench_R) + R6y;
      R8y = (S3stg_bench_R * s3.W3rdstage_R) + R7y;
      R9y = (s3.D3stg - s3.y3rdStage_R) + R8y;

      // Stage collapsing
      if (ns === "1"){
        L6x=L7x=L8x=L9x=L5x; L6y=L7y=L8y=L9y=L5y;
        R6x=R7x=R8x=R9x=R5x; R6y=R7y=R8y=R9y=R5y;
      } else if (ns === "2"){
        L8x=L9x=L7x; L8y=L9y=L7y;
        R8x=R9x=R7x; R8y=R9y=R7y;
      } else if (ns === "0"){
        L6x=L7x=L8x=L9x=L5x; L6y=L7y=L8y=L9y=L5y;
        R6x=R7x=R8x=R9x=R5x; R6y=R7y=R8y=R9y=R5y;
      }
    } else {
      // --- WITHOUT INNER BERM ---
      const dYbanks = s1.Depth - s1.ybed;
      const Wbed = s1.Width - (2 * (s1.mbanks * dYbanks));
      const halfWbed = 0.5 * Wbed;

      const S2stg_bench_R = safeDiv(s2.Ybench_R, s2.Wbench_R);
      const S2stg_bench_L = safeDiv(s2.Ybench_L, s2.Wbench_L);
      const D3s_bk_R = s3.D3stg - s3.y3rdStage_R;
      const D3s_bk_L = s3.D3stg - s3.y3rdStage_L;
      const W3s_bk_R = s3.Mbanks_3stg * D3s_bk_R;
      const W3s_bk_L = s3.Mbanks_3stg * D3s_bk_L;

      // X (Station)
      CLx = s1.thalweg_shift + adv.X_datum;

      // Left
      L1x = -EPS + CLx;
      L2x = (-(-0.25 + (-0.25 * s1.Roundness)) * (L1x + CLx - (0.5 * Wbed))) + L1x;
      L3x = -0.25 * (CLx + (0.5 * Wbed)) + L2x;

      if (s1.thalweg_shift !== 0){
        L4x = ((-0.25 + (-0.25 * Math.abs(s1.Roundness - 1))) * ((0.5 * Wbed) + s1.thalweg_shift)) + L3x;
      }else{
        L4x = ((-0.25 + (-0.25 * Math.abs(s1.Roundness - 1))) * (0.5 * Wbed)) + L3x;
      }

      L5x = -((s1.mbanks * adv.Left_Mbanks_BKF_Multiplier) * (dYbanks * adv.Left_BKF_Height_Multiplier)) + L4x;
      L6x = -s2.Wbench_L + L5x;
      L7x = -s2.Mbanks_2stg * (s2.D2stg - s2.Ybench_L) + L6x;
      L8x = -s3.W3rdstage_L + L7x;
      L9x = -W3s_bk_L + L8x;

      // Right
      R1x = EPS + CLx;
      R2x = ((0.25 + (0.25 * s1.Roundness)) * (R1x + CLx + (0.5 * Wbed))) + R1x;
      R3x = 0.25 * ((0.5 * Wbed) - s1.thalweg_shift) + R2x;

      if (s1.thalweg_shift !== 0){
        R4x = ((0.25 + (0.25 * Math.abs(s1.Roundness - 1))) * ((0.5 * Wbed) - s1.thalweg_shift)) + R3x;
      }else{
        R4x = ((0.25 + (0.25 * Math.abs(s1.Roundness - 1))) * (0.5 * Wbed)) + R3x;
      }

      R5x = ((s1.mbanks * adv.Right_Mbanks_BKF_Multiplier) * (dYbanks * adv.Right_BKF_Height_Multiplier)) + R4x;
      R6x = s2.Wbench_R + R5x;
      R7x = s2.Mbanks_2stg * (s2.D2stg - s2.Ybench_R) + R6x;
      R8x = s3.W3rdstage_R + R7x;
      R9x = W3s_bk_R + R8x;

      // Y (Elevation)
      CLy = 0 + adv.Y_datum;

      L1y = (EPS * adv.Left_BKF_Bottom_Slope_Multiplier) + CLy;
      L2y = (0.25 * s1.ybed * adv.Left_BKF_Bottom_Slope_Multiplier) + L1y;
      L3y = (0.25 * s1.ybed * adv.Left_BKF_Bottom_Slope_Multiplier) + L2y;
      L4y = (0.5  * s1.ybed * adv.Left_BKF_Bottom_Slope_Multiplier) + L3y;
      L5y = (dYbanks * adv.Left_BKF_Height_Multiplier) + L4y;
      L6y = s2.Ybench_L + L5y;
      L7y = (s2.D2stg - s2.Ybench_L) + L6y;
      L8y = s3.y3rdStage_L + L7y;
      L9y = (s3.D3stg - s3.y3rdStage_L) + L8y;

      // Right uses Left_BKF_Bottom_Slope_Multiplier for R1..R4 (matches original)
      R1y = (EPS * adv.Left_BKF_Bottom_Slope_Multiplier) + CLy;
      R2y = (0.25 * s1.ybed * adv.Left_BKF_Bottom_Slope_Multiplier) + R1y;
      R3y = (0.25 * s1.ybed * adv.Left_BKF_Bottom_Slope_Multiplier) + R2y;
      R4y = (0.5  * s1.ybed * adv.Left_BKF_Bottom_Slope_Multiplier) + R3y;
      R5y = (dYbanks * adv.Right_BKF_Height_Multiplier) + R4y;
      R6y = s2.Ybench_R + R5y;
      R7y = (s2.D2stg - s2.Ybench_R) + R6y;
      R8y = s3.y3rdStage_R + R7y;
      R9y = (s3.D3stg - s3.y3rdStage_R) + R8y;

      // Stage collapsing
      if (ns === "1"){
        L6x=L7x=L8x=L9x=L5x; L6y=L7y=L8y=L9y=L5y;
        R6x=R7x=R8x=R9x=R5x; R6y=R7y=R8y=R9y=R5y;
      } else if (ns === "2"){
        L8x=L9x=L7x; L8y=L9y=L7y;
        R8x=R9x=R7x; R8y=R9y=R7y;
      } else if (ns === "0"){
        L6x=L7x=L8x=L9x=L5x; L6y=L7y=L8y=L9y=L5y;
        R6x=R7x=R8x=R9x=R5x; R6y=R7y=R8y=R9y=R5y;
      }
    }

    // Assemble arrays and round
    const xPoints = [L9x,L8x,L7x,L6x,L5x,L4x,L3x,L2x,L1x,CLx,R1x,R2x,R3x,R4x,R5x,R6x,R7x,R8x,R9x].map(round2);
    const yPoints = [L9y,L8y,L7y,L6y,L5y,L4y,L3y,L2y,L1y,CLy,R1y,R2y,R3y,R4y,R5y,R6y,R7y,R8y,R9y].map(round2);

    // Stage labels (exactly as in C#)
    let stageLabels = new Array(19).fill("");
    if (!isIB){
      if (ns === "3"){
        stageLabels = ["Left 3rd Stage","","Left 2nd Stage","","Left Bankfull","","","","","Thalweg","","","","","Right Bankfull","","Right 2nd Stage","","Right 3rd Stage"];
      } else if (ns === "2"){
        stageLabels = ["","","Left 2nd Stage","","Left Bankfull","","","","","Thalweg","","","","","Right Bankfull","","Right 2nd Stage","",""];
      } else if (ns === "1"){
        stageLabels = ["","","","","Left Bankfull","","","","","Thalweg","","","","","Right Bankfull","","","",""];
      }
    } else {
      if (ns === "3"){
        stageLabels = ["Left 3rd Stage","","Left 2nd Stage","","Left Bankfull","","","Left Inner Berm","","Thalweg","","Right Inner Berm","","","Right Bankfull","","Right 2nd Stage","","Right 3rd Stage"];
      } else if (ns === "2"){
        stageLabels = ["","","Left 2nd Stage","","Left Bankfull","","","Left Inner Berm","","Thalweg","","Right Inner Berm","","","Right Bankfull","","Right 2nd Stage","",""];
      } else if (ns === "1"){
        stageLabels = ["","","","","Left Bankfull","","","","","Thalweg","","","","","Right Bankfull","","","",""];
      }
    }

    // Build result objects
    const results = names.map((name, i) => ({
      name,
      description: desc[name],
      station: xPoints[i],
      elevation: yPoints[i],
      stage: stageLabels[i]
    }));
    // Ensure unique stations before returning; prefer L5/R5 over others
    function dedupeByStationKeepBanks(rows){
      const keyOf = (v)=> Number.isFinite(v) ? v.toFixed(3) : String(v);
      const isBank = (r)=> r && (r.name === 'L5' || r.name === 'R5');
      const seen = new Map();
      const kept = [];
      for(const r of rows){
        const k = keyOf(r.station);
        if(!seen.has(k)){
          seen.set(k, kept.length);
          kept.push(r);
        }else{
          const idx = seen.get(k);
          const prev = kept[idx];
          if(isBank(prev)){
            // keep existing bank row, drop current
            continue;
          }
          if(isBank(r)){
            // replace previous non-bank with bank row
            kept[idx] = r;
          }
          // else drop current duplicate
        }
      }
      return kept;
    }
    return dedupeByStationKeepBanks(results);
  }

  // Expose globally
  global.calculateCrossSection = calculateCrossSection;

})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
