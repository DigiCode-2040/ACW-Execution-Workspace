"""Local-only, allowlisted subprocess runner with thread-safe snapshots,
session persistence, run history, ETA, and configurable concurrency queue.
"""
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
SESSIONS_DIR = ROOT / 'sessions'
HISTORY_FILE = RUN_DIR / 'history.jsonl'
SESSION_FILE = RUN_DIR / 'last_session.json'
DEFAULT_MAX_CONCURRENT = 4
MAX_LINES = 250
MAX_RUNS = 200
MAX_HISTORY_PER_SCRIPT = 30
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
        self.queue = deque()  # job_ids waiting for a slot
        self.max_concurrent = DEFAULT_MAX_CONCURRENT
        self.registry = self._discover_scripts()
        self._averages = {}  # script_name -> list of recent runtimes
        RUN_DIR.mkdir(exist_ok=True)
        SESSIONS_DIR.mkdir(exist_ok=True)
        self._load_averages()
        self._restore_last_session()

    # ------------------------------------------------------------------
    # Script discovery
    # ------------------------------------------------------------------
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
                raise ValueError(
                    f'{path.name} must define ACW_COMMAND and ACW_DESCRIPTION '
                    f'in its first {METADATA_LINES} lines.'
                )
            name = metadata['command']
            if not COMMAND_PATTERN.fullmatch(name):
                raise ValueError(
                    f'{path.name} has invalid ACW_COMMAND {name!r}; '
                    'use lowercase letters, numbers, and hyphens.'
                )
            if name in registry:
                raise ValueError(
                    f'Duplicate ACW_COMMAND {name!r} in {path.name} '
                    f'and {registry[name]["path"].name}.'
                )
            label = metadata.get('label') or name.replace('-', ' ').title()
            output_dirs = metadata.get('output_dir', [])
            if any(not output_dir for output_dir in output_dirs):
                raise ValueError(f'{path.name} has an empty ACW_OUTPUT_DIR declaration.')
            registry[name] = {
                'label': label,
                'path': resolved,
                'description': metadata['description'],
                'output_dirs': [
                    (ROOT / output_dir).resolve()
                    if not Path(output_dir).expanduser().is_absolute()
                    else Path(output_dir).expanduser().resolve()
                    for output_dir in output_dirs
                ],
            }
        return registry

    def catalog(self):
        with self.lock:
            return self._catalog_entries()

    def _catalog_entries(self):
        return [
            {'name': name, 'label': item['label'], 'description': item['description']}
            for name, item in self.registry.items()
        ]

    def reload_catalog(self):
        registry = self._discover_scripts()
        with self.lock:
            self.registry = registry
            return self._catalog_entries()

    def edit_script(self, name):
        with self.lock:
            if not isinstance(name, str) or name not in self.registry:
                raise ValueError('Unknown script. Reload the catalog and select an approved script.')
            path = self.registry[name]['path'].resolve(strict=True)
        if path.parent != SCRIPT_DIR.resolve() or not path.is_file() or path.suffix.lower() != '.py':
            raise ValueError('Selected script is missing or invalid.')
        if os.name == 'nt':
            subprocess.Popen(['notepad.exe', str(path)])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-t', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
        return {'ok': True}

    # ------------------------------------------------------------------
    # Concurrency configuration
    # ------------------------------------------------------------------
    def get_max_concurrent(self):
        with self.lock:
            return self.max_concurrent

    def set_max_concurrent(self, value):
        value = int(value)
        if value < 1 or value > 32:
            raise ValueError('max_concurrent must be between 1 and 32')
        with self.lock:
            self.max_concurrent = value
            self._drain_queue()
        return {'ok': True, 'max_concurrent': value}

    def _active_count(self):
        return sum(1 for j in self.jobs.values() if j['status'] in ('queued', 'running', 'cancelling'))

    def _running_count(self):
        return sum(1 for j in self.jobs.values() if j['status'] in ('running', 'cancelling'))

    # ------------------------------------------------------------------
    # Job start + queue
    # ------------------------------------------------------------------
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

            job_id = uuid.uuid4().hex[:10]
            script = self.registry[name]
            avg = self._average_for(name)
            job = dict(
                id=job_id,
                name=name,
                label=script['label'],
                status='queued',
                started=None,
                ended=None,
                runtime=0,
                cpu=0,
                memory_mb=0,
                peak_memory_mb=0,
                exit_code=None,
                stage='Queued',
                progress=0,
                quality=None,
                output=[],
                events=[],
                artifacts=[],
                pid=None,
                stage_started=None,
                stage_timings={},
                eta_seconds=None,
                historical_avg_seconds=avg,
                arguments=list(arguments),
            )
            self.jobs[job_id] = job
            self.job_scripts[job_id] = script
            self.job_arguments[job_id] = tuple(arguments)
            self._event(job, 'queued', 'Job accepted')

            # Prune old finished jobs if over limit
            if len(self.jobs) > MAX_RUNS:
                for key in list(self.jobs):
                    if self.jobs[key]['status'] not in ('queued', 'running', 'cancelling'):
                        del self.jobs[key]
                        self.job_scripts.pop(key, None)
                        self.job_arguments.pop(key, None)
                        break

            if self._running_count() < self.max_concurrent:
                self._launch(job_id)
            else:
                self.queue.append(job_id)
                self._event(job, 'queued', f'Waiting for slot (queue position {len(self.queue)})')

        return {'id': job_id}

    def _launch(self, job_id):
        """Start the worker thread for a queued job. Caller must hold lock."""
        job = self.jobs.get(job_id)
        if not job or job['status'] != 'queued':
            return
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()

    def _drain_queue(self):
        """Launch queued jobs up to max_concurrent. Caller must hold lock."""
        while self.queue and self._running_count() < self.max_concurrent:
            next_id = self.queue.popleft()
            if next_id in self.jobs and self.jobs[next_id]['status'] == 'queued':
                self._launch(next_id)

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------
    def _event(self, job, kind, message):
        job['events'].append({'time': stamp(), 'kind': kind, 'message': str(message)[:500]})
        job['events'] = job['events'][-MAX_LINES:]

    def _run(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            path = self.job_scripts[job_id]['path'].resolve()
            arguments = self.job_arguments[job_id]
            if path.parent != SCRIPT_DIR.resolve() or not path.is_file():
                job['status'] = 'failed'
                self._event(job, 'error', 'Approved script missing or invalid')
                self._drain_queue()
                return
            job['status'] = 'running'
            job['started'] = stamp()
            job['stage_started'] = stamp()
            self._event(job, 'running', 'Process starting')

        try:
            proc = subprocess.Popen(
                [sys.executable, '-u', str(path), *arguments],
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
                stdin=subprocess.DEVNULL,
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
                start_new_session=(os.name != 'nt'),
            )
            with self.lock:
                self.processes[job_id] = proc
                job['pid'] = proc.pid

            start = time.monotonic()
            reader = threading.Thread(target=self._read_output, args=(job_id, proc), daemon=True)
            reader.start()

            # Stable Process handle for parent; re-fetch children carefully
            parent_ps = None
            if psutil:
                try:
                    parent_ps = psutil.Process(proc.pid)
                    parent_ps.cpu_percent(None)  # prime
                except psutil.Error:
                    parent_ps = None

            child_handles = {}  # pid -> Process (stable across loops)

            while proc.poll() is None:
                with self.lock:
                    job = self.jobs.get(job_id)
                    if not job:
                        break
                    elapsed = time.monotonic() - start
                    job['runtime'] = round(elapsed, 2)

                    # ETA from progress
                    progress = float(job.get('progress') or 0)
                    if progress > 0 and progress < 100:
                        job['eta_seconds'] = round((elapsed / progress) * (100 - progress), 1)
                    else:
                        job['eta_seconds'] = None

                    if parent_ps:
                        try:
                            # Update child handles
                            current_children = parent_ps.children(recursive=True)
                            current_pids = {c.pid for c in current_children}
                            # Drop gone children
                            for pid in list(child_handles):
                                if pid not in current_pids:
                                    child_handles.pop(pid, None)
                            # Add new children and prime them
                            for c in current_children:
                                if c.pid not in child_handles:
                                    try:
                                        c.cpu_percent(None)
                                        child_handles[c.pid] = c
                                    except psutil.Error:
                                        pass

                            family = [parent_ps] + list(child_handles.values())
                            mem = 0.0
                            cpu = 0.0
                            for x in family:
                                try:
                                    if x.is_running():
                                        mem += x.memory_info().rss
                                        cpu += x.cpu_percent(None)
                                except psutil.Error:
                                    pass
                            job['memory_mb'] = round(mem / 1048576, 1)
                            job['peak_memory_mb'] = max(job['peak_memory_mb'], job['memory_mb'])
                            job['cpu'] = round(cpu, 1)
                        except psutil.Error:
                            pass
                time.sleep(0.35)

            reader.join(timeout=2)
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    return
                job['runtime'] = round(time.monotonic() - start, 2)
                job['exit_code'] = proc.returncode
                job['ended'] = stamp()
                job['eta_seconds'] = None
                # Close out final stage timing
                if job.get('stage_started') and job.get('stage'):
                    try:
                        st = DateTime_parse(job['stage_started'])
                        duration = (datetime.now(timezone.utc) - st).total_seconds()
                        job['stage_timings'][job['stage']] = round(duration, 2)
                    except Exception:
                        pass
                job['status'] = (
                    'cancelled' if job['status'] == 'cancelling'
                    else ('completed' if proc.returncode == 0 else 'failed')
                )
                if job['status'] == 'completed':
                    job['stage'] = 'Complete'
                    job['progress'] = 100
                    self._event(job, 'stage', 'Complete')
                    self._record_history(job)
                self._event(job, job['status'], f'Process exited with code {proc.returncode}')
                self._save(job)
                self._auto_save_session()
                self._drain_queue()
        except Exception as exc:
            with self.lock:
                job = self.jobs.get(job_id)
                if job:
                    job['status'] = 'failed'
                    job['ended'] = stamp()
                    self._event(job, 'error', str(exc))
                    self._auto_save_session()
                self._drain_queue()
        finally:
            with self.lock:
                self.processes.pop(job_id, None)

    def _read_output(self, job_id, proc):
        for raw in proc.stdout:
            line = raw.rstrip('\r\n')[:1000]
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    break
                if line.startswith('ACW_EVENT:'):
                    try:
                        data = json.loads(line[len('ACW_EVENT:'):])
                        if not isinstance(data, dict):
                            raise ValueError('Expected object')
                        if 'stage' in data:
                            new_stage = str(data['stage'])[:100]
                            # Record previous stage duration
                            if job.get('stage_started') and job.get('stage') and job['stage'] != new_stage:
                                try:
                                    st = DateTime_parse(job['stage_started'])
                                    duration = (datetime.now(timezone.utc) - st).total_seconds()
                                    job['stage_timings'][job['stage']] = round(duration, 2)
                                except Exception:
                                    pass
                                job['stage_started'] = stamp()
                            elif not job.get('stage_started'):
                                job['stage_started'] = stamp()
                            job['stage'] = new_stage
                        if 'progress' in data:
                            job['progress'] = max(0, min(100, float(data['progress'])))
                        if 'quality' in data:
                            job['quality'] = max(0, min(100, float(data['quality'])))
                        if 'artifact' in data:
                            art = str(data['artifact'])[:250]
                            if art not in job['artifacts']:
                                job['artifacts'].append(art)
                        self._event(job, 'stage', job['stage'])
                    except (ValueError, TypeError):
                        self._event(job, 'warning', 'Invalid structured event')
                else:
                    job['output'].append(line)
                    job['output'] = job['output'][-MAX_LINES:]
                    self._event(job, 'output', line)

    # ------------------------------------------------------------------
    # History / averages
    # ------------------------------------------------------------------
    def _load_averages(self):
        self._averages = {}
        if not HISTORY_FILE.exists():
            return
        try:
            for line in HISTORY_FILE.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    name = rec.get('name')
                    runtime = rec.get('runtime')
                    if name and isinstance(runtime, (int, float)) and runtime > 0:
                        self._averages.setdefault(name, []).append(float(runtime))
                except (json.JSONDecodeError, TypeError):
                    continue
            # Keep only last N
            for name in self._averages:
                self._averages[name] = self._averages[name][-MAX_HISTORY_PER_SCRIPT:]
        except OSError:
            pass

    def _average_for(self, name):
        vals = self._averages.get(name) or []
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    def _record_history(self, job):
        if job.get('status') != 'completed' or not job.get('runtime'):
            return
        rec = {
            'name': job['name'],
            'label': job.get('label'),
            'runtime': job['runtime'],
            'peak_memory_mb': job.get('peak_memory_mb'),
            'exit_code': job.get('exit_code'),
            'quality': job.get('quality'),
            'ended': job.get('ended'),
            'stage_timings': job.get('stage_timings') or {},
            'job_id': job['id'],
        }
        try:
            with open(HISTORY_FILE, 'a', encoding='utf-8') as f:
                f.write(json.dumps(rec) + '\n')
        except OSError:
            pass
        # Update in-memory averages
        name = job['name']
        self._averages.setdefault(name, []).append(float(job['runtime']))
        self._averages[name] = self._averages[name][-MAX_HISTORY_PER_SCRIPT:]

    def get_history(self, script_name=None, limit=50):
        """Return recent history records, optionally filtered by script name."""
        records = []
        if not HISTORY_FILE.exists():
            return records
        try:
            lines = HISTORY_FILE.read_text(encoding='utf-8').splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if script_name and rec.get('name') != script_name:
                        continue
                    records.append(rec)
                    if len(records) >= limit:
                        break
                except (json.JSONDecodeError, TypeError):
                    continue
        except OSError:
            pass
        return records

    def compare_script(self, script_name, limit=10):
        """Return recent runs for a script plus average, useful for comparison UI."""
        runs = self.get_history(script_name=script_name, limit=limit)
        avg = self._average_for(script_name)
        return {
            'name': script_name,
            'average_runtime': avg,
            'runs': runs,
            'count': len(runs),
        }

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------
    def _job_snapshot_for_persist(self, job):
        """Return a JSON-serializable copy safe for session restore (no live process state)."""
        return {
            'id': job['id'],
            'name': job['name'],
            'label': job.get('label'),
            'status': job['status'] if job['status'] not in ('running', 'cancelling', 'queued') else 'interrupted',
            'started': job.get('started'),
            'ended': job.get('ended') or stamp(),
            'runtime': job.get('runtime', 0),
            'cpu': job.get('cpu', 0),
            'memory_mb': job.get('memory_mb', 0),
            'peak_memory_mb': job.get('peak_memory_mb', 0),
            'exit_code': job.get('exit_code'),
            'stage': job.get('stage'),
            'progress': job.get('progress', 0),
            'quality': job.get('quality'),
            'output': (job.get('output') or [])[-MAX_LINES:],
            'events': (job.get('events') or [])[-MAX_LINES:],
            'artifacts': job.get('artifacts') or [],
            'stage_timings': job.get('stage_timings') or {},
            'historical_avg_seconds': job.get('historical_avg_seconds'),
            'arguments': job.get('arguments') or [],
        }

    def _auto_save_session(self):
        """Silently persist current jobs to last_session.json."""
        try:
            with self.lock:
                data = {
                    'saved_at': stamp(),
                    'max_concurrent': self.max_concurrent,
                    'jobs': [self._job_snapshot_for_persist(j) for j in self.jobs.values()],
                }
            SESSION_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError:
            pass

    def _restore_last_session(self):
        """Load last session on startup (finished/interrupted jobs only)."""
        if not SESSION_FILE.exists():
            return
        try:
            data = json.loads(SESSION_FILE.read_text(encoding='utf-8'))
            jobs = data.get('jobs') or []
            max_c = data.get('max_concurrent')
            if isinstance(max_c, int) and 1 <= max_c <= 32:
                self.max_concurrent = max_c
            for j in jobs:
                if not isinstance(j, dict) or not j.get('id'):
                    continue
                jid = j['id']
                # Never restore as running/queued
                status = j.get('status', 'interrupted')
                if status in ('running', 'cancelling', 'queued'):
                    status = 'interrupted'
                job = dict(
                    id=jid,
                    name=j.get('name', 'unknown'),
                    label=j.get('label', j.get('name', 'unknown')),
                    status=status,
                    started=j.get('started'),
                    ended=j.get('ended'),
                    runtime=j.get('runtime', 0),
                    cpu=j.get('cpu', 0),
                    memory_mb=j.get('memory_mb', 0),
                    peak_memory_mb=j.get('peak_memory_mb', 0),
                    exit_code=j.get('exit_code'),
                    stage=j.get('stage', '—'),
                    progress=j.get('progress', 0),
                    quality=j.get('quality'),
                    output=j.get('output') or [],
                    events=j.get('events') or [],
                    artifacts=j.get('artifacts') or [],
                    pid=None,
                    stage_started=None,
                    stage_timings=j.get('stage_timings') or {},
                    eta_seconds=None,
                    historical_avg_seconds=j.get('historical_avg_seconds'),
                    arguments=j.get('arguments') or [],
                )
                self.jobs[jid] = job
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    def save_session(self, name=None):
        """Save current workspace to a named session file (or auto name)."""
        with self.lock:
            data = {
                'saved_at': stamp(),
                'name': name or f'session-{stamp().replace(":", "").replace("+", "")}',
                'max_concurrent': self.max_concurrent,
                'jobs': [self._job_snapshot_for_persist(j) for j in self.jobs.values()],
            }
        safe_name = re.sub(r'[^\w\-.]', '_', data['name'])[:80]
        path = SESSIONS_DIR / f'{safe_name}.json'
        path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        # Also update last_session
        try:
            SESSION_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError:
            pass
        return {'ok': True, 'path': str(path), 'name': data['name'], 'jobs': len(data['jobs'])}

    def list_sessions(self):
        sessions = []
        for path in sorted(SESSIONS_DIR.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                sessions.append({
                    'name': data.get('name') or path.stem,
                    'saved_at': data.get('saved_at'),
                    'jobs': len(data.get('jobs') or []),
                    'file': path.name,
                })
            except (OSError, json.JSONDecodeError):
                continue
        return sessions

    def load_session(self, name_or_file):
        """Replace current jobs with a previously saved session."""
        # Resolve file
        candidate = SESSIONS_DIR / name_or_file
        if not candidate.exists():
            candidate = SESSIONS_DIR / f'{name_or_file}.json'
        if not candidate.exists():
            # try matching by name field
            for path in SESSIONS_DIR.glob('*.json'):
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if data.get('name') == name_or_file:
                        candidate = path
                        break
                except (OSError, json.JSONDecodeError):
                    continue
        if not candidate.exists():
            raise ValueError(f'Session not found: {name_or_file}')

        data = json.loads(candidate.read_text(encoding='utf-8'))
        with self.lock:
            # Refuse to load while jobs are active
            if any(j['status'] in ('running', 'cancelling', 'queued') for j in self.jobs.values()):
                raise ValueError('Cannot load session while jobs are active. Stop or wait for them first.')
            self.jobs.clear()
            self.job_scripts.clear()
            self.job_arguments.clear()
            self.queue.clear()
            max_c = data.get('max_concurrent')
            if isinstance(max_c, int) and 1 <= max_c <= 32:
                self.max_concurrent = max_c
            for j in data.get('jobs') or []:
                if not isinstance(j, dict) or not j.get('id'):
                    continue
                status = j.get('status', 'interrupted')
                if status in ('running', 'cancelling', 'queued'):
                    status = 'interrupted'
                jid = j['id']
                self.jobs[jid] = dict(
                    id=jid,
                    name=j.get('name', 'unknown'),
                    label=j.get('label', j.get('name', 'unknown')),
                    status=status,
                    started=j.get('started'),
                    ended=j.get('ended'),
                    runtime=j.get('runtime', 0),
                    cpu=j.get('cpu', 0),
                    memory_mb=j.get('memory_mb', 0),
                    peak_memory_mb=j.get('peak_memory_mb', 0),
                    exit_code=j.get('exit_code'),
                    stage=j.get('stage', '—'),
                    progress=j.get('progress', 0),
                    quality=j.get('quality'),
                    output=j.get('output') or [],
                    events=j.get('events') or [],
                    artifacts=j.get('artifacts') or [],
                    pid=None,
                    stage_started=None,
                    stage_timings=j.get('stage_timings') or {},
                    eta_seconds=None,
                    historical_avg_seconds=j.get('historical_avg_seconds'),
                    arguments=j.get('arguments') or [],
                )
            self._auto_save_session()
        return {'ok': True, 'jobs': len(self.jobs), 'name': data.get('name')}

    def clear_session(self):
        """Clear all jobs from the workspace (active jobs must be stopped first)."""
        with self.lock:
            if any(j['status'] in ('running', 'cancelling') for j in self.jobs.values()):
                raise ValueError('Stop running jobs before clearing the workspace.')
            # Cancel any remaining queued
            self.queue.clear()
            for j in self.jobs.values():
                if j['status'] == 'queued':
                    j['status'] = 'cancelled'
                    j['ended'] = stamp()
            self.jobs.clear()
            self.job_scripts.clear()
            self.job_arguments.clear()
            self._auto_save_session()
        return {'ok': True}

    # ------------------------------------------------------------------
    # Persistence of individual job snapshots
    # ------------------------------------------------------------------
    def _save(self, job):
        try:
            (RUN_DIR / f"{job['id']}.json").write_text(json.dumps(job, indent=2), encoding='utf-8')
        except OSError:
            pass

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(list(reversed(list(self.jobs.values())))))

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------
    def _artifact_path(self, job_id, artifact):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ValueError('Unknown job')
            if not isinstance(artifact, str) or artifact not in job['artifacts']:
                raise ValueError('Artifact was not reported by this job')
            roots = [ROOT.resolve(), *self.job_scripts.get(job_id, {}).get('output_dirs', [])]

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
            subprocess.Popen(
                ['explorer', f'/select,{path}'] if path.is_file() else ['explorer', str(path)]
            )
        elif sys.platform == 'darwin':
            subprocess.Popen(
                ['open', '-R', str(path)] if path.is_file() else ['open', str(path)]
            )
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
            if not job:
                raise ValueError('Unknown job')
            # Allow cancelling queued jobs
            if job['status'] == 'queued':
                if job_id in self.queue:
                    try:
                        self.queue.remove(job_id)
                    except ValueError:
                        pass
                job['status'] = 'cancelled'
                job['ended'] = stamp()
                self._event(job, 'cancelled', 'Removed from queue')
                self._auto_save_session()
                return {'ok': True}
            proc = self.processes.get(job_id)
            if not proc or job['status'] != 'running':
                raise ValueError('Job is not running')
            job['status'] = 'cancelling'
            self._event(job, 'warning', 'Cancellation requested')
        try:
            if psutil:
                parent = psutil.Process(proc.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        return {'ok': True}


def DateTime_parse(iso_str):
    """Parse ISO timestamp that may lack timezone."""
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError:
        return datetime.fromisoformat(iso_str).replace(tzinfo=timezone.utc)
