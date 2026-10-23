"""Append-only human transcriptions retain their machine line and verified original."""
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam
from pydantic import Field
from . import main as core, store
from .ocr import LineLocationIn, verified_line_location

router=APIRouter(prefix='/api/v1')
VERSION='ocr-line-transcription-1.0'


class TranscriptionIn(LineLocationIn):
    evidence_hash: str=Field(pattern=r'^[a-f0-9]{64}$')
    text: str=Field(strict=True,min_length=1,max_length=2000)
    reason: str=Field(strict=True,min_length=1,max_length=2000)
    original_page_checked: bool=Field(strict=True)


def validate_transcription(p,e,*,verify_bytes=True):
    trace=e.get('ocr_transcription',{})
    if not isinstance(trace,dict) or trace.get('version')!=VERSION:
        raise HTTPException(409,'扫描人工抄录来源记录无效，请重新核对原页')
    location=trace.get('source_location',{})
    if not isinstance(location,dict) or not isinstance(trace.get('original_line'),dict):
        raise HTTPException(409,'扫描人工抄录原行记录无效')
    source=next((row for row in p['evidence'] if row['id']==location.get('evidence_id')),None)
    if not source or source.get('method')!='local_ocr' or source.get('ocr_transcription'):
        raise HTTPException(409,'人工抄录必须追溯至机器识别原行，不允许循环引用')
    try:body=LineLocationIn(expected_line=trace['original_line'],evidence_hash=location.get('evidence_hash'))
    except ValueError:raise HTTPException(409,'扫描人工抄录来源校验值无效') from None
    if not body.evidence_hash:raise HTTPException(409,'扫描人工抄录缺少来源校验值')
    rebuilt=verified_line_location(p,location.get('evidence_id'),location.get('line'),body,verify_bytes=verify_bytes)
    if rebuilt!=location or e.get('text')!=trace.get('text') or trace.get('original_page_checked') is not True:
        raise HTTPException(409,'扫描人工抄录或原行来源已变化，请重新记录并复核')
    if any(e.get(k)!=location.get(k) for k in ('document_id','document_hash','page','bbox','coordinate_space','page_geometry')):
        raise HTTPException(409,'扫描人工抄录的位置或原件版本已变化')


@router.post('/projects/{pid}/evidence/{eid}/ocr-lines/{number}/transcriptions')
def transcribe(pid:str,eid:str,body:TranscriptionIn,number:int=PathParam(ge=1,le=2000),a=Depends(core.actor)):
    core.edit(a)
    if not body.text.strip() or not body.reason.strip() or body.original_page_checked is not True:
        raise HTTPException(422,'请填写核对文字及依据，并确认已对照原页')
    with store.transaction() as c:
        p=core.project(c,pid,a)
        location=verified_line_location(p,eid,number,body)
        text,reason=body.text.strip(),body.reason.strip()
        key=store.digest({'source':location['evidence_hash'],'line':number,'text':text,'reason':reason,'actor':a['username']})
        previous=next((e for e in p['evidence'] if e.get('ocr_transcription',{}).get('request_key')==key),None)
        if previous:
            validate_transcription(p,previous)
            return {'evidence':previous,'duplicate':True}
        record={'id':store.uid(),'document_id':location['document_id'],'document_hash':location['document_hash'],
                'page':location['page'],'bbox':location['bbox'],'coordinate_space':location['coordinate_space'],
                'page_geometry':location['page_geometry'],'text':text,'method':'manual','state':'draft',
                'ocr_transcription':{'version':VERSION,'source_location':location,
                    'original_line':{k:location[k] for k in ('text','bbox','confidence')},'text':text,'reason':reason,
                    'original_page_checked':True,'created_by':a['username'],'created_at':store.now(),'request_key':key}}
        p['evidence'].append(record)
        store.audit(c,p,a,'OCR_LINE_TRANSCRIBED',record)
        store.save(c,p)
        return {'evidence':record,'duplicate':False}
