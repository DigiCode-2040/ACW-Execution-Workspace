"""Local-only, allowlisted subprocess runner with thread-safe snapshots."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

try:
    import psutil
except ImportError:
    psutil = None

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / 'scripts'
RUN_DIR = ROOT / 'runs'
MAX_CONCURRENT = 4
MAX_LINES = 250
MAX_RUNS = 100

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.processes = {}
        self.registry = {
            'demo': {'label': 'Demo Pipeline', 'path': SCRIPT_DIR / 'demo_pipeline.py', 'description': 'Instrumented example workflow'},
            'quality': {'label': 'Quality Check', 'path': SCRIPT_DIR / 'quality_check.py', 'description': 'Illustrative validation task'},
        }
        RUN_DIR.mkdir(exist_ok=True)

    def catalog(self):
        return [{'name': name, 'label': item['label'], 'description': item['description']} for name, item in self.registry.items()]

    def start(self, name):
        if not isinstance(name, str) or name not in self.registry:
            raise ValueError('Unknown job. Use the catalog to select an approved script.')
        with self.lock:
            running = sum(j['status'] in ('queued', 'running') for j in self.jobs.values())
            if running >= MAX_CONCURRENT:
                raise ValueError(f'Concurrency limit reached ({MAX_CONCURRENT}).')
            job_id = uuid.uuid4().hex[:10]
            job = dict(id=job_id, name=name, label=self.registry[name]['label'], status='queued',
                       started=None, ended=None, runtime=0, cpu=0, memory_mb=0, peak_memory_mb=0,
                       exit_code=None, stage='Queued', progress=0, quality=None, output=[], events=[],
                       artifacts=[], pid=None)
            self.jobs[job_id] = job
            self._event(job, 'queued', 'Job accepted')
            if len(self.jobs) > MAX_RUNS:
                for key in list(self.jobs):
                    if self.jobs[key]['status'] not in ('queued', 'running'):
                        del self.jobs[key]
                        break
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return {'id': job_id}

    def _event(self, job, kind, message):
        job['events'].append({'time': stamp(), 'kind': kind, 'message': str(message)[:500]})
        job['events'] = job['events'][-MAX_LINES:]

    def _run(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            path = self.registry[job['name']]['path'].resolve()
            if path.parent != SCRIPT_DIR.resolve() or not path.is_file():
                job['status'] = 'failed'
                self._event(job, 'error', 'Approved script missing or invalid')
                return
            job['status'] = 'running'
            job['started'] = stamp()
            self._event(job, 'running', 'Process starting')
        try:
            proc = subprocess.Popen([sys.executable, '-u', str(path)], cwd=str(ROOT),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, bufsize=1, encoding='utf-8', errors='replace',
                                    stdin=subprocess.DEVNULL, shell=False,
                                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
                                    start_new_session=(os.name != 'nt'))
            with self.lock:
                self.processes[job_id] = proc
                job['pid'] = proc.pid
            start = time.monotonic()
            reader = threading.Thread(target=self._read_output, args=(job_id, proc), daemon=True)
            reader.start()
            p = psutil.Process(proc.pid) if psutil else None
            if p:
                try: p.cpu_percent(None)
                except psutil.Error: p = None
            while proc.poll() is None:
                with self.lock:
                    job['runtime'] = round(time.monotonic() - start, 2)
                    if p:
                        try:
                            family = [p] + p.children(recursive=True)
                            mem = sum(x.memory_info().rss for x in family if x.is_running()) / 1048576
                            cpu = sum(x.cpu_percent(None) for x in family if x.is_running())
                            job['memory_mb'] = round(mem, 1)
                            job['peak_memory_mb'] = max(job['peak_memory_mb'], job['memory_mb'])
                            job['cpu'] = round(cpu, 1)
                        except psutil.Error: pass
                time.sleep(.35)
            reader.join(timeout=2)
            with self.lock:
                job['runtime'] = round(time.monotonic() - start, 2)
                job['exit_code'] = proc.returncode
                job['ended'] = stamp()
                job['status'] = 'cancelled' if job['status'] == 'cancelling' else ('completed' if proc.returncode == 0 else 'failed')
                self._event(job, job['status'], f'Process exited with code {proc.returncode}')
                self._save(job)
        except Exception as exc:
            with self.lock:
                job['status'] = 'failed'
                job['ended'] = stamp()
                self._event(job, 'error', str(exc))
        finally:
            with self.lock: self.processes.pop(job_id, None)

    def _read_output(self, job_id, proc):
        for raw in proc.stdout:
            line = raw.rstrip('\r\n')[:1000]
            with self.lock:
                job = self.jobs[job_id]
                if line.startswith('ACW_EVENT:'):
                    try:
                        data = json.loads(line[len('ACW_EVENT:'):])
                        if not isinstance(data, dict): raise ValueError('Expected object')
                        if 'stage' in data: job['stage'] = str(data['stage'])[:100]
                        if 'progress' in data: job['progress'] = max(0, min(100, float(data['progress'])))
                        if 'quality' in data: job['quality'] = max(0, min(100, float(data['quality'])))
                        if 'artifact' in data: job['artifacts'].append(str(data['artifact'])[:250])
                        self._event(job, 'stage', job['stage'])
                    except (ValueError, TypeError): self._event(job, 'warning', 'Invalid structured event')
                else:
                    job['output'].append(line)
                    job['output'] = job['output'][-MAX_LINES:]
                    self._event(job, 'output', line)

    def _save(self, job):
        try:
            (RUN_DIR / f"{job['id']}.json").write_text(json.dumps(job, indent=2), encoding='utf-8')
        except OSError: pass

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(list(reversed(list(self.jobs.values())))))

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job: raise ValueError('Unknown job')
            proc = self.processes.get(job_id)
            if not proc or job['status'] != 'running': raise ValueError('Job is not running')
            job['status'] = 'cancelling'
            self._event(job, 'warning', 'Cancellation requested')
        try:
            if psutil:
                parent = psutil.Process(proc.pid)
                for child in parent.children(recursive=True):
                    try: child.terminate()
                    except psutil.Error: pass
            proc.terminate()
        except (ProcessLookupError, OSError): pass
        return {'ok': True}
