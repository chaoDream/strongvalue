import {OCRLineList} from './OCRLineSource';
import React,{useState,useEffect} from 'react';
type Obj=Record<string,any>;
export default function OCRPanel({p,user,request,refresh,sourceLink}:{p:Obj,user:Obj,request:any,refresh:any,sourceLink:any}){
 const [cap,setCap]=useState<Obj|null>(null),[did,setDid]=useState(''),[page,setPage]=useState('1'),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{let live=true;request('/ocr/capabilities').then((v:Obj)=>{if(live)setCap(v)}).catch((e:Error)=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 const docs=p.documents.filter((d:Obj)=>!d.superseded&&d.name.toLowerCase().endsWith('.pdf'));
 const doc=docs.find((d:Obj)=>d.id===did);
 const jobs=p.jobs.filter((j:Obj)=>j.kind==='local_ocr'&&(!did||j.document_id===did));
 const editable=['admin','manager','estimator'].includes(user.role);
 const run=async()=>{setBusy(true);setError('');try{await request('/projects/'+p.id+'/documents/'+did+'/ocr','POST',{page:Number(page),document_hash:doc.hash});await refresh(p.id)}catch(e:any){setError(e.message)}finally{setBusy(false)}};
 const retry=async(id:string)=>{setBusy(true);setError('');try{await request('/projects/'+p.id+'/jobs/'+id+'/retry','POST');await refresh(p.id)}catch(e:any){setError(e.message)}finally{setBusy(false)}};
 return <section className="card space-top" style={{padding:24}}><div className="panel-head"><h3>扫描页文字识别</h3></div><p>在本机识别选定PDF页面，不向第三方发送资料。识别文字可能有误，采用前请核对原图；本操作不自动生成工程量。</p><p className="muted">{cap?.notice||'正在检查本机识别能力…'}</p>{error&&<div role="alert" className="notice">{error}</div>}<div className="form-grid"><label className="field"><span>当前PDF</span><select value={did} onChange={e=>{setDid(e.target.value);setPage('1')}}><option value="">请选择原始PDF</option>{docs.map((d:Obj)=><option key={d.id} value={d.id}>{d.name} · V{d.version}</option>)}</select></label><label className="field"><span>页码</span><input type="number" min="1" max={doc?.pages||500} value={page} onChange={e=>setPage(e.target.value)}/></label></div><button className="btn" disabled={!editable||!doc||!cap?.available||busy||!Number.isInteger(Number(page))||Number(page)<1||Number(page)>(doc?.pages||500)} onClick={run}>识别本页文字</button>{jobs.map((j:Obj)=>{const ev=p.evidence.find((e:Obj)=>e.id===j.evidence_id);return <div key={j.id} className="space-top"><b>{p.documents.find((d:Obj)=>d.id===j.document_id)?.name} · 第{j.page}页</b><p>{j.message}</p>{j.state==='failed'&&editable&&<button className="text-btn" disabled={busy} onClick={()=>retry(j.id)}>重试本页识别</button>}{ev&&<details><summary>核对识别文字（{ev.ocr.lines.length}行）</summary>{sourceLink(ev.id)}<p className="muted">机器识别候选；置信度不代表工程准确率。请在原页核对，随后人工记录事实或图纸信息。</p><OCRLineList pid={p.id} evidence={ev} request={request}/></details>}</div>})}</section>
}
