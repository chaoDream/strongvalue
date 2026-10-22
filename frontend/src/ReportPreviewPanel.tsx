import React,{useEffect,useRef,useState} from 'react';
import {FileCheck2,Download,ExternalLink} from 'lucide-react';
import PdfPreview from './DocumentPreview';
type Obj=Record<string,any>;
type Props={p:Obj;quoteId:string;templateId:string;artifacts:Obj[];request:(path:string,method?:string,body?:any)=>Promise<any>;onGenerated:()=>Promise<any>};
const status:Obj={approved:'当前报价已完成范围复核；本文件仍保留生成时的审核标识。',unapproved:'当前报价尚未获得有效成果复核，不能以历史文件代替当前审核。',stale:'该报价来源或审批已失效，历史文件仅供追溯。',unpriced:'这是无价草稿的固定留档，请按文件内范围和日期核对。',history:'这是独立业务的历史文件，审核范围以文件内说明为准。',unavailable:'当前报价记录不可用，请按历史文件核对。',unverified:'当前输入无法通过校验，历史文件仅供追溯。'};
function artifactLabel(a:Obj){return (a.purpose==='engineering_settlement'?'结算差异 V'+a.settlement_version:a.purpose==='integration_trial'?'往返试验 V'+a.trial_version:a.purpose==='quote_comparison'?'报价版本对账':a.quote_version?'报价 V'+a.quote_version:'无价草稿')+' · '+(a.approved?'生成时已复核':'生成时草稿')+' · '+new Date(a.timestamp).toLocaleString('zh-CN')}

export default function ReportPreviewPanel({p,quoteId,templateId,artifacts,request,onGenerated}:Props){
 const [view,setView]=useState<Obj|null>(null),[historyId,setHistoryId]=useState(''),[page,setPage]=useState(1),[busy,setBusy]=useState(false),[error,setError]=useState(''),[origin,setOrigin]=useState('');
 const epoch=useRef(0),base='/projects/'+p.id;
 useEffect(()=>()=>{epoch.current++},[]);
 useEffect(()=>{epoch.current++;setView(null);setHistoryId('');setPage(1);setBusy(false);setError('')},[p.updated_at]);
 async function load(id?:string){
  const token=++epoch.current;setBusy(true);setError('');setView(null);setPage(1);
  try{
   const result=id?await request(base+'/artifacts/'+id+'/preview'):await request(base+'/report-previews','POST',{quote_id:quoteId==='unpriced'?'':quoteId,unpriced:quoteId==='unpriced',template_id:templateId});
   if(token!==epoch.current)return;
   setView(result);setOrigin(id?'历史留档':'本次生成');setHistoryId(result.artifact.id);
   if(!id)await onGenerated();
  }catch(e:any){if(token===epoch.current)setError(e.message)}finally{if(token===epoch.current)setBusy(false)}
 }
 const artifact=view?.artifact,sectionIndex=view?.sections.findIndex((s:Obj)=>page>=s.page&&page<=s.end_page)??-1;
 const files=artifacts.filter(a=>a.format==='pdf');
 return <section className="card space-bottom report-reader" aria-label="PDF分页审阅">
  <div className="section-head"><div><h2><FileCheck2 size={18}/> PDF分页审阅</h2><p>按选定报价和企业模板生成实际PDF，预览与下载使用同一份留档文件。</p></div><button className="btn" disabled={busy} onClick={()=>load()}>{busy?'正在读取…':'生成并预览实际 PDF'}</button></div>
  <div className="tool-strip"><label className="field"><span>历史PDF文件</span><select aria-label="历史PDF文件" value={historyId} disabled={busy||!files.length} onChange={e=>{setHistoryId(e.target.value);if(e.target.value)load(e.target.value);else {epoch.current++;setView(null);setError('')}}}><option value="">选择留档PDF</option>{files.map(a=><option key={a.id} value={a.id}>{artifactLabel(a)}</option>)}</select></label><small>查看历史文件不会重新生成、签署审核或改变文件内容。</small></div>
  {!view&&!busy&&!error&&<p className="form-note">生成本次PDF，或选择历史文件后按章节、页码审阅。</p>}
  {error&&<p className="error-inline" role="alert">{error}</p>}
  {view&&artifact&&<>
   <div className="report-file-info" role="status"><div><b>{origin} · {artifact.quote_version?'报价 V'+artifact.quote_version:'无价草稿或独立留档'}</b><span className={'badge '+(artifact.approved?'approved':'draft')}>文件生成时：{artifact.approved?'已复核':'草稿'}</span></div><p>{status[view.current_status]}</p><p>{artifact.report_context?.project_name||'工程名称以文件内为准'} · {artifact.template?artifact.template.name+' V'+artifact.template.version:'通用报表样式'} · {view.page_count}页 · {new Date(artifact.timestamp).toLocaleString('zh-CN')}</p><p className="mono">文件 SHA256：{artifact.hash}</p></div>
   <div className="report-reader-nav"><label className="field"><span>报表章节</span><select aria-label="报表章节" disabled={!view.sections.length} value={sectionIndex<0?'':String(sectionIndex)} onChange={e=>setPage(view.sections[Number(e.target.value)].page)}>{!view.sections.length&&<option value="">旧文件无章节索引，可按页码审阅</option>}{view.sections.map((s:Obj,i:number)=><option key={i} value={i}>{s.title} · 第{s.page}{s.end_page>s.page?'–'+s.end_page:''}页</option>)}</select></label><span>第{page} / {view.page_count}页</span><a className="text-btn" href={'/api/v1'+base+'/artifacts/'+artifact.id+'/file'}><Download size={14}/>下载同一文件</a><a className="text-btn" href={view.file_url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>新窗口审阅</a></div>
   <PdfPreview key={artifact.id} title="实际成果PDF预览" url={view.file_url} initialPage={page} onPageChange={setPage}/>
   <p className="form-note">{view.notice}</p>
  </>}
 </section>;
}
