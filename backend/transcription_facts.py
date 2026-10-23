"""Explicit facts from human OCR transcriptions, adopted into independent review."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from . import main as core, store, facts
from .parsers import fact_candidates

router=APIRouter(prefix='/api/v1')
VERSION='transcription-facts-1.0'


def source(p,eid):
    evidence=core.require_evidence(p,eid)
    if 'ocr_transcription' not in evidence:
        raise HTTPException(422,'此入口只读取已保存的扫描人工抄录；其他资料请使用工程事实入口')
    return evidence,core.find(p,'documents',evidence['document_id'])


def candidates(evidence):
    rows=[]
    for index,record in enumerate(fact_candidates(evidence['text'])):
        row={'parser_version':VERSION,'normalization_version':facts.VERSION,'evidence_id':evidence['id'],
             'evidence_hash':store.digest(evidence),'ordinal':index+1,**record}
        row['id']=store.digest(row)[:32]
        rows.append(row)
    return rows


def active_fact(p,cid):
    return next((f for f in p['facts'] if f.get('transcription_adoption',{}).get('candidate',{}).get('id')==cid
                 and f['state'] not in ('rejected','superseded')),None)


@router.get('/projects/{pid}/evidence/{eid}/transcription-facts')
def read(pid:str,eid:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),a=Depends(core.actor)):
    with store.connect() as conn:
        p=core.project(conn,pid,a);e,d=source(p,eid);rows=candidates(e)
        result=[]
        for row in rows[offset:offset+limit]:
            active=active_fact(p,row['id'])
            result.append({**row,'object_hash':store.digest(row),'active_fact_id':active['id'] if active else None})
        return {'candidates':result,'total':len(rows),'offset':offset,'limit':limit,
                'next_offset':offset+limit if offset+limit<len(rows) else None,
                'source':{'document_name':d['name'],'page':e['page'],'text':e['text'],
                          'machine_text':e['ocr_transcription']['original_line']['text'],'reason':e['ocr_transcription']['reason']},
                'notice':'仅从人工抄录的明确标签或数值单位提取；逐项核对后形成待复核事实，不自动选定冲突值或修改工程量。'}


class AdoptIn(BaseModel):
    object_hash:str=Field(pattern=r'^[a-f0-9]{64}$')
    reason:str=Field(strict=True,min_length=1,max_length=2000)
    model_config={'extra':'forbid'}


@router.post('/projects/{pid}/evidence/{eid}/transcription-facts/{cid}/adopt')
def adopt(pid:str,eid:str,cid:str,body:AdoptIn,a=Depends(core.actor)):
    core.edit(a)
    if not body.reason.strip():raise HTTPException(422,'请填写事实字段及数值的核对依据')
    with store.transaction() as conn:
        p=core.project(conn,pid,a);e,d=source(p,eid)
        row=next((row for row in candidates(e) if row['id']==cid),None)
        if not row or body.object_hash!=store.digest(row):
            raise HTTPException(409,'抄录事实候选或来源已变化，请重新读取并核对')
        if active_fact(p,cid):raise HTTPException(409,'此候选已有事实记录，请复核或拒绝原记录，不能重复采用')
        normalized=facts.normalize(row['key'],row['original_value'],row['original_unit'],known=True)
        record=facts.bind(normalized,e,d,'manual',row['raw_text'])
        record['confidence_category']='人工抄录候选待核'
        record['transcription_adoption']={'candidate':row,'candidate_hash':store.digest(row),'reason':body.reason.strip(),
                                          'created_by':a['username'],'created_at':store.now()}
        facts.append(p,record)
        core.invalidate(p,'facts')
        store.audit(conn,p,a,'TRANSCRIPTION_FACT_ADOPTED',{'fact_id':record['id'],'candidate_id':cid,
                    'candidate_hash':store.digest(row),'evidence_id':eid,'reason':body.reason.strip()})
        store.save(conn,p)
        return record
