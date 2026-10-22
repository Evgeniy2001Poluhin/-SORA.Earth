#!/usr/bin/env python3
"""Explicit full replay tools: export/evaluate offline, or one bounded cache batch."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.forecasting.replay_p0 import canonical, digest, parse_json, need
from app.services.forecasting.replay_cache_worker import read_regular, write_new, bounded_fetch
from app.services.forecasting.replay_full import export_snapshot, load_snapshot, evaluate_series, compare_reference, P0_RESULT_PIN
from app.services.forecasting.replay_acquisition import run_batch


def frozen_head():
    need(not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,timeout=10).strip(),
         'tracked source must be committed')
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,timeout=10,text=True).strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    batch = sub.add_parser('batch')
    batch.add_argument('--cache',type=Path,required=True)
    batch.add_argument('--recovery-sha256',required=True)
    batch.add_argument('--continuation-sha256',required=True)
    batch.add_argument('--calendar-retry-sha256')
    batch.add_argument('--confirm-code-commit',required=True)
    batch.add_argument('--max-requests',type=int,required=True)
    batch.add_argument('--max-seconds',type=int,required=True)
    for command in ('export','evaluate'):
        part = sub.add_parser(command)
        part.add_argument('--protocol',type=Path,required=True)
        part.add_argument('--protocol-sha256',required=True)
        part.add_argument('--output',type=Path,required=True)
        if command == 'export':
            part.add_argument('--cache',type=Path,required=True)
            part.add_argument('--p0',type=Path,required=True)
            part.add_argument('--p0-protocol',type=Path,default=ROOT/'docs/research/M3_REPLAY_P0.json')
        else:
            part.add_argument('--snapshot',type=Path,required=True)
            part.add_argument('--manifest-sha256',required=True)
            part.add_argument('--p0-result',type=Path,required=True)
            part.add_argument('--p0-result-sha256',required=True)
    args = parser.parse_args(argv)
    try:
        head = frozen_head()
        if args.command == 'batch':
            def guard():
                need(frozen_head() == args.confirm_code_commit, 'batch execution source differs')
            guard()
            result = run_batch(args.cache,recovery_pin=args.recovery_sha256,
                               continuation_pin=args.continuation_sha256,code_commit=head,
                               max_requests=args.max_requests,max_seconds=args.max_seconds,
                               fetch=bounded_fetch,clock=lambda:datetime.now(timezone.utc),
                               monotonic=time.monotonic,sleep=time.sleep,guard=guard,
                               **({'calendar_retry_pin':args.calendar_retry_sha256}
                                  if args.calendar_retry_sha256 else {}))
            print(canonical(result).decode(),end='')
            return 4 if result['status'] == 'paused' else 3 if result['status'] == 'quota_wait' else 0
        raw = read_regular(args.protocol,100000)
        need(digest(raw) == args.protocol_sha256,'external full protocol pin differs')
        protocol = parse_json(raw)
        need(protocol['analysis_commit'] == head,'analysis source differs')
        if args.command == 'export':
            pin = export_snapshot(args.cache,args.p0,args.p0_protocol,protocol,args.output)
            print(canonical({'status':'exported','manifest_sha256':pin,'full_cache_complete':True}).decode(),end='')
        else:
            need(not args.output.exists() and not args.output.is_symlink(),'new report path required')
            reference = read_regular(args.p0_result,2097152)
            need(digest(reference) == args.p0_result_sha256 == P0_RESULT_PIN, 'original P0 reference differs')
            start = time.monotonic()
            series = load_snapshot(args.snapshot,protocol,args.manifest_sha256)
            report = evaluate_series(*series,protocol)
            report['p0_comparison'] = compare_reference(report,reference,args.p0_result_sha256)
            report.update(manifest_sha256=args.manifest_sha256,analysis_commit=head,
                          elapsed_seconds=time.monotonic()-start,
                          peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
                          runtime_versions={'python':platform.python_version()})
            write_new(args.output,canonical(report))
            print('FULL_REPLAY_EVALUATION_COMPLETE exploratory_only')
        return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        result = {'status':'refused','error_class':type(exc).__name__}
        if args.command == 'export' and args.output.is_dir():
            result.update(snapshot_directory_exists=True,durability='unconfirmed; verify existing artifact')
        print(canonical(result).decode(),end='',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
