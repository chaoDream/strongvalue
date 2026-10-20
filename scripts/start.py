#!/usr/bin/env python3
"""Start the production build on loopback. No external services required."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path.insert(0, str(root))
if not (root/'frontend/dist/index.html').exists():
    sys.exit('请先构建前端：cd frontend && pnpm install && pnpm build')
import uvicorn
uvicorn.run('backend.main:app', host=os.getenv('QIANG_HOST', '127.0.0.1'), port=int(os.getenv('QIANG_PORT', '8000')))
