from pathlib import Path
path = Path('index.html')
with path.open('r', encoding='utf-8') as f:
    text = f.read()
start = text.find('    <!-- Designer Panel -->')
if start == -1:
    raise SystemExit('designer panel start not found')
end_marker = '    <!-- RESULTS -->'
end = text.find(end_marker, start)
if end == -1:
    raise SystemExit('results marker not found')
new_panel = '''    <!-- Designer Panel -->
    <div id="tab-designer" class="tabPanel">
      <div class="controls" style="margin-bottom:8px;">
        <!-- Removed Auto Apply from here per spec -->
        <label>
          Number of Stages
          <select id="des-numStages">
            <option value="1" selected>1</option>
            <option value="2">2</option>
            <option value="3">3</option>
          </select>
        </label>
      </div>

      <details class="acc" open>
        <summary>Stage 1 (Bankfull/Channel)</summary>
        <div class="controls">
          <label><span class="labelTitle">Width <span class="unit-len">(ft)</span></span>
            <input id="des-s1-Width" type="number" step="any" value="20">
          </label>
          <label><span class="labelTitle">Depth <span class="unit-len">(ft)</span></span>
            <input id="des-s1-Depth" type="number" step="any" value="2">
          </label>
          <label>Side Slope, m:1
            <input id="des-s1-mbanks" type="number" step="any" value="2">
          </label>
          <label>Bed Slope, ybed
            <input id="des-s1-ybed" type="number" step="any" value="0.5">
          </label>
          <label><span class="labelTitle">Thalweg Shift (+R/-L) <span class="unit-len">(ft)</span></span>
            <input id="des-s1-thalweg_shift" type="number" step="any" value="0">
          </label>
          <label>Roundness (0-1)
            <input id="des-s1-Roundness" type="number" min="0" max="1" step="any" value="0">
          </label>
        </div>
      </details>

      <details class="acc">
        <summary>Stage 2 (Benches)</summary>
        <div class="controls">
          <label><span class="labelTitle">&#916;Depth <span class="unit-len">(ft)</span></span>
            <input id="des-s2-D2stg" type="number" step="any" value="2">
          </label>
          <label>Side Slope, m:1
            <input id="des-s2-Mbanks_2stg" type="number" step="any" value="2">
          </label>
          <label><span class="labelTitle">Bench L Width <span class="unit-len">(ft)</span></span>
            <input id="des-s2-Wbench_L" type="number" step="any" value="10">
          </label>
          <label><span class="labelTitle">Bench R Width <span class="unit-len">(ft)</span></span>
            <input id="des-s2-Wbench_R" type="number" step="any" value="10">
          </label>
          <label><span class="labelTitle">Bench L y <span class="unit-len">(ft)</span></span>
            <input id="des-s2-Ybench_L" type="number" step="any" value="0.2">
          </label>
          <label><span class="labelTitle">Bench R y <span class="unit-len">(ft)</span></span>
            <input id="des-s2-Ybench_R" type="number" step="any" value="0.2">
          </label>
        </div>
      </details>

      <details class="acc">
        <summary>Stage 3 (Floodplain)</summary>
        <div class="controls">
          <label><span class="labelTitle">&#916;Depth <span class="unit-len">(ft)</span></span>
            <input id="des-s3-D3stg" type="number" step="any" value="0.5">
          </label>
          <label>Side Slope, m:1
            <input id="des-s3-Mbanks_3stg" type="number" step="any" value="2">
          </label>
          <label><span class="labelTitle">Stage 3 L Width <span class="unit-len">(ft)</span></span>
            <input id="des-s3-W3rdstage_L" type="number" step="any" value="10">
          </label>
          <label><span class="labelTitle">Stage 3 R Width <span class="unit-len">(ft)</span></span>
            <input id="des-s3-W3rdstage_R" type="number" step="any" value="10">
          </label>
          <label><span class="labelTitle">Stage 3 L y <span class="unit-len">(ft)</span></span>
            <input id="des-s3-y3rdStage_L" type="number" step="any" value="0.2">
          </label>
          <label><span class="labelTitle">Stage 3 R y <span class="unit-len">(ft)</span></span>
            <input id="des-s3-y3rdStage_R" type="number" step="any" value="0.2">
          </label>
        </div>
      </details>

      <details class="acc">
        <summary>Inner Berm</summary>
        <div class="controls">
          <label><span class="labelTitle">Inner Berm Enabled</span>
            <input id="des-isInnerBerm" class="chk-sm" type="checkbox">
          </label>
          <label><span class="labelTitle">WIB <span class="unit-len">(ft)</span></span>
            <input id="des-ib-WIB" type="number" step="any" value="1">
          </label>
          <label><span class="labelTitle">DmaxIB <span class="unit-len">(ft)</span></span>
            <input id="des-ib-DmaxIB" type="number" step="any" value="0.5">
          </label>
        </div>
      </details>

      <details class="acc">
        <summary>Advanced</summary>
        <div class="controls" style="margin-top:6px;">
          <label><span class="labelTitle">Tie-out L <span class="unit-len">(ft/ft)</span></span>
            <input id="des-adv-tieout_L" type="number" step="any" value="3">
          </label>
          <label><span class="labelTitle">Tie-out R <span class="unit-len">(ft/ft)</span></span>
            <input id="des-adv-tieout_R" type="number" step="any" value="3">
          </label>
          <label>Left BKF H Mult
            <input id="des-adv-Left_BKF_Height_Multiplier" type="number" step="any" value="1">
          </label>
          <label>Right BKF H Mult
            <input id="des-adv-Right_BKF_Height_Multiplier" type="number" step="any" value="1">
          </label>
          <label>Left m@BKF Mult
            <input id="des-adv-Left_Mbanks_BKF_Multiplier" type="number" step="any" value="1">
          </label>
          <label>Right m@BKF Mult
            <input id="des-adv-Right_Mbanks_BKF_Multiplier" type="number" step="any" value="1">
          </label>
          <label>Left BKF bottom y Mult
            <input id="des-adv-Left_BKF_Bottom_Slope_Multiplier" type="number" step="any" value="1">
          </label>
          <label>Right BKF bottom y Mult
            <input id="des-adv-Right_BKF_Bottom_Slope_Multiplier" type="number" step="any" value="1">
          </label>
          <label><span class="labelTitle">X datum <span class="unit-len">(ft)</span></span>
            <input id="des-adv-X_datum" type="number" step="any" value="0">
          </label>
          <label><span class="labelTitle">Y datum <span class="unit-len">(ft)</span></span>
            <input id="des-adv-Y_datum" type="number" step="any" value="0">
          </label>
        </div>
      </details>

      <div class="actions designer-actions">
        <div class="segSwitch" role="radiogroup" aria-label="Replace or Merge mode">
          <input type="radio" id="mergeReplace" name="mergeMode" value="replace" checked>
          <label for="mergeReplace">Replace current XS</label>
          <input type="radio" id="mergeMerge" name="mergeMode" value="merge">
          <label for="mergeMerge">Merge into XS</label>
        </div>

        <div class="applyGroup">
          <label class="toggle">
            <span>Automatically Apply</span>
            <span class="switch">
              <input id="designerAutoApply" type="checkbox">
              <span class="slider" aria-hidden="true"></span>
            </span>
          </label>

          <button id="designerApply" title="Apply design to XS">Apply</button>
        </div>
      </div>

      <input type="hidden" id="des-mergeMode" value="replace">

    </div>

'''
text = text[:start] + new_panel + text[end:]
with path.open('w', encoding='utf-8', newline='') as f:
    f.write(text)
