"""Read-only display projections of current DXF candidates; never measurement input."""
from decimal import Decimal,ROUND_HALF_UP
from . import store
from .engine import precise
from .planar_takeoff import linear

VERSION='dxf-route-projection-1.0'
WIDTH,HEIGHT,PADDING=800,400,40


@precise
def project(items,projection):
    axes=tuple(projection)
    eligible=[r for r in items if r['eligible']]
    coordinates={r['id']:[tuple(linear(p[k],r['unit']) for k in axes) for p in r['points']] for r in eligible}
    points=[p for ps in coordinates.values() for p in ps]
    if not points:
        return {'version':VERSION,'projection':projection,'width':WIDTH,'height':HEIGHT,'bounds_m':None,'objects':[],
                'eligible_count':0,'excluded_count':len(items),'display_only':True}
    low=[min(p[k] for p in points) for k in range(2)];high=[max(p[k] for p in points) for k in range(2)]
    span=[high[k]-low[k] for k in range(2)]
    scales=[Decimal(size)/distance for size,distance in zip((WIDTH-2*PADDING,HEIGHT-2*PADDING),span) if distance]
    scale=min(scales) if scales else Decimal(1)
    left=(Decimal(WIDTH)-span[0]*scale)/2;bottom=(Decimal(HEIGHT)+span[1]*scale)/2
    objects=[]
    for row in eligible:
        ps=coordinates[row['id']]
        plotted=[{'x':format((left+(p[0]-low[0])*scale).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP),'f'),
                  'y':format((bottom-(p[1]-low[1])*scale).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP),'f')} for p in ps]
        objects.append({'candidate_id':row['id'],'object_hash':store.digest(row),'handle':row['handle'],'layer':row['layer'],
                        'points':plotted,'collapsed_segments':[i+1 for i,(a,b) in enumerate(zip(plotted,plotted[1:])) if a==b]})
    return {'version':VERSION,'projection':projection,'width':WIDTH,'height':HEIGHT,'display_only':True,
            'bounds_m':{axis:{'min':format(low[k],'f'),'max':format(high[k],'f')} for k,axis in enumerate(axes)},
            'eligible_count':len(eligible),'excluded_count':len(items)-len(eligible),'objects':objects}
