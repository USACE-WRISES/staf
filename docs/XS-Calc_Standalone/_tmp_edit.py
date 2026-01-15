from __future__ import print_function
from pathlib import Path
import re
path = Path('index.html')
with path.open('r', encoding='utf-8') as f:
    text = f.read()
for input_id in ['des-s1-Mtieout_L','des-s1-Mtieout_R','des-s2-Mtieout_2stg_L','des-s2-Mtieout_2stg_R','des-s3-Mtieout_3stg_L','des-s3-Mtieout_3stg_R']:
    pattern = re.compile(r'\s*<label>[\s\S]*?id="%s"[\s\S]*?</label>' % re.escape(input_id))
    text, count = pattern.subn('\n', text)
    print(input_id, 'removed', count)
if 'des-adv-tieout_L' not in text:
    marker = '        <div class="controls" style="margin-top:6px;">'
    addition = '''        <div class="controls" style="margin-top:6px;">\n          <label><span class="labelTitle">Tie-out L <span class="unit-len">(ft/ft)</span></span>\n            <input id="des-adv-tieout_L" type="number" step="any" value="3">\n          </label>\n          <label><span class="labelTitle">Tie-out R <span class="unit-len">(ft/ft)</span></span>\n            <input id="des-adv-tieout_R" type="number" step="any" value="3">\n          </label>'''
    if marker not in text:
        raise SystemExit('marker missing')
    text = text.replace(marker, addition, 1)
with path.open('w', encoding='utf-8', newline='') as f:
    f.write(text)
