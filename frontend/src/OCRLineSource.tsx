import React,{useState,useEffect,useRef} from 'react';
import {createPortal} from 'react-dom';
import TranscriptionFacts from './TranscriptionFacts';
import DocumentPreview from './DocumentPreview';
import {regionSpace,validBox} from './evidenceRegion';
type Obj=Record<string,any>;
type Props={pid:string;evidenceId:string;evidenceHash?:string;line:Obj;number:number;request:any;canTranscribe?:boolean;onTranscribed?:()=>Promise<any>};
export default function OCRLineSource({pid,evidenceId,evidenceHash,line,number,request,canTranscribe=false,onTranscribed}:Props){
 const [open,setOpen]=useState(false),[result,setResult]=useState<Obj|null>(null),[error,setError]=useState('');
 const [text,setText]=useState(''),[reason,setReason]=useState(''),[checked,setChecked]=useState(false),[saving,setSaving]=useState(false),[saved,setSaved]=useState('');
 const sequence=useRef(0);
 // A changed project/evidence/line closes any old preview and fences late responses.
 const signature=JSON.stringify([pid,evidenceId,evidenceHash,number,line.text,line.bbox,line.confidence]);
 useEffect(()=>{sequence.current++;setOpen(false);setResult(null);setError('');setText('');setReason('');setChecked(false);setSaved('');setSaving(false);return()=>{sequence.current++}},[signature]);
 async function locate(){const id=++sequence.current;setOpen(true);setResult(null);setError('');setChecked(false);setReason('');setSaved('');try{
   const body:Obj={expected_line:{text:line.text,bbox:line.bbox,confidence:line.confidence}};
   if(evidenceHash)body.evidence_hash=evidenceHash;
   const value=await request('/projects/'+pid+'/evidence/'+evidenceId+'/ocr-lines/'+number+'/locate','POST',body);
   if(id===sequence.current){setResult(value);setText(value.text)};
 }catch(e:any){if(id===sequence.current)setError(e.message)}}
 async function transcribe(){if(!result)return;const id=sequence.current;setSaving(true);setError('');try{
  const response=await request('/projects/'+pid+'/evidence/'+evidenceId+'/ocr-lines/'+number+'/transcriptions','POST',
   {expected_line:{text:result.text,bbox:result.bbox,confidence:result.confidence},evidence_hash:result.evidence_hash,text,reason,original_page_checked:checked});
  if(id!==sequence.current)return;setSaved((response.duplicate?'已有相同记录，未重复保存':'已保存人工抄录')+'；事实及图纸登记仍须另行独立复核。');
  try{await onTranscribed?.()}catch{if(id===sequence.current)setSaved('人工抄录已保存；列表更新失败，请关闭后刷新项目。')}
 }catch(e:any){if(id===sequence.current)setError(e.message)}finally{if(id===sequence.current)setSaving(false)}}
 function close(){if(saving)return;sequence.current++;setOpen(false);setResult(null)}
 return <><button type="button" className="text-btn" onClick={locate}>定位第{number}行原图</button>{open&&createPortal(<div className="overlay" style={{zIndex:1200}} onMouseDown={e=>{e.stopPropagation();close()}}><section className="modal wide" role="dialog" aria-modal="true" aria-label="扫描识别原行定位" onMouseDown={e=>e.stopPropagation()}><header><h2>扫描识别原行定位</h2><button type="button" className="icon-btn" aria-label="关闭原行定位" onClick={close}>×</button></header><div className="modal-body">{error&&<p role="alert" className="error-inline">{error}</p>}{!result?<p role="status">{error?'请返回列表重新核对当前来源。':'正在核对原件及识别位置…'}</p>:<><p><b>{result.document_name} · 第{result.page}页 · 第{result.line}行</b></p><p>{result.text}</p><p className="form-note">{result.notice}</p>{result.coordinate_space===regionSpace&&validBox(result.bbox)?<DocumentPreview url={'/api/v1/projects/'+pid+'/documents/'+result.document_id+'/file'} title="扫描原行核对" initialPage={result.page} highlight={{page:result.page,bbox:result.bbox,label:result.text,key:result.evidence_hash+':'+result.line}}/>:<p role="alert" className="error-inline">位置约定无效，请重新读取当前识别结果。</p>}{canTranscribe&&<section className="space-top"><h3>人工核对抄录</h3><p className="form-note">对照原图录入正确文字和依据。机器原文与已有记录保留；保存后可作为新证据，不自动生成事实或工程量。</p><label className="field"><span>核对后文字</span><textarea maxLength={2000} rows={3} value={text} disabled={saving} onChange={e=>{setText(e.target.value);setChecked(false);setSaved('')}}/></label><label className="field"><span>核对或纠正依据</span><textarea maxLength={2000} rows={2} value={reason} disabled={saving} onChange={e=>{setReason(e.target.value);setChecked(false);setSaved('')}}/></label><label className="check-option"><input type="checkbox" checked={checked} disabled={saving} onChange={e=>setChecked(e.target.checked)}/>我已对照本行原图核对文字及修改依据</label><button type="button" className="btn" disabled={saving||!text.trim()||!reason.trim()||!checked||!!saved} onClick={transcribe}>{saving?'保存中…':'保存人工抄录证据'}</button>{saved&&<p role="status">{saved}</p>}</section>}</>}</div><footer><button type="button" className="btn" onClick={close}>返回核对</button></footer></section></div>,document.body)}</>
}

export function OCRLineList({pid,evidence,request,canTranscribe=false,onTranscribed,transcriptions=[]}:{pid:string;evidence:Obj;request:any;canTranscribe?:boolean;onTranscribed?:()=>Promise<any>;transcriptions?:Obj[]}){
 const [offset,setOffset]=useState(0);
 useEffect(()=>setOffset(0),[pid,evidence.id]);
 const lines=evidence.ocr?.lines||[],start=Math.min(offset,Math.max(0,Math.floor((lines.length-1)/50)*50));
 return <><div style={{maxHeight:320,overflow:'auto'}}>{lines.slice(start,start+50).map((line:Obj,i:number)=><div key={i+start}><p><b>第{start+i+1}行：</b>{line.text}</p><OCRLineSource pid={pid} evidenceId={evidence.id} line={line} number={start+i+1} request={request} canTranscribe={canTranscribe} onTranscribed={onTranscribed}/>{transcriptions.filter(e=>e.ocr_transcription.source_location.line===start+i+1).map(e=><details key={e.id}><summary>人工抄录：{e.text.slice(0,60)}</summary><p>{e.text}</p><p>{e.ocr_transcription.reason}</p><small>{e.ocr_transcription.created_by} · {e.ocr_transcription.created_at} · 证据 {e.id}</small><div><TranscriptionFacts pid={pid} evidenceId={e.id} editable={canTranscribe} request={request} onChanged={onTranscribed}/></div></details>)}</div>)}</div>{lines.length>50&&<div className="actions"><button type="button" className="text-btn" disabled={start===0} onClick={()=>setOffset(start-50)}>上一批原行</button><span>{start+1}—{Math.min(start+50,lines.length)} / {lines.length}行</span><button type="button" className="text-btn" disabled={start+50>=lines.length} onClick={()=>setOffset(start+50)}>下一批原行</button></div>}</>
}
