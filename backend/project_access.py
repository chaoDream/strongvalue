"""Project membership narrows tenant access; organization roles remain authoritative."""
import copy
from contextvars import ContextVar
from fastapi import APIRouter, Depends, HTTPException
from . import main as core, store

router = APIRouter(prefix='/api/v1')
denial_context = ContextVar('project_access_denials', default=None)


def denied(pid):
    scope=denial_context.get()
    if scope is not None: scope['projects'].add(pid)


def audit_denials(scope, method, path):
    a=scope['actor']
    if not a or not scope['projects']: return
    with store.transaction() as c:
        for pid in sorted(scope['projects']):
            p=core.task_project(c,pid,a)
            store.audit(c,p,a,'PROJECT_ACCESS_DENIED',{'reason':'非项目成员的访问已拒绝','method':method,'path':path[:500]})


def config(p):
    return copy.deepcopy(p.get('access', {'mode':'tenant','member_ids':[],'version':0}))


def allowed(p, a):
    if p['tenant'] != a.get('tenant'):
        return False
    access=config(p)
    return a.get('role')=='admin' or access['mode']=='tenant' or a.get('id') in access['member_ids']


def manage(p, a):
    return allowed(p,a) and a.get('role') in ('admin','manager')


def members(c, tenant):
    return [dict(r) for r in c.execute('SELECT id,username,role,active FROM users WHERE tenant=? ORDER BY username',(tenant,))]


def target(c, p, a):
    access=config(p)
    return {'access':access, 'object_hash':store.digest(access), 'can_manage':manage(p,a),
            'members':members(c,a['tenant']) if manage(p,a) else [],
            'notice':'企业管理员始终可访问本企业项目；成员名单不改变编制、复核等组织角色。已下载文件、历史导出和先前复制到其他项目的资料不能通过成员调整追回。'}


@router.get('/projects/{pid}/access')
def read(pid: str, a=Depends(core.actor)):
    with store.connect() as c:
        return target(c,core.project(c,pid,a),a)


@router.put('/projects/{pid}/access')
def update(pid: str, body: dict, a=Depends(core.actor)):
    if a['role'] not in ('admin','manager'):
        raise HTTPException(403,'仅企业管理员或有权访问的项目负责人可以维护项目成员')
    mode=body.get('mode');ids=body.get('member_ids');reason=body.get('reason')
    if mode not in ('tenant','restricted') or not isinstance(ids,list) or len(ids)>200 or any(not isinstance(v,str) for v in ids) or len(set(ids))!=len(ids):
        raise HTTPException(422,'请选择共享范围及不重复的本企业成员（最多200人）')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>3000:
        raise HTTPException(422,'项目访问范围变更须填写原因（最多3000字）')
    if mode=='tenant' and ids:
        raise HTTPException(422,'企业共享模式不设置单独成员名单')
    with store.transaction() as c:
        p=core.project(c,pid,a);before=config(p)
        if store.digest(before)!=body.get('object_hash'):
            raise HTTPException(409,'项目成员或共享范围已变化，请刷新后重新核对')
        if a['role']!='admin' and mode!=before['mode']:
            raise HTTPException(403,'仅企业管理员可以切换项目共享范围')
        active={row['id'] for row in members(c,a['tenant']) if row['active']}
        if any(uid not in active for uid in ids):
            raise HTTPException(422,'成员须为本企业当前启用的账号')
        if mode=='restricted' and a['role']!='admin' and a['id'] not in ids:
            raise HTTPException(409,'项目负责人不能从受限名单中移除自己，请由企业管理员处理')
        access={'mode':mode,'member_ids':sorted(ids),'version':before['version']+1}
        p['access']=access
        store.audit(c,p,a,'PROJECT_ACCESS_UPDATED',{'before':before,'after':access,'reason':reason.strip()})
        store.save(c,p)
        return target(c,p,a)
