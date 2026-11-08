"""One-time migration; originals preserved under runtime/vue-backup."""
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
web = root / 'web'
for name in ['index', 'workbench', 'report', 'about', 'guide', 'mock']:
    path = web / (name + '.html')
    original = path.read_text(encoding='utf-8')
    if 'vue/main.js' in original:
        continue
    (root / 'runtime/vue-backup' / path.name).write_text(original, encoding='utf-8')
    match = re.search(r'<body([^>]*)>([\s\S]*?)</body>', original)
    if not match:
        raise ValueError(name)
    body = match[2]
    modules = re.findall(r'<script[^>]*type="module"[^>]*src="([^"]+)"[^>]*></script>', body)
    body = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', body)
    if name == 'workbench':
        modules.remove('unified-workbench.js')
        body = body.replace('id="mode-voice" aria-pressed="true"', 'id="mode-voice" @click="selectMode(\'voice\')" :aria-pressed="mode === \'voice\'"')
        body = body.replace('id="mode-demo" aria-pressed="false"', 'id="mode-demo" @click="selectMode(\'demo\')" :aria-pressed="mode === \'demo\'"')
        body = body.replace('id="voice-panel"', 'id="voice-panel" :hidden="mode !== \'voice\'"')
        body = body.replace('id="demo-panel" hidden', 'id="demo-panel" :hidden="mode !== \'demo\'"')
    component = 'export default ' + json.dumps({'name': name.title() + 'Page', 'template': body}, ensure_ascii=False) + ';\n'
    (web / 'vue' / (name + '.js')).write_text(component, encoding='utf-8')
    replacement = '<body' + match[1] + '><div id="vue-app"><p role="status">正在打开智语外呼…</p></div><script type="module" src="vue/main.js"></script></body>'
    html = original[:match.start()] + replacement + original[match.end():]
    if name == 'workbench':
        html = html.replace('</head>', '<link rel="stylesheet" href="vue/workbench.css"></head>')
    path.write_text(html, encoding='utf-8')
    (web / 'vue' / (name + '-modules.js')).write_text('export default ' + json.dumps(modules) + ';', encoding='utf-8')
