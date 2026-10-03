#!/usr/bin/env python3
"""Measure deep readiness on declared synthetic sizes, without private data."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import platform
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.config import Settings
from app.db import CURRENT_SCHEMA_VERSION
from app.main import create_app


def measure(sizes:list[int],scratch_parent:Path)->dict:
    if any(isinstance(size,bool) or not 0<=size<=10000 for size in sizes):raise ValueError('synthetic size out of range')
    scratch_parent.mkdir(parents=True,exist_ok=True)
    measurements=[]
    for size in sizes:
        with tempfile.TemporaryDirectory(prefix='readiness-',dir=scratch_parent) as scratch:
            app=create_app(Settings(data_dir=Path(scratch),admin_token='synthetic-readiness'))
            with TestClient(app) as api:
                with app.state.database.connect() as connection:
                    connection.executemany("INSERT INTO sites(external_key,name,site_kind,precision,location_basis,created_at,updated_at) VALUES(?,?,'bunker','unknown','map_reference','2026-10-03','2026-10-03')",[(f'synthetic:{id}',f'Synthetic site {id}') for id in range(size)])
                samples=[]
                for _ in range(5):
                    started=time.perf_counter();response=api.get('/api/ready');elapsed=(time.perf_counter()-started)*1000
                    if response.status_code!=200:raise RuntimeError('synthetic readiness failed')
                    samples.append(round(elapsed,3))
                measurements.append({'synthetic_sites':size,'samples_ms':samples,'min_ms':min(samples),'max_ms':max(samples),'scope':'integrity_check plus structural/semantic and authentication qualification'})
    return {'measured_at':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),'hardware':{'system':platform.system(),'machine':platform.machine(),'processor':platform.processor()},'schema_version':CURRENT_SCHEMA_VERSION,'measurements':measurements,'claim':'Local synthetic timings only; no production latency target is certified.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--scratch-parent',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--sizes',type=int,nargs='+',default=[100,1000])
    args=parser.parse_args();receipt=measure(args.sizes,args.scratch_parent)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(receipt,indent=2)+'\n')

if __name__=='__main__':main()
