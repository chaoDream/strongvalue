"""Tenant-scoped document requests; closure never substitutes for coverage review."""
import copy
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store

router = APIRouter(prefix='/api/v1')
CLOSED = {'approved', 'cancelled'}


def text(body, key, limit=3000):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise HTTPException(422, '请填写有效的'+{'name':'任务名称','detail':'缺项说明','reason':'操作依据','note':'补充说明'}.get(key,key))
    return value.strip()


def assignment(c, body, tenant):
    username = body.get('assignee')
    if not isinstance(username, str) or not c.execute('SELECT 1 FROM users WHERE tenant=? AND username=? AND active=1 AND role IN (\'admin\',\'manager\',\'estimator\')', (tenant,username)).fetchone():
        raise HTTPException(422, '负责人须为本企业启用中的编制、负责人或管理员账号')
    try:
        due = date.fromisoformat(body['due_date']).isoformat()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(422, '请填写有效的计划完成日期') from None
    return username, due


def validate_submission(p, task):
    if not task.get('evidence_ids') or not task.get('submissions'):
        raise HTTPException(409, '请先提交补充说明及原始依据')
    for eid in task['evidence_ids']:
        evidence = core.require_evidence(p, eid)
        document = core.find(p, 'documents', evidence['document_id'])
        if document['specialty'] != task['specialty']:
            raise HTTPException(409, '补充依据的专业与补件任务不一致')
        if evidence['document_hash'] != document['hash'] or not (store.ROOT/'originals'/document['hash']).is_file():
            raise HTTPException(409, '补充依据的原件关联失效或文件缺失')


def task_issues(p):
    issues = []
    for task in p.get('document_requests', []):
        if not task['blocking'] or task['specialty'] not in p['scope'] or task['state'] == 'cancelled':
            continue
        if task['state'] != 'approved':
            issues.append(task['specialty']+'补件任务未关闭：'+task['name'])
        else:
            try:
                validate_submission(p, task)
            except HTTPException:
                issues.append(task['specialty']+'已关闭补件依据失效，须重新提交复核：'+task['name'])
    return issues


def source_issues(p):
    result = {}
    for task in p.get('document_requests', []):
        if task['state'] == 'approved':
            try:
                validate_submission(p, task)
            except HTTPException as exc:
                result[task['id']] = str(exc.detail)
    return result


@router.get('/projects/{pid}/document-request-assignees')
def assignees(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        core.project(c, pid, a)
        return [dict(r) for r in c.execute("SELECT username,role FROM users WHERE tenant=? AND active=1 AND role IN ('admin','manager','estimator') ORDER BY username",(a['tenant'],))]


@router.post('/projects/{pid}/document-requests')
def create(pid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    name, detail = text(body,'name',150), text(body,'detail')
    if not isinstance(body.get('specialty'),str) or body['specialty'] not in core.REQUIREMENTS or type(body.get('blocking')) is not bool:
        raise HTTPException(422, '请选择专业及是否阻断本次范围成果')
    with store.transaction() as c:
        p = core.project(c, pid, a)
        owner, due = assignment(c,body,a['tenant'])
        task = {'id':store.uid(),'name':name,'detail':detail,'specialty':body['specialty'],'blocking':body['blocking'],
                'assignee':owner,'due_date':due,'state':'draft','version':1,'created_at':store.now(),
                'created_by':a['username'],'evidence_ids':[],'submissions':[]}
        p['document_requests'].append(task)
        core.invalidate(p,'quotes')
        store.audit(c,p,a,'DOCUMENT_REQUEST_CREATED',task)
        store.save(c,p)
    return task


@router.post('/projects/{pid}/document-requests/{tid}/actions')
def action(pid: str, tid: str, body: dict, a=Depends(core.actor)):
    core.edit(a)
    reason = text(body,'reason')
    with store.transaction() as c:
        p = core.project(c,pid,a);task = core.find(p,'document_requests',tid)
        if store.digest(task) != body.get('object_hash'):
            raise HTTPException(409,'任务已变化，请刷新后重新操作')
        privileged = a['role'] in ('admin','manager')
        if not privileged and a['username'] != task['assignee']:
            raise HTTPException(403,'仅任务负责人或企业管理员/项目负责人可更新此任务')
        kind = body.get('action')
        before = copy.deepcopy(task)
        if kind in ('cancel','reopen'):
            if not privileged:
                raise HTTPException(403,'仅企业管理员或项目负责人可取消或重新开启任务')
            if kind == 'cancel' and task['state'] == 'cancelled':
                raise HTTPException(409,'任务已经取消')
            if kind == 'reopen' and task['state'] not in CLOSED:
                raise HTTPException(409,'只能重新开启已关闭或已取消的任务')
            task['state'] = 'cancelled' if kind == 'cancel' else 'draft'
            task['evidence_ids'] = []
        elif kind == 'update':
            if task['state'] in CLOSED or task['state'] == 'submitted':
                raise HTTPException(409,'已关闭或送审任务不能直接修改；请重新开启或等待复核')
            owner, due = assignment(c,body,a['tenant'])
            if not privileged and (owner != task['assignee'] or body.get('blocking') != task['blocking']):
                raise HTTPException(403,'调整负责人或阻断等级须由管理员或项目负责人处理')
            if type(body.get('blocking')) is not bool:
                raise HTTPException(422,'阻断标记无效')
            task.update(assignee=owner,due_date=due,blocking=body['blocking'])
        elif kind == 'start':
            if task['state'] not in ('draft','needs_info','rejected'):
                raise HTTPException(409,'当前任务不能开始处理')
            task['state'] = 'in_progress'
        elif kind == 'submit':
            if task['state'] in CLOSED or task['state'] == 'submitted':
                raise HTTPException(409,'任务已关闭或已在复核中')
            ids = body.get('evidence_ids')
            if not isinstance(ids,list) or not 1 <= len(ids) <= 20 or any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids):
                raise HTTPException(422,'请选择1至20条不同的当前原始依据')
            submission = {'id':store.uid(),'author':a['username'],'timestamp':store.now(),'note':text(body,'note'),'evidence_ids':ids}
            task['evidence_ids'] = ids
            task['submissions'].append(submission)
            validate_submission(p,task)
            task['state'] = 'submitted'
        else:
            raise HTTPException(422,'任务操作无效')
        task.update(version=task['version']+1,updated_at=store.now())
        core.invalidate(p,'quotes')
        store.audit(c,p,a,'DOCUMENT_REQUEST_'+kind.upper(),{'task_id':tid,'reason':reason,'before':before,'after':task})
        store.save(c,p)
    return task
