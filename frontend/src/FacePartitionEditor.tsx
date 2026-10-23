import React,{useState} from 'react';
import {Plus,Trash2} from 'lucide-react';
type Obj=Record<string,any>;
type Props={localEdge?:boolean;faces:Obj[];groups:Obj[];onChange:(groups:Obj[])=>void;onApply:()=>void;busy:boolean;dirty:boolean;evidence:(value:string,change:(v:string)=>void,label:string)=>React.ReactNode};
export default function FacePartitionEditor({faces,groups,onChange,onApply,busy,dirty,evidence,localEdge=false}:Props){
  const [selected,setSelected]=useState('');
  const face=faces.find(f=>f.face_id===selected)||faces[0];
  if(!face)return null;
  const edge=localEdge&&face.geometry_type==='polygon_side',cap=localEdge&&face.geometry_type==='polygon_cap';
  const group=groups.find(g=>g.face_id===face.face_id)||{face_id:face.face_id,u_cuts:[],v_cuts:[]};
  function update(axis:string,cuts:Obj[]){const next={...group,[axis+'_cuts']:cuts},others=groups.filter(g=>g.face_id!==face.face_id);
    if(!next.line_cuts?.length)delete next.line_cuts;
    onChange(next.u_cuts.length||next.v_cuts.length||next.line_cuts?.length?[...others,next]:others);}
  function lineField(i:number,key:string,value:any){update('line',(group.line_cuts||[]).map((c:Obj,j:number)=>j===i?{...c,[key]:value}:c));}
  function separate(enabled:boolean){const next={...group};if(enabled)next.separate_regions=true;else {delete next.separate_regions;delete next.point_contact;}onChange([...groups.filter(g=>g.face_id!==face.face_id),next]);}
  function contact(setting:Obj|null){const next={...group};if(setting)next.point_contact=setting;else delete next.point_contact;onChange([...groups.filter(g=>g.face_id!==face.face_id),next]);}
  return <details className="compound-part"><summary>局部分区设置（{groups.length}个原始面）</summary>
    <p className="form-note">{edge?'本侧面用局部S/Z：S从原边起点沿P起→P终方向量取，Z从局部底面0起算，不能填共同XY或绝对标高。':cap?'本上下净面用共同设计X/Y坐标。窗口只核定其中扣孔后的净区，凹形可能分成多块，空白或孔内零净量窗口不生成面；不能把矩形窗口面积当作工程量。':'分割线填写面内对应轴的共同设计坐标值，不能填像素或距面边的相对距离。'}每轴最多8条，坐标严格位于原始面内；应用后新子面须重新核定，未变分面保留决定。</p>
    <label className="field"><span>选择要分区的原始面</span><select aria-label="选择要分区的原始面" value={face.face_id} onChange={e=>setSelected(e.target.value)}>{faces.map(f=><option key={f.face_id} value={f.face_id}>原F{f.index} · {localEdge?f.label:`${f.normal}=${f.plane}m`} · {f.u_axis} {f.u_from}至{f.u_to}m / {f.v_axis} {f.v_from}至{f.v_to}m</option>)}</select></label>
    {cap&&<div className="compound-part"><label className="check-option"><input aria-label="分离净块独立核定" type="checkbox" checked={!!group.separate_regions} disabled={busy||!(group.u_cuts.length||group.v_cuts.length||group.line_cuts?.length)} onChange={e=>separate(e.target.checked)}/>分离净块独立核定</label><p className="form-note">先录入分割线，再选择是否将每个裁剪范围内不连通的净块分别核定。启用或停用会重新生成区域，变化面须重新填写决定和依据。默认遇到点接触或边界分支阻断；可在下方明确选择点接触口径。每块派生孔会单独扣除，不能把凸框当面积。</p>
      <label className="check-option"><input aria-label="按净区内部连通处理点接触" type="checkbox" checked={!!group.point_contact} disabled={busy||!group.separate_regions} onChange={e=>contact(e.target.checked?{mode:'interior',basis:'',evidence_id:''}:null)}/>按净区内部连通处理点接触</label><p className="form-note">零宽接点不把两块面积合并；孔在一点触边时，派生边界可能重复经过该点，孔仍不计面积。须填写处理说明和依据；此口径不能证明结构连接、实际施工接触或防水贯通。</p>
      {group.point_contact&&<><label className="field"><span>点接触处理说明</span><textarea aria-label="点接触处理说明" maxLength={2000} value={group.point_contact.basis} onChange={e=>contact({...group.point_contact,basis:e.target.value})}/></label><label className="field"><span>点接触处理原始依据</span>{evidence(group.point_contact.evidence_id,v=>contact({...group.point_contact,evidence_id:v}),'点接触处理原始依据')}</label></>}
    </div>}
    {['u','v'].map(axis=><fieldset className="compound-part" key={axis}><legend>沿{face[axis+'_axis'].toUpperCase()}坐标分割 · 范围{face[axis+'_from']}至{face[axis+'_to']}m</legend>
      {group[axis+'_cuts'].map((cut:Obj,i:number)=><div className="compound-part" key={i}><div className="form-grid">
        <label className="field"><span>{face[axis+'_axis'].toUpperCase()}分割坐标{i+1}</span><input aria-label={`${axis}分割坐标${i+1}`} type="number" step="any" min="-1000000000000" max="1000000000000" value={cut.value} onChange={e=>update(axis,group[axis+'_cuts'].map((c:Obj,j:number)=>j===i?{...c,value:e.target.value}:c))}/></label>
        <label className="field"><span>原始单位</span><select aria-label={`${axis}分割坐标${i+1}单位`} value={cut.unit} onChange={e=>update(axis,group[axis+'_cuts'].map((c:Obj,j:number)=>j===i?{...c,unit:e.target.value}:c))}>{['m','mm','cm','dm','km'].map(u=><option key={u}>{u}</option>)}</select></label>
        <label className="field"><span>原始依据</span>{evidence(cut.evidence_id,v=>update(axis,group[axis+'_cuts'].map((c:Obj,j:number)=>j===i?{...c,evidence_id:v}:c)),`${axis}分割坐标${i+1}依据`)}</label>
      </div><button className="text-btn" type="button" disabled={busy} onClick={()=>update(axis,group[axis+'_cuts'].filter((_:Obj,j:number)=>j!==i))}><Trash2 size={14}/>移除此分割线</button></div>)}
      <button className="btn" type="button" disabled={busy||group[axis+'_cuts'].length>=8} onClick={()=>update(axis,[...group[axis+'_cuts'],{value:'',unit:'m',evidence_id:''}])}><Plus size={14}/>增加{face[axis+'_axis'].toUpperCase()}分割线</button>
    </fieldset>)}
    {cap&&<fieldset className="compound-part"><legend>两点定义的斜向分割（最多4条）</legend><p className="form-note">使用共同设计XY坐标；直线会向两端无限延伸，分割整个原净面，可与X/Y窗口组合。两点X、Y都须不同；每个坐标及分割做法须有依据。净区内分离块默认合为一项，启用独立净块后分别核定；L正/负侧按服务器规范式A×X+B×Y+C的正负确定，反向录入不会改变区域身份。</p>
      {(group.line_cuts||[]).map((cut:Obj,i:number)=><div className="compound-part" key={i}><b>斜线L{i+1}</b><div className="form-grid">
        {['x1','y1','x2','y2'].map(key=><React.Fragment key={key}><label className="field"><span>L{i+1} {key.toUpperCase()}设计坐标</span><input aria-label={`斜线${i+1} ${key}`} type="number" step="any" value={cut[key]} onChange={e=>lineField(i,key,e.target.value)}/></label><label className="field"><span>{key.toUpperCase()}原始依据</span>{evidence(cut.evidence[key],v=>lineField(i,'evidence',{...cut.evidence,[key]:v}),`斜线${i+1} ${key}依据`)}</label></React.Fragment>)}
        <label className="field"><span>两点坐标原始单位</span><select aria-label={`斜线${i+1}单位`} value={cut.unit} onChange={e=>lineField(i,'unit',e.target.value)}>{['m','mm','cm','dm','km'].map(u=><option key={u}>{u}</option>)}</select></label>
        <label className="field"><span>分割做法原始依据</span>{evidence(cut.evidence_id,v=>lineField(i,'evidence_id',v),`斜线${i+1}做法依据`)}</label></div>
        <label className="field"><span>分割与做法说明</span><textarea aria-label={`斜线${i+1}做法说明`} maxLength={2000} value={cut.basis} onChange={e=>lineField(i,'basis',e.target.value)}/></label>
        <button className="text-btn" type="button" disabled={busy} onClick={()=>update('line',group.line_cuts.filter((_:Obj,j:number)=>j!==i))}><Trash2 size={14}/>移除斜线L{i+1}</button>
      </div>)}
      <button className="btn" type="button" disabled={busy||(group.line_cuts||[]).length>=4} onClick={()=>update('line',[...(group.line_cuts||[]),{x1:'',y1:'',x2:'',y2:'',unit:'m',evidence:{x1:'',y1:'',x2:'',y2:''},basis:'',evidence_id:''}])}><Plus size={14}/>增加斜向分割线</button>
    </fieldset>}
    <p className="form-note">分区移除后会恢复原始面，该恢复面也须重新核定。</p>
    {dirty&&<p role="status" className="form-note">分区设置尚未应用，暂不可预览或送审。</p>}
    <button className="btn" type="button" disabled={busy||!dirty} onClick={onApply}>应用分区并重新核定子面</button>
  </details>;
}
