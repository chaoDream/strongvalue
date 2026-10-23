import React,{useState,useEffect,useRef} from 'react';
import {createPortal} from 'react-dom';
import DocumentPreview from './DocumentPreview';
import {regionSpace,validBox} from './evidenceRegion';
type Obj=Record<string,any>;
type Props={pid:string;evidenceId:string;evidenceHash?:string;line:Obj;number:number;request:any};
export default function OCRLineSource({pid,evidenceId,evidenceHash,line,number,request}:Props){
 const [open,setOpen]=useState(false),[result,setResult]=useState<Obj|null>(null),[error,setError]=useState('');
 const sequence=useRef(0);
 // A changed project/evidence/line closes any old preview and fences late responses.
 const signature=JSON.stringify([pid,evidenceId,evidenceHash,number,line.text,line.bbox,line.confidence]);
 useEffect(()=>{sequence.current++;setOpen(false);setResult(null);setError('');return()=>{sequence.current++}},[signature]);
 async function locate(){const id=++sequence.current;setOpen(true);setResult(null);setError('');try{
   const body:Obj={expected_line:{text:line.text,bbox:line.bbox,confidence:line.confidence}};
   if(evidenceHash)body.evidence_hash=evidenceHash;
   const value=await request('/projects/'+pid+'/evidence/'+evidenceId+'/ocr-lines/'+number+'/locate','POST',body);
   if(id===sequence.current)setResult(value);
 }catch(e:any){if(id===sequence.current)setError(e.message)}}
 function close(){sequence.current++;setOpen(false);setResult(null)}
 return <><button type="button" className="text-btn" onClick={locate}>定位第{number}行原图</button>{open&&createPortal(<div className="overlay" style={{zIndex:1200}} onMouseDown={e=>{e.stopPropagation();close()}}><section className="modal wide" role="dialog" aria-modal="true" aria-label="扫描识别原行定位" onMouseDown={e=>e.stopPropagation()}><header><h2>扫描识别原行定位</h2><button type="button" className="icon-btn" aria-label="关闭原行定位" onClick={close}>×</button></header><div className="modal-body">{error?<p role="alert" className="error-inline">{error}</p>:!result?<p role="status">正在核对原件及识别位置…</p>:<><p><b>{result.document_name} · 第{result.page}页 · 第{result.line}行</b></p><p>{result.text}</p><p className="form-note">{result.notice}</p>{result.coordinate_space===regionSpace&&validBox(result.bbox)?<DocumentPreview url={'/api/v1/projects/'+pid+'/documents/'+result.document_id+'/file'} title="扫描原行核对" initialPage={result.page} highlight={{page:result.page,bbox:result.bbox,label:result.text,key:result.evidence_hash+':'+result.line}}/>:<p role="alert" className="error-inline">位置约定无效，请重新读取当前识别结果。</p>}</>}</div><footer><button type="button" className="btn" onClick={close}>返回核对</button></footer></section></div>,document.body)}</>
}

export function OCRLineList({pid,evidence,request}:{pid:string;evidence:Obj;request:any}){
 const [offset,setOffset]=useState(0);
 useEffect(()=>setOffset(0),[pid,evidence.id]);
 const lines=evidence.ocr?.lines||[],start=Math.min(offset,Math.max(0,Math.floor((lines.length-1)/50)*50));
 return <><div style={{maxHeight:320,overflow:'auto'}}>{lines.slice(start,start+50).map((line:Obj,i:number)=><div key={i+start}><p><b>第{start+i+1}行：</b>{line.text}</p><OCRLineSource pid={pid} evidenceId={evidence.id} line={line} number={start+i+1} request={request}/></div>)}</div>{lines.length>50&&<div className="actions"><button type="button" className="text-btn" disabled={start===0} onClick={()=>setOffset(start-50)}>上一批原行</button><span>{start+1}—{Math.min(start+50,lines.length)} / {lines.length}行</span><button type="button" className="text-btn" disabled={start+50>=lines.length} onClick={()=>setOffset(start+50)}>下一批原行</button></div>}</>
}
