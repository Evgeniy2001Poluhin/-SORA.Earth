#!/usr/bin/env python3
"""Explicit offline stage1 export/evaluation. No acquisition or live M3 changes."""
from pathlib import Path
import argparse
import platform
import resource
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.forecasting.replay_cache_worker import read_regular,write_new
from app.services.forecasting.replay_p0 import canonical,digest,parse_json
from app.services.forecasting.replay_stage1 import export_snapshot,load_snapshot,evaluate_series


def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
 for name in ('export','evaluate'):
  part=sub.add_parser(name);part.add_argument('--protocol',type=Path,required=True)
  part.add_argument('--protocol-sha256',required=True);part.add_argument('--output',type=Path,required=True)
  if name=='export':
   part.add_argument('--cache',type=Path,required=True);part.add_argument('--p0',type=Path,required=True)
   part.add_argument('--p0-protocol',type=Path,default=ROOT/'docs/research/M3_REPLAY_P0.json')
  else:
   part.add_argument('--snapshot',type=Path,required=True);part.add_argument('--manifest-sha256',required=True)
 args=p.parse_args(argv)
 try:
  if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,timeout=10).strip():
   raise ValueError('tracked source must be committed before analysis')
  head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,timeout=10,text=True).strip()
  raw=read_regular(args.protocol,100000)
  if digest(raw)!=args.protocol_sha256:raise ValueError('external protocol pin differs')
  protocol=parse_json(raw)
  if args.command=='export':
   pin=export_snapshot(args.cache,args.p0,args.p0_protocol,protocol,args.output)
   print(canonical({'status':'exported','manifest_sha256':pin,'full_cache_complete':False}).decode(),end='')
  else:
   import numpy,pandas
   start=time.monotonic();verification,forecasts,test=load_snapshot(args.snapshot,protocol,args.manifest_sha256)
   report=evaluate_series(verification,forecasts,test,protocol)
   report.update({'manifest_sha256':args.manifest_sha256,'analysis_commit':head,
    'elapsed_seconds':time.monotonic()-start,
    'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
    'runtime_versions':{'python':platform.python_version(),'numpy':numpy.__version__,'pandas':pandas.__version__}})
   write_new(args.output,canonical(report));print('STAGE1_EVALUATION_COMPLETE exploratory_only')
  return 0
 except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
  result={'status':'refused','error_class':type(exc).__name__}
  if args.command=='export' and args.output.is_dir():
   result['snapshot_directory_exists']=True
   result['durability']='unconfirmed; verify existing artifact before use'
  print(canonical(result).decode(),end='',file=sys.stderr)
  return 2

if __name__=='__main__':raise SystemExit(main())
