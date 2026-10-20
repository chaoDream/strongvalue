#!/usr/bin/env python3
"""Reproducible synthetic deterministic-engine benchmark, excludes AI/storage/network."""
import copy
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.engine import calculate

items=[]
for i in range(500):
    items.append({'id':str(i),'quantity':'123.456','quotas':[
        {'code':f'MATH-{j}','coefficient':'1.025','resources':[
            {'name':'测试人工','type':'人工','consumption':'1.12','price':'135.555'},
            {'name':'测试材料','type':'材料','consumption':'1.05','price':'356.235'}]} for j in range(4)]})
rules={'fees':[{'name':'测试管理','basis':{'op':'add','args':['labor','machinery']},'rate':'.08'},
               {'name':'测试费用','basis':'running','rate':'.03'}]}
baseline=calculate(items,rules)
durations=[]
for _ in range(20):
    start=time.perf_counter();result=calculate(items,rules);durations.append(time.perf_counter()-start)
    assert result==baseline
print(json.dumps({'dataset':'synthetic mathematics only','items':500,'quotas':2000,'resource_rows':4000,'runs':20,
                  'median_seconds':round(statistics.median(durations),6),'p95_seconds':round(sorted(durations)[18],6),
                  'scope':'pure deterministic engine; excludes DB/API, AI, parsing, professional software and exports'},indent=2))
