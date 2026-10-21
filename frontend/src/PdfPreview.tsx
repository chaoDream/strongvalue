import React, {useEffect, useRef, useState} from 'react';
import {getDocument, GlobalWorkerOptions, type PDFDocumentProxy} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {ChevronLeft,ChevronRight,ZoomIn,ZoomOut,RefreshCw,ExternalLink,ScanLine} from 'lucide-react';
import {validBox,boxBetween,type Box,type Region} from './evidenceRegion';

GlobalWorkerOptions.workerSrc=workerUrl;

export default function PdfPreview({url,title='PDF文档',initialPage=1,highlight,onCapture,onPageChange}:{url:string,title?:string,initialPage?:number;highlight?:Region|null;onCapture?:(region:Region)=>void;onPageChange?:(page:number)=>void}){
 const [doc,setDoc]=useState<PDFDocumentProxy|null>(null),[page,setPage]=useState(initialPage),[zoom,setZoom]=useState(1),[width,setWidth]=useState(400),[error,setError]=useState(''),[loading,setLoading]=useState(true),[rendering,setRendering]=useState(false);
 const container=useRef<HTMLDivElement>(null),surface=useRef<HTMLDivElement>(null);
 const [size,setSize]=useState({width:0,height:0}),[selecting,setSelecting]=useState(false),[selection,setSelection]=useState<Box|null>(null);
 const origin=useRef<[number,number]|null>(null);
 useEffect(()=>{setSelection(null);origin.current=null;setSelecting(false)},[url,page]);
 useEffect(()=>{onPageChange?.(page)},[page]);
 useEffect(()=>{if(doc&&highlight)setPage(Math.max(1,Math.min(highlight.page,doc.numPages)))},[highlight?.key,doc]);
 function point(e:React.PointerEvent<HTMLDivElement>):[number,number]{const r=e.currentTarget.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))]}
 function finish(e:React.PointerEvent<HTMLDivElement>){if(!origin.current)return;const box=boxBetween(origin.current,point(e));origin.current=null;setSelection(box[2]>.002&&box[3]>.002?box:null);if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId)}
 function boxStyle(box:Box){return {left:box[0]*100+'%',top:box[1]*100+'%',width:box[2]*100+'%',height:box[3]*100+'%'}}
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
     setSize({width:viewport.width/ratio,height:viewport.height/ratio});
     renderTask=pdfPage.render({canvas,viewport});
     return renderTask.promise;
   }).then(()=>{if(active)setRendering(false)}).catch((e:any)=>{if(active&&e?.name!=='RenderingCancelledException'){setRendering(false);setError('本页渲染失败，可尝试缩放或下载原件。')}});
   return()=>{active=false;renderTask?.cancel();canvas.remove()};
 },[doc,page,zoom,width,title]);
 return <div className="pdf-app" ref={container} aria-label={title}>
   <div className="pdf-controls"><button className="icon-btn" aria-label="PDF上一页" disabled={!doc||page<=1} onClick={()=>setPage(v=>v-1)}><ChevronLeft size={17}/></button><label className="pdf-page-label"><input aria-label="PDF页码" type="number" min="1" max={doc?.numPages||1} value={page} disabled={!doc} onChange={e=>setPage(Math.max(1,Math.min(doc?.numPages||1,Number(e.target.value)||1)))}/> / {doc?.numPages||'—'}</label><button className="icon-btn" aria-label="PDF下一页" disabled={!doc||page>=doc.numPages} onClick={()=>setPage(v=>v+1)}><ChevronRight size={17}/></button><span className="pdf-controls-spacer"/><button className="icon-btn" aria-label="缩小PDF" disabled={zoom<=.5} onClick={()=>setZoom(v=>Math.max(.5,v-.25))}><ZoomOut size={16}/></button><span className="pdf-zoom">{Math.round(zoom*100)}%</span><button className="icon-btn" aria-label="放大PDF" disabled={zoom>=3} onClick={()=>setZoom(v=>Math.min(3,v+.25))}><ZoomIn size={16}/></button><a className="icon-btn" aria-label="打开PDF原文件" href={url} target="_blank" rel="noreferrer"><ExternalLink size={15}/></a></div>
   {error&&<div role="alert" className="error-inline">{error}</div>}
   {onCapture&&<div className="pdf-region-tools"><button className="btn" disabled={!doc||rendering||loading||!!error} aria-pressed={selecting} onClick={()=>{setSelecting(!selecting);setSelection(null)}}><ScanLine size={14}/>{selecting?'退出框选':'框选原图证据'}</button><button className="btn" disabled={!selection||!validBox(selection)||rendering} onClick={()=>{if(selection){onCapture({page,bbox:selection});setSelecting(false);setSelection(null)}}}>记录所选区域</button><button className="text-btn" disabled={!doc||rendering||loading||!!error} onClick={()=>onCapture({page,bbox:[.1,.1,.3,.15]})}>按百分比填写区域</button><span>区域只定位原文，不换算工程尺寸。</span></div>}
   {highlight&&page===highlight.page&&validBox(highlight.bbox)&&<div className="pdf-region-caption" role="status">已定位证据区域：{highlight.label||'原图标注'}（第{page}页）</div>}
   <div className="pdf-canvas-scroll">{loading&&<div className="pdf-loading"><RefreshCw size={18} className="spin"/>正在读取文档…</div>}<div className="pdf-page-stage" style={size.width?size:undefined}><div className="pdf-canvas-surface" ref={surface}/>{!loading&&!rendering&&!error&&<>{highlight&&page===highlight.page&&validBox(highlight.bbox)&&<div className="pdf-region-highlight" style={boxStyle(highlight.bbox)} aria-label="已定位原图证据框"/>}{selection&&<div className="pdf-region-highlight selected" style={boxStyle(selection)} aria-label="当前框选区域"/>}{selecting&&<div className="pdf-region-selector" aria-label="拖动框选原图区域" onPointerDown={e=>{if(e.button!==0)return;origin.current=point(e);setSelection(null);e.currentTarget.setPointerCapture(e.pointerId)}} onPointerMove={e=>{if(origin.current)setSelection(boxBetween(origin.current,point(e)))}} onPointerUp={finish} onPointerCancel={()=>{origin.current=null;setSelection(null)}}/>}</>}</div>{rendering&&!loading&&<div role="status" className="pdf-rendering">正在绘制第{page}页…</div>}</div>
 </div>;
}
