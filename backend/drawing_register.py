"""Manual, reviewed drawing identities and cross-sheet references; no inferred geometry."""
import io
import unicodedata
from pathlib import Path
from pypdf import PdfReader
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store
from .task_execution import original_bytes

router=APIRouter(prefix='/api/v1')
VIEWS={'plan':'平面图','elevation':'立面图','section':'剖面图','detail':'详图','system':'系统图','other':'其他图纸'}
KINDS={'section':'剖面索引','elevation':'立面索引','detail':'详图索引','related':'专业互参'}
COLLECTIONS={'drawing_sheets','drawing_references'}


def text(body,key,limit=200):
    value=body.get(key)
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise HTTPException(422,'请填写有效的图纸字段：'+key)
    return value.strip()


def current(p,collection):
    series={}
    for row in p.get(collection,[]): series[row['series_id']]=row
    return list(series.values())


def assert_current(p,collection,row):
    if not any(r['id']==row['id'] for r in current(p,collection)):
        raise HTTPException(409,'历史图纸登记及互参只供查阅，请使用最新修订')


def number_key(value):
    return ''.join(unicodedata.normalize('NFKC',value).split()).casefold()


def conflicts(p):
    groups={}
    for row in current(p,'drawing_sheets'):
        doc=next((d for d in p['documents'] if d['id']==row['document_id']),{})
        if row['state']=='rejected' or not doc or doc.get('superseded'): continue
        groups.setdefault((row['discipline'],number_key(row['drawing_no'])),[]).append(row)
    return [{'discipline':key[0],'drawing_no':rows[0]['drawing_no'],'sheet_ids':[r['id'] for r in rows]}
            for key,rows in groups.items() if len(rows)>1]


def source(p,eid,document_id,page,raw_text,*,verify_bytes=True):
    e=core.require_evidence(p,eid);d=core.find(p,'documents',document_id)
    if d.get('superseded') or e['document_id']!=d['id'] or e.get('document_hash')!=d['hash']:
        raise HTTPException(409,'图纸依据不属于所选当前原件')
    if Path(d['name']).suffix.lower() not in ('.pdf','.dxf','.dwg','.ifc'):
        raise HTTPException(422,'图纸目录支持PDF、CAD和BIM原件；其他资料请使用工程事实')
    raw=original_bytes(d) if verify_bytes else None
    if d['name'].lower().endswith('.pdf'):
        if type(page) is not int or page<1 or e.get('page')!=page:
            raise HTTPException(422,'图纸页码须与所选PDF证据页一致')
        if verify_bytes:
            try: count=len(PdfReader(io.BytesIO(raw)).pages)
            except Exception: raise HTTPException(409,'无法核对原始PDF页数') from None
            if page>count: raise HTTPException(422,'图纸页码超出原始PDF')
    elif page is not None:
        raise HTTPException(422,'CAD/BIM登记不伪造固定PDF页码，请留空')
    if not isinstance(raw_text,str) or not raw_text.strip() or len(raw_text)>2000 or raw_text not in e['text']:
        raise HTTPException(422,'图纸摘录须为所选证据中的连续原文，最多2000字')
    return e,d


def validate_sheet(p,row,*,check_conflict=True):
    assert_current(p,'drawing_sheets',row)
    e,d=source(p,row['evidence_id'],row['document_id'],row['page'],row['raw_text'])
    if row['document_hash']!=d['hash'] or row['evidence_hash']!=store.digest(e):
        raise HTTPException(409,'图纸身份依据已变化，请修订登记并重新复核')
    trace=row.get('candidate_trace',{})
    if trace.get('ocr'):
        from .drawing_candidates import ocr_origin
        if ocr_origin(p,e,trace['fields'])!=trace['ocr']:
            raise HTTPException(409,'扫描识别任务或原行依据已变化，请重新登记并复核')
    if check_conflict and any(row['id'] in g['sheet_ids'] for g in conflicts(p)):
        raise HTTPException(409,'同专业同图号有多个当前候选，请先拒绝不采纳登记')


def endpoints(p,row,*,saved=True):
    if row['from_sheet_id']==row['to_sheet_id']:raise HTTPException(422,'互参须选择两张不同图纸')
    sheets=[]
    for side in ('from','to'):
        sheet=core.find(p,'drawing_sheets',row[side+'_sheet_id'])
        assert_current(p,'drawing_sheets',sheet)
        if sheet['state']!='approved':raise HTTPException(409,'互参两端图纸须先独立复核通过')
        validate_sheet(p,sheet)
        if saved and row[side+'_sheet_hash']!=sheet_stamp(p,sheet):
            raise HTTPException(409,'互参所指图纸或审批已变化，请修订互参并重新复核')
        sheets.append(sheet)
    if row['kind']!='related' and sheets[1]['view_type']!=row['kind']:
        raise HTTPException(422,'索引类型与目标图纸视图不一致')
    return sheets


def sheet_stamp(p,sheet):
    review=next((r for r in reversed(p['reviews']) if r['collection']=='drawing_sheets' and r['object_id']==sheet['id']),None)
    if not review or review['decision']!='approve':raise HTTPException(409,'图纸缺少当前独立复核记录')
    return store.digest({'sheet':sheet,'review':review})


def validate_reference(p,row):
    assert_current(p,'drawing_references',row)
    start,_=endpoints(p,row)
    e,d=source(p,row['evidence_id'],start['document_id'],start['page'],row['raw_text'])
    if row['marker'] not in row['raw_text'] or row['document_hash']!=d['hash'] or row['evidence_hash']!=store.digest(e):
        raise HTTPException(409,'互参标记或原文依据已变化，请重新核对索引')


def diagnostics(p):
    out=[];source_issues={key:{} for key in COLLECTIONS}
    for collection,validate,label in [('drawing_sheets',validate_sheet,'图纸登记'),('drawing_references',validate_reference,'图纸互参')]:
        for row in current(p,collection):
            if row['state']=='rejected':continue
            name=row.get('drawing_no') or row.get('component_name')
            if row['state']!='approved':out.append(label+' '+name+' 尚未复核通过')
            try:validate(p,row)
            except HTTPException as exc:
                source_issues[collection][row['id']]=str(exc.detail)
                out.append(label+' '+name+'：'+str(exc.detail))
    return {'issues':list(dict.fromkeys(out)),'source_issues':source_issues}


def issues(p):
    return diagnostics(p)['issues']


def projection(p):
    return {'current_sheet_ids':[r['id'] for r in current(p,'drawing_sheets')],
            'current_reference_ids':[r['id'] for r in current(p,'drawing_references')],
            'conflicts':conflicts(p),**diagnostics(p),'view_types':VIEWS,'reference_kinds':KINDS}


def report_tables(p, current_checks=False):
    states={'draft':'待复核','approved':'已审定','rejected':'不采纳','needs_info':'待补依据','stale':'需重新复核'}
    sheets=p.get('drawing_sheets',[]);refs=p.get('drawing_references',[])
    sheet_ids={r['id'] for r in current(p,'drawing_sheets')};ref_ids={r['id'] for r in current(p,'drawing_references')}
    rows=[]
    for r in sheets:
        doc=next((d for d in p['documents'] if d['id']==r['document_id']),{})
        rows.append([r['drawing_no']+' / '+r['title'],r['discipline']+' / '+VIEWS[r['view_type']],r['edition']+' / R'+str(r['revision']),
                     ('当前登记' if r['id'] in sheet_ids else '历史登记')+' / '+states.get(r['state'],r['state']),
                     doc.get('name','来源缺失')+' / '+str(r['page'] or '无固定页码'),r['evidence_id']+' / '+r['raw_text'],r['reason']])
    result=[('图纸目录与版次',['图号 / 图名','专业 / 视图','图纸版次 / 登记修订','登记范围 / 审核状态','原始文件 / 页码','证据ID / 摘录','登记依据'],rows)] if sheets else []
    rows=[]
    for r in refs:
        ends=[next((s for s in sheets if s['id']==r[k+'_sheet_id']),{}) for k in ('from','to')]
        rows.append([r['component_name']+' / '+KINDS[r['kind']],r['marker']+' / R'+str(r['revision']),
                     ' → '.join(s.get('drawing_no','来源缺失') for s in ends),
                     ' → '.join(s.get('edition','')+' / R'+str(s.get('revision','')) for s in ends),
                     ('当前互参' if r['id'] in ref_ids else '历史互参')+' / '+states.get(r['state'],r['state']),
                     r['evidence_id']+' / '+r['raw_text'],r['reason']])
    if refs:result.append(('平立剖图纸互参',['构件 / 索引类型','索引标记 / 互参修订','起点 → 目标图号','所指图纸登记版本','互参范围 / 审核状态','原始证据 / 摘录','关联依据'],rows))
    adopted=[]
    labels={'drawing_no':'图号','title':'图名','edition':'版次','view_type':'视图类型'}
    for r in sheets:
        trace=r.get('candidate_trace')
        if not trace:continue
        for key,label in labels.items():
            original=trace['fields'].get(key,[])
            adopted.append([r['drawing_no']+' / R'+str(r['revision']),trace['parser_version']+' / '+label,
                            ' / '.join(o['value'] for o in original) or '原文未提取，人工补充',
                            (VIEWS[r[key]] if key=='view_type' else r[key])+('；人工补充或修正：'+r['reason'] if key in trace['corrections'] else '；核对采用'),
                            trace['evidence_id']+' / '+trace['id'],'；'.join(o['raw_text'] for o in original) or '请核对登记原文及人工说明'])
    if adopted:result.append(('图纸身份候选采用',['图纸 / 登记修订','采用规则 / 字段','原文候选值','登记值 / 修正说明','候选证据 / ID','字段摘录'],adopted))
    ocr_rows=[]
    for r in sheets:
        trace=r.get('candidate_trace',{});ocr=trace.get('ocr');verification=trace.get('ocr_verification',{})
        if not ocr:continue
        for key,label in labels.items():
            lines=ocr['field_lines'].get(key,[])
            ocr_rows.append([r['drawing_no']+' / R'+str(r['revision']),label,
                ocr['engine']+' / '+str(ocr['revision']),
                '；'.join(str(line['line'])+'行：'+line['text']+' / 置信度 '+str(line['confidence'])+' / 显示页位置 '+str(line['bbox']) for line in lines) or '无机器候选，人工补填',
                verification.get('checked_by','')+' / '+verification.get('checked_at','')+' / 已核对原页全部字段',
                '机器置信度不代表工程准确率；仍须独立复核'])
    if ocr_rows:result.append(('扫描标题栏字段核对',['图纸 / 修订','字段','识别器 / 版本','识别原行 / 位置','编制人核对记录','边界'],ocr_rows))
    if current_checks and (sheets or refs):
        pending=issues(p)
        if pending:result.append(('图纸校核待办',['当前未解决事项'],[[s] for s in pending]))
    return result


def save_record(pid,collection,body,a,previous_id='',candidate_id=''):
    core.edit(a)
    allowed=({'document_id','page','drawing_no','title','edition','view_type','discipline','evidence_id','raw_text','reason'} if collection=='drawing_sheets' else
             {'from_sheet_id','to_sheet_id','component_name','kind','marker','evidence_id','raw_text','reason'})
    if set(body)-allowed-({'object_hash','ocr_source_checked'} if candidate_id else {'object_hash'} if previous_id else set()):raise HTTPException(422,'图纸登记含不支持字段')
    with store.transaction() as c:
        p=core.project(c,pid,a);old=core.find(p,collection,previous_id) if previous_id else None
        candidate=None
        if candidate_id:
            from .drawing_candidates import require_candidate
            candidate=require_candidate(p,candidate_id,body.get('object_hash'))
            if candidate.get('ocr') and body.get('ocr_source_checked') is not True:
                raise HTTPException(422,'请对照扫描原图核对全部字段及人工补填，并确认已核对原页')
            if not candidate.get('ocr') and 'ocr_source_checked' in body:
                raise HTTPException(422,'当前候选不是扫描文字识别来源')
            if any(body.get(k)!=candidate[k] for k in ('document_id','page','evidence_id')):
                raise HTTPException(409,'采用候选须绑定原来的文件、页码和证据；其他来源请人工登记')
        if old:
            assert_current(p,collection,old)
            if body.get('object_hash')!=store.digest(old):raise HTTPException(409,'图纸记录已变化，请刷新后修订')
        row={'id':store.uid(),'series_id':old['series_id'] if old else store.uid(),'revision':old['revision']+1 if old else 1,
             'previous_id':previous_id or None,'state':'draft','created_by':a['username'],'created_at':store.now(),
             'evidence_id':text(body,'evidence_id',100),'raw_text':text(body,'raw_text',2000),'reason':text(body,'reason',2000)}
        if collection=='drawing_sheets':
            row.update({k:text(body,k) for k in ['document_id','drawing_no','title','edition','view_type','discipline']},page=body.get('page'))
            if row['view_type'] not in VIEWS or row['discipline'] not in core.REQUIREMENTS:raise HTTPException(422,'图纸视图或专业无效')
            e,d=source(p,row['evidence_id'],row['document_id'],row['page'],row['raw_text'])
        else:
            row.update({k:text(body,k) for k in ['from_sheet_id','to_sheet_id','component_name','kind','marker']})
            if row['kind'] not in KINDS:raise HTTPException(422,'图纸互参类型无效')
            start,end=endpoints(p,row,saved=False)
            row.update(from_sheet_hash=sheet_stamp(p,start),to_sheet_hash=sheet_stamp(p,end))
            e,d=source(p,row['evidence_id'],start['document_id'],start['page'],row['raw_text'])
            if row['marker'] not in row['raw_text']:raise HTTPException(422,'索引标记须出现在所选原文摘录中')
        row.update(document_hash=d['hash'],evidence_hash=store.digest(e))
        if collection=='drawing_sheets':
            row['identity_origin']=('ocr_labels' if candidate.get('ocr') else 'explicit_labels') if candidate else 'manual_revision' if old else 'manual'
            if candidate:
                row['candidate_trace']={**candidate,'candidate_hash':store.digest(candidate),
                    'corrections':{k:{'suggested':candidate['suggestions'].get(k),'adopted':row[k]}
                                   for k in ('drawing_no','title','edition','view_type') if candidate['suggestions'].get(k)!=row[k]}}
        if candidate and candidate.get('ocr'):
            row['candidate_trace']['ocr_verification']={'checked_by':a['username'],'checked_at':row['created_at'],
                'fields':['drawing_no','title','edition','view_type'],'original_page_checked':True}
        p[collection].append(row)
        core.invalidate(p,'components' if collection=='drawing_sheets' else 'quotes')
        store.audit(c,p,a,'DRAWING_SHEET_RECORDED' if collection=='drawing_sheets' else 'DRAWING_REFERENCE_RECORDED',row)
        if candidate:
            store.audit(c,p,a,'DRAWING_IDENTITY_CANDIDATE_ADOPTED',{'candidate_id':candidate_id,'candidate_hash':store.digest(candidate),
                'sheet_id':row['id'],'corrections':row['candidate_trace']['corrections']})
        store.save(c,p)
    return row


@router.post('/projects/{pid}/drawing-sheets')
def add_sheet(pid:str,body:dict,a=Depends(core.actor)):return save_record(pid,'drawing_sheets',body,a)


@router.post('/projects/{pid}/drawing-sheets/{sid}/revisions')
def revise_sheet(pid:str,sid:str,body:dict,a=Depends(core.actor)):return save_record(pid,'drawing_sheets',body,a,sid)


@router.post('/projects/{pid}/drawing-references')
def add_reference(pid:str,body:dict,a=Depends(core.actor)):return save_record(pid,'drawing_references',body,a)


@router.post('/projects/{pid}/drawing-references/{rid}/revisions')
def revise_reference(pid:str,rid:str,body:dict,a=Depends(core.actor)):return save_record(pid,'drawing_references',body,a,rid)
