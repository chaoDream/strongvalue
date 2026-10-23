"""Bounded on-device OCR; derived text is unreviewed and never auto-creates quantities."""
import json
import math
import platform
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from . import main as core, store
from .evidence_regions import page_region, SPACE
from .task_execution import claim, current, validate_source, original_bytes, fail

router = APIRouter(prefix='/api/v1')
VERSION = 'local-vision-ocr-1.0'
OCR_LOCK = threading.Lock()


def capability():
    available = platform.system() == 'Darwin' and Path('/usr/bin/swift').is_file() and shutil.which('xcrun') is not None
    return {'available': available, 'engine': VERSION, 'local_only': True,
            'notice': '本机逐页识别；文字与位置均须核对原图，不能作为工程尺寸。' if available else '本地OCR目前需要macOS及Swift工具链；其他部署请人工标注或接入视觉模型。'}


def validate_lines(result):
    lines = result.get('lines')
    if not isinstance(lines, list) or len(lines) > 2000:
        raise ValueError('OCR行数超过2000或输出无效，请拆分页面或人工标注')
    output = []
    for line in lines:
        text, box, confidence = line.get('text'), line.get('bbox'), line.get('confidence')
        if not isinstance(text, str) or len(text) > 2000:
            raise ValueError('OCR文字输出超出限制')
        if not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in box):
            raise ValueError('OCR位置输出无效')
        x, y, w, h = box
        if x < -1e-6 or y < -1e-6 or w <= 0 or h <= 0 or x+w > 1.000001 or y+h > 1.000001:
            raise ValueError('OCR位置超出显示页')
        if type(confidence) not in (float, int) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('OCR置信度输出无效')
        x, y = max(0, x), max(0, y)
        if text.strip():
            output.append({'text': text, 'bbox': [x, y, min(w, 1-x), min(h, 1-y)], 'confidence': confidence})
    if sum(len(line['text'])+1 for line in output) > 100000:
        raise ValueError('OCR文字超过100000字，请人工分区域标注')
    return output


def recognize(raw, page):
    if not capability()['available']:
        raise ValueError(capability()['notice'])
    with tempfile.TemporaryDirectory(prefix='qiang-local-ocr-') as folder:
        path = Path(folder)/'source.pdf'
        path.write_bytes(raw)
        try:
            # Separate cache per invocation; no shell, network service or shared source path.
            process = subprocess.run(['/usr/bin/swift', '-module-cache-path', str(Path(folder)/'cache'),
                                      str(Path(__file__).parent/'native'/'ocr.swift'), str(path), str(page)],
                                     capture_output=True, timeout=90, check=True)
        except subprocess.TimeoutExpired:
            raise ValueError('本地OCR超过90秒，请重试或手工标注') from None
        except subprocess.CalledProcessError:
            raise ValueError('本地OCR运行失败，请检查Swift工具链或手工标注') from None
        if len(process.stdout) > 2*1024*1024:
            raise ValueError('OCR输出超过2MB，请人工分区域标注')
        result = json.loads(process.stdout)
        return {'lines': validate_lines(result), 'revision': result['revision'],
                'width': result['width'], 'height': result['height']}


class OCRIn(BaseModel):
    page: int = Field(strict=True, ge=1, le=500)
    document_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


@router.get('/ocr/capabilities')
def capabilities(a=Depends(core.actor)):
    return capability()


def validate_job(p, d, job):
    validate_source(d, job)
    if job.get('engine') != VERSION:
        raise HTTPException(409, 'OCR版本已变化，请重新选择页面识别')
    page_region(d, {'page': job['page']})
    if not d['name'].lower().endswith('.pdf'):
        raise HTTPException(422, '本地OCR仅支持PDF逐页识别')


@router.post('/projects/{pid}/documents/{did}/ocr')
def submit(pid: str, did: str, body: OCRIn, tasks: BackgroundTasks, a=Depends(core.actor)):
    core.edit(a)
    with store.transaction() as c:
        p = core.project(c, pid, a); d = core.find(p, 'documents', did)
        job = {'id': store.uid(), 'kind': 'local_ocr', 'document_id': did, 'source_hash': body.document_hash,
               'page': body.page, 'engine': VERSION, 'state': 'queued', 'attempts': 0, 'message': '等待本地扫描文字识别'}
        validate_job(p, d, job)
        original_bytes(d)
        prior = next((j for j in p['jobs'] if j.get('kind') == 'local_ocr' and j.get('source_hash') == d['hash']
                      and j.get('page') == body.page and j.get('engine') == VERSION and j['state'] in ('queued', 'running', 'completed', 'failed')), None)
        if prior:
            return prior
        if not capability()['available']:
            raise HTTPException(503, capability()['notice'])
        p['jobs'].append(job)
        store.audit(c, p, a, 'OCR_REQUESTED', job)
        store.save(c, p)
    tasks.add_task(ocr_job, pid, did, job['id'], a)
    return job


def ocr_job(pid, did, jid, a):
    token = claim(pid, did, jid, a)
    if token is None:
        return
    try:
        # One native recognizer per server process. Database transactions are not held while recognizing.
        with OCR_LOCK:
            with store.connect() as c:
                p = core.task_project(c, pid, a); d = core.find(p, 'documents', did); job = core.find(p, 'jobs', jid)
                if not current(job, token): return
                validate_job(p, d, job)
                raw = original_bytes(d)
                page = job['page']
            result = recognize(raw, page)
        with store.transaction() as c:
            p = core.task_project(c, pid, a); d = core.find(p, 'documents', did); job = core.find(p, 'jobs', jid)
            if not current(job, token): return
            validate_job(p, d, job); original_bytes(d)
            lines = validate_lines(result)
            region = page_region(d, {'page': page})
            if lines:
                evidence = {'id': store.uid(), 'document_id': did, 'document_hash': d['hash'], 'page': page,
                            'text': '\n'.join(line['text'] for line in lines), 'method': 'local_ocr', **region,
                            'state': 'draft', 'ocr': {'engine': VERSION, 'revision': result['revision'],
                            'width': result['width'], 'height': result['height'], 'lines': lines,
                            'coordinate_space': SPACE, 'job_id': jid,
                            'notice': '机器识别候选，须对照原页；置信度不代表工程准确率。'}}
                p['evidence'].append(evidence)
                job['evidence_id'] = evidence['id']
            job.update(state='completed', finished_at=store.now(),
                       message='扫描文字已识别，须核对原图；未自动生成事实或工程量' if lines else '本页未识别到文字，请人工标注；不能据此判定图纸没有内容')
            store.audit(c, p, a, 'OCR_COMPLETED', {'job_id': jid, 'page': page, 'lines': len(lines), 'evidence_id': job.get('evidence_id')})
            store.save(c, p)
    except Exception as exc:
        fail(pid, did, jid, a, token, exc, model=True, failure_event='OCR_FAILED')
