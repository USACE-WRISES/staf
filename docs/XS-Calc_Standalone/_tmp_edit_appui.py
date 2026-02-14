from pathlib import Path
path = Path('app.ui.js')
with path.open('r', encoding='utf-8') as f:
    text = f.read()
marker1 = '      // Choose tie-out slopes'
start = text.find(marker1)
if start == -1:
    raise SystemExit('marker1 not found')
end = text.find('      const bed = ', start)
if end == -1:
    raise SystemExit('bed marker not found')
text = text[:start] + '      const mL = params.stage1?.Mtieout_L;\r\n      const mR = params.stage1?.Mtieout_R;\r\n\r\n' + text[end:]
marker2 = '        const design = rows.map'
start2 = text.find(marker2)
if start2 == -1:
    raise SystemExit('design marker not found')
start2 = text.find('        const ns = ', start2)
if start2 == -1:
    raise SystemExit('ns marker not found')
end2 = text.find('        const dLeft = design[0], ', start2)
if end2 == -1:
    raise SystemExit('design left marker not found')
text = text[:start2] + '        const mL = params.stage1?.Mtieout_L;\r\n        const mR = params.stage1?.Mtieout_R;\r\n\r\n' + text[end2:]
with path.open('w', encoding='utf-8', newline='') as f:
    f.write(text)
