"""Local-only, allowlisted subprocess runner with thread-safe snapshots."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
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
METADATA_LINES = 20
COMMAND_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')
ARTIFACT_METADATA = re.compile(r'^\s*#\s*ACW_(COMMAND|DESCRIPTION|LABEL|OUTPUT_DIR):\s*(.*?)\s*$')
OPENABLE_ARTIFACT_EXTENSIONS = {
    '.7z', '.bmp', '.csv', '.docx', '.gif', '.jpeg', '.jpg', '.json',
    '.log', '.md', '.pdf', '.png', '.pptx', '.rar', '.txt', '.webp',
    '.xlsx', '.zip',
}

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.processes = {}
        self.job_scripts = {}
        self.job_arguments = {}
        self.registry = self._discover_scripts()
        RUN_DIR.mkdir(exist_ok=True)

    def _discover_scripts(self):
        registry = {}
        script_dir = SCRIPT_DIR.resolve()
        for path in sorted(SCRIPT_DIR.glob('*.py')):
            resolved = path.resolve()
            if resolved.parent != script_dir:
                continue
            lines = path.read_text(encoding='utf-8').splitlines()[:METADATA_LINES]
            metadata = {}
            for line in lines:
                match = ARTIFACT_METADATA.match(line)
                if match:
                    key, value = match.group(1).lower(), match.group(2)
                    if key == 'output_dir':
                        metadata.setdefault(key, []).append(value)
                    else:
                        metadata[key] = value
            if not metadata:
                continue
            if not metadata.get('command') or not metadata.get('description'):
                raise ValueError(f'{path.name} must define ACW_COMMAND and ACW_DESCRIPTION in its first {METADATA_LINES} lines.')

            name = metadata['command']
            if not COMMAND_PATTERN.fullmatch(name):
                raise ValueError(f'{path.name} has invalid ACW_COMMAND {name!r}; use lowercase letters, numbers, and hyphens.')
            if name in registry:
                raise ValueError(f'Duplicate ACW_COMMAND {name!r} in {path.name} and {registry[name]["path"].name}.')

            label = metadata.get('label') or name.replace('-', ' ').title()
            output_dirs = metadata.get('output_dir', [])
            if any(not output_dir for output_dir in output_dirs):
                raise ValueError(f'{path.name} has an empty ACW_OUTPUT_DIR declaration.')
            registry[name] = {
                'label': label,
                'path': resolved,
                'description': metadata['description'],
                'output_dirs': [
                    (ROOT / output_dir).resolve() if not Path(output_dir).expanduser().is_absolute()
                    else Path(output_dir).expanduser().resolve()
                    for output_dir in output_dirs
                ],
            }
        return registry

    def catalog(self):
        with self.lock:
            return self._catalog_entries()

    def _catalog_entries(self):
        return [{'name': name, 'label': item['label'], 'description': item['description']} for name, item in self.registry.items()]

    def reload_catalog(self):
        registry = self._discover_scripts()
        with self.lock:
            self.registry = registry
            return self._catalog_entries()

    def start(self, name, arguments=None):
        if arguments is None:
            arguments = []
        if not isinstance(arguments, list) or len(arguments) > 128:
            raise ValueError('Script arguments must be a list containing at most 128 strings.')
        if any(not isinstance(argument, str) or '\0' in argument for argument in arguments):
            raise ValueError('Script arguments must be strings without null characters.')
        if sum(len(argument) for argument in arguments) > 30000:
            raise ValueError('Combined script arguments are too long.')
        with self.lock:
            if not isinstance(name, str) or name not in self.registry:
                raise ValueError('Unknown job. Use the catalog to select an approved script.')
            running = sum(j['status'] in ('queued', 'running') for j in self.jobs.values())
            if running >= MAX_CONCURRENT:
                raise ValueError(f'Concurrency limit reached ({MAX_CONCURRENT}).')
            job_id = uuid.uuid4().hex[:10]
            script = self.registry[name]
            job = dict(id=job_id, name=name, label=self.registry[name]['label'], status='queued',
                       started=None, ended=None, runtime=0, cpu=0, memory_mb=0, peak_memory_mb=0,
                       exit_code=None, stage='Queued', progress=0, quality=None, output=[], events=[],
                       artifacts=[], pid=None)
            self.jobs[job_id] = job
            self.job_scripts[job_id] = script
            self.job_arguments[job_id] = tuple(arguments)
            self._event(job, 'queued', 'Job accepted')
            if len(self.jobs) > MAX_RUNS:
                for key in list(self.jobs):
                    if self.jobs[key]['status'] not in ('queued', 'running'):
                        del self.jobs[key]
                        self.job_scripts.pop(key, None)
                        self.job_arguments.pop(key, None)
                        break
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return {'id': job_id}

    def _event(self, job, kind, message):
        job['events'].append({'time': stamp(), 'kind': kind, 'message': str(message)[:500]})
        job['events'] = job['events'][-MAX_LINES:]

    def _run(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            path = self.job_scripts[job_id]['path'].resolve()
            arguments = self.job_arguments[job_id]
            if path.parent != SCRIPT_DIR.resolve() or not path.is_file():
                job['status'] = 'failed'
                self._event(job, 'error', 'Approved script missing or invalid')
                return
            job['status'] = 'running'
            job['started'] = stamp()
            self._event(job, 'running', 'Process starting')
        try:
            proc = subprocess.Popen([sys.executable, '-u', str(path), *arguments], cwd=str(ROOT),
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
                if job['status'] == 'completed':
                    job['stage'] = 'Complete'
                    job['progress'] = 100
                    self._event(job, 'stage', 'Complete')
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

    def _artifact_path(self, job_id, artifact):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ValueError('Unknown job')
            if not isinstance(artifact, str) or artifact not in job['artifacts']:
                raise ValueError('Artifact was not reported by this job')
            roots = [ROOT.resolve(), *self.job_scripts[job_id]['output_dirs']]

        path = Path(artifact).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError('Artifact does not exist or cannot be resolved') from exc
        if not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError('Artifact is outside the project and declared output directories')
        return resolved

    def open_artifact(self, job_id, artifact):
        path = self._artifact_path(job_id, artifact)
        if not path.is_file():
            raise ValueError('Only files can be opened as artifacts')
        if path.suffix.lower() not in OPENABLE_ARTIFACT_EXTENSIONS:
            raise ValueError('This artifact type cannot be opened directly from the workspace')
        self._open_path(path)
        return {'ok': True}

    def reveal_artifact(self, job_id, artifact):
        path = self._artifact_path(job_id, artifact)
        target = path.parent if path.is_file() else path
        if os.name == 'nt':
            subprocess.Popen(['explorer', f'/select,{path}'] if path.is_file() else ['explorer', str(path)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', str(path)] if path.is_file() else ['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(target)])
        return {'ok': True}

    @staticmethod
    def _open_path(path):
        if os.name == 'nt':
            os.startfile(str(path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])

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
