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
    onChange(next.u_cuts.length||next.v_cuts.length?[...others,next]:others);}
  return <details className="compound-part"><summary>局部分区设置（{groups.length}个原始面）</summary>
    <p className="form-note">{edge?'本侧面用局部S/Z：S从原边起点沿P起→P终方向量取，Z从局部底面0起算，不能填共同XY或绝对标高。':cap?'本上下净面用共同设计X/Y坐标。窗口只核定其中扣孔后的净区，凹形可能分成多块，空白或孔内零净量窗口不生成面；不能把矩形窗口面积当作工程量。':'分割线填写面内对应轴的共同设计坐标值，不能填像素或距面边的相对距离。'}每轴最多8条，坐标严格位于原始面内；应用后新子面须重新核定，未变分面保留决定。</p>
    <label className="field"><span>选择要分区的原始面</span><select aria-label="选择要分区的原始面" value={face.face_id} onChange={e=>setSelected(e.target.value)}>{faces.map(f=><option key={f.face_id} value={f.face_id}>原F{f.index} · {localEdge?f.label:`${f.normal}=${f.plane}m`} · {f.u_axis} {f.u_from}至{f.u_to}m / {f.v_axis} {f.v_from}至{f.v_to}m</option>)}</select></label>
    {['u','v'].map(axis=><fieldset className="compound-part" key={axis}><legend>沿{face[axis+'_axis'].toUpperCase()}坐标分割 · 范围{face[axis+'_from']}至{face[axis+'_to']}m</legend>
      {group[axis+'_cuts'].map((cut:Obj,i:number)=><div className="compound-part" key={i}><div className="form-grid">
        <label className="field"><span>{face[axis+'_axis'].toUpperCase()}分割坐标{i+1}</span><input aria-label={`${axis}分割坐标${i+1}`} type="number" step="any" min="-1000000000000" max="1000000000000" value={cut.value} onChange={e=>update(axis,group[axis+'_cuts'].map((c:Obj,j:number)=>j===i?{...c,value:e.target.value}:c))}/></label>
        <label className="field"><span>原始单位</span><select aria-label={`${axis}分割坐标${i+1}单位`} value={cut.unit} onChange={e=>update(axis,group[axis+'_cuts'].map((c:Obj,j:number)=>j===i?{...c,unit:e.target.value}:c))}>{['m','mm','cm','dm','km'].map(u=><option key={u}>{u}</option>)}</select></label>
        <label className="field"><span>原始依据</span>{evidence(cut.evidence_id,v=>update(axis,group[axis+'_cuts'].map((c:Obj,j:number)=>j===i?{...c,evidence_id:v}:c)),`${axis}分割坐标${i+1}依据`)}</label>
      </div><button className="text-btn" type="button" disabled={busy} onClick={()=>update(axis,group[axis+'_cuts'].filter((_:Obj,j:number)=>j!==i))}><Trash2 size={14}/>移除此分割线</button></div>)}
      <button className="btn" type="button" disabled={busy||group[axis+'_cuts'].length>=8} onClick={()=>update(axis,[...group[axis+'_cuts'],{value:'',unit:'m',evidence_id:''}])}><Plus size={14}/>增加{face[axis+'_axis'].toUpperCase()}分割线</button>
    </fieldset>)}
    <p className="form-note">分区移除后会恢复原始面，该恢复面也须重新核定。</p>
    {dirty&&<p role="status" className="form-note">分区设置尚未应用，暂不可预览或送审。</p>}
    <button className="btn" type="button" disabled={busy||!dirty} onClick={onApply}>应用分区并重新核定子面</button>
  </details>;
}
