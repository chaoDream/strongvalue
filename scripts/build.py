#!/usr/bin/env python3
"""Build already installed frontend dependencies without package-manager side effects."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
bundled = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
node = os.getenv('QIANG_NODE') or shutil.which('node') or (str(bundled) if bundled.exists() else None)
if not node or not (root/'frontend/node_modules/typescript/bin/tsc').exists():
    sys.exit('需要 Node.js 22+ 和前端依赖。请在 frontend/ 执行 pnpm install。')
pdfjs = root/'frontend/node_modules/pdfjs-dist'
if pdfjs.exists():
    assets = root/'frontend/public/pdf-assets'
    assets.mkdir(parents=True, exist_ok=True)
    for folder in ['cmaps', 'standard_fonts', 'wasm']:
        shutil.copytree(pdfjs/folder, assets/folder, dirs_exist_ok=True)
    shutil.copyfile(pdfjs/'LICENSE', assets/'LICENSE.pdfjs')
for script, args in [('typescript/bin/tsc', ['-b']), ('vite/bin/vite.js', ['build'])]:
    subprocess.run([node, str(root/'frontend/node_modules'/script), *args], cwd=root/'frontend', check=True)
