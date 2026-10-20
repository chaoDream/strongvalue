import React, {useEffect, useRef, useState} from 'react';
import {getDocument, GlobalWorkerOptions, type PDFDocumentProxy} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {ChevronLeft,ChevronRight,ZoomIn,ZoomOut,RefreshCw,ExternalLink} from 'lucide-react';

GlobalWorkerOptions.workerSrc=workerUrl;

export default function PdfPreview({url,title='PDF文档',initialPage=1}:{url:string,title?:string,initialPage?:number}){
 const [doc,setDoc]=useState<PDFDocumentProxy|null>(null),[page,setPage]=useState(initialPage),[zoom,setZoom]=useState(1),[width,setWidth]=useState(400),[error,setError]=useState(''),[loading,setLoading]=useState(true),[rendering,setRendering]=useState(false);
 const container=useRef<HTMLDivElement>(null),surface=useRef<HTMLDivElement>(null);
 useEffect(()=>{const el=container.current;if(!el)return;const resize=new ResizeObserver(entries=>setWidth(Math.max(200,entries[0].contentRect.width-28)));resize.observe(el);return()=>resize.disconnect()},[]);
 useEffect(()=>{
   let active=true;setLoading(true);setDoc(null);setError('');setPage(initialPage);setZoom(1);
   const task=getDocument({url,withCredentials:true,cMapUrl:'/pdf-assets/cmaps/',cMapPacked:true,standardFontDataUrl:'/pdf-assets/standard_fonts/',wasmUrl:'/pdf-assets/wasm/',isEvalSupported:false});
   task.promise.then(value=>{if(active){setDoc(value);setPage(Math.min(Math.max(1,initialPage),value.numPages));setLoading(false)}}).catch(()=>{if(active){setError('无法读取PDF；请核实文件、登录状态或下载原件审阅。');setLoading(false)}});
   return()=>{active=false;task.destroy().catch(()=>{})};
 },[url]);
 useEffect(()=>{if(doc)setPage(Math.max(1,Math.min(initialPage,doc.numPages)))},[initialPage,doc]);
 useEffect(()=>{
   if(!doc||!surface.current)return;
   let active=true;let renderTask:ReturnType<Awaited<ReturnType<PDFDocumentProxy['getPage']>>['render']>|null=null;
   const host=surface.current,canvas=document.createElement('canvas');
   canvas.setAttribute('role','img');canvas.setAttribute('aria-label',title+' 第'+page+'页');host.replaceChildren(canvas);setRendering(true);setError('');
   doc.getPage(page).then(pdfPage=>{
     if(!active)return;
     const base=pdfPage.getViewport({scale:1}),ratio=Math.min(window.devicePixelRatio||1,2),viewport=pdfPage.getViewport({scale:width/base.width*zoom*ratio});
     canvas.width=Math.floor(viewport.width);canvas.height=Math.floor(viewport.height);canvas.style.width=viewport.width/ratio+'px';canvas.style.height=viewport.height/ratio+'px';
     renderTask=pdfPage.render({canvas,viewport});
     return renderTask.promise;
   }).then(()=>{if(active)setRendering(false)}).catch((e:any)=>{if(active&&e?.name!=='RenderingCancelledException'){setRendering(false);setError('本页渲染失败，可尝试缩放或下载原件。')}});
   return()=>{active=false;renderTask?.cancel();canvas.remove()};
 },[doc,page,zoom,width,title]);
 return <div className="pdf-app" ref={container} aria-label={title}>
   <div className="pdf-controls"><button className="icon-btn" aria-label="PDF上一页" disabled={!doc||page<=1} onClick={()=>setPage(v=>v-1)}><ChevronLeft size={17}/></button><label className="pdf-page-label"><input aria-label="PDF页码" type="number" min="1" max={doc?.numPages||1} value={page} disabled={!doc} onChange={e=>setPage(Math.max(1,Math.min(doc?.numPages||1,Number(e.target.value)||1)))}/> / {doc?.numPages||'—'}</label><button className="icon-btn" aria-label="PDF下一页" disabled={!doc||page>=doc.numPages} onClick={()=>setPage(v=>v+1)}><ChevronRight size={17}/></button><span className="pdf-controls-spacer"/><button className="icon-btn" aria-label="缩小PDF" disabled={zoom<=.5} onClick={()=>setZoom(v=>Math.max(.5,v-.25))}><ZoomOut size={16}/></button><span className="pdf-zoom">{Math.round(zoom*100)}%</span><button className="icon-btn" aria-label="放大PDF" disabled={zoom>=3} onClick={()=>setZoom(v=>Math.min(3,v+.25))}><ZoomIn size={16}/></button><a className="icon-btn" aria-label="打开PDF原文件" href={url} target="_blank" rel="noreferrer"><ExternalLink size={15}/></a></div>
   {error&&<div role="alert" className="error-inline">{error}</div>}
   <div className="pdf-canvas-scroll">{loading&&<div className="pdf-loading"><RefreshCw size={18} className="spin"/>正在读取文档…</div>}<div className="pdf-canvas-surface" ref={surface}/>{rendering&&!loading&&<div role="status" className="pdf-rendering">正在绘制第{page}页…</div>}</div>
 </div>;
}
