"""Read-only explicit title-block candidates, adopted through the reviewed register."""
import re
import unicodedata
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from . import main as core, store

router=APIRouter(prefix='/api/v1')
VERSION='drawing-identity-labels-1.0'
FIELDS={
    'drawing_no':('图号',['图号','图纸编号','Drawing No.','Drawing No']),
    'title':('图名',['图名','图纸名称','图纸标题','Drawing Title']),
    'edition':('版次',['版次','图纸版次','图纸版本','修订版次','Revision','Rev.']),
    'view_type':('视图类型',['视图类型','图纸类型','View Type']),
}
VIEWS={'平面图':'plan','立面图':'elevation','剖面图':'section','详图':'detail','系统图':'system','其他图纸':'other',
       'plan':'plan','elevation':'elevation','section':'section','detail':'detail','system':'system','other':'other'}
ALIASES='|'.join(re.escape(a) for _,names in FIELDS.values() for a in sorted(names,key=len,reverse=True))
PATTERN=re.compile(r'(?:^|[\n\r；;\t，,]|(?<=\s))(?P<label>'+ALIASES+r')[^\S\r\n]*[:：][^\S\r\n]*(?P<value>[^\n\r；;\t]*?)(?=(?:\s+|[，,])(?:'+ALIASES+r')\s*[:：]|[\n\r；;\t]|$)',re.I)


def identity_fields(text):
    fields={};warnings=[];overflow=False
    for index,match in enumerate(PATTERN.finditer(text)):
        if index>=100:
            overflow=True;warnings.append('单份证据超过100个标签，候选已截断，请另记标题栏证据后登记')
            break
        key=next(k for k,(_,names) in FIELDS.items() if match['label'].casefold() in [n.casefold() for n in names])
        value=match['value'].strip()
        if not value:continue
        if len(value)>200:
            warnings.append(FIELDS[key][0]+'超过200字，未预填，请人工核对')
            continue
        normalized=' '.join(unicodedata.normalize('NFKC',value).split())
        comparison=''.join(normalized.split()).casefold() if key=='drawing_no' else normalized.casefold() if key in ('edition','view_type') else normalized
        if key=='view_type':comparison=VIEWS.get(comparison,comparison)
        raw=match.group(0).lstrip('\n\r；;\t，, ')
        start=match.start()+len(match.group(0))-len(raw)
        fields.setdefault(key,[]).append({'value':value,'comparison':comparison,'raw_text':raw,'start':start,'end':match.end()})
    if not fields.get('drawing_no') and not fields.get('title'):return None
    suggestions={};conflicts=[];output={}
    for key,occurrences in fields.items():
        distinct={o['comparison'] for o in occurrences}
        if len(distinct)>1:conflicts.append(key)
        elif key!='view_type':suggestions[key]=occurrences[0]['value']
        else:
            proposed=VIEWS.get(occurrences[0]['comparison'])
            if proposed:suggestions[key]=proposed
            else:warnings.append('视图类型不在受控目录中，请人工选择')
        output[key]=[o for o in occurrences if o['raw_text']]
    spans=[o for occurrences in output.values() for o in occurrences]
    start=min(o['start'] for o in spans);end=max(o['end'] for o in spans)
    adoptable=end-start<=2000 and not overflow
    if end-start>2000:warnings.append('字段间距超过2000字，请在原图另记标题栏证据后登记')
    return {'fields':output,'suggestions':suggestions,'conflicting_fields':conflicts,
            'missing_fields':[k for k in FIELDS if k not in suggestions],
            'raw_text':text[start:min(end,start+2000)],'adoptable':adoptable,'warnings':list(dict.fromkeys(warnings))}


def candidates(p):
    docs={d['id']:d for d in p['documents'] if not d.get('superseded') and Path(d['name']).suffix.lower() in ('.pdf','.dxf','.dwg','.ifc')}
    out=[]
    for e in p['evidence']:
        d=docs.get(e['document_id'])
        if not d or e.get('method') not in ('local_text','manual') or e.get('document_hash')!=d['hash']:continue
        if d['name'].lower().endswith('.pdf') and (type(e.get('page')) is not int or e['page']<1):continue
        result=identity_fields(e['text'])
        if not result:continue
        out.append({'id':store.digest({'version':VERSION,'document_id':d['id'],'evidence_id':e['id']})[:32],
                    'parser_version':VERSION,'document_id':d['id'],'document_hash':d['hash'],'document_name':d['name'],
                    'page':e['page'] if d['name'].lower().endswith('.pdf') else None,'discipline':d['specialty'],
                    'evidence_id':e['id'],'evidence_hash':store.digest(e),'source_method':e['method'],**result})
    return out


def active_adoption(p,candidate_id):
    from .drawing_register import current
    series={s['series_id'] for s in p['drawing_sheets'] if s.get('candidate_trace',{}).get('id')==candidate_id}
    return next((s for s in current(p,'drawing_sheets') if s['series_id'] in series and s['state']!='rejected'),None)


def require_candidate(p,candidate_id,object_hash):
    row=next((r for r in candidates(p) if r['id']==candidate_id),None)
    if not row:raise HTTPException(409,'标题栏候选已失效，请重新读取当前原文')
    if object_hash!=store.digest(row):raise HTTPException(409,'候选或来源已变化，请重新读取并核对')
    if not row['adoptable']:raise HTTPException(409,'候选原文范围过大，请另记标题栏证据后人工登记')
    if active_adoption(p,candidate_id):raise HTTPException(409,'此候选已有当前登记，请修订该登记，不能重复采用')
    return row


@router.get('/projects/{pid}/drawing-candidates')
def read_candidates(pid:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),document_id:str='',a=Depends(core.actor)):
    with store.connect() as c:
        p=core.project(c,pid,a);rows=candidates(p)
        if document_id:
            core.find(p,'documents',document_id)
            rows=[r for r in rows if r['document_id']==document_id]
        result=[]
        for r in rows[offset:offset+limit]:
            active=active_adoption(p,r['id'])
            result.append({**r,'object_hash':store.digest(r),'active_sheet_id':active['id'] if active else None})
        return {'candidates':result,'total':len(rows),'offset':offset,'limit':limit,
                'next_offset':offset+limit if offset+limit<len(rows) else None,'parser_version':VERSION,
                'field_labels':{k:v[0] for k,v in FIELDS.items()},
                'notice':'仅按明确标签提取候选，未核验语义或审批；采用时核对原件，不推断缺项、视图或构件尺寸。'}


@router.post('/projects/{pid}/drawing-candidates/{cid}/adopt')
def adopt(pid:str,cid:str,body:dict,a=Depends(core.actor)):
    from .drawing_register import save_record
    return save_record(pid,'drawing_sheets',body,a,candidate_id=cid)
