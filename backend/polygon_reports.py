"""Printable tables from the same immutable takeoff snapshot."""


def tables(takeoffs):
    items = [t for t in takeoffs if t.get('polygon_summary')]
    if not items: return []
    labels = {'outer_area': '单组外轮廓面积（m²）', 'hole_area': '单组扣孔面积（m²）', 'net_area': '单组净面积（m²）',
              'hole_count': '扣孔数', 'point_count': '总顶点数', 'thickness': '统一竖向厚度（m）', 'multiplicity': '整组重复数量',
              'unrounded_quantity': '未修约总量', 'rounding_digits': '总量修约位数', 'rounding_adjustment': '总量修约差额'}
    return [
        ('多边形净量汇总', ['构件', '比较项', '结果'],
         [[t['name'], label, '不适用' if t['polygon_summary'][key] is None else str(t['polygon_summary'][key])] for t in items for key, label in labels.items()]+
         [[t['name'], key, value] for t in items for key, value in [('工程量', t['quantity']+' '+t['unit']), ('水平坐标口径', t['coordinate_basis']),
          ('计量与扣孔依据', t['measurement_basis']), ('算法与修约', t['rule_version']+'；'+t['rounding_rule']), ('适用范围', t['notice'])]]),
        ('多边形外轮廓与孔', ['构件', '轮廓 / 类型', '方向 / 顶点数', '未修约面积（m²）', '计量说明 / 依据'],
         [[t['name'], 'R'+str(r['index'])+' '+r['name']+' / '+('外轮廓' if r['role']=='outer' else '扣孔'),
           ('逆时针' if r['orientation']=='counterclockwise' else '顺时针')+' / '+str(len(r['points'])), r['area'], r['measurement_basis']+' / '+r['evidence_id']]
          for t in items for r in t['polygon_rings']]),
        ('多边形原始顶点', ['构件 / 轮廓', '顶点', '原始XY / 单位', '标准XY（m）', '逐坐标证据'],
         [[t['name']+' / R'+str(r['index'])+' '+r['name'], 'P'+str(p['index'])+' '+p['name'],
           'X='+str(p['parameters']['x'])+'；Y='+str(p['parameters']['y'])+' / '+p['unit'],
           'X='+p['normalized_parameters']['x']+'；Y='+p['normalized_parameters']['y'],
           'X：'+p['evidence']['x']+'；Y：'+p['evidence']['y']] for t in items for r in t['polygon_rings'] for p in r['points']]),
        ('多边形逐边计算', ['构件 / 轮廓', '边 / 起终点', '相对首点的有向叉积（m²）', '轮廓面积计算说明'],
         [[t['name']+' / R'+str(r['index']), 'E'+str(e['index'])+' / P'+str(e['from_index'])+'→P'+str(e['to_index']), e['cross_product'],
           '有向叉积合计 '+r['signed_double_area']+'，取绝对值÷2＝'+r['area']+'m²；逐边不修约，负贡献不截断']
          for t in items for r in t['polygon_rings'] for e in r['edges']]),
        ('多边形扣孔边界校验', ['构件', '轮廓 / 孔', '关系 / 检查结果'],
         [[t['name'], c['left']+' / '+c['right'], ('孔严格在外轮廓内部，边界无接触或相交' if c['relation']=='strictly_inside' else '孔之间分离，无接触、交叠或包含')+' / 通过']
          for t in items for c in t['polygon_checks']]+
         [[t['name'], '无扣孔', '明确无扣孔；外轮廓已通过简单多边形检查'] for t in items if not t['polygon_checks']]),
    ]
