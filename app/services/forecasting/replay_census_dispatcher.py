"""Bounded census orchestration. Approval is an externally pinned owner record.

This module never infers human consent from a specification or an approval-shaped
JSON file. The trusted operator must obtain consent first and supply its record
digest independently. A missing final result is an uncertainty barrier, not retry.
"""
from pathlib import Path
import fcntl
import os
import re
import stat
import subprocess

from .replay_source_census import CENSUS_ID, _load, _file, _same_pair
from .replay_source_gap_contract import canonical
from .replay_cache_worker import digest, need, stamp, sync_dir, utc, write_new

MAX_STEPS = 10
APPROVAL_FIELDS = frozenset({'schema', 'decision', 'execution_commit', 'policy_sha256',
                             'root', 'journal_root', 'max_steps', 'owner_text', 'approved_at'})
RUN_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z')


def exact_source(root, commit):
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], timeout=10).decode().strip()
    dirty = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], timeout=10)
    need(actual == commit and not dirty, 'exact clean dispatch source required')


def loaded_source(root, commit, *, loaded_commit, loaded_clean):
    """Bind disk checks to the revision captured before importing application code."""
    need(loaded_clean and loaded_commit == commit, 'loaded execution revision differs')
    exact_source(root, commit)


def approve(raw, pin, *, collector, journal_root, now, steps):
    need(isinstance(pin, str) and re.fullmatch(r'[0-9a-f]{64}', pin) and digest(raw) == pin,
         'independent approval digest differs')
    record = _load(raw)
    need(type(record) is dict and set(record) == APPROVAL_FIELDS, 'approval fields differ')
    need(type(record['schema']) is int and record['schema'] == 1 and
         record['decision'] == 'approve:' + CENSUS_ID, 'explicit census approval required')
    need(record['execution_commit'] == collector.code_commit and
         record['policy_sha256'] == collector.policy_pin and
         record['root'] == str(collector.root.resolve()) and
         record['journal_root'] == str(journal_root.resolve()), 'approval scope differs')
    need(type(record['max_steps']) is int and 1 <= steps <= record['max_steps'] <= MAX_STEPS,
         'dispatch step bound differs')
    need(isinstance(record['owner_text'], str) and record['owner_text'].strip() and
         stamp(record['approved_at']) <= utc(now), 'owner decision text or time differs')
    return record


class CensusDispatcher:
    def __init__(self, collector, journal_root, *, execution_root=None):
        self.c = collector
        self.root = Path(journal_root)
        need(self.root.is_absolute() and self.root == self.root.resolve(),
             'canonical absolute dispatch journal required')
        for protected in (collector.parent, collector.gap, collector.root):
            need(_same_pair(self.root, protected.resolve()), 'separate dispatch namespace required')
        source = Path(execution_root or Path(__file__).resolve().parents[3]).resolve()
        need(_same_pair(self.root, source) and _same_pair(collector.root.resolve(), source),
             'dispatch and acquisition outputs must stay outside execution source')

    def _unfinished(self):
        """A prior start without a valid result forbids even a different invocation ID."""
        for entry in self.root.iterdir():
            if entry.name == 'dispatch.lock':
                continue
            need(entry.is_dir() and not entry.is_symlink() and RUN_ID.fullmatch(entry.name),
                 'unexpected dispatch entry')
            start = _load(_file(entry / 'start.json'))
            need(type(start) is dict and set(start) == {
                'schema', 'run_id', 'mode', 'steps', 'execution_commit', 'policy_sha256',
                'approval_sha256', 'started_at'} and type(start['schema']) is int and start['schema'] == 1 and
                 start['run_id'] == entry.name and start['execution_commit'] == self.c.code_commit and
                 start['policy_sha256'] == self.c.policy_pin and
                 type(start['steps']) is int and 1 <= start['steps'] <= MAX_STEPS and
                 start['mode'] in ('initialize', 'step'), 'dispatch start differs')
            result = _load(_file(entry / 'result.json'))
            need(type(result) is dict and set(result) == {
                'schema', 'run_id', 'start_sha256', 'finished_at', 'transport_calls',
                'results', 'status', 'automatic_retries'} and
                 type(result['schema']) is int and result['schema'] == 1 and
                 result['start_sha256'] == digest(_file(entry / 'start.json')) and
                 result['run_id'] == entry.name and type(result['transport_calls']) is int and
                 0 <= result['transport_calls'] <= start['steps'] and
                 type(result['automatic_retries']) is int and result['automatic_retries'] == 0 and
                 type(result['results']) is list and 1 <= len(result['results']) <= start['steps'] and
                 all(type(r) is dict and isinstance(r.get('status'), str) for r in result['results']) and
                 result['status'] == result['results'][-1]['status'] and
                 stamp(result['finished_at']) >= stamp(start['started_at']),
                 'unfinished or inconsistent dispatch requires review')

    def run(self, *, approval_raw, approval_pin, run_id, mode, steps, clock, guard, fetch=None, bundle=None):
        need(mode in ('initialize', 'step') and type(steps) is int and 1 <= steps <= MAX_STEPS and
             isinstance(run_id, str) and RUN_ID.fullmatch(run_id), 'dispatch mode or bound differs')
        need(mode != 'initialize' or steps == 1, 'initialize is one operation')
        now = utc(clock())
        approve(approval_raw, approval_pin, collector=self.c, journal_root=self.root, now=now, steps=steps)
        guard()
        need(self.root.parent.is_dir() and not self.root.parent.is_symlink(), 'journal parent required')
        if not os.path.lexists(self.root):
            self.root.mkdir(mode=0o700)
            sync_dir(self.root.parent)
        need(self.root.is_dir() and not self.root.is_symlink(), 'journal directory refused')
        fd = os.open(self.root / 'dispatch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            need(stat.S_ISREG(os.fstat(fd).st_mode), 'regular dispatch lock required')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError('dispatch already active') from exc
            self._unfinished()
            directory = self.root / run_id
            need(not os.path.lexists(directory), 'dispatch ID already used')
            directory.mkdir(mode=0o700)
            sync_dir(self.root)
            start = {'schema': 1, 'run_id': run_id, 'mode': mode, 'steps': steps,
                     'execution_commit': self.c.code_commit, 'policy_sha256': self.c.policy_pin,
                     'approval_sha256': approval_pin, 'started_at': now.isoformat()}
            start_raw = canonical(start)
            write_new(directory / 'start.json', start_raw)
            results, calls = [], 0
            def counted_fetch(*args):
                nonlocal calls
                calls += 1
                need(calls <= steps, 'transport bound exceeded')
                return fetch(*args)
            try:
                if mode == 'initialize':
                    need(bundle is not None, 'initialization bundle required')
                    result = self.c.initialize(bundle, now=utc(clock()), guard=guard)
                    results.append(result)
                else:
                    need(fetch is not None and bundle is None, 'explicit step transport required')
                    for index in range(steps):
                        # Per-operation start is durable before invoking the worker.
                        op_start = {'index': index, 'at': utc(clock()).isoformat(),
                                    'before_state_sha256': digest(_file(self.c.root / 'state.json'))}
                        write_new(directory / ('step-%02d-start.json' % index), canonical(op_start))
                        result = self.c.step(fetch=counted_fetch, clock=clock, guard=guard)
                        op_result = {'index': index, 'at': utc(clock()).isoformat(), 'result': result,
                                     'after_state_sha256': digest(_file(self.c.root / 'state.json'))}
                        write_new(directory / ('step-%02d-result.json' % index), canonical(op_result))
                        results.append(result)
                        if result['status'] not in ('fetched_ok', 'recorded_run_unavailable'):
                            break
            except Exception as exc:
                # Keep start without final result as a persistent uncertainty barrier.
                write_new(directory / 'error.json', canonical({'error_class': type(exc).__name__,
                                                               'transport_calls': calls}))
                raise
            final = {'schema': 1, 'run_id': run_id, 'start_sha256': digest(start_raw),
                     'finished_at': utc(clock()).isoformat(), 'transport_calls': calls,
                     'results': results, 'status': results[-1]['status'], 'automatic_retries': 0}
            write_new(directory / 'result.json', canonical(final))
            return final
        finally:
            os.close(fd)
