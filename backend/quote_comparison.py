"""Snapshot-only reconciliation; explicit quantity-first attribution, not causality."""
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation, localcontext
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from . import main as core, store
from .engine import money

router = APIRouter(prefix='/api/v1')


def _validate_quote(q):
    if store.digest(q['snapshot']) != q['input_hash'] or store.digest({k:v for k,v in q.items() if k!='hash'}) != q['hash']:
        raise HTTPException(409,'报价快照完整性异常，不能生成对账结论')
    if len({b['id'] for b in q['lines']}) != len(q['lines']):
        raise HTTPException(409,'报价清单标识重复，无法唯一对账')
    numeric=[q['subtotal'],q['total']]+[b[k] for b in q['lines'] for k in ['quantity','unit_price','amount']]+[f['amount'] for f in q['fees']]
    if any(not Decimal(v).is_finite() for v in numeric):
        raise HTTPException(409,'报价快照包含非有限数字')
    subtotal = sum((Decimal(b['amount']) for b in q['lines']),Decimal(0))
    fees = sum((Decimal(f['amount']) for f in q['fees']),Decimal(0))
    if subtotal != Decimal(q['subtotal']) or subtotal+fees != Decimal(q['total']):
        raise HTTPException(409,'报价快照分项、费用与总价不闭合')


def validate_quote(q):
    try:
        _validate_quote(q)
    except (KeyError,TypeError,ValueError,InvalidOperation,AttributeError):
        raise HTTPException(409,'报价快照结构或数字无效，不能生成对账结论') from None


def resource_changes(before,after):
    def flatten(b):
        return {(qi,ri):{'quota_code':q['code'],'coefficient':q.get('coefficient','1'),**({'conversion':q['conversion']} if 'conversion' in q else {}),**r}
                for qi,q in enumerate((b or {}).get('quotas',[])) for ri,r in enumerate(q.get('resources',[]))}
    old,new = flatten(before),flatten(after)
    rows=[]
    for key in sorted(old.keys()|new.keys()):
        a,b=old.get(key),new.get(key)
        if a!=b:
            fields=sorted(k for k in (a or {}).keys()|(b or {}).keys() if (a or {}).get(k)!=(b or {}).get(k))
            rows.append({'quota_position':key[0]+1,'resource_position':key[1]+1,'before':a,'after':b,'fields':fields})
    return rows


def compare_quotes(p,left,right):
    l,r=core.find(p,'quotes',left),core.find(p,'quotes',right)
    with localcontext() as ctx:
        ctx.prec=80
        validate_quote(l);validate_quote(r)
        old,new={b['id']:b for b in l['lines']},{b['id']:b for b in r['lines']}
        changes=[]
        effects={k:Decimal(0) for k in ['quantity','unit_price','rounding','added','removed','basis_change']}
        for bid in sorted(old.keys()|new.keys()):
            a,b=old.get(bid),new.get(bid)
            keys=['name','unit','quantity','unit_price','amount','features','node','code','quotas','resources_snapshot']
            fields=[k for k in keys if (a or {}).get(k)!=(b or {}).get(k)]
            if not fields:continue
            delta=Decimal((b or {}).get('amount','0'))-Decimal((a or {}).get('amount','0'))
            attributed={k:'0.00' for k in effects}
            comparable=bool(a and b and a['unit']==b['unit'] and a.get('code')==b.get('code') and a.get('features')==b.get('features'))
            if not a:attributed['added']=money(delta)
            elif not b:attributed['removed']=money(delta)
            elif not comparable:attributed['basis_change']=money(delta)
            else:
                quantity=Decimal(money((Decimal(b['quantity'])-Decimal(a['quantity']))*Decimal(a['unit_price'])))
                price=Decimal(money(Decimal(b['quantity'])*(Decimal(b['unit_price'])-Decimal(a['unit_price']))))
                attributed.update(quantity=money(quantity),unit_price=money(price),rounding=money(delta-quantity-price))
            for k,v in attributed.items():effects[k]+=Decimal(v)
            changes.append({'id':bid,'name':(b or a)['name'],'kind':'added' if not a else 'removed' if not b else 'changed',
                            'fields':fields,'before':a,'after':b,'amount_delta':money(delta),'comparable':comparable,
                            'attribution':attributed,'resource_changes':resource_changes(a,b)})
        fees=[]
        for i in range(max(len(l['fees']),len(r['fees']))):
            a=l['fees'][i] if i<len(l['fees']) else None;b=r['fees'][i] if i<len(r['fees']) else None
            if a!=b:fees.append({'position':i+1,'before':a,'after':b,'amount_delta':money(Decimal((b or {}).get('amount','0'))-Decimal((a or {}).get('amount','0')))})
        subtotal_delta=Decimal(r['subtotal'])-Decimal(l['subtotal'])
        fee_delta=sum((Decimal(f['amount_delta']) for f in fees),Decimal(0))
        total_delta=Decimal(r['total'])-Decimal(l['total'])
        closed=sum(effects.values(),Decimal(0))==subtotal_delta and subtotal_delta+fee_delta==total_delta
        if not closed:raise HTTPException(409,'版本差异未闭合，不能生成对账结论')
        report={'schema':'qiangquote-comparison-v1','project_id':p['id'],'left_id':l['id'],'right_id':r['id'],
                'left_hash':l['hash'],'right_hash':r['hash'],'left_version':l['version'],'right_version':r['version'],
                'left_total':l['total'],'right_total':r['total'],'total_delta':money(total_delta),'subtotal_delta':money(subtotal_delta),
                'fee_delta':money(fee_delta),'attribution':{k:money(v) for k,v in effects.items()},'closed':closed,'lines':changes,
                'rules_changed':store.digest(l['snapshot']['rules'])!=store.digest(r['snapshot']['rules']),
                'pricing_date_changed':l['snapshot'].get('pricing_date')!=r['snapshot'].get('pricing_date'),
                'scope_changed':l['scope']!=r['scope'],'fees_before':l['fees'],'fees_after':r['fees'],'fee_changes':fees,
                'left_stale':left in p['stale_quotes'],'right_stale':right in p['stale_quotes'],
                'notice':'按清单ID关联；先按原单价计数量变化，再按新数量计单价变化，修约余额单列。单位、编码或项目特征变化全额列入口径变化；新增/移除单列。资源及费用按原始顺序位置展示，不以同名合并，也不推断专业因果。对账单不批准任一报价。'}
        report['hash']=store.digest(report)
        return report


@router.post('/projects/{pid}/quotes/comparison-export')
def export_comparison(pid: str, body: dict, a=Depends(core.actor)):
    with store.transaction() as c:
        p=core.project(c,pid,a)
        report=compare_quotes(p,body.get('left'),body.get('right'))
        if body.get('left_hash')!=report['left_hash'] or body.get('right_hash')!=report['right_hash'] or body.get('report_hash')!=report['hash']:
            raise HTTPException(409,'所选版本校验值不一致，请重新对比')
        payload=json.dumps(report,ensure_ascii=False,indent=2).encode();sha=hashlib.sha256(payload).hexdigest()
        directory=store.ROOT/'exports';directory.mkdir(exist_ok=True);path=directory/sha
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise HTTPException(409,'同哈希留档文件异常，请恢复后重试')
        if not path.exists():path.write_bytes(payload)
        artifact={'id':store.uid(),'format':'json','purpose':'quote_comparison','quote_id':report['right_id'],'quote_version':report['right_version'],
                  'hash':sha,'approved':False,'timestamp':store.now(),'actor':a['username'],'media':'application/json','suffix':'json',
                  'comparison':{k:report[k] for k in ['left_id','right_id','left_version','right_version','left_hash','right_hash','hash']}}
        c.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(artifact['id'],pid,a['tenant'],store.canonical(artifact)))
        store.audit(c,p,a,'QUOTE_COMPARISON_EXPORTED',artifact)
    return StreamingResponse(io.BytesIO(payload),media_type='application/json',headers={
        'Content-Disposition':'attachment; filename="QiangQuote-comparison-V'+str(report['left_version'])+'-V'+str(report['right_version'])+'.json"','X-Artifact-Hash':sha})
