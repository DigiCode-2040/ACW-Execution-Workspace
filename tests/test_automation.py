import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import acw.engine as engine_module
import acw.automation as automation_module
from acw.engine import Engine


class AutomationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('first', 'second', 'third'):
            (scripts / f'{name}.py').write_text(
                f'# ACW_COMMAND: {name}\n# ACW_DESCRIPTION: Test {name}\nprint("{name}")\n',
                encoding='utf-8',
            )
        runs = self.root / 'runs'
        directories = patch.multiple(
            engine_module, ROOT=self.root, SCRIPT_DIR=scripts, RUN_DIR=runs,
            SESSIONS_DIR=self.root / 'sessions', HISTORY_FILE=runs / 'history.jsonl',
            SESSION_FILE=runs / 'last_session.json', REGISTERED_SCRIPTS_FILE=runs / 'registered_scripts.json',
        )
        directories.start()
        self.addCleanup(directories.stop)
        self.engine = Engine()
        self.automation = self.engine.automation
        self.addCleanup(self.automation.stop)
        self.now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.clock = patch.object(automation_module, 'utc_now', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.thread_patch = patch.object(engine_module.threading, 'Thread')
        self.thread_patch.start()
        self.addCleanup(self.thread_patch.stop)

    def group(self, mode='sequential', scripts=None):
        return self.automation.save_group('Test group', scripts or ['first', 'second', 'third'], mode)

    def schedule(self, kind='script', target='first', repeat='once'):
        return self.automation.create_schedule(kind, target, (self.now + timedelta(seconds=10)).isoformat(), repeat)

    def test_group_create_edit_and_delete_persist(self):
        group = self.group()
        self.assertEqual(Engine().automation.state()['groups'][0]['scripts'], ['first', 'second', 'third'])
        self.automation.save_group('Renamed', ['third', 'first'], 'parallel', group['id'])
        self.assertEqual(Engine().automation.groups[group['id']]['mode'], 'parallel')
        self.automation.delete_group(group['id'])
        self.assertEqual(Engine().automation.groups, {})

    def test_group_validation_and_missing_member_preflight(self):
        for name, scripts, mode in [('', ['first', 'second'], 'parallel'),
                                     ('x', ['first'], 'parallel'),
                                     ('x', ['first', 'first'], 'parallel'),
                                     ('x', ['first', 'second'], 'other'),
                                     ('x', ['first', 'missing'], 'sequential')]:
            with self.subTest(name=name, scripts=scripts, mode=mode), self.assertRaises(ValueError):
                self.automation.save_group(name, scripts, mode)
        group = self.group()
        (self.root / 'scripts' / 'second.py').unlink()
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.automation.start_group(group['id'])
        self.assertEqual(self.engine.jobs, {})
        self.assertFalse(self.automation.state()['groups'][0]['available'])

    def test_sequential_execution_preserves_order(self):
        group = self.group()
        result = self.automation.start_group(group['id'])
        run = self.automation.runs[result['id']]
        self.assertEqual(len(run['job_ids']), 1)
        for index, name in enumerate(group['scripts']):
            self.assertEqual(len(run['job_ids']), index + 1)
            job = self.engine.jobs[run['job_ids'][index]]
            self.assertEqual(job['name'], name)
            self.assertEqual(job['group_name'], 'Test group')
            self.automation.tick(self.now)
            self.assertEqual(len(run['job_ids']), index + 1)
            job['status'] = 'completed'
            self.automation.tick(self.now)
        self.assertEqual(run['status'], 'completed')

    def test_sequential_failure_and_cancellation_stop_remaining_members(self):
        group = self.group()
        for status in ('failed', 'cancelled'):
            with self.subTest(status=status):
                result = self.automation.start_group(group['id'])
                run = self.automation.runs[result['id']]
                self.engine.jobs[run['job_ids'][0]]['status'] = status
                self.automation.tick(self.now)
                self.assertEqual(run['status'], status)
                self.assertEqual(len(run['job_ids']), 1)

    def test_parallel_execution_obeys_global_concurrency_limit(self):
        self.engine.set_max_concurrent(1)
        run = self.automation.start_group(self.group('parallel')['id'])
        self.assertEqual(self.engine._running_count(), 1)
        self.assertEqual(len(self.engine.queue), 2)
        for job_id in run['job_ids']:
            self.assertEqual(self.engine.jobs[job_id]['status'], 'running')
            with self.engine.lock:
                self.engine.jobs[job_id]['status'] = 'completed'
                self.engine._drain_queue()
                self.assertLessEqual(self.engine._running_count(), 1)
        self.automation.tick(self.now)
        self.assertEqual(self.automation.runs[run['id']]['status'], 'completed')

    def test_stop_group_cancels_queued_members_and_prevents_overlap(self):
        self.engine.set_max_concurrent(1)
        group = self.group('parallel')
        run = self.automation.start_group(group['id'])
        with self.assertRaisesRegex(ValueError, 'active run'):
            self.automation.start_group(group['id'])
        self.automation.cancel_group(run['id'])
        self.assertFalse(self.engine.queue)
        self.assertEqual([self.engine.jobs[key]['status'] for key in run['job_ids']],
                         ['cancelling', 'cancelled', 'cancelled'])
        with self.assertRaisesRegex(ValueError, 'active run'):
            self.automation.start_group(group['id'])

    def test_editing_definition_does_not_change_active_run(self):
        group = self.group()
        run = self.automation.start_group(group['id'])
        self.automation.save_group('Changed', ['second', 'first'], 'parallel', group['id'])
        current = self.automation.runs[run['id']]
        self.assertEqual(current['scripts'], ['first', 'second', 'third'])
        self.assertEqual(current['mode'], 'sequential')
        with self.assertRaisesRegex(ValueError, 'active group'):
            self.automation.delete_group(group['id'])

    def test_group_deletion_pauses_referencing_schedules(self):
        group = self.group()
        schedule = self.schedule('group', group['id'], 'hourly')
        self.automation.delete_group(group['id'])
        self.assertFalse(self.automation.schedules[schedule['id']]['enabled'])
        self.assertFalse(Engine().automation.schedules[schedule['id']]['enabled'])

    def test_once_schedule_dispatches_exactly_once_and_persists_consumption(self):
        schedule = self.schedule()
        self.automation.tick(self.now + timedelta(seconds=9))
        self.assertFalse(self.engine.jobs)
        self.automation.tick(self.now + timedelta(seconds=10))
        self.automation.tick(self.now + timedelta(seconds=11))
        self.assertEqual(len(self.engine.jobs), 1)
        self.assertFalse(self.automation.schedules[schedule['id']]['enabled'])
        self.assertFalse(Engine().automation.schedules[schedule['id']]['enabled'])

    def test_recurring_schedule_keeps_cadence_without_catchup_burst(self):
        for repeat, interval in [('hourly', 3600), ('daily', 86400)]:
            with self.subTest(repeat=repeat):
                self.engine.jobs.clear()
                self.automation.schedules.clear()
                schedule = self.schedule(repeat=repeat)
                due = self.now + timedelta(seconds=10)
                self.automation.tick(due + timedelta(seconds=3 * interval + 5))
                self.assertEqual(len(self.engine.jobs), 1)
                next_run = automation_module.parse_time(self.automation.schedules[schedule['id']]['next_run'])
                self.assertEqual(next_run, due + timedelta(seconds=4 * interval))

    def test_due_group_schedule_uses_sequential_group_execution(self):
        group = self.group()
        schedule = self.schedule('group', group['id'])
        self.automation.tick(self.now + timedelta(seconds=10))
        self.assertEqual(len(self.automation.runs), 1)
        self.assertEqual(len(self.engine.jobs), 1)
        self.assertIn('Started group', self.automation.schedules[schedule['id']]['last_result'])

    def test_scheduled_target_does_not_overlap_existing_run(self):
        for kind in ('script', 'group'):
            with self.subTest(kind=kind):
                target = 'first' if kind == 'script' else self.group()['id']
                schedule = self.schedule(kind, target)
                if kind == 'script':
                    self.engine.start(target)
                else:
                    self.automation.start_group(target)
                count = len(self.engine.jobs)
                self.automation.tick(self.now + timedelta(seconds=10))
                self.assertEqual(len(self.engine.jobs), count)
                self.assertIn('already running', self.automation.schedules[schedule['id']]['last_result'])

    def test_missing_scheduled_script_records_failure(self):
        schedule = self.schedule()
        (self.root / 'scripts' / 'first.py').unlink()
        self.automation.tick(self.now + timedelta(seconds=10))
        self.assertFalse(self.engine.jobs)
        self.assertTrue(self.automation.schedules[schedule['id']]['last_result'].startswith('Failed:'))

    def test_persistence_failure_prevents_dispatch_and_preserves_occurrence(self):
        schedule = self.schedule()
        with patch.object(self.automation, '_write', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.automation.tick(self.now + timedelta(seconds=10))
        self.assertFalse(self.engine.jobs)
        self.assertTrue(self.automation.schedules[schedule['id']]['enabled'])
        self.automation.tick(self.now + timedelta(seconds=11))
        self.assertEqual(len(self.engine.jobs), 1)

    def test_missing_later_group_member_stops_sequence(self):
        run = self.automation.start_group(self.group()['id'])
        self.engine.jobs[run['job_ids'][0]]['status'] = 'completed'
        (self.root / 'scripts' / 'second.py').unlink()
        self.automation.tick(self.now)
        current = self.automation.runs[run['id']]
        self.assertEqual(current['status'], 'failed')
        self.assertEqual(len(current['job_ids']), 1)
        self.assertIn('unavailable', current['message'])

    def test_job_pruning_keeps_members_needed_by_an_active_group(self):
        group = self.group()
        with patch.object(engine_module, 'MAX_RUNS', 1):
            result = self.automation.start_group(group['id'])
            run = self.automation.runs[result['id']]
            for index in range(3):
                self.engine.jobs[run['job_ids'][index]]['status'] = 'completed'
                self.automation.tick(self.now)
        self.assertEqual(run['status'], 'completed')
        self.assertTrue(all(job_id in self.engine.jobs for job_id in run['job_ids']))

    def test_pause_resume_skips_missed_occurrences(self):
        schedule = self.schedule(repeat='hourly')
        self.automation.set_schedule_enabled(schedule['id'], False)
        later = self.now + timedelta(hours=2)
        self.automation.tick(later)
        self.assertFalse(self.engine.jobs)
        with patch.object(automation_module, 'utc_now', return_value=later):
            self.automation.set_schedule_enabled(schedule['id'], True)
        item = self.automation.schedules[schedule['id']]
        self.assertGreater(automation_module.parse_time(item['next_run']), later)
        self.automation.tick(later)
        self.assertFalse(self.engine.jobs)
        self.automation.delete_schedule(schedule['id'])
        self.assertEqual(Engine().automation.schedules, {})

    def test_reopening_skips_closed_app_times_and_does_not_run_backlog(self):
        once = self.schedule()
        repeat = self.schedule(repeat='hourly')
        later = self.now + timedelta(hours=3)
        restored = Engine().automation
        with patch.object(automation_module, 'utc_now', return_value=later):
            restored.start()
        self.addCleanup(restored.stop)
        self.assertFalse(restored.schedules[once['id']]['enabled'])
        self.assertGreater(automation_module.parse_time(restored.schedules[repeat['id']]['next_run']), later)
        self.assertFalse(restored.engine.jobs)
        self.assertEqual(restored.schedules[once['id']]['last_result'], 'Missed while app was closed')

    def test_schedule_requires_valid_future_timezone_and_target(self):
        for start in ('invalid', '2030-01-01T10:00', self.now.isoformat()):
            with self.subTest(start=start), self.assertRaises(ValueError):
                self.automation.create_schedule('script', 'first', start, 'once')
        with self.assertRaises(ValueError):
            self.schedule(target='missing')
        with self.assertRaises(ValueError):
            self.schedule(repeat='weekly')

    def test_bad_settings_are_reported_without_preventing_engine_startup(self):
        self.automation.path.write_text('[]', encoding='utf-8')
        restored = Engine().automation
        self.assertIn('Unable to load', restored.state()['error'])
        restored.start()
        self.addCleanup(restored.stop)
        self.assertEqual(restored.groups, {})
        self.assertEqual(restored.schedules, {})
        restored.save_group('Recovered', ['first', 'second'], 'sequential')
        self.assertIsNone(restored.state()['error'])

    def test_real_scheduler_runs_group_and_stops_with_app(self):
        self.thread_patch.stop()
        self.clock.stop()
        group = self.group()
        due = automation_module.utc_now() + timedelta(seconds=0.2)
        self.automation.create_schedule('group', group['id'], due.isoformat(), 'once')
        self.automation.start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.automation.runs and next(iter(self.automation.runs.values()))['status'] == 'completed':
                break
            time.sleep(0.05)
        self.assertEqual(next(iter(self.automation.runs.values()))['status'], 'completed')
        self.automation.create_schedule('script', 'first',
                                        (automation_module.utc_now() + timedelta(seconds=0.2)).isoformat(), 'once')
        self.automation.stop()
        time.sleep(0.65)
        self.assertFalse(self.automation._thread.is_alive())
        self.assertEqual([job['name'] for job in self.engine.jobs.values()], ['first', 'second', 'third'])


if __name__ == '__main__':
    unittest.main()
