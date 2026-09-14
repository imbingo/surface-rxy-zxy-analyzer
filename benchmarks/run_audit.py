"""Serial case matrix: avoid benchmark contention and preserve per-case logs."""
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'audit_outputs'/'performance_20260914'
cases=[
    ('import','csv','100000,500000,1000000,2000000,5000000,8000000',[]),
    ('import','matrix','100000,500000,1000000,2000000,5000000,8000000',[]),
    ('import','pixel','100000,500000,1000000,2000000,5000000',[]),
    ('import','zygo','100000,500000,1000000,2000000,5000000',[]),
    ('import','quoted','100000,2000000',[]),
    ('import','space','100000,2000000',[]),
    ('import','tab','100000,2000000',[]),
    ('import','keyence','100000,2000000',[]),
    ('import','csv','2000000,5000000',['--sampled']),
    ('import','matrix','2000000,5000000',['--sampled']),
    ('render','matrix','30000,60000,100000,300000,1000000',[]),
    ('raster_stages','matrix','100000,500000,1000000,2000000,5000000',[]),
    ('gui','matrix','100000,2000000,5000000,8000000',[]),
    ('smart','matrix','100000,500000,1000000,2000000,5000000',[]),
    ('smart','physical','100000,500000,1000000,2000000,5000000',[]),
    ('smart','sampled_matrix','100000',[]),
    ('kernel','matrix','100000',['--profile']),
    ('import','csv','100000',['--profile']),
    ('import','matrix','100000',['--profile']),
    ('import','zygo','100000',['--profile']),
    ('smart','matrix','100000',['--profile']),
    ('smart','physical','100000',['--profile']),
    ('render','matrix','100000',['--profile']),
]
for i,(suite,kind,points,extra) in enumerate(cases):
    label=f'{i:02}_{suite}_{kind}'
    print('START',label,points,flush=True)
    with (OUT/(label+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run([sys.executable,str(ROOT/'benchmarks/performance_benchmark.py'),
            '--suite',suite,'--kind',kind,'--points',points,'--timeout','600',*extra],stdout=log,stderr=subprocess.STDOUT)
    print('END',label,result.returncode,flush=True)
