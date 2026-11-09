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
REGISTERED_SCRIPTS_FILE = RUN_DIR / 'registered_scripts.json'
DEFAULT_MAX_CONCURRENT = 4
MAX_LINES = 250
MAX_RUNS = 200
MAX_HISTORY_PER_SCRIPT = 30
METADATA_LINES = 20
COMMAND_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')
PYTHON_HEADER_METADATA = re.compile(
    r'^\s*#\s*ACW_(COMMAND|DESCRIPTION|LABEL|OUTPUT_DIR):\s*(.*?)\s*$',
    re.IGNORECASE,
)
BATCH_HEADER_METADATA = re.compile(
    r'^\s*(?:REM\b\s*|::\s*)ACW_(COMMAND|DESCRIPTION|LABEL|OUTPUT_DIR):\s*(.*?)\s*$',
    re.IGNORECASE,
)
SINGLE_HEADER_KEYS = {'command', 'description', 'label'}
UNSAFE_BATCH_PATH_CHARACTERS = set('%"!^&|<>()\r\n')
OPENABLE_ARTIFACT_EXTENSIONS = {
    '.7z', '.bmp', '.csv', '.docx', '.gif', '.jpeg', '.jpg', '.json',
    '.log', '.md', '.pdf', '.png', '.pptx', '.rar', '.txt', '.webp',
    '.xlsx', '.zip',
}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def parse_acw_header(path):
    """Parse ACW metadata from a supported script's first metadata lines."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in ('.py', '.bat', '.cmd'):
        raise ValueError('Choose a Python, .bat, or .cmd script.')
    try:
        with path.open(encoding='utf-8-sig', errors='replace') as source:
            lines = [source.readline() for _ in range(METADATA_LINES)]
    except OSError as exc:
        raise ValueError(f'Unable to read script header: {exc}') from exc

    metadata = {}
    seen = set()
    pattern = (
        PYTHON_HEADER_METADATA
        if suffix == '.py'
        else BATCH_HEADER_METADATA
    )
    for line in lines:
        match = pattern.match(line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key in SINGLE_HEADER_KEYS and key in seen:
            raise ValueError(f'Duplicate ACW_{key.upper()} declaration.')
        seen.add(key)
        if key == 'output_dir':
            metadata.setdefault(key, []).append(value)
        else:
            metadata[key] = value

    if not metadata:
        if suffix in ('.bat', '.cmd'):
            raise ValueError(
                f'{path.name} must define ACW_COMMAND and ACW_DESCRIPTION '
                f'in its first {METADATA_LINES} lines.'
            )
        return {}
    if not metadata.get('command') or not metadata.get('description'):
        raise ValueError(
            f'{path.name} must define ACW_COMMAND and ACW_DESCRIPTION '
            f'in its first {METADATA_LINES} lines.'
        )
    if any(not output_dir for output_dir in metadata.get('output_dir', [])):
        raise ValueError(f'{path.name} has an empty ACW_OUTPUT_DIR declaration.')
    if not COMMAND_PATTERN.fullmatch(metadata['command']):
        raise ValueError(
            f'{path.name} has invalid ACW_COMMAND {metadata["command"]!r}; '
            'use lowercase letters, numbers, and hyphens.'
        )
    return metadata


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.audit_session_job_ids = set()
        self.processes = {}
        self.job_scripts = {}
        self.job_arguments = {}
        self.queue = deque()  # job_ids waiting for a slot
        self.max_concurrent = DEFAULT_MAX_CONCURRENT
        RUN_DIR.mkdir(exist_ok=True)
        SESSIONS_DIR.mkdir(exist_ok=True)
        self.custom_scripts = self._load_registered_scripts()
        self._refresh_registered_metadata()
        self.registry = self._build_registry(self._discover_scripts())
        self._averages = {}  # script_name -> list of recent runtimes
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
            metadata = parse_acw_header(path)
            if not metadata:
                continue
            name = metadata['command']
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
                'name': name,
                'label': label,
                'path': resolved,
                'working_directory': ROOT.resolve(),
                'description': metadata['description'],
                'registered': False,
                'kind': 'python',
                'output_dirs': [
                    (ROOT / output_dir).resolve()
                    if not Path(output_dir).expanduser().is_absolute()
                    else Path(output_dir).expanduser().resolve()
                    for output_dir in output_dirs
                ],
            }
        return registry

    def _load_registered_scripts(self):
        if not REGISTERED_SCRIPTS_FILE.exists():
            return {}
        try:
            data = json.loads(REGISTERED_SCRIPTS_FILE.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f'Unable to read registered scripts: {exc}') from exc
        if not isinstance(data, dict):
            raise ValueError('Registered scripts file must contain an object.')

        scripts = {}
        for name, item in data.items():
            if (
                not isinstance(name, str)
                or not COMMAND_PATTERN.fullmatch(name)
                or not isinstance(item, dict)
                or not isinstance(item.get('path'), str)
                or not isinstance(item.get('working_directory'), str)
                or not isinstance(item.get('label'), str)
            ):
                raise ValueError(f'Invalid registered script entry: {name!r}.')
            path = Path(item['path']).expanduser()
            working_directory = Path(item['working_directory']).expanduser()
            kind = item.get('kind', 'python')
            if (
                not path.is_absolute()
                or path.suffix.lower() not in ('.py', '.bat', '.cmd')
                or not working_directory.is_absolute()
                or kind not in ('python', 'batch')
                or (kind == 'python' and path.suffix.lower() != '.py')
                or (kind == 'batch' and path.suffix.lower() not in ('.bat', '.cmd'))
                or not isinstance(item.get('output_dirs', []), list)
                or any(not isinstance(directory, str) for directory in item.get('output_dirs', []))
            ):
                raise ValueError(f'Invalid paths for registered script {name!r}.')
            scripts[name] = {
                'name': name,
                'label': item['label'],
                'path': path,
                'working_directory': working_directory,
                'description': item.get('description') or str(path),
                'registered': True,
                'kind': kind,
                'output_dirs': [
                    (ROOT / output_dir).resolve()
                    if not Path(output_dir).expanduser().is_absolute()
                    else Path(output_dir).expanduser().resolve()
                    for output_dir in item.get('output_dirs', [])
                ],
            }
        return scripts

    def _save_registered_scripts(self, scripts):
        data = {
            name: {
                'label': item['label'],
                'path': str(item['path']),
                'working_directory': str(item['working_directory']),
                'description': item['description'],
                'kind': item.get('kind', 'python'),
                'output_dirs': [
                    str(directory) for directory in item.get('output_dirs', [])
                ],
            }
            for name, item in scripts.items()
        }
        temporary = REGISTERED_SCRIPTS_FILE.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
        temporary.replace(REGISTERED_SCRIPTS_FILE)

    def _refresh_registered_metadata(self):
        updated = dict(self.custom_scripts)
        changed = False
        for name, old_item in self.custom_scripts.items():
            path = Path(old_item['path'])
            if not path.is_file():
                continue
            try:
                metadata = parse_acw_header(path)
            except ValueError:
                continue
            if metadata and metadata.get('command') != name:
                continue
            item = dict(old_item)
            if metadata:
                item['label'] = metadata.get('label') or name.replace('-', ' ').title()
                item['description'] = metadata['description']
                item['output_dirs'] = self._output_directories(metadata.get('output_dir', []))
            if item != old_item:
                updated[name] = item
                changed = True
        if changed:
            self._save_registered_scripts(updated)
            self.custom_scripts = updated

    def _build_registry(self, discovered):
        registry = dict(discovered)
        for name, item in self.custom_scripts.items():
            if name in registry:
                continue
            registry[name] = item
        return registry

    @staticmethod
    def _script_available(script):
        path = Path(script['path']).expanduser()
        working_directory = Path(script['working_directory']).expanduser()
        kind = script.get('kind', 'python')
        expected_suffixes = ('.py',) if kind == 'python' else ('.bat', '.cmd')
        if (
            path.suffix.lower() not in expected_suffixes
            or not path.is_file()
            or not working_directory.is_dir()
        ):
            return False
        if kind == 'batch' and os.name != 'nt':
            return False
        if kind == 'batch' and set(str(path.resolve())) & UNSAFE_BATCH_PATH_CHARACTERS:
            return False
        if script.get('registered'):
            try:
                metadata = parse_acw_header(path)
            except ValueError:
                return False
            if kind == 'batch' or metadata:
                return metadata.get('command') == script.get('name')
        return True

    @staticmethod
    def _output_directories(values):
        return [
            (ROOT / output_dir).resolve()
            if not Path(output_dir).expanduser().is_absolute()
            else Path(output_dir).expanduser().resolve()
            for output_dir in values
        ]

    @staticmethod
    def _batch_launch_command(path):
        path = str(Path(path).resolve())
        if set(path) & UNSAFE_BATCH_PATH_CHARACTERS:
            raise ValueError(
                'Batch script paths containing cmd.exe metacharacters are not supported.'
            )
        executable = Engine._batch_cmd_executable()
        return f'"{executable}" /d /s /c ""{path}""'

    @staticmethod
    def _batch_cmd_executable():
        if os.name != 'nt':
            raise ValueError('Windows batch scripts can only run on Windows.')
        system_root = os.environ.get('SystemRoot')
        if not system_root:
            raise RuntimeError('SystemRoot is not set; cannot locate cmd.exe.')
        executable = Path(system_root) / 'System32' / 'cmd.exe'
        if not executable.is_file():
            raise RuntimeError(f'Windows command interpreter not found: {executable}')
        return str(executable)

    @staticmethod
    def _validate_script_path(path):
        if not isinstance(path, str) or '\0' in path:
            raise ValueError('Choose a Python, .bat, or .cmd script file.')
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError('The selected script does not exist or cannot be resolved.') from exc
        if not resolved.is_file() or resolved.suffix.lower() not in ('.py', '.bat', '.cmd'):
            raise ValueError('The selected file must be a Python, .bat, or .cmd script.')
        return resolved

    @staticmethod
    def _validate_working_directory(directory):
        if not isinstance(directory, str) or '\0' in directory:
            raise ValueError('Choose an existing working directory.')
        try:
            resolved = Path(directory).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError('The selected working directory does not exist or cannot be resolved.') from exc
        if not resolved.is_dir():
            raise ValueError('The selected working directory must be a folder.')
        return resolved

    def _registered_script_summary(self, name, item):
        conflict = self.registry.get(name) is not item
        available = not conflict and self._script_available(item)
        summary = {
            'name': name,
            'label': item['label'],
            'description': item['description'],
            'kind': item.get('kind', 'python'),
            'path': str(item['path']),
            'working_directory': str(item['working_directory']),
            'output_dirs': [str(path) for path in item.get('output_dirs', [])],
            'available': available,
        }
        if conflict:
            summary['reason'] = 'Command name conflicts with a script in the built-in catalog.'
        elif not available:
            if item.get('kind') == 'batch' and os.name != 'nt':
                summary['reason'] = 'Batch scripts can only run on Windows.'
            elif not Path(item['path']).is_file():
                summary['reason'] = 'Script file is unavailable.'
            elif not Path(item['working_directory']).is_dir():
                summary['reason'] = 'Working directory is unavailable.'
            elif (
                item.get('kind') == 'batch'
                and set(str(Path(item['path']).resolve())) & UNSAFE_BATCH_PATH_CHARACTERS
            ):
                summary['reason'] = (
                    'Batch script paths containing cmd.exe metacharacters are not supported.'
                )
            elif item.get('registered'):
                try:
                    metadata = parse_acw_header(item['path'])
                except ValueError as exc:
                    summary['reason'] = str(exc)
                else:
                    summary['reason'] = (
                        'ACW_COMMAND no longer matches the registered command.'
                        if metadata.get('command') != name
                        else 'Batch script is unavailable.'
                    )
            else:
                summary['reason'] = 'Script file or working directory is unavailable.'
        return summary

    def list_registered_scripts(self):
        with self.lock:
            return [
                self._registered_script_summary(name, item)
                for name, item in self.custom_scripts.items()
            ]

    def register_script(
        self, name, path, working_directory, label=None, description=None, trusted=False
    ):
        if trusted is not True:
            raise ValueError('Confirm that you trust this script before registering it.')
        if not isinstance(name, str) or not COMMAND_PATTERN.fullmatch(name):
            raise ValueError('Script command name must use lowercase letters, numbers, and hyphens, and start with a letter.')
        resolved = self._validate_script_path(path)
        kind = 'python' if resolved.suffix.lower() == '.py' else 'batch'
        if kind == 'batch':
            unsafe = set(str(resolved)) & UNSAFE_BATCH_PATH_CHARACTERS
            if unsafe:
                raise ValueError(
                    'Batch script paths containing cmd.exe metacharacters are not supported.'
                )
        metadata = parse_acw_header(resolved)
        if kind == 'batch' or metadata:
            if metadata.get('command') != name:
                raise ValueError('The command name must match ACW_COMMAND in the script header.')
        resolved_label = metadata.get('label') or label
        resolved_description = metadata.get('description') or description
        if (
            not isinstance(resolved_label, str)
            or not resolved_label.strip()
            or len(resolved_label.strip()) > 80
        ):
            raise ValueError('Enter a display name of 1 to 80 characters.')
        if not isinstance(resolved_description, str) or not resolved_description.strip():
            resolved_description = f'Registered script · {resolved}'
        if len(resolved_description.strip()) > 500:
            raise ValueError('Script description must be 500 characters or fewer.')
        cwd = self._validate_working_directory(working_directory)
        discovered = self._discover_scripts()
        with self.lock:
            if name in discovered or name in self.custom_scripts:
                raise ValueError(f'A script named {name!r} is already available.')
            if any(Path(item['path']).resolve() == resolved for item in (*discovered.values(), *self.custom_scripts.values())):
                raise ValueError('This script is already registered.')
            item = {
                'name': name,
                'label': resolved_label.strip(),
                'path': resolved,
                'working_directory': cwd,
                'description': resolved_description.strip(),
                'registered': True,
                'kind': kind,
                'output_dirs': self._output_directories(metadata.get('output_dir', [])),
            }
            updated = {**self.custom_scripts, name: item}
            self._save_registered_scripts(updated)
            self.custom_scripts = updated
            self.registry = self._build_registry(discovered)
            return self._registered_script_summary(name, item)

    def relink_registered_script(self, name, path):
        resolved = self._validate_script_path(path)
        with self.lock:
            if name not in self.custom_scripts:
                raise ValueError('Unknown registered script.')
            discovered = self._discover_scripts()
            old = self.custom_scripts[name]
            kind = 'python' if resolved.suffix.lower() == '.py' else 'batch'
            if kind != old.get('kind', 'python'):
                raise ValueError('Relink the script to a file of the same kind.')
            metadata = parse_acw_header(resolved)
            if (kind == 'batch' or metadata) and metadata.get('command') != name:
                raise ValueError('The command name must match ACW_COMMAND in the script header.')
            if kind == 'batch' and set(str(resolved)) & UNSAFE_BATCH_PATH_CHARACTERS:
                raise ValueError('Batch script paths containing cmd.exe metacharacters are not supported.')
            if any(
                other_name != name and Path(item['path']).resolve() == resolved
                for other_name, item in {**discovered, **self.custom_scripts}.items()
            ):
                raise ValueError('This script is already registered under another name.')
            item = dict(old)
            if metadata:
                item['label'] = metadata.get('label') or name.replace('-', ' ').title()
                item['description'] = metadata['description']
                item['output_dirs'] = self._output_directories(metadata.get('output_dir', []))
            if Path(old['working_directory']) == Path(old['path']).parent:
                item['working_directory'] = resolved.parent
            item['path'] = resolved
            updated = {**self.custom_scripts, name: item}
            self._save_registered_scripts(updated)
            self.custom_scripts = updated
            self.registry = self._build_registry(discovered)
            return self._registered_script_summary(name, item)

    def remove_registered_script(self, name):
        with self.lock:
            if name not in self.custom_scripts:
                raise ValueError('Unknown registered script.')
            discovered = self._discover_scripts()
            updated = {key: item for key, item in self.custom_scripts.items() if key != name}
            self._save_registered_scripts(updated)
            self.custom_scripts = updated
            self.registry = self._build_registry(discovered)
        return {'ok': True}

    def catalog(self):
        with self.lock:
            return self._catalog_entries()

    def _catalog_entries(self):
        return [
            {
                'name': name,
                'label': item['label'],
                'description': item['description'],
                'kind': item.get('kind', 'python'),
            }
            for name, item in self.registry.items()
            if self._script_available(item)
        ]

    def reload_catalog(self):
        discovered = self._discover_scripts()
        with self.lock:
            self._refresh_registered_metadata()
            self.registry = self._build_registry(discovered)
            return self._catalog_entries()

    def edit_script(self, name):
        with self.lock:
            if not isinstance(name, str) or name not in self.registry:
                raise ValueError('Unknown script. Reload the catalog and select an approved script.')
            path = Path(self.registry[name]['path']).resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in ('.py', '.bat', '.cmd'):
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
            script = self.registry[name]
            if not self._script_available(script):
                raise ValueError('Selected script or working directory is unavailable.')
            kind = script.get('kind', 'python')
            if kind == 'batch' and arguments:
                raise ValueError(
                    'Batch scripts do not accept runtime arguments yet; run the batch file without arguments.'
                )

            job_id = uuid.uuid4().hex[:10]
            avg = self._average_for(name)
            job = dict(
                id=job_id,
                name=name,
                label=script['label'],
                kind=kind,
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
            self.audit_session_job_ids.add(job_id)
            self.job_scripts[job_id] = script
            self.job_arguments[job_id] = tuple(arguments)
            self._event(job, 'queued', 'Job accepted')

            # Prune old finished jobs if over limit
            if len(self.jobs) > MAX_RUNS:
                for key in list(self.jobs):
                    if self.jobs[key]['status'] not in ('queued', 'running', 'cancelling'):
                        del self.jobs[key]
                        self.audit_session_job_ids.discard(key)
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
        # Reserve the slot before the worker can compete for the scheduler lock.
        job['status'] = 'running'
        try:
            threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        except Exception as exc:
            job['status'] = 'failed'
            job['ended'] = stamp()
            self._event(job, 'error', f'Unable to start worker: {exc}')
            self._save(job)
            self._auto_save_session()

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

    def _record_stage_timing(self, job):
        stage = job.get('stage')
        stage_started = job.get('stage_started')
        if not stage or not stage_started:
            return
        try:
            started_at = DateTime_parse(stage_started)
            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
        except (AttributeError, TypeError, ValueError) as exc:
            self._event(job, 'warning', f'Unable to record timing for stage {stage}: {exc}')
            return
        job['stage_timings'][stage] = round(duration, 2)

    def _run(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            if job['status'] == 'cancelling':
                job['status'] = 'cancelled'
                job['ended'] = stamp()
                self._event(job, 'cancelled', 'Cancelled before process start')
                self._save(job)
                self._auto_save_session()
                self._drain_queue()
                return
            if job['status'] not in ('queued', 'running'):
                return
            script = self.job_scripts[job_id]
            path = Path(script['path']).resolve()
            working_directory = Path(script.get('working_directory', ROOT)).resolve()
            arguments = self.job_arguments[job_id]
            kind = script.get('kind', 'python')
            if not self._script_available(script):
                job['status'] = 'failed'
                job['ended'] = stamp()
                self._event(job, 'error', 'Approved script missing or invalid')
                self._save(job)
                self._auto_save_session()
                self._drain_queue()
                return
            job['status'] = 'running'
            job['started'] = stamp()
            job['stage_started'] = stamp()
            self._event(job, 'running', 'Process starting')

        try:
            command = [sys.executable, '-u', str(path), *arguments]
            executable = None
            if kind == 'batch':
                # cmd.exe uses non-CRT parsing, so pass only this fixed command line verbatim.
                command = self._batch_launch_command(path)
                executable = self._batch_cmd_executable()
            proc = subprocess.Popen(
                command,
                executable=executable,
                cwd=str(working_directory),
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
                cancel_requested = job['status'] == 'cancelling'
            if cancel_requested:
                self._terminate_process_tree(proc)

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
            if not reader.is_alive() and proc.stdout:
                proc.stdout.close()
            with self.lock:
                job = self.jobs.get(job_id)
                if not job:
                    return
                job['runtime'] = round(time.monotonic() - start, 2)
                job['exit_code'] = proc.returncode
                job['ended'] = stamp()
                job['eta_seconds'] = None
                self._record_stage_timing(job)
                job['stage_started'] = None
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
                    self._record_stage_timing(job)
                    job['stage_started'] = None
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
                                self._record_stage_timing(job)
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
            'kind': job.get('kind', 'python'),
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

    def audit_current_session(self):
        """Return jobs started during this app launch, excluding restored workspace jobs."""
        with self.lock:
            current_jobs = [
                job for job_id, job in self.jobs.items()
                if job_id in self.audit_session_job_ids
            ]
            return json.loads(json.dumps(list(reversed(current_jobs))))

    def search_audit(self, query, limit=100):
        """Search saved job snapshots and history records for prior runs."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError('Enter a search term to search stored runs.')
        try:
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError('Search limit must be a positive integer.') from exc
        if limit < 1:
            raise ValueError('Search limit must be a positive integer.')
        limit = min(limit, 100)
        terms = query.casefold().split()

        with self.lock:
            current_job_ids = set(self.audit_session_job_ids)

        records = {}
        warnings = []

        def add_record(record):
            if not isinstance(record, dict):
                return
            job_id = record.get('id') or record.get('job_id')
            if not isinstance(job_id, str) or not job_id or job_id in current_job_ids:
                return
            normalized = dict(record)
            normalized.setdefault('id', job_id)
            normalized.setdefault('status', 'completed')
            records.setdefault(job_id, normalized)

        def add_session_file(path):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f'{path.name}: {exc}')
                return
            if isinstance(data, dict):
                session_jobs = data.get('jobs') or []
                if isinstance(session_jobs, list):
                    for record in session_jobs:
                        add_record(record)
                else:
                    warnings.append(f'{path.name}: jobs must be a list.')

        for path in RUN_DIR.glob('*.json'):
            if path.name in (SESSION_FILE.name, REGISTERED_SCRIPTS_FILE.name):
                continue
            try:
                add_record(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f'{path.name}: {exc}')

        if SESSION_FILE.exists():
            add_session_file(SESSION_FILE)
        if SESSIONS_DIR.exists():
            for path in SESSIONS_DIR.glob('*.json'):
                add_session_file(path)

        if HISTORY_FILE.exists():
            try:
                for line in HISTORY_FILE.read_text(encoding='utf-8').splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        warnings.append(f'{HISTORY_FILE.name}: skipped an invalid record.')
                        continue
                    add_record(record)
            except OSError as exc:
                warnings.append(f'{HISTORY_FILE.name}: {exc}')

        matching = []
        for record in records.values():
            searchable = json.dumps(record, ensure_ascii=False, default=str).casefold()
            if all(term in searchable for term in terms):
                matching.append(record)
        matching.sort(
            key=lambda record: str(record.get('ended') or record.get('started') or ''),
            reverse=True,
        )
        return {'jobs': matching[:limit], 'count': len(matching), 'warnings': warnings}

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
            'kind': job.get('kind', 'python'),
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
                    kind=j.get('kind', 'python'),
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
            self.audit_session_job_ids.clear()
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
                    kind=j.get('kind', 'python'),
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
            self.audit_session_job_ids.clear()
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
            if job['status'] != 'running':
                raise ValueError('Job is not running')
            job['status'] = 'cancelling'
            self._event(job, 'warning', 'Cancellation requested')
        if proc:
            self._terminate_process_tree(proc)
        return {'ok': True}

    @staticmethod
    def _terminate_process_tree(proc):
        if psutil:
            try:
                parent = psutil.Process(proc.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
            except psutil.Error:
                pass
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass


def DateTime_parse(iso_str):
    """Parse ISO timestamp that may lack timezone."""
    if iso_str.endswith('Z'):
        iso_str = iso_str[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(iso_str)
    except ValueError:
        return datetime.fromisoformat(iso_str).replace(tzinfo=timezone.utc)
