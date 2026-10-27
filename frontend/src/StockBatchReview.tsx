import React,{useEffect,useRef,useState} from 'react';
import {StockOperationDetails} from './StockOperations';
import {OffcutIssueDetails} from './StockOffcutIssues';
type Obj=Record<string,any>;
const phases:Obj={approved:'已审继续预留',draft:'待复核继续预留',needs_info:'待补件继续预留',rejected:'已拒仍占用',reserved:'继续预留',partially_issued:'已部分领出',issued:'已全部领出',remaining_released:'未领余量已释放',released:'已释放'};
const names:Obj={stock_operations:'原材领料单',stock_offcut_issues:'余料分批领用单'};
export default function StockBatchReview({p,user,view,request,onChanged,sourceLink}:any){
 const [opened,setOpened]=useState(false),[collection,setCollection]=useState('stock_operations'),[snapshot,setSnapshot]=useState<Obj>(view),[selected,setSelected]=useState<string[]>([]),[reason,setReason]=useState(''),[decision,setDecision]=useState('approve'),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState('');
 const epoch=useRef(0);
 useEffect(()=>{epoch.current++;setOpened(false);setBusy(false);setSelected([]);setError('');setResult('');return()=>{epoch.current++}},[p.id]);
 if(user.role!=='reviewer')return null;
 const rows=(collection==='stock_operations'?snapshot.operations.filter((x:Obj)=>['issue','partial_issue'].includes(x.record.kind)):snapshot.offcut_issues?.records||[]).filter((x:Obj)=>['draft','needs_info'].includes(x.phase));
 const targets=rows.slice(0,200);
 function open(){setSnapshot(view);setSelected([]);setReason('');setDecision('approve');setError('');setResult('');setOpened(true)}
 function close(){if(!busy){epoch.current++;setOpened(false)}}
 async function reload(){const token=epoch.current;setBusy(true);setError('');try{const v=await request('/projects/'+p.id+'/stock-inventory');if(token===epoch.current){setSnapshot(v);setSelected([])}}catch(e:any){if(token===epoch.current)setError(e.message)}finally{if(token===epoch.current)setBusy(false)}}
 async function submit(e:React.FormEvent){e.preventDefault();const token=epoch.current;setBusy(true);setError('');try{
  const v=await request('/projects/'+p.id+'/reviews/batch','POST',{collection,decision,reason,targets:targets.filter((x:Obj)=>selected.includes(x.record.id)).map((x:Obj)=>({object_id:x.record.id,object_hash:x.object_hash}))});
  if(token!==epoch.current)return;setOpened(false);setResult('已完成'+v.reviews.length+'单独立复核；批次编号 '+v.batch_id+'。库存未出库，请逐单核查并执行。');await onChanged();
 }catch(e:any){if(token===epoch.current)setError(e.message)}finally{if(token===epoch.current)setBusy(false)}}
 return <section className="space-top" aria-label="库存领料批量独立复核"><div className="tool-strip"><button className="btn" disabled={busy} onClick={open}>批量独立复核领料单</button><span className="tool-hint">同类最多200单，逐单留痕；任一失败整批不提交，审定不出库。</span></div>{result&&<p role="status">{result}</p>}{error&&!opened&&<p role="alert" className="error-inline">{error}</p>}
 {opened&&<div className="overlay" onMouseDown={close}><section className="modal wide" role="dialog" aria-modal="true" aria-label="批量独立复核领料单" onMouseDown={e=>e.stopPropagation()}><header><h2>批量独立复核领料单</h2><button className="icon-btn" disabled={busy} aria-label="关闭批量领料复核" onClick={close}>×</button></header><form onSubmit={submit}><div className="modal-body">
 <label className="field"><span>领料单类型</span><select aria-label="领料单类型" disabled={busy} value={collection} onChange={e=>{setCollection(e.target.value);setSelected([]);setError('')}}>{Object.entries(names).map(([k,v]:any)=><option key={k} value={k}>{v}</option>)}</select></label>
 <p className="form-note">待处理{rows.length}单，显示前200单；仅列待复核或待补件且未执行、未取消的单据。请逐单展开原件、接收人、日期、原分配行及当前未领余额后选择。审批不增加预留；多份草稿可引用同一余额，实际执行仍重新核查，不能把审批当作可重复领料额度。</p>
 <button type="button" className="text-btn" disabled={busy} onClick={reload}>重新读取待审单据（清空选择）</button>
 <div className="batch-targets">{targets.map((x:Obj)=>{const item=x.record,source=(collection==='stock_operations'?snapshot.reservations:snapshot.offcut_reuse?.reservations||[]).find((v:Obj)=>v.record.id===(item.reservation_id||item.input.reference_id));return <article className="batch-target" key={item.id}>
 <label className="check-option"><input type="checkbox" disabled={busy} checked={selected.includes(item.id)} onChange={()=>setSelected(s=>s.includes(item.id)?s.filter(v=>v!==item.id):[...s,item.id])}/>{item.source_name||item.demand?.source_name} · {item.input.custodian} · {item.input.business_date}<small>{item.id}</small></label>
 <details><summary>展开本单、未领余额和原件</summary>{collection==='stock_operations'?<StockOperationDetails item={item} sourceLink={sourceLink}/>:<OffcutIssueDetails item={item} sourceLink={sourceLink}/>}<p>原预留单：{item.reservation_id||item.input.reference_id}；读取时状态：{phases[source?.phase]||source?.phase||'来源不存在'}；以下余额仅为读取时快照。</p>{source?.progress?.map((a:Obj)=><p key={a.allocation_index}>原分配{a.allocation_index}：原预留{a.quantity}根／累计已领{a.issued}根／未领{a.remaining}根</p>)}{x.issue&&<p role="alert" className="error-inline">当前核查：{x.issue}。不能据此通过，须处理来源或取消重建。</p>}</details>
 </article>})}{!targets.length&&<p>当前类型没有待处理领料单。</p>}</div>
 <label className="field"><span>统一复核决定</span><select aria-label="统一复核决定" disabled={busy} value={decision} onChange={e=>setDecision(e.target.value)}><option value="approve">通过审定</option><option value="reject">拒绝本批单据</option><option value="request_more_info">要求补充资料</option></select></label>
 <label className="field"><span>逐单核查依据与共同结论</span><textarea required maxLength={3000} aria-label="逐单核查依据与共同结论" disabled={busy} value={reason} onChange={e=>setReason(e.target.value)}/></label><p className="form-note">不同结论或不同核查依据请分别提交。任一对象、原件或通过条件变化，整批不提交；失败后重新读取并逐项核对。已审、已拒、已执行及已取消单的历史不在此重审。</p>{error&&<p role="alert" className="error-inline">{error}</p>}
 </div><footer><span>已选{selected.length}单</span><button type="button" className="btn" disabled={busy} onClick={close}>取消</button><button className="btn primary" disabled={busy||!selected.length||!reason.trim()}>提交所选领料单独立复核</button></footer></form></section></div>}</section>
}
