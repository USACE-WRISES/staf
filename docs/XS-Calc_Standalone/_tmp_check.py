from __future__ import print_function
from pathlib import Path
import re
with Path('index.html').open('r', encoding='utf-8') as f:
    text = f.read()
for input_id in ['des-s1-Mtieout_L','des-s1-Mtieout_R','des-s2-Mtieout_2stg_L','des-s2-Mtieout_2stg_R','des-s3-Mtieout_3stg_L','des-s3-Mtieout_3stg_R']:
    pattern = re.compile(r'<label>[\s\S]*?id="%s"[\s\S]*?</label>' % re.escape(input_id))
    print('{} {}'.format(input_id, bool(pattern.search(text))))
