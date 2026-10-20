import React,{lazy,Suspense} from 'react';
const PdfPreview=lazy(()=>import('./PdfPreview'));
export default function DocumentPreview(props:{url:string,title?:string,initialPage?:number}){
 return <Suspense fallback={<div className="pdf-loading">正在载入文档阅读器…</div>}><PdfPreview {...props}/></Suspense>
}
