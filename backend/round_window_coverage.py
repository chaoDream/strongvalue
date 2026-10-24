"""A geometric certificate for complete coverage, without numeric snapping."""
from decimal import localcontext, ROUND_HALF_EVEN
from .curved_intersections import N, square, ast, value, PRECISION


def certificate(lp,la,up,ua,hp,ha,bounds,asts):
    with localcontext() as ctx:
        ctx.prec=PRECISION;ctx.rounding=ROUND_HALF_EVEN
        lower=(N(0),N(0),N(lp['diameter'],la['diameter'])/2,'下层圆')
        upper=(N(0),N(0),N(up['diameter'],ua['diameter'])/2,'上层圆')
        hn={k:N(v,ha[k]) for k,v in hp.items()};circles=[lower,upper]
        disk=(hn['x'],hn['y'],hn['diameter']/2,'孔圆') if 'diameter' in hn else None
        if disk:circles.append(disk)
        x1,x2,y1,y2=[N(v,asts[k]) for v,k in zip(bounds,('x_start','x_end','y_start','y_end'))]
        lo=max([x1,N(0)-lower[2]],key=lambda n:n.v);hi=min([x2,lower[2]],key=lambda n:n.v)
        proof=dict(method='各分段内窗口外露平台均被本孔包含，按同一平台毛面积扣减；不是数值容差归零',working_digits=PRECISION,output_digits=80,terms=[])
        if lo.v>=hi.v:return proof
        cuts={lo.v:lo,hi.v:hi};horizontal=[y1,y2]
        def add(x):
            if lo.v<x.v<hi.v:cuts.setdefault(x.v,x)
        if not disk:
            rx1,rx2=hn['x']-hn['length']/2,hn['x']+hn['length']/2;ry1,ry2=hn['y']-hn['width']/2,hn['y']+hn['width']/2
            add(rx1);add(rx2);horizontal.extend((ry1,ry2))
        for cx,cy,r,label in circles:
            add(cx-r);add(cx+r)
            for y in horizontal:
                dy=y-cy
                if abs(dy.v)<=r.v:
                    dx=(square(r)-square(dy)).sqrt();add(cx-dx);add(cx+dx)
        for i,(x,y,r,label) in enumerate(circles):
            for X,Y,R,other in circles[:i]:
                dx,dy=X-x,Y-y;d2=square(dx)+square(dy)
                if (r.v-R.v)**2<d2.v<(r.v+R.v)**2:
                    t=d2+square(r)-square(R);disc=N(4)*d2*square(r)-square(t)
                    foot=x+t*dx/(N(2)*d2);offset=dy*disc.sqrt()/(N(2)*d2);add(foot-offset);add(foot+offset)
        def vertical(c,x):
            cx,cy,r,label=c
            if abs(x.v-cx.v)>=r.v:return None
            height=(square(r)-square(x-cx)).sqrt()
            return (cy-height,label+'下弧'),(cy+height,label+'上弧')
        points=[cuts[v] for v in sorted(cuts)]
        for a,b in zip(points,points[1:]):
            mid=(a+b)/2;l=vertical(lower,mid)
            if not l:continue
            bottom=max([l[0],(y1,'窗口下界')],key=lambda t:t[0].v);top=min([l[1],(y2,'窗口上界')],key=lambda t:t[0].v)
            if bottom[0].v>=top[0].v:continue
            u=vertical(upper,mid)
            if u:
                pieces=[(bottom,min([top,u[0]],key=lambda t:t[0].v)),(max([bottom,u[1]],key=lambda t:t[0].v),top)]
            else:pieces=[(bottom,top)]
            h=vertical(disk,mid) if disk else (((ry1,'孔下边'),(ry2,'孔上边')) if rx1.v<mid.v<rx2.v else None)
            for low,high in pieces:
                if low[0].v>=high[0].v:continue
                if not h or low[0].v<h[0][0].v or high[0].v>h[1][0].v:return None
                values=dict(x_from=a,x_to=b,x_mid=mid,lower_margin=low[0]-h[0][0],upper_margin=h[1][0]-high[0])
                proof['terms'].append(dict(lower_boundary=low[1],upper_boundary=high[1],**{k:str(value(n)) for k,n in values.items()},formula_asts={k:ast(n) for k,n in values.items()}))
        return proof
