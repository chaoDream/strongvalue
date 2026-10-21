import React, {useEffect, useState} from 'react';
import {Palette, Plus, X} from 'lucide-react';
type Obj = Record<string,any>;
type Props = {section:string;user:Obj;request:(path:string,method?:string,body?:any)=>Promise<any>;value:string;onChange:(id:string)=>void};
const colors:Obj={'173B36':'松绿','244B78':'深蓝','6A4732':'棕褐','584274':'紫灰'};
export default function ReportTemplatesPanel({section,user,request,value,onChange}:Props){
 const [templates,setTemplates]=useState<Obj[]>([]),[dialog,setDialog]=useState<Obj|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const enabled=section==='reports'||section==='settings';
 async function load(){setTemplates(await request('/organization/report-templates'))}
 useEffect(()=>{let alive=true;if(enabled)request('/organization/report-templates').then(x=>{if(alive)setTemplates(x)}).catch(e=>{if(alive)setError(e.message)});return()=>{alive=false}},[section,user.tenant]);
 if(!enabled)return null;
 const canManage=['admin','manager'].includes(user.role);
 function open(template?:Obj){setError('');setDialog(template?{...template,previous_id:template.id}:{name:'企业内部报表',organization:'',cover_note:'',footer:'',accent:'173B36'})}
 async function save(event:React.FormEvent){event.preventDefault();setBusy(true);setError('');try{const result=await request('/organization/report-templates','POST',dialog);await load();onChange(result.id);setDialog(null)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 const selected=templates.find(x=>x.id===value);
 return <><section className="card space-bottom template-panel"><div className="section-head"><div><h2><Palette size={17}/> 企业报表样式</h2><p>企业独立保存版本，只调整封面说明、页脚和配色；保留金额、证据与审核标识。</p></div>{canManage&&<button className="btn" onClick={()=>open()}><Plus size={14}/>新建模板</button>}</div>
  <div className="template-controls"><label className="field"><span>{section==='reports'?'本次导出使用的模板':'查看模板版本'}</span><select value={value} onChange={e=>onChange(e.target.value)}><option value="">默认通用模板</option>{templates.map(t=><option value={t.id} key={t.id}>{t.name} · V{t.version} · {t.organization||'未填写单位'}</option>)}</select></label>{canManage&&selected&&<button className="btn" disabled={templates.some(t=>t.family===selected.family&&t.version>selected.version)} onClick={()=>open(selected)}>基于此版修订</button>}</div>
  {selected&&<div className="template-summary"><b>{selected.organization||'未填写编制单位'}</b><p>{selected.cover_note||'无附加说明'}</p><small>页脚：{selected.footer||'无'} · 配色：{colors[selected.accent]} · {selected.purpose}</small></div>}
  {section==='reports'&&<p className="form-note template-note">所选模板应用于下方实际PDF预览及PDF、Excel、JSON及ZIP证据包导出。左侧封面摘要仅用于导航；以实际生成文件为准。旧模板和旧导出文件保留原样。</p>}
  {error&&!dialog&&<p className="error-inline">{error}</p>}
 </section>{dialog&&<div className="overlay" onMouseDown={()=>!busy&&setDialog(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="保存企业报表模板版本" onMouseDown={e=>e.stopPropagation()}><header><h2>{dialog.previous_id?'修订报表模板':'新建报表模板'}</h2><button className="icon-btn" disabled={busy} onClick={()=>setDialog(null)} aria-label="关闭模板窗口"><X size={18}/></button></header><form onSubmit={save}><div className="modal-body">
  {[['name','模板名称',80],['organization','编制单位',120],['footer','页脚备注',40]].map(([key,label,limit])=><label className="field" key={key}><span>{label}</span><input required={key==='name'} maxLength={Number(limit)} value={dialog[key]} onChange={e=>setDialog({...dialog,[key]:e.target.value})}/></label>)}
  <label className="field"><span>封面内部说明</span><textarea maxLength={1000} rows={4} value={dialog.cover_note} onChange={e=>setDialog({...dialog,cover_note:e.target.value})}/></label>
  <label className="field"><span>表头配色</span><select value={dialog.accent} onChange={e=>setDialog({...dialog,accent:e.target.value})}>{Object.entries(colors).map(([key,label]:any)=><option value={key} key={key}>{label}</option>)}</select></label>
  <p className="form-note">保存为不可覆盖的新版本，不代表官方表式或签章认可。</p>{error&&<p className="error-inline" role="alert">{error}</p>}
 </div><footer><button type="button" className="btn" disabled={busy} onClick={()=>setDialog(null)}>取消</button><button className="btn primary" disabled={busy}>{busy?'保存中…':'保存模板版本'}</button></footer></form></section></div>}</>;
}
