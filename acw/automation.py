"""Saved script groups and an in-process scheduler; never installs OS tasks."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import uuid
from datetime import datetime, timedelta, timezone


ACTIVE = {'queued', 'running', 'cancelling'}
INTERVALS = {'once': None, 'hourly': 3600, 'daily': 86400}


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError('Choose a valid start date and time.')
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Choose a valid start date and time.') from exc
    if instant.tzinfo is None:
        raise ValueError('Start time must include a timezone.')
    return instant.astimezone(timezone.utc)


class Automation:
    def __init__(self, engine, path):
        self.engine = engine
        self.path = Path(path)
        self.groups = {}
        self.schedules = {}
        self.runs = {}
        self.error = None
        self._stop = threading.Event()
        self._thread = None
        self._load()

    def _write(self, groups, schedules):
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps({'groups': list(groups.values()),
                                         'schedules': list(schedules.values())}, indent=2),
                             encoding='utf-8')
        temporary.replace(self.path)

    def _commit(self, groups, schedules):
        self._write(groups, schedules)
        self.groups, self.schedules = groups, schedules
        self.error = None

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Automation file must contain an object.')
            groups, schedules = {}, {}
            for key in ('groups', 'schedules'):
                if not isinstance(data.get(key, []), list):
                    raise ValueError(f'Automation {key} must be a list.')
            for group in data.get('groups', []):
                if not isinstance(group, dict) or not isinstance(group.get('id'), str):
                    raise ValueError('Invalid saved group.')
                self._validate_group(group.get('name'), group.get('scripts'), group.get('mode'))
                if not group['id'] or group['id'] in groups:
                    raise ValueError('Duplicate or empty group ID.')
                groups[group['id']] = group
            for schedule in data.get('schedules', []):
                if (not isinstance(schedule, dict) or not isinstance(schedule.get('id'), str)
                        or not schedule['id'] or schedule['id'] in schedules
                        or schedule.get('target_kind') not in ('script', 'group')
                        or not isinstance(schedule.get('target_id'), str)
                        or not isinstance(schedule.get('enabled'), bool)
                        or schedule.get('repeat') not in INTERVALS):
                    raise ValueError('Invalid saved schedule.')
                parse_time(schedule.get('next_run'))
                schedules[schedule['id']] = schedule
            self.groups, self.schedules = groups, schedules
        except (OSError, ValueError, TypeError) as exc:
            self.error = f'Unable to load automation settings: {exc}'

    @staticmethod
    def _validate_group(name, scripts, mode):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('Enter a group name of 1 to 80 characters.')
        if (not isinstance(scripts, list) or not 2 <= len(scripts) <= 64
                or any(not isinstance(script, str) or not script for script in scripts)
                or len(set(scripts)) != len(scripts)):
            raise ValueError('Select 2 to 64 different scripts for a group.')
        if mode not in ('sequential', 'parallel'):
            raise ValueError('Choose sequential or parallel execution.')

    def _check_scripts(self, scripts):
        for name in scripts:
            script = self.engine.registry.get(name)
            if not script or not self.engine._script_available(script):
                raise ValueError(f'Script {name!r} is unavailable. Reload or relink it first.')

    def _check_target(self, kind, target):
        if kind == 'script':
            self._check_scripts([target])
        elif kind == 'group' and target in self.groups:
            self._check_scripts(self.groups[target]['scripts'])
        else:
            raise ValueError('Select an available script or saved group.')

    def state(self):
        with self.engine.lock:
            groups = []
            for group in self.groups.values():
                item = dict(group)
                try:
                    self._check_scripts(group['scripts'])
                    item['available'] = True
                except ValueError as exc:
                    item['available'] = False
                    item['reason'] = str(exc)
                groups.append(item)
            return json.loads(json.dumps({'groups': groups,
                                          'schedules': list(self.schedules.values()),
                                          'runs': list(reversed(list(self.runs.values()))),
                                          'error': self.error}))

    def save_group(self, name, scripts, mode, group_id=None):
        self._validate_group(name, scripts, mode)
        with self.engine.lock:
            if group_id is not None and group_id not in self.groups:
                raise ValueError('Unknown group.')
            self._check_scripts(scripts)
            if any(group['name'].casefold() == name.strip().casefold() and key != group_id
                   for key, group in self.groups.items()):
                raise ValueError('A group with this name already exists.')
            group_id = group_id or uuid.uuid4().hex
            group = {'id': group_id, 'name': name.strip(), 'scripts': list(scripts), 'mode': mode}
            self._commit({**self.groups, group_id: group}, self.schedules)
            return dict(group)

    def delete_group(self, group_id):
        with self.engine.lock:
            if group_id not in self.groups:
                raise ValueError('Unknown group.')
            if any(run['group_id'] == group_id and run['status'] == 'running'
                   for run in self.runs.values()):
                raise ValueError('Stop the active group run before deleting it.')
            schedules = {key: ({**item, 'enabled': False, 'last_result': 'Group removed'}
                              if item['target_kind'] == 'group' and item['target_id'] == group_id
                              else item) for key, item in self.schedules.items()}
            self._commit({key: group for key, group in self.groups.items() if key != group_id}, schedules)
            return {'ok': True}

    def start_group(self, group_id):
        with self.engine.lock:
            self._check_target('group', group_id)
            if self._target_active('group', group_id):
                raise ValueError('This group already has an active run.')
            group = self.groups[group_id]
            run = {'id': uuid.uuid4().hex, 'group_id': group_id, 'name': group['name'],
                   'scripts': list(group['scripts']), 'mode': group['mode'], 'job_ids': [],
                   'status': 'running', 'started': utc_now().isoformat(), 'ended': None,
                   'message': ''}
            self.runs[run['id']] = run
            self._advance_run(run)
            # Keep all active runs and a bounded list of finished runs.
            finished = [key for key, item in self.runs.items() if item['status'] != 'running']
            for key in finished[:-100]:
                del self.runs[key]
            return json.loads(json.dumps(run))

    def _advance_run(self, run):
        statuses = [self.engine.jobs.get(job_id, {}).get('status', 'failed')
                    for job_id in run['job_ids']]
        if run['mode'] == 'sequential' and any(status not in ACTIVE | {'completed'} for status in statuses):
            run['status'] = 'cancelled' if 'cancelled' in statuses else 'failed'
            run['message'] = 'Remaining scripts stopped because a member did not complete successfully.'
        elif any(status in ACTIVE for status in statuses):
            return
        elif len(run['job_ids']) == len(run['scripts']):
            run['status'] = ('failed' if 'failed' in statuses else
                             'cancelled' if 'cancelled' in statuses else 'completed')
        else:
            pending = run['scripts'][len(run['job_ids']):]
            if run['mode'] == 'sequential':
                pending = pending[:1]
            try:
                for name in pending:
                    job_id = self.engine.start(name)['id']
                    self.engine.jobs[job_id]['group_name'] = run['name']
                    self.engine.jobs[job_id]['group_run_id'] = run['id']
                    run['job_ids'].append(job_id)
            except (ValueError, OSError) as exc:
                run['message'] = str(exc)
                self._cancel_members(run)
                run['status'] = 'failed'
        if run['status'] != 'running':
            run['ended'] = utc_now().isoformat()

    def _cancel_members(self, run):
        for job_id in run['job_ids']:
            if self.engine.jobs.get(job_id, {}).get('status') in ('queued', 'running'):
                self.engine.cancel(job_id)

    def cancel_group(self, run_id):
        with self.engine.lock:
            run = self.runs.get(run_id)
            if not run or run['status'] != 'running':
                raise ValueError('This group run is not active.')
            self._cancel_members(run)
            run.update(status='cancelled', ended=utc_now().isoformat(), message='Stopped by user.')
            return {'ok': True}

    def _target_active(self, kind, target):
        if kind == 'group':
            return any(run['group_id'] == target and (
                run['status'] == 'running' or any(
                    self.engine.jobs.get(job_id, {}).get('status') in ACTIVE
                    for job_id in run['job_ids'])) for run in self.runs.values())
        return any(job['name'] == target and job['status'] in ACTIVE for job in self.engine.jobs.values())

    def create_schedule(self, kind, target, start_at, repeat):
        instant = parse_time(start_at)
        if repeat not in INTERVALS:
            raise ValueError('Choose once, every hour, or every 24 hours.')
        if instant <= utc_now():
            raise ValueError('Choose a start date and time in the future.')
        with self.engine.lock:
            self._check_target(kind, target)
            schedule = {'id': uuid.uuid4().hex, 'target_kind': kind, 'target_id': target,
                        'target_name': self.groups[target]['name'] if kind == 'group'
                        else self.engine.registry[target]['label'],
                        'repeat': repeat, 'next_run': instant.isoformat(), 'enabled': True,
                        'last_run': None, 'last_result': 'Waiting'}
            self._commit(self.groups, {**self.schedules, schedule['id']: schedule})
            return dict(schedule)

    @staticmethod
    def _skip_past(schedule, now, reason):
        due = parse_time(schedule['next_run'])
        interval = INTERVALS[schedule['repeat']]
        if interval is None:
            schedule.update(enabled=False, last_result=reason)
        else:
            steps = int((now - due).total_seconds() // interval) + 1
            schedule.update(next_run=(due + timedelta(seconds=steps * interval)).isoformat(),
                            last_result=reason)

    def set_schedule_enabled(self, schedule_id, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('Enabled must be true or false.')
        with self.engine.lock:
            if schedule_id not in self.schedules:
                raise ValueError('Unknown schedule.')
            item = dict(self.schedules[schedule_id])
            if enabled:
                self._check_target(item['target_kind'], item['target_id'])
                if parse_time(item['next_run']) <= utc_now():
                    if item['repeat'] == 'once':
                        raise ValueError('This one-time schedule has passed. Create a new schedule.')
                    self._skip_past(item, utc_now(), 'Resumed; missed occurrences skipped')
            item['enabled'] = enabled
            self._commit(self.groups, {**self.schedules, schedule_id: item})
            return {'ok': True}

    def delete_schedule(self, schedule_id):
        with self.engine.lock:
            if schedule_id not in self.schedules:
                raise ValueError('Unknown schedule.')
            self._commit(self.groups, {key: item for key, item in self.schedules.items() if key != schedule_id})
            return {'ok': True}

    def tick(self, now=None):
        """Dispatch due occurrences once; called by the app-owned scheduler thread."""
        now = now or utc_now()
        with self.engine.lock:
            for run in list(self.runs.values()):
                if run['status'] == 'running':
                    self._advance_run(run)
            for schedule_id in list(self.schedules):
                schedule = self.schedules[schedule_id]
                if not schedule['enabled'] or parse_time(schedule['next_run']) > now:
                    continue
                item = dict(schedule)
                self._skip_past(item, now, 'Dispatching')
                item['last_run'] = now.isoformat()
                # Persist the consumed occurrence before starting any process.
                self._commit(self.groups, {**self.schedules, schedule_id: item})
                try:
                    if self._target_active(item['target_kind'], item['target_id']):
                        item['last_result'] = 'Skipped: target already running'
                    elif item['target_kind'] == 'group':
                        run = self.start_group(item['target_id'])
                        item['last_result'] = f"Started group run {run['id'][:8]}"
                    else:
                        job = self.engine.start(item['target_id'])
                        item['last_result'] = f"Started job {job['id']}"
                except (ValueError, OSError) as exc:
                    item['last_result'] = f'Failed: {exc}'
                self._commit(self.groups, {**self.schedules, schedule_id: item})

    def start(self):
        with self.engine.lock:
            if self._thread and self._thread.is_alive():
                return
            now = utc_now()
            schedules = {key: dict(item) for key, item in self.schedules.items()}
            for item in schedules.values():
                if item['enabled'] and parse_time(item['next_run']) <= now:
                    self._skip_past(item, now, 'Missed while app was closed')
            if schedules != self.schedules:
                self._commit(self.groups, schedules)
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name='acw-scheduler')
            self._thread.start()

    def _loop(self):
        while not self._stop.wait(0.5):
            try:
                self.tick()
            except Exception as exc:
                with self.engine.lock:
                    self.error = f'Scheduler error: {exc}'

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
