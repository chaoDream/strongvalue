import React,{useState} from 'react';
type Obj=Record<string,any>;
export default function PolygonFaceDiagram({faces,decisions}:{faces:Obj[];decisions:Obj[]}){
  const [selected,setSelected]=useState('');
  const f=faces.find(v=>v.face_id===selected)||faces[0];
  if(!f)return null;
  const d=decisions.find(v=>v.face_id===f.face_id),fill=d?.included===true?'#85aa94':d?.included===false?'#d7dfda':'#e4c889';
  const rings=f.boundary_rings||[],points=rings.flatMap((r:Obj)=>r.points);
  const x0=Math.min(...points.map((p:Obj)=>Number(p.x))),x1=Math.max(...points.map((p:Obj)=>Number(p.x)));
  const y0=Math.min(...points.map((p:Obj)=>Number(p.y))),y1=Math.max(...points.map((p:Obj)=>Number(p.y)));
  const safe=x1>x0&&y1>y0&&rings.every((r:Obj)=>r.points.every((p:Obj,i:number)=>{const q=r.points[(i+1)%r.points.length];return Number(p.x)!==Number(q.x)||Number(p.y)!==Number(q.y)}));
  const scale=safe?Math.min(640/(x1-x0),210/(y1-y0)):1;
  const path=rings.map((r:Obj)=>r.points.map((p:Obj,i:number)=>`${i?'L':'M'}${40+(Number(p.x)-x0)*scale},${245-(Number(p.y)-y0)*scale}`).join(' ')+' Z').join(' ');
  const l=Number(f.edge_length),h=Number(f.height),s=l>0&&h>0?Math.min(640/l,200/h):0;
  const siblings=f.parent_face_id?faces.filter(v=>v.parent_face_id===f.parent_face_id):[f];
  const sideSafe=s>0&&siblings.every(v=>!v.parent_face_id||(Number(v.u_to)>Number(v.u_from)&&Number(v.v_to)>Number(v.v_from)));
  return <><label className="field"><span>核对多边形边界面</span><select aria-label="核对多边形边界面" value={f.face_id} onChange={e=>setSelected(e.target.value)}>{faces.map(v=><option key={v.face_id} value={v.face_id}>F{v.index} · {v.label}</option>)}</select></label>
    <p className="wrap-cell">F{f.index} · {f.label} · {f.area} m²<br/>{f.position}<br/>{f.calculation} = {f.area} m²</p>
    {(f.geometry_type==='polygon_cap'?safe:sideSafe)?<div className="planar-diagram"><svg viewBox="0 0 720 280" role="img" aria-label="多边形实体边界面示意，非测量图">
      {f.geometry_type==='polygon_cap'?<path d={path} fillRule="evenodd" fill={fill} stroke="#456a54" strokeWidth="1.5"/>:<><rect x="40" y={245-h*s} width={l*s} height={h*s} fill="#edf1ee" stroke="#456a54"/>{siblings.map(v=>{const a=decisions.find(vv=>vv.face_id===v.face_id),u0=Number(v.u_from||0),u1=Number(v.u_to||l),v0=Number(v.v_from||0),v1=Number(v.v_to||h),color=a?.included===true?'#85aa94':a?.included===false?'#d7dfda':'#e4c889';return <g key={v.face_id}><rect x={40+u0*s} y={245-v1*s} width={(u1-u0)*s} height={(v1-v0)*s} fill={color} stroke={v.face_id===f.face_id?'#213e2b':'#6a8171'} strokeWidth={v.face_id===f.face_id?2.5:1}/><text x={44+u0*s} y={260-v1*s} fontSize="12">F{v.index}</text></g>})}</>}
      <text x="40" y="270" fontSize="12" fill="#536b5b">{f.geometry_type==='polygon_cap'?'XY设计平面 · 孔为空白':'沿原边展开 · 横向S距离，竖向局部Z高度'} · F{f.index}</text>
    </svg><p className="form-note">绿色计入、灰色排除、黄色待核定。只供逐面定位，不可测量；局部底面0不代表设计绝对标高，粗边框为当前面，子面保留原边S/Z位置；逐边侧面不自动合并。</p></div>:<p className="form-note">当前精度不适合绘制，请核对上方精确坐标及面积。</p>}</>;
}
