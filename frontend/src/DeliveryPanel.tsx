import React,{useEffect,useState} from 'react';
import {PackageCheck,ShieldCheck} from 'lucide-react';
type Obj=Record<string,any>;
export default function DeliveryPanel({p,quoteId,artifacts,request}:{p:Obj;quoteId:string;artifacts:Obj[];request:(path:string,method?:string,body?:any)=>Promise<any>}){
 const [check,setCheck]=useState<Obj|null>(null),[artifactId,setArtifactId]=useState(''),[result,setResult]=useState<Obj|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{setCheck(null);setResult(null);setArtifactId('');setError('')},[p.id,quoteId,p.updated_at]);
 async function run(kind:'delivery'|'artifact'){
  setBusy(true);setError('');if(kind==='artifact')setResult(null);else setCheck(null);
  try{const value=await request('/projects/'+p.id+(kind==='delivery'?'/delivery-check'+(quoteId==='unpriced'?'?unpriced=true':quoteId?'?quote_id='+encodeURIComponent(quoteId):''):'/artifacts/'+artifactId+'/verify'));if(kind==='delivery')setCheck(value);else setResult(value)}catch(e:any){setError(e.message)}finally{setBusy(false)}
 }
 return <section className="card space-bottom delivery-panel"><div className="section-head"><div><h2><PackageCheck size={18}/> 交付证据完整性</h2><p>ZIP包含选定版本原件、快照、审核日志、PDF / Excel及离线校验工具；原件去重后上限100MB。</p></div><button className="btn" disabled={busy} onClick={()=>run('delivery')}>{busy?'校验中…':'检查选定版本证据'}</button></div>
  <p className="form-note">完整性检查通过只说明文件与记录一致，不代表专业审定。包内包含原始资料及审核意见，请按项目保密范围交付；业务未审或版本失效时仍标记草稿。</p>
  {check&&<div className={'notice '+(check.valid?'neutral':'')}><ShieldCheck size={20}/><div><b>{check.valid?'文件与审计链检查通过':'完整性检查未通过，ZIP交付已阻断'}</b><p>{check.quote_version?'报价 V'+check.quote_version:'无价草稿'} · {check.document_count} 份资料 / {check.unique_original_count} 个原件 · {(check.original_bytes/1024/1024).toFixed(2)} MB · {check.audit_event_count} 条审计事件</p><p>{check.status}{check.stale_quote?' · 所选版本来源已变更':''}</p>{check.issues.map((issue:string)=><p key={issue} role="alert">{issue}</p>)}<details><summary>业务阻断及校验边界</summary>{check.business_blockers.map((issue:string)=><p key={issue}>{issue}</p>)}<p>{check.notice}</p><p>审计链末端：<span className="mono">{check.audit_head_hash}</span></p></details></div></div>}
  {artifacts.length>0&&<><div className="tool-strip"><label className="field"><span>历史文件完整性核查</span><select value={artifactId} onChange={e=>{setArtifactId(e.target.value);setResult(null)}}><option value="">选择已留存导出件</option>{artifacts.map(a=><option key={a.id} value={a.id}>{a.purpose==='procurement_analysis'?'采购成本测算 · ':a.purpose==='stock_inventory'?'库存台账 · ':a.purpose==='engineering_settlement'?'结算差异 V'+a.settlement_version+' · ':''}{a.format.toUpperCase()} · {a.purpose==='procurement_analysis'?'独立采购测算':a.purpose==='stock_inventory'?'独立业务记录':a.quote_version?'V'+a.quote_version:'无价草稿'} · {new Date(a.timestamp).toLocaleString('zh-CN')}</option>)}</select></label><button className="btn" disabled={busy||!artifactId} onClick={()=>run('artifact')}>校验历史文件</button></div>{result&&<div role="status" className="gateway-info"><b>{result.valid?'历史文件内容与留档哈希一致':'历史文件校验失败'}</b>{result.issue&&<p>{result.issue}</p>}<p className="mono">留档 SHA256：{result.expected_hash}</p><p className="mono">实际 SHA256：{result.actual_hash||'文件不可读取'}</p><small>哈希一致不代表该历史版本仍可作为当前审核成果。</small></div>}</>}
  {error&&<p role="alert" className="error-inline">{error}</p>}
 </section>
}
