"""Reproducible rendering-kernel benchmark, not an end-to-end UI benchmark."""
import json
import sys
import time
import tracemalloc
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from surface_analyzer.rendering.raster import build_xy_raster
from surface_analyzer.rendering.lod import spatial_lod_indices, critical_indices


def main():
    rng = np.random.default_rng(20260912)
    results = []
    for n in (100000,500000,1000000,2000000):
        x,y = rng.uniform(-15,15,(2,n))
        z = .45+1e-5*x+2e-5*y
        z[1234] += .02
        z[2345] -= .015
        source = np.arange(n)
        required = critical_indices(x,y,z,source,(1e-5,2e-5,.45))
        record = {'points':n,'input_mib':(x.nbytes+y.nbytes+z.nbytes+source.nbytes)/1024**2}
        for name,fn in (
                ('raster',lambda:build_xy_raster(x,y,z,(-15,15,-15,15),(1000,800))),
                ('zoom',lambda:build_xy_raster(x,y,z,(-3,3,-3,3),(1000,800))),
                ('lod',lambda:spatial_lod_indices(x,y,source,30000,required))):
            tracemalloc.start()
            start = time.perf_counter()
            value = fn()
            record[name+'_seconds'] = round(time.perf_counter()-start,6)
            record[name+'_peak_alloc_mib'] = round(tracemalloc.get_traced_memory()[1]/1024**2,2)
            tracemalloc.stop()
            if name == 'lod':
                record['lod_points'] = len(value)
                assert {1234,2345}.issubset(value)
        results.append(record)
    print(json.dumps(results,indent=2))


if __name__ == '__main__':
    main()
