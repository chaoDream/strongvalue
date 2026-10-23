"""Boundary of one horizontal polygon extruded vertically; no inferred contacts."""
from decimal import Decimal
from . import store
from .compound_takeoff import op
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH
from .polygon_takeoff import calculate

VERSION = 'polygon-boundary-1.0'
NOTICE = '仅从已审水平多边形等厚实体生成上下净面、外轮廓逐边侧面及贯通孔逐边内侧面。竖向位置使用局部底面0至来源厚度，不表示绝对高程；不推断模板接触、防水做法、孔内施工、搭接或与其他实体接触。逐面须有据核定，不支持多边形面局部分区、曲面或变厚。'
ROUNDING = 'Decimal 80位；边长平方根按80位精度计算，采用来源未修约净面积与厚度，逐面不另行修约，计入面合计后乘来源重复数量；不采用已修约体积反推面积'


@precise
def boundary(source):
    if source.get('quantity_type') != 'volume':
        raise DomainError('多边形表面须来源于等厚体积构件，面积模式没有竖向厚度')
    result = calculate(source)
    rings = result['polygon_rings']; summary = result['polygon_summary']
    thickness = Decimal(summary['thickness']); net = Decimal(summary['net_area'])
    thickness_ast = op('multiply', {'dimension': 'thickness'}, {'constant': LENGTH[source['thickness_unit']]})
    net_ast = op('subtract', rings[0]['formula_ast'], op('add', *[r['formula_ast'] for r in rings[1:]]) if len(rings)>1 else {'constant':'0'})
    faces = []

    def add(meta, area, formula):
        faces.append({**meta, 'face_id':store.digest(meta), 'index':len(faces)+1,
                      'area':str(quantity_number(area)), 'formula_ast':formula})

    footprint = [{'index':r['index'], 'role':r['role'], 'points':[p['normalized_parameters'] for p in r['points']]} for r in rings]
    for normal, label, level in [('z-', '底部净面', '0'), ('z+', '顶部净面', str(thickness))]:
        add({'geometry_type':'polygon_cap', 'normal':normal, 'label':label, 'local_z':level,
             'boundary_rings':footprint, 'position':f'局部Z={level}m；外轮廓扣除全部贯通孔',
             'calculation':'外轮廓面积 '+summary['outer_area']+' − 孔面积 '+summary['hole_area']}, net, net_ast)
    totals = {'z-':net, 'z+':net, 'outer':Decimal(0), 'hole':Decimal(0)}
    for ring in rings:
        points = ring['points']; ri = ring['index']
        for i, a in enumerate(points):
            b = points[(i+1)%len(points)]; j=(i+1)%len(points)+1
            delta = [Decimal(b['normalized_parameters'][axis])-Decimal(a['normalized_parameters'][axis]) for axis in ('x','y')]
            length = sum((v*v for v in delta),Decimal(0)).sqrt()
            def coordinate(point, index, axis):
                return op('multiply', {'dimension':f'{ri}.{index}.{axis}'}, {'constant':LENGTH[point['unit']]})
            diffs = [op('subtract', coordinate(b,j,axis),coordinate(a,i+1,axis)) for axis in ('x','y')]
            length_ast=op('sqrt',op('add',*[op('multiply',d,d) for d in diffs]))
            area=quantity_number(length*thickness); totals[ring['role'] if ring['role']=='hole' else 'outer']+=area
            label=('外侧' if ring['role']=='outer' else '孔内侧')+f' R{ri} {ring["name"]} · E{i+1} P{i+1}→P{j}'
            start,end=a['normalized_parameters'],b['normalized_parameters']
            add({'geometry_type':'polygon_side','normal':'outer' if ring['role']=='outer' else 'hole', 'label':label,
                 'ring_index':ri,'edge_index':i+1,'from_index':i+1,'to_index':j,'from_xy':start,'to_xy':end,
                 'edge_length':str(length),'height':str(thickness),
                 'position':f'XY ({start["x"]},{start["y"]})→({end["x"]},{end["y"]})m；局部Z 0→{thickness}m',
                 'calculation':f'√(({delta[0]})²+({delta[1]})²) × {thickness}'}, area, op('multiply',length_ast,thickness_ast))
    return {'faces':faces,'total_area':str(sum((Decimal(f['area']) for f in faces),Decimal(0))),
            'normal_totals':{k:str(v) for k,v in totals.items()},'dimensions':result['dimensions'],
            'source_evidence':result['source_evidence'],'unit_conversions':result['unit_conversions']}
