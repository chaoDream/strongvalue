import React,{lazy,Suspense} from 'react';
import type {Region} from './evidenceRegion';
const PdfPreview=lazy(()=>import('./PdfPreview'));
export default function DocumentPreview(props:{url:string,title?:string,initialPage?:number;highlight?:Region|null;onCapture?:(region:Region)=>void;onPageChange?:(page:number)=>void}){
 return <Suspense fallback={<div className="pdf-loading">正在载入文档阅读器…</div>}><PdfPreview {...props}/></Suspense>
}
