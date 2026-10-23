"""Exact net-area partitions by evidence-backed infinite design XY lines.

The convex clipping domain is retained separately from the original polygon.
Directed bridges in a clipped concave ring cancel in the signed area integral.
"""
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import reduce
from math import gcd, lcm
from . import store
from .compound_takeoff import op, text
from .engine import DomainError, precise, quantity_number
from .measurement_units import LENGTH, VERSION as UNIT_VERSION
from .planar_takeoff import linear
from .polygon_cap_partitions import area, checked, decimal, rational, editor_faces

VERSION = 'polygon-boundary-1.3'
CLIP_RULE = 'xy-line-rational-1.0'
NOTICE = '上下净面可按共同设计X/Y窗口及两点定义的无限斜线分区。分割延伸至整个原净面，不仅是两点之间的线段；每区按原外轮廓扣孔，凸裁剪框不等于计量范围。同一区内分离净块合为一项，零净量区不生成面；不推断实际做法、施工接触、搭接、翻边或绝对标高。侧面仍使用原边局部S/Z。'
ROUNDING = '原始设计坐标、线交点、凸裁剪框及逐孔扣减用精确有理数计算；净面积以派生分子/分母AST保存，Decimal 80位转换显示，200位记录子区合计精度差，不调平子区。精确有理数子区合计须等于原净面，计入面合计后仅乘一次来源重复数量'
FIELDS = ('x1', 'y1', 'x2', 'y2')


def coefficients(v):
    dx, dy = v['x2']-v['x1'], v['y2']-v['y1']
    if not dx or not dy:
        raise DomainError('斜向分割的两点须不同且X、Y都不同；轴向分割请使用X/Y分割线')
    c = (-dy, dx, dy*v['x1']-dx*v['y1'])
    denominator = lcm(*(n.denominator for n in c))
    nums = [int(n*denominator) for n in c]
    divisor = reduce(gcd, (abs(n) for n in nums))
    sign = 1 if nums[0] > 0 else -1
    return tuple(Fraction(n//divisor*sign) for n in nums)


def distance(p, line):
    return checked(line[0]*p[0]+line[1]*p[1]+line[2])


def clip(points, line, positive):
    if not points:
        return []
    result = []; a = points[-1]; da = distance(a, line)
    for b in points:
        db = distance(b, line)
        inside_a = da >= 0 if positive else da <= 0
        inside_b = db >= 0 if positive else db <= 0
        if inside_a != inside_b:
            ratio = checked(da/(da-db))
            q = tuple(checked(a[k]+ratio*(b[k]-a[k])) for k in (0, 1))
            if not result or result[-1] != q:
                result.append(q)
        if inside_b and (not result or result[-1] != b):
            result.append(b)
        a, da = b, db
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return result


def canonical_polygon(points):
    # Cell identity depends on the geometric domain, not line order or endpoints.
    while len(points) >= 3:
        keep = [p for i, p in enumerate(points)
                if (p[0]-points[i-1][0])*(points[(i+1)%len(points)][1]-p[1]) !=
                   (p[1]-points[i-1][1])*(points[(i+1)%len(points)][0]-p[0])]
        if len(keep) == len(points):
            break
        points = keep
    if len(points) < 3 or not area(points):
        return []
    # Input rectangle is CCW and clipping preserves that orientation.
    start = min(range(len(points)), key=lambda i: points[i])
    return points[start:]+points[:start]


def clipped_area(ring, box, sides):
    from .polygon_cap_partitions import clip as axis_clip
    for axis, value, greater in ((0, box[0], True), (0, box[1], False), (1, box[2], True), (1, box[3], False)):
        ring = axis_clip(ring, axis, value, greater)
    for line, positive in sides:
        ring = clip(ring, line, positive)
    return area(ring)


@precise
def partition(source, groups, original):
    from .polygon_cap_partitions import partition as windows
    originals = {f['face_id']: f for f in editor_faces(original['faces'])}
    configs = {}; legacy = []; seen = set()
    for group in groups:
        if not isinstance(group, dict) or set(group) not in ({'face_id', 'u_cuts', 'v_cuts'}, {'face_id', 'u_cuts', 'v_cuts', 'line_cuts'}):
            raise DomainError('分区须明确原面、X/Y或局部S/Z分割，斜向分割仅用于上下净面')
        fid = group['face_id']
        if not isinstance(fid, str) or fid not in originals or fid in seen:
            raise DomainError('分区原始面不存在、已过期或重复')
        seen.add(fid); f = originals[fid]
        if 'line_cuts' not in group:
            legacy.append(group); continue
        cuts = group['line_cuts']
        if f['geometry_type'] != 'polygon_cap' or not isinstance(cuts, list) or not 1 <= len(cuts) <= 4:
            raise DomainError('仅上下净面可录入1至4条斜向分割线；没有斜线请移除配置字段')
        lines = []; line_ids = set()
        for i, cut in enumerate(cuts, 1):
            if not isinstance(cut, dict) or set(cut) != set(FIELDS) | {'unit', 'evidence', 'basis', 'evidence_id'}:
                raise DomainError('斜向分割须保留两点XY原值、长度单位、逐坐标依据及分割做法依据')
            if not isinstance(cut['evidence'], dict) or set(cut['evidence']) != set(FIELDS):
                raise DomainError('斜向分割两点的四个坐标须分别关联原始依据')
            v = {key: Fraction(linear(cut[key], cut['unit'])) for key in FIELDS}
            line = coefficients(v); lid = store.digest([str(n) for n in line])
            if lid in line_ids:
                raise DomainError('斜向分割线重复或共线，不能用不同端点重复分割')
            line_ids.add(lid)
            rings = [[(Fraction(p['x']), Fraction(p['y'])) for p in r['points']] for r in f['boundary_rings']]
            signed_areas = [area(clip(rings[0], line, side))-sum((area(clip(r, line, side)) for r in rings[1:]), Fraction(0)) for side in (False, True)]
            if min(signed_areas) <= 0:
                raise DomainError('斜向分割线须穿过原净区，两侧净面积均须大于零；仅触边或位于区域外不能分割')
            text(cut['basis'], '斜向分割做法说明')
            for key in FIELDS:
                text(cut['evidence'][key], '斜向分割逐坐标原始依据', 200)
            text(cut['evidence_id'], '斜向分割做法原始依据', 200)
            lines.append({'index': i, 'id': lid, 'coefficients': line, 'values': v, 'input': cut})
        configs[fid] = lines
        base = {k: v for k, v in group.items() if k != 'line_cuts'}
        if group['u_cuts'] or group['v_cuts']:
            legacy.append(base)
        elif not isinstance(group['u_cuts'], list) or not isinstance(group['v_cuts'], list):
            raise DomainError('X/Y分割须为列表')
    if not configs:
        return windows(source, groups, original)
    result = windows(source, legacy, original) if legacy else original
    line_records = []; dimensions = result['dimensions']; refs = result['source_evidence']
    for gi, (fid, lines) in enumerate(configs.items(), 1):
        for line in lines:
            cut = line['input']; prefix = f'cap_line.{gi}.{line["index"]}.'
            for key in FIELDS:
                parameter = prefix+key
                dimensions[parameter] = str(cut[key]); refs[parameter] = cut['evidence'][key]
                result['unit_conversions'].append({'parameter': parameter, 'original_value': str(cut[key]), 'source_unit': cut['unit'],
                    'factor': LENGTH[cut['unit']], 'canonical_value': str(decimal(line['values'][key])), 'canonical_unit': 'm', 'rule_version': UNIT_VERSION})
            refs[prefix+'basis'] = cut['evidence_id']
            line_records.append({'parent_face_id': fid, 'parent_index': originals[fid]['index'], 'line_index': line['index'], 'line_id': line['id'],
                'original': {**cut, **{key: str(cut[key]) for key in FIELDS}}, 'normalized_coordinates': {key: str(decimal(v)) for key, v in line['values'].items()},
                'coefficients': [str(n) for n in line['coefficients']], 'rule_version': CLIP_RULE})
    faces = []; accum = {fid: {'areas': [], 'zero': 0, 'candidates': 0} for fid in configs}
    # Check before area work, including cells wholly outside the net polygon.
    jobs = []; budget = 0
    for face in result['faces']:
        fid = face.get('parent_face_id', face['face_id'])
        if fid not in configs:
            jobs.append((face, None)); budget += 1; continue
        f = {**originals[fid], **face}
        box = [Fraction(f[k]) for k in ('u_from', 'u_to', 'v_from', 'v_to')]
        x0, x1, y0, y1 = box
        cells = [([(x0,y0),(x1,y0),(x1,y1),(x0,y1)], [])]
        for line in sorted(configs[fid], key=lambda v: v['coefficients']):
            split = []
            for points, sides in cells:
                for positive in (False, True):
                    polygon = canonical_polygon(clip(points, line['coefficients'], positive))
                    if polygon:
                        split.append((polygon, sides+[(line, positive)]))
            cells = split
        budget += len(cells)
        if budget > 1000:
            raise DomainError('斜线分割区域与计量面合计超过1000项，请拆分计量范围')
        jobs.append((f, (fid, box, cells)))
    if budget > 1000:
        raise DomainError('斜线分割区域与计量面合计超过1000项，请拆分计量范围')
    for f, job in jobs:
        if job is None:
            faces.append(f); continue
        fid, box, cells = job; a = accum[fid]
        rings = [[(Fraction(p['x']), Fraction(p['y'])) for p in r['points']] for r in f['boundary_rings']]
        for polygon, sides in sorted(cells, key=lambda cell: cell[0]):
            a['candidates'] += 1
            areas = [clipped_area(r, box, [(line['coefficients'], pos) for line, pos in sides]) for r in rings]
            net = checked(areas[0]-sum(areas[1:], Fraction(0)))
            if net < 0:
                raise DomainError('斜线净区扣孔面积超过外面积，请核对来源')
            if not net:
                a['zero'] += 1; continue
            frac = rational(net); index = len(a['areas'])+1; a['areas'].append(net)
            domain = [[str(x), str(y)] for x, y in polygon]
            side_records = [{'line_id': line['id'], 'line_index': line['index'], 'positive': pos} for line, pos in sorted(sides, key=lambda pair: pair[0]['index'])]
            faces.append({**f, 'parent_face_id': fid, 'parent_index': originals[fid]['index'], 'sub_index': index,
                'face_id': store.digest({'parent': fid, 'domain': domain, 'rule': CLIP_RULE}), 'label': originals[fid]['label']+f' · 斜线净区{index}',
                'position': f'原F{originals[fid]["index"]}；'+ '、'.join(f'L{s["line_index"]}{"正侧" if s["positive"] else "负侧"}' for s in side_records)+f'；局部Z={f["local_z"]}m；精确范围见凸裁剪框坐标',
                'calculation': f'斜线及XY框内外面积 {decimal(areas[0])} − 逐孔面积 {decimal(sum(areas[1:],Fraction(0)))}（精确有理数扣减）',
                'area': str(quantity_number(decimal(net))), 'formula_ast': op('divide', {'constant': frac['numerator']}, {'constant': frac['denominator']}),
                'cap_clip_rule': CLIP_RULE, 'cap_rational_area': frac, 'cap_cut_sides': side_records,
                'cap_clip_domain': [{'x': str(decimal(x)), 'y': str(decimal(y)), 'exact_x': rational(x), 'exact_y': rational(y)} for x, y in polygon],
                'cap_clip_rings': [{'ring_index': i+1, 'role': 'outer' if i==0 else 'hole', 'area': str(decimal(value)), **rational(value)} for i, value in enumerate(areas)]})
    checks = [c for c in result.get('surface_cap_partition_checks', []) if c['parent_face_id'] not in configs]
    for fid, a in accum.items():
        parent = originals[fid]; exact = sum(a['areas'], Fraction(0))
        rings = [[(Fraction(p['x']), Fraction(p['y'])) for p in r['points']] for r in parent['boundary_rings']]
        whole = area(rings[0])-sum((area(r) for r in rings[1:]), Fraction(0))
        if exact != whole:
            raise DomainError('斜线分区净面积未精确闭合，请核对分割范围')
        with localcontext() as ctx:
            ctx.prec = 200
            display_sum = sum((Decimal(f['area']) for f in faces if f.get('parent_face_id')==fid), Decimal(0))
            checks.append({'parent_face_id': fid, 'parent_index': parent['index'], 'child_count': len(a['areas']), 'zero_window_count': a['zero'],
                'candidate_region_count': a['candidates'], 'line_count': len(configs[fid]), 'parent_area': parent['area'], 'children_area': str(display_sum),
                'area_difference': str(display_sum-Decimal(parent['area'])), 'exact_rational_tiling': True, **rational(whole)})
    totals = {k: Decimal(0) for k in result['normal_totals']}
    for f in faces:
        totals[f['normal']] += Decimal(f['area'])
    result.update(faces=[{**f, 'index': i+1} for i, f in enumerate(faces)], surface_cap_partition_checks=checks, surface_partition_lines=line_records,
        normal_totals={k: str(v) for k, v in totals.items()}, total_area=str(sum((Decimal(f['area']) for f in faces), Decimal(0))))
    return result
