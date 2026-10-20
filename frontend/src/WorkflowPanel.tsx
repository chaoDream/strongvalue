import React, {useEffect, useState} from 'react';
import {ShieldCheck, Search, X, CircleDollarSign, ClipboardCheck} from 'lucide-react';

type Obj = Record<string, any>;
type Props = {
  section: string; p: Obj; user: Obj;
  request: (path: string, method?: string, body?: any) => Promise<any>;
  refresh: () => Promise<any>;
  onReview: (collection: string, item: Obj) => void;
  sourceLink: (id: string) => React.ReactNode;
  reviewDetails: (collection: string, item: Obj) => React.ReactNode;
};
const names: Obj = {facts:'工程事实', components:'构件尺寸', takeoffs:'工程量', boq:'清单', rules:'计价规则',
  coverage:'资料完整性', dimension_candidates:'尺寸候选', reconciliations:'专业底稿', catalog:'定额候选',
  changes:'变更签证', price_changes:'资源调价', quotes:'报价成果'};
const states: Obj = {draft:'待复核', approved:'已审定', stale:'已失效', rejected:'已拒绝', needs_info:'待补件', conflict:'存在冲突'};
const today = () => new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai'}).format(new Date());
const cash = (value: string) => Number(value).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2});
function Field({label, children}: any) {return <label className="field"><span>{label}</span>{children}</label>}

export default function WorkflowPanel({section,p,user,request,refresh,onReview,sourceLink,reviewDetails}: Props) {
  const base = '/projects/'+p.id;
  const [diagnostics,setDiagnostics] = useState<Obj|null>(null);
  const [rows,setRows] = useState<Obj[]>([]);
  const [dialog,setDialog] = useState<'batch'|'prices'|null>(null);
  const [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const [collection,setCollection] = useState('boq'), [targets,setTargets] = useState<Obj[]>([]), [total,setTotal] = useState(0);
  const [selected,setSelected] = useState<string[]>([]), [reason,setReason] = useState(''), [decision,setDecision] = useState('approve');
  const [prices,setPrices] = useState<Obj>({}), [query,setQuery] = useState(''), [uniform,setUniform] = useState('');
  const [metadata,setMetadata] = useState<Obj>({});
  const enabled = ['takeoff','boq','pricing','summary','reports','settings'].includes(section);
  useEffect(() => {
    let alive = true;
    if (section==='boq') request(base+'/boq/diagnostics').then(x=>{if(alive)setDiagnostics(x)}).catch(e=>{if(alive)setError(e.message)});
    if (section==='pricing') request(base+'/resource-prices').then(x=>{if(alive)setRows(x)}).catch(e=>{if(alive)setError(e.message)});
    return () => {alive=false};
  },[p.id,p.updated_at,section]);
  useEffect(() => {
    let alive=true;
    if(dialog==='batch') {
      setBusy(true);setTargets([]);setSelected([]);setError('');
      request(base+'/review-batch-targets?collection='+collection).then(x=>{if(alive){setTargets(x.targets);setTotal(x.total)}})
        .catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setBusy(false)});
    }
    return ()=>{alive=false};
  },[dialog,collection,p.id]);
  if(!enabled) return null;
  const activeEvidence = p.evidence.filter((e:Obj)=>p.documents.some((d:Obj)=>d.id===e.document_id&&!d.superseded));
  const toggle = (key: string) => setSelected(current=>current.includes(key)?current.filter(x=>x!==key):[...current,key]);
  function openBatch() {
    setReason('');setDecision('approve');setError('');
    setCollection(section==='takeoff'?'takeoffs':section==='pricing'?'price_changes':section==='reports'?'quotes':'boq');setDialog('batch');
  }
  function openPrices() {
    setSelected([]);setPrices(Object.fromEntries(rows.map(row=>[row.key,row.resource.price])));setQuery('');setUniform('');setReason('');setError('');
    setMetadata({source:'',license:'',period:'',region:p.region,tax_mode:'不含税',valid_from:p.pricing_date||today(),valid_until:'',evidence_id:'',source_type:'供应商报价'});
    setDialog('prices');
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();setBusy(true);setError('');
    try {
      if(dialog==='batch') {
        await request(base+'/reviews/batch','POST',{collection,decision,reason,
          targets:targets.filter(x=>selected.includes(x.object.id)).map(x=>({object_id:x.object.id,object_hash:x.object_hash}))});
      } else {
        await request(base+'/price-changes','POST',{name:'资源价格变更',reason,metadata,
          entries:rows.filter(x=>selected.includes(x.key)).map(x=>({key:x.key,boq_hash:x.boq_hash,resource_hash:x.resource_hash,price:prices[x.key]}))});
      }
      setDialog(null);await refresh();
    } catch(e:any) {setError(e.message)} finally {setBusy(false)}
  }
  async function apply(id: string) {
    setBusy(true);setError('');
    try {await request(base+'/price-changes/'+id+'/apply','POST');await refresh()}
    catch(e:any) {setError(e.message)} finally {setBusy(false)}
  }
  const filtered = rows.filter(row=>(row.resource.name+' '+row.boq_name+' '+row.quota_code+' '+row.resource.type).includes(query));
  return <>
    {error&&!dialog&&<p className="error-inline" role="alert">{error}</p>}
    <div className="tool-strip">
      <button className="btn" disabled={busy||user.role!=='reviewer'} onClick={openBatch}><ShieldCheck size={15}/>批量专业复核</button>
      {section==='pricing'&&<button className="btn" disabled={busy||user.role==='reviewer'||!rows.length} onClick={openPrices}><CircleDollarSign size={15}/>建立资源调价单</button>}
      <span className="tool-hint">批量操作保留逐项审核记录；任一对象变化则整批不提交</span>
    </div>
    {section==='boq'&&diagnostics&&<section className="card space-bottom allocation-panel">
      <div className="section-head"><div><h2><ClipboardCheck size={17}/> 清单数量分配检查</h2><p>{diagnostics.notice}</p></div><span className={'badge '+(diagnostics.passed?'approved':'draft')}>{diagnostics.passed?'分配闭合':diagnostics.issues.length+' 项待处理'}</span></div>
      {diagnostics.issues.length>0&&<ul className="allocation-issues">{diagnostics.issues.map((x:Obj,i:number)=><li key={i}>{x.message}</li>)}</ul>}
      {diagnostics.quantities.length>0&&<div className="table-wrap"><table><thead><tr><th>算量书</th><th>已登记工程量</th><th>已分配</th><th>未分配 / 超量</th></tr></thead><tbody>{diagnostics.quantities.map((x:Obj)=><tr key={x.takeoff_id}><td>{x.name}</td><td>{x.quantity} {x.unit}</td><td>{x.assigned}</td><td>{x.remaining}</td></tr>)}</tbody></table></div>}
    </section>}
    {section==='pricing'&&(p.price_changes||[]).length>0&&<section className="card space-bottom"><div className="section-head"><div><h2>资源调价与影响记录</h2><p>下列金额只包含受影响清单，不含费用和税金。先复核，再应用并重新审定清单。</p></div></div>
      {(p.price_changes||[]).map((change:Obj)=><article className="price-change-record" key={change.id}>
        <div className="section-head"><b>{change.name}</b><span>{states[change.state]}{p.applied_price_changes?.includes(change.id)?' · 已应用':''}</span></div>
        <p>{change.reason}</p><div className="metrics-strip"><span>{change.impact.resource_count} 条资源 / {change.impact.boq_count} 项清单</span><span>原金额 ¥{cash(change.impact.before_subtotal)}</span><span>新金额 ¥{cash(change.impact.after_subtotal)}</span><b>差额 ¥{cash(change.impact.delta)}</b></div>
        <details><summary>逐项价格、期次与来源</summary><div className="table-wrap"><table><thead><tr><th>清单 / 资源</th><th>原单价</th><th>新单价</th><th>来源与许可</th><th>适用条件</th><th>证据</th></tr></thead><tbody>{change.entries.map((e:Obj)=><tr key={e.key}><td>{e.boq_name}<small className="block">{e.after.name} · {e.after.type}</small></td><td>{e.before.price} 元/{e.before.unit}</td><td>{e.after.price} 元/{e.after.unit}</td><td>{e.after.source_type} · {e.after.source}<small className="block">{e.after.license}</small></td><td>{e.after.region} / {e.after.period} / {e.after.tax_mode}<small className="block">{e.after.valid_from} 至 {e.after.valid_until}</small></td><td>{sourceLink(e.after.evidence_id)}</td></tr>)}</tbody></table></div></details>
        <div className="tool-strip"><button className="text-btn" onClick={()=>onReview('price_changes',change)}>专业复核与依据</button>{!p.applied_price_changes?.includes(change.id)&&<button className="btn" disabled={busy||user.role==='reviewer'||change.state!=='approved'} onClick={()=>apply(change.id)}>应用已审调价</button>}</div>
      </article>)}
    </section>}
    {dialog&&<div className="overlay" onMouseDown={()=>!busy&&setDialog(null)}><section className="modal wide workflow-modal" role="dialog" aria-modal="true" aria-label={dialog==='batch'?'批量专业复核':'建立资源调价单'} onMouseDown={e=>e.stopPropagation()}>
      <header><h2>{dialog==='batch'?'批量专业复核':'建立资源调价单'}</h2><button className="icon-btn" disabled={busy} onClick={()=>setDialog(null)} aria-label="关闭操作窗口"><X size={18}/></button></header>
      <form onSubmit={submit}><div className="modal-body">
        {dialog==='batch'?<>
          <Field label="审核对象类型"><select value={collection} disabled={busy} onChange={e=>setCollection(e.target.value)}>{Object.entries(names).map(([key,name]:any)=><option key={key} value={key}>{name}</option>)}</select></Field>
          <p className="form-note">待处理 {total} 项，本页最多展示200项。请逐项展开核查后勾选。任一项校验失败，本批次全部回滚。</p>
          <div className="batch-targets">{targets.map(target=><article className="batch-target" key={target.object.id}>
            <label className="check-option"><input type="checkbox" disabled={busy} checked={selected.includes(target.object.id)} onChange={()=>toggle(target.object.id)}/>{target.object.name||target.object.label||target.object.specialty||'成果 V'+target.object.version}<span className="badge draft">{states[target.object.state]||target.object.state}</span></label>
            <details><summary>展开本项内容和原始依据</summary>{reviewDetails(collection,target.object)}</details>
          </article>)}{!targets.length&&!busy&&<p>当前类型没有待处理对象。</p>}</div>
          <Field label="统一审核决定"><select value={decision} disabled={busy} onChange={e=>setDecision(e.target.value)}><option value="approve">通过审定</option><option value="reject">拒绝并使下游失效</option><option value="request_more_info">要求补充资料</option></select></Field>
        </>:<>
          <div className="notice neutral"><CircleDollarSign size={18}/><p>明确选择每条资源并填写新单价。提交后先形成影响试算和待审调价单，不直接修改价格。历史审核价不能直接成为当前资源价。</p></div>
          <Field label="筛选资源、清单或定额"><div className="search-box"><Search size={16}/><input value={query} onChange={e=>setQuery(e.target.value)} placeholder="输入名称或编码"/></div></Field>
          <div className="tool-strip"><label className="check-option"><input type="checkbox" checked={filtered.length>0&&filtered.every(x=>selected.includes(x.key))} onChange={e=>setSelected(e.target.checked?[...new Set([...selected,...filtered.map(x=>x.key)])].slice(0,500):selected.filter(key=>!filtered.some(x=>x.key===key)))}/>选择筛选结果（最多500条）</label><span>已选 {selected.length} 条</span></div>
          <div className="bounded-table resource-price-selection"><table><thead><tr><th>选择</th><th>资源 / 单位</th><th>清单 / 定额</th><th>原单价</th><th>新单价（元）</th></tr></thead><tbody>{filtered.map(row=><tr key={row.key}><td><input type="checkbox" aria-label={'选择'+row.boq_name+'中的'+row.resource.name} checked={selected.includes(row.key)} disabled={!selected.includes(row.key)&&selected.length>=500} onChange={()=>toggle(row.key)}/></td><td>{row.resource.name}<small className="block">{row.resource.type} · {row.resource.unit}</small></td><td>{row.boq_name}<small className="block">{row.quota_code}</small></td><td>{row.resource.price}</td><td><input aria-label={row.resource.name+'的新单价'} required={selected.includes(row.key)} disabled={!selected.includes(row.key)} type="number" step="any" min="0" value={prices[row.key]??''} onChange={e=>setPrices({...prices,[row.key]:e.target.value})}/></td></tr>)}</tbody></table></div>
          <div className="tool-strip"><input aria-label="统一新单价" type="number" min="0" step="any" value={uniform} onChange={e=>setUniform(e.target.value)} placeholder="对已选资源填写统一单价"/><button type="button" className="btn" disabled={!selected.length||uniform===''} onClick={()=>setPrices({...prices,...Object.fromEntries(selected.map(key=>[key,uniform]))})}>填入已选资源</button></div>
          <div className="form-grid"><Field label="价格来源类型"><select value={metadata.source_type} onChange={e=>setMetadata({...metadata,source_type:e.target.value})}>{['官方信息价','供应商报价','询价','人工录入'].map(x=><option key={x}>{x}</option>)}</select></Field><Field label="税制口径"><select value={metadata.tax_mode} onChange={e=>setMetadata({...metadata,tax_mode:e.target.value})}><option>不含税</option><option>含税</option></select></Field>
            {[['source','来源名称'],['license','授权或使用许可'],['period','价格期次'],['region','适用地区'],['valid_from','生效日期'],['valid_until','有效截至']].map(([key,label])=><Field label={label} key={key}><input required type={key.startsWith('valid_')?'date':'text'} value={metadata[key]} onChange={e=>setMetadata({...metadata,[key]:e.target.value})}/></Field>)}</div>
          <Field label="价格原始依据"><select required value={metadata.evidence_id} onChange={e=>setMetadata({...metadata,evidence_id:e.target.value})}><option value="">选择当前原始证据</option>{activeEvidence.map((e:Obj)=><option key={e.id} value={e.id}>{p.documents.find((d:Obj)=>d.id===e.document_id)?.name} · {e.text.slice(0,45)}</option>)}</select></Field>
        </>}
        <Field label={dialog==='batch'?'逐项核查依据与共同结论':'调价原因、合同约定与适用依据'}><textarea required maxLength={3000} rows={4} value={reason} onChange={e=>setReason(e.target.value)}/></Field>
        {error&&<p className="error-inline" role="alert">{error}</p>}
      </div><footer><button type="button" className="btn" disabled={busy} onClick={()=>setDialog(null)}>取消</button><button type="submit" className="btn primary" disabled={busy||!selected.length}>{busy?'处理中…':dialog==='batch'?'提交 '+selected.length+' 项审核':'生成调价单与影响试算'}</button></footer></form>
    </section></div>}
  </>;
}
