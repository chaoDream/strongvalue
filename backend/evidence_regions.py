"""Displayed PDF coordinates locate source text; never calculate design dimensions."""
import hashlib
import io
import math
from pathlib import Path
from fastapi import HTTPException
from pypdf import PdfReader
from . import store

SPACE = 'pdf_display_normalized_v1'


def page_region(document, body):
    page = body.get('page')
    if type(page) is not int or page < 1:
        raise HTTPException(422, '页号须为正整数')
    bbox = body.get('bbox')
    is_pdf = Path(document['name']).suffix.lower() == '.pdf'
    if bbox is not None:
        if not is_pdf:
            raise HTTPException(422, '页内区域定位仅支持PDF原件')
        if body.get('coordinate_space') != SPACE or body.get('document_hash') != document['hash']:
            raise HTTPException(409, '原件版本或坐标约定不一致，请重新打开当前PDF框选')
        if not isinstance(bbox, list) or len(bbox) != 4 or any(type(v) not in (float,int) or not math.isfinite(v) for v in bbox):
            raise HTTPException(422, '证据区域须为四个有限数字')
        x,y,w,h = bbox
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > 1 or y+h > 1:
            raise HTTPException(422, '证据框须完整位于当前显示页内，宽高须大于零')
    if not is_pdf:
        return {'bbox':None}
    try:
        raw = (store.ROOT/'originals'/document['hash']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != document['hash']:
            raise ValueError('hash mismatch')
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError('encrypted document')
        if page > len(reader.pages):
            raise HTTPException(422, '证据页码超出原始PDF实际页数')
        pdf_page = reader.pages[page-1]
        geometry = {'rotation': int(pdf_page.rotation or 0) % 360,
                    'crop_box': [float(v) for v in pdf_page.cropbox]}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, '无法核对PDF原件，请恢复原件并重新打开') from None
    return {'bbox':bbox, 'coordinate_space':SPACE if bbox is not None else None, 'page_geometry':geometry}
