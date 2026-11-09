import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import acw.engine as engine_module
from acw.engine import Engine, parse_acw_header


class RegisteredScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.script_directory = self.root / 'scripts'
        self.run_directory = self.root / 'runs'
        self.sessions_directory = self.root / 'sessions'
        self.external_directory = self.root / 'external'
        self.script_directory.mkdir()
        self.run_directory.mkdir()
        self.external_directory.mkdir()
        self.external_script = self.external_directory / 'custom_task.py'
        self.external_script.write_text('print("done")\n', encoding='utf-8')
        self.directory_patch = patch.multiple(
            engine_module,
            ROOT=self.root,
            SCRIPT_DIR=self.script_directory,
            RUN_DIR=self.run_directory,
            SESSIONS_DIR=self.sessions_directory,
            HISTORY_FILE=self.run_directory / 'history.jsonl',
            SESSION_FILE=self.run_directory / 'last_session.json',
            REGISTERED_SCRIPTS_FILE=self.run_directory / 'registered_scripts.json',
        )
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)
        self.engine = Engine()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def register_external(self):
        return self.engine.register_script(
            'custom-task',
            str(self.external_script),
            str(self.external_directory),
            'Custom Task',
            'Run the registered task',
            True,
        )

    def test_invalid_session_load_preserves_workspace_and_saved_session(self):
        self.register_external()
        with patch.object(self.engine, '_launch'):
            job_id = self.engine.start('custom-task')['id']
        self.engine.jobs[job_id]['status'] = 'completed'
        self.engine._auto_save_session()
        before = self.engine.snapshot()
        saved = engine_module.SESSION_FILE.read_bytes()
        scripts = dict(self.engine.job_scripts)
        arguments = dict(self.engine.job_arguments)
        invalid_sessions = [
            [], None, {'jobs': {}}, {'jobs': [None]},
            {'jobs': [{'id': ['invalid']}]},
            {'jobs': [{'id': 'valid'}, {'id': []}]},
            {'jobs': [{'id': 'duplicate'}, {'id': 'duplicate'}]},
            {'jobs': [{'id': 'bad-output', 'output': 'not a list'}]},
        ]
        for data in invalid_sessions:
            with self.subTest(data=data):
                (self.sessions_directory / 'invalid.json').write_text(
                    json.dumps(data), encoding='utf-8',
                )
                with self.assertRaises(ValueError):
                    self.engine.load_session('invalid.json')
                self.assertEqual(self.engine.snapshot(), before)
                self.assertEqual(self.engine.job_scripts, scripts)
                self.assertEqual(self.engine.job_arguments, arguments)
                self.assertEqual(self.engine.audit_session_job_ids, {job_id})
                self.assertEqual(engine_module.SESSION_FILE.read_bytes(), saved)

    def test_invalid_last_session_does_not_prevent_startup(self):
        for data in ([], None, {'jobs': {}}, {'jobs': [{'id': []}]},
                     {'jobs': [{'id': 'valid'}, {'id': []}], 'max_concurrent': 2}):
            with self.subTest(data=data):
                engine_module.SESSION_FILE.write_text(json.dumps(data), encoding='utf-8')
                restored = Engine()
                self.assertEqual(restored.snapshot(), [])
                self.assertEqual(restored.get_max_concurrent(), engine_module.DEFAULT_MAX_CONCURRENT)

    def test_session_listing_and_name_lookup_skip_invalid_files(self):
        self.sessions_directory.mkdir(exist_ok=True)
        (self.sessions_directory / 'bad.json').write_text('[]', encoding='utf-8')
        (self.sessions_directory / 'good.json').write_text(
            json.dumps({'name': 'Friendly name', 'jobs': [{'id': 'old', 'status': 'running'}],
                        'max_concurrent': 2}), encoding='utf-8',
        )
        self.assertEqual([item['name'] for item in self.engine.list_sessions()], ['Friendly name'])
        result = self.engine.load_session('Friendly name')
        self.assertEqual(result['jobs'], 1)
        self.assertEqual(self.engine.jobs['old']['status'], 'interrupted')
        self.assertIsNone(self.engine.jobs['old']['stage_started'])
        self.assertEqual(self.engine.get_max_concurrent(), 2)

    def test_external_artifact_roots_survive_restart_and_named_session_load(self):
        with tempfile.TemporaryDirectory() as outside:
            output = Path(outside).resolve()
            artifact = output / 'result.txt'
            artifact.write_text('result', encoding='utf-8')
            other = output / 'not-reported.txt'
            other.write_text('other', encoding='utf-8')
            self.external_script.write_text(
                '# ACW_COMMAND: custom-task\n# ACW_DESCRIPTION: Artifact test\n'
                '# ACW_LABEL: Custom Task\n# ACW_OUTPUT_DIR: ' + str(output) + '\n',
                encoding='utf-8',
            )
            self.register_external()
            with patch.object(self.engine, '_launch'):
                job_id = self.engine.start('custom-task')['id']
            job = self.engine.jobs[job_id]
            job['status'] = 'completed'
            job['artifacts'] = [str(artifact)]
            self.engine.save_session('artifacts')
            # Older snapshots can recover roots from the trusted catalog.
            legacy = json.loads(engine_module.SESSION_FILE.read_text(encoding='utf-8'))
            legacy['jobs'][0].pop('output_dirs')
            engine_module.SESSION_FILE.write_text(json.dumps(legacy), encoding='utf-8')
            self.assertEqual(Engine()._artifact_path(job_id, str(artifact)), artifact)
            self.engine._auto_save_session()
            # The run's approved roots should survive later catalog changes.
            self.engine.remove_registered_script('custom-task')
            restored = Engine()
            for engine in (self.engine, restored):
                with self.subTest(engine=engine):
                    self.assertEqual(engine._artifact_path(job_id, str(artifact)), artifact)
                    with self.assertRaisesRegex(ValueError, 'not reported'):
                        engine._artifact_path(job_id, str(other))
            restored.clear_session()
            restored.load_session('artifacts')
            with patch.object(restored, '_open_path') as open_path:
                restored.open_artifact(job_id, str(artifact))
            open_path.assert_called_once_with(artifact)
            with tempfile.TemporaryDirectory() as unapproved:
                outside_roots = Path(unapproved) / 'outside.txt'
                outside_roots.write_text('outside', encoding='utf-8')
                restored.jobs[job_id]['artifacts'].append(str(outside_roots))
                with self.assertRaisesRegex(ValueError, 'outside the project'):
                    restored._artifact_path(job_id, str(outside_roots))

    def test_valid_session_load_is_rejected_while_jobs_are_active(self):
        self.register_external()
        self.engine.save_session('empty')
        with patch.object(engine_module.threading, 'Thread'):
            job_id = self.engine.start('custom-task')['id']
        before = self.engine.snapshot()
        with self.assertRaisesRegex(ValueError, 'while jobs are active'):
            self.engine.load_session('empty')
        self.assertEqual(self.engine.snapshot(), before)
        self.assertIn(job_id, self.engine.job_scripts)

    def test_launch_failure_remains_in_audit_after_workspace_clear(self):
        self.register_external()
        with patch.object(self.engine, '_launch'):
            job_id = self.engine.start('custom-task')['id']
        with patch.object(engine_module.subprocess, 'Popen', side_effect=OSError('launch failed')):
            self.engine._run(job_id)
        self.engine.clear_session()
        result = self.engine.search_audit(job_id)
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['jobs'][0]['status'], 'failed')
        self.assertTrue(any('launch failed' in event['message']
                            for event in result['jobs'][0]['events']))

    def test_stage_timings_accumulate_across_repeated_stages(self):
        self.register_external()
        with patch.object(self.engine, '_launch'):
            job_id = self.engine.start('custom-task')['id']
        job = self.engine.jobs[job_id]
        job['stage'] = 'Recurring'
        job['stage_started'] = '2026-01-01T00:00:00+00:00'
        def change_stage(stage, second):
            instant = datetime(2026, 1, 1, 0, 0, second, tzinfo=timezone.utc)
            with patch.object(engine_module, 'datetime', wraps=datetime) as clock:
                clock.now.return_value = instant
                self.engine._read_output(job_id, SimpleNamespace(stdout=[
                    'ACW_EVENT:' + json.dumps({'stage': stage}) + '\n',
                ]))
        change_stage('Other', 3)
        change_stage('Recurring', 4)
        change_stage('Other', 6)
        self.assertEqual(job['stage_timings']['Recurring'], 5.0)
        self.assertEqual(job['stage_timings']['Other'], 1.0)

    def test_registration_persists_path_and_working_directory(self):
        self.register_external()

        restored = Engine()
        entry = next(item for item in restored.catalog() if item['name'] == 'custom-task')
        self.assertEqual(entry['label'], 'Custom Task')
        self.assertEqual(entry['kind'], 'python')
        registered = restored.list_registered_scripts()[0]
        self.assertEqual(registered['path'], str(self.external_script.resolve()))
        self.assertEqual(registered['working_directory'], str(self.external_directory.resolve()))
        self.assertTrue(registered['available'])

    def test_slots_are_reserved_before_workers_start_and_queue_drains_in_order(self):
        self.register_external()
        self.engine.set_max_concurrent(1)
        # Delay every worker deterministically, including while the queue drains.
        with patch.object(engine_module.threading, 'Thread') as thread:
            ids = [self.engine.start('custom-task')['id'] for _ in range(5)]
            self.assertEqual(thread.return_value.start.call_count, 1)
            self.assertEqual(list(self.engine.queue), ids[1:])
            for index, job_id in enumerate(ids):
                self.assertEqual(self.engine.jobs[job_id]['status'], 'running')
                with self.engine.lock:
                    self.engine.jobs[job_id]['status'] = 'completed'
                    self.engine._drain_queue()
                    self.assertLessEqual(self.engine._running_count(), 1)
                self.assertEqual(thread.return_value.start.call_count, min(index + 2, 5))
                self.assertEqual(list(self.engine.queue), ids[index + 2:])

    def test_limit_changes_respect_reserved_slots(self):
        self.register_external()
        self.engine.set_max_concurrent(1)
        with patch.object(engine_module.threading, 'Thread') as thread:
            ids = [self.engine.start('custom-task')['id'] for _ in range(5)]
            self.engine.set_max_concurrent(3)
            self.assertEqual(thread.return_value.start.call_count, 3)
            self.engine.set_max_concurrent(1)
            with self.engine.lock:
                for job_id in ids[:2]:
                    self.engine.jobs[job_id]['status'] = 'completed'
                    self.engine._drain_queue()
            self.assertEqual(thread.return_value.start.call_count, 3)
            self.assertEqual(list(self.engine.queue), ids[3:])

    def test_cancellation_before_worker_starts_releases_slot(self):
        self.register_external()
        self.engine.set_max_concurrent(1)
        with patch.object(engine_module.threading, 'Thread'), \
                patch.object(engine_module.subprocess, 'Popen') as popen:
            first = self.engine.start('custom-task')['id']
            second = self.engine.start('custom-task')['id']
            self.engine.cancel(first)
            self.engine._run(first)
            popen.assert_not_called()
            self.assertEqual(self.engine.jobs[first]['status'], 'cancelled')
            self.assertEqual(self.engine.jobs[second]['status'], 'running')

    def test_thread_start_failure_releases_slot_and_queue_continues(self):
        self.register_external()
        self.engine.set_max_concurrent(1)
        with patch.object(engine_module.threading, 'Thread') as thread:
            ids = [self.engine.start('custom-task')['id'] for _ in range(3)]
            thread.return_value.start.side_effect = [RuntimeError('no thread'), None]
            with self.engine.lock:
                self.engine.jobs[ids[0]]['status'] = 'completed'
                self.engine._drain_queue()
            self.assertEqual(self.engine.jobs[ids[1]]['status'], 'failed')
            self.assertEqual(self.engine.jobs[ids[2]]['status'], 'running')
            self.assertEqual(self.engine._running_count(), 1)
            self.assertFalse(self.engine.queue)

    def test_invalid_script_and_working_directory_are_rejected(self):
        not_python = self.external_directory / 'notes.txt'
        not_python.write_text('not a script', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Python'):
            self.engine.register_script(
                'bad-script', str(not_python), str(self.external_directory), 'Bad Script',
                'Bad script', True,
            )
        with self.assertRaisesRegex(ValueError, 'working directory'):
            self.engine.register_script(
                'custom-task', str(self.external_script), str(self.root / 'missing'), 'Custom Task',
                'Custom task', True,
            )

    def test_registration_requires_explicit_trust_confirmation(self):
        with self.assertRaisesRegex(ValueError, 'trust'):
            self.engine.register_script(
                'custom-task', str(self.external_script), str(self.external_directory),
                'Custom Task', 'Run the task', False,
            )

    def test_batch_header_parser_supports_rem_and_double_colon_comments(self):
        batch = self.external_directory / 'custom_task.cmd'
        batch.write_text(
            'REM ACW_COMMAND: custom-task\n'
            ':: ACW_DESCRIPTION: Run the custom Windows task\n'
            'REM ACW_LABEL: Custom Windows Task\n'
            'REM ACW_OUTPUT_DIR: output\n'
            'echo done\n',
            encoding='utf-8',
        )

        metadata = parse_acw_header(batch)

        self.assertEqual(metadata['command'], 'custom-task')
        self.assertEqual(metadata['description'], 'Run the custom Windows task')
        self.assertEqual(metadata['label'], 'Custom Windows Task')
        self.assertEqual(metadata['output_dir'], ['output'])

    def test_header_parser_rejects_missing_invalid_and_late_required_fields(self):
        batch = self.external_directory / 'invalid.bat'
        batch.write_text('echo first\nREM ACW_COMMAND: invalid-name\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ACW_COMMAND and ACW_DESCRIPTION'):
            parse_acw_header(batch)

        batch.write_text(
            'REM ACW_COMMAND: Invalid_Name\n'
            'REM ACW_DESCRIPTION: Invalid command\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(ValueError, 'invalid ACW_COMMAND'):
            parse_acw_header(batch)

        batch.write_text(
            '\n'.join(['echo setup'] * 20 + [
                'REM ACW_COMMAND: too-late',
                'REM ACW_DESCRIPTION: Not read',
            ]),
            encoding='utf-8',
        )
        with self.assertRaisesRegex(ValueError, 'first 20 lines'):
            parse_acw_header(batch)

    def test_batch_registration_requires_matching_header_and_persists_kind(self):
        batch = self.external_directory / 'batch_task.bat'
        batch.write_text(
            'REM ACW_COMMAND: batch-task\n'
            'REM ACW_DESCRIPTION: Run a trusted batch job\n'
            'REM ACW_LABEL: Batch Task\n'
            'REM ACW_OUTPUT_DIR: batch-output\n'
            'echo done\n',
            encoding='utf-8',
        )
        registered = self.engine.register_script(
            'batch-task', str(batch), str(self.external_directory),
            'Ignored label', 'Ignored description', True,
        )

        self.assertEqual(registered['kind'], 'batch')
        self.assertEqual(registered['label'], 'Batch Task')
        restored = Engine()
        stored = next(item for item in restored.list_registered_scripts() if item['name'] == 'batch-task')
        self.assertEqual(stored['kind'], 'batch')
        self.assertEqual(stored['description'], 'Run a trusted batch job')
        self.assertEqual(stored['output_dirs'], [str((self.root / 'batch-output').resolve())])

    def test_batch_registration_rejects_missing_header_and_name_mismatch(self):
        batch = self.external_directory / 'batch_task.bat'
        batch.write_text('echo no header\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ACW_COMMAND and ACW_DESCRIPTION'):
            self.engine.register_script(
                'batch-task', str(batch), str(self.external_directory),
                'Batch Task', 'Batch description', True,
            )
        batch.write_text(
            'REM ACW_COMMAND: other-name\n'
            'REM ACW_DESCRIPTION: Batch description\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(ValueError, 'match ACW_COMMAND'):
            self.engine.register_script(
                'batch-task', str(batch), str(self.external_directory),
                'Batch Task', 'Batch description', True,
            )

    def test_catalog_reload_refreshes_and_revalidates_registered_batch_header(self):
        batch = self.external_directory / 'batch_task.bat'
        batch.write_text(
            'REM ACW_COMMAND: batch-task\n'
            'REM ACW_DESCRIPTION: Original description\n'
            'REM ACW_LABEL: Original label\n',
            encoding='utf-8',
        )
        self.engine.register_script(
            'batch-task', str(batch), str(self.external_directory),
            'Batch Task', 'Original description', True,
        )
        batch.write_text(
            'REM ACW_COMMAND: batch-task\n'
            'REM ACW_DESCRIPTION: Updated description\n'
            'REM ACW_LABEL: Updated label\n',
            encoding='utf-8',
        )

        with patch.object(self.engine, '_script_available', return_value=True):
            catalog = self.engine.reload_catalog()
        entry = next(item for item in catalog if item['name'] == 'batch-task')
        self.assertEqual(entry['description'], 'Updated description')
        self.assertEqual(entry['label'], 'Updated label')

        batch.write_text(
            'REM ACW_COMMAND: renamed-task\n'
            'REM ACW_DESCRIPTION: Changed command\n',
            encoding='utf-8',
        )
        catalog = self.engine.reload_catalog()
        self.assertNotIn('batch-task', {item['name'] for item in catalog})
        registered = next(
            item for item in self.engine.list_registered_scripts()
            if item['name'] == 'batch-task'
        )
        self.assertFalse(registered['available'])
        expected_reason = 'no longer matches' if os.name == 'nt' else 'only run on Windows'
        self.assertIn(expected_reason, registered['reason'])

    def test_batch_runtime_arguments_are_rejected_before_launch(self):
        batch = self.external_directory / 'batch_task.bat'
        batch.write_text(
            'REM ACW_COMMAND: batch-task\n'
            'REM ACW_DESCRIPTION: Run a trusted batch job\n',
            encoding='utf-8',
        )
        self.engine.register_script(
            'batch-task', str(batch), str(self.external_directory),
            'Batch Task', 'Run a trusted batch job', True,
        )
        with patch.object(self.engine, '_script_available', return_value=True), \
                patch.object(self.engine, '_launch') as launch:
            with self.assertRaisesRegex(ValueError, 'do not accept runtime arguments'):
                self.engine.start('batch-task', ['input.txt'])
        launch.assert_not_called()

    def test_batch_launch_uses_quoted_path_without_user_arguments(self):
        with patch.object(Engine, '_batch_cmd_executable', return_value='cmd.exe'):
            command = Engine._batch_launch_command(self.external_directory / 'a batch task.cmd')

        self.assertIn(' /d /s /c ', command)
        self.assertTrue(command.endswith(f'""{self.external_directory / "a batch task.cmd"}""'))
        self.assertNotIn(' && ', command)

    @unittest.skipUnless(os.name == 'nt', 'Windows batch execution requires cmd.exe')
    def test_batch_run_captures_output_and_exit_code(self):
        batch = self.external_directory / 'batch task.cmd'
        batch.write_text(
            '@echo off\n'
            'REM ACW_COMMAND: batch-task\n'
            'REM ACW_DESCRIPTION: Run a batch process test\n'
            'echo batch-output\n'
            'exit /b 7\n',
            encoding='utf-8',
        )
        self.engine.register_script(
            'batch-task', str(batch), str(self.external_directory),
            'Batch Task', 'Run a batch process test', True,
        )

        started = self.engine.start('batch-task')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            job = next(item for item in self.engine.snapshot() if item['id'] == started['id'])
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(0.05)

        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['kind'], 'batch')
        self.assertEqual(job['exit_code'], 7)
        self.assertTrue(any('batch-output' in line for line in job['output']))

    @unittest.skipUnless(os.name == 'nt', 'Windows batch cancellation requires cmd.exe')
    def test_batch_job_can_be_cancelled(self):
        batch = self.external_directory / 'batch_loop.cmd'
        batch.write_text(
            '@echo off\n'
            'REM ACW_COMMAND: batch-loop\n'
            'REM ACW_DESCRIPTION: Batch cancellation test\n'
            ':loop\n'
            'goto loop\n',
            encoding='utf-8',
        )
        self.engine.register_script(
            'batch-loop', str(batch), str(self.external_directory),
            'Batch Loop', 'Batch cancellation test', True,
        )
        started = self.engine.start('batch-loop')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = next(item for item in self.engine.snapshot() if item['id'] == started['id'])
            if job['status'] == 'running':
                break
            time.sleep(0.02)
        self.assertEqual(job['status'], 'running')

        self.engine.cancel(started['id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = next(item for item in self.engine.snapshot() if item['id'] == started['id'])
            if job['status'] == 'cancelled':
                break
            time.sleep(0.02)
        self.assertEqual(job['status'], 'cancelled')

    def test_batch_paths_with_cmd_metacharacters_are_rejected(self):
        batch = self.external_directory / 'unsafe&task.bat'
        batch.write_text(
            'REM ACW_COMMAND: unsafe-task\n'
            'REM ACW_DESCRIPTION: Unsafe path test\n',
            encoding='utf-8',
        )
        with self.assertRaisesRegex(ValueError, 'metacharacters'):
            self.engine.register_script(
                'unsafe-task', str(batch), str(self.external_directory),
                'Unsafe Task', 'Unsafe path test', True,
            )

    def test_missing_registered_script_remains_visible_but_not_runnable(self):
        self.register_external()
        self.external_script.unlink()

        entry = self.engine.list_registered_scripts()[0]
        self.assertFalse(entry['available'])
        self.assertNotIn('custom-task', {item['name'] for item in self.engine.catalog()})
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.engine.start('custom-task')

    def test_conflict_with_new_builtin_does_not_hide_registration_or_break_startup(self):
        self.register_external()
        built_in = self.script_directory / 'built_in.py'
        built_in.write_text(
            '# ACW_COMMAND: custom-task\n'
            '# ACW_DESCRIPTION: Built-in command\n'
            'print("built in")\n',
            encoding='utf-8',
        )

        restored = Engine()

        self.assertIn('custom-task', {item['name'] for item in restored.catalog()})
        registered = restored.list_registered_scripts()[0]
        self.assertFalse(registered['available'])
        self.assertIn('conflicts', registered['reason'])

    def test_relink_updates_path_and_default_working_directory(self):
        self.register_external()
        moved_directory = self.root / 'moved'
        moved_directory.mkdir()
        moved_script = moved_directory / 'custom_task.py'
        moved_script.write_text('print("moved")\n', encoding='utf-8')
        self.external_script.unlink()

        entry = self.engine.relink_registered_script('custom-task', str(moved_script))
        self.assertTrue(entry['available'])
        self.assertEqual(entry['working_directory'], str(moved_directory.resolve()))

    def test_remove_registration_keeps_original_script(self):
        self.register_external()

        self.engine.remove_registered_script('custom-task')

        self.assertTrue(self.external_script.is_file())
        self.assertEqual(self.engine.list_registered_scripts(), [])

    def test_audit_current_session_excludes_restored_jobs(self):
        self.engine.jobs['restored-job'] = {
            'id': 'restored-job',
            'name': 'custom-task',
            'status': 'completed',
            'output': [],
            'events': [],
            'artifacts': [],
        }
        self.register_external()
        with patch.object(self.engine, '_launch'):
            started = self.engine.start('custom-task')

        current = self.engine.audit_current_session()

        self.assertEqual([job['id'] for job in current], [started['id']])

    def test_audit_search_finds_saved_logs_and_excludes_current_jobs(self):
        self.register_external()
        old_job = {
            'id': 'old-job',
            'name': 'custom-task',
            'label': 'Custom Task',
            'status': 'failed',
            'started': '2025-01-01T10:00:00+00:00',
            'ended': '2025-01-01T10:01:00+00:00',
            'output': ['PermissionError: C:\\archive\\reports\\result.csv'],
            'events': [{'kind': 'error', 'message': 'Access denied'}],
            'artifacts': [],
        }
        (self.run_directory / 'old-job.json').write_text(
            json.dumps(old_job), encoding='utf-8'
        )
        with patch.object(self.engine, '_launch'):
            current = self.engine.start('custom-task')

        result = self.engine.search_audit('PermissionError archive result.csv')

        self.assertEqual(result['count'], 1)
        self.assertEqual(result['jobs'][0]['id'], 'old-job')
        self.assertNotEqual(result['jobs'][0]['id'], current['id'])

    def test_audit_search_uses_successful_history_when_snapshot_is_missing(self):
        (self.run_directory / 'history.jsonl').write_text(
            '{"job_id":"history-job","name":"custom-task","runtime":4.2,'
            '"ended":"2025-02-01T10:00:00+00:00"}\n',
            encoding='utf-8',
        )

        result = self.engine.search_audit('custom-task history-job')

        self.assertEqual(result['count'], 1)
        self.assertEqual(result['jobs'][0]['id'], 'history-job')
        self.assertEqual(result['jobs'][0]['status'], 'completed')

    def test_audit_search_includes_saved_session_snapshots(self):
        self.sessions_directory.mkdir(exist_ok=True)
        stored_job = {
            'id': 'session-job',
            'name': 'custom-task',
            'status': 'failed',
            'output': ['Could not open C:\\data\\archive'],
            'events': [],
            'artifacts': [],
        }
        snapshot = {'jobs': [stored_job]}
        engine_module.SESSION_FILE.write_text(json.dumps(snapshot), encoding='utf-8')
        (self.sessions_directory / 'older.json').write_text(
            json.dumps(snapshot), encoding='utf-8'
        )

        result = self.engine.search_audit('archive')

        self.assertEqual(result['count'], 1)
        self.assertEqual(result['jobs'][0]['id'], 'session-job')
        self.assertEqual(result['jobs'][0]['status'], 'failed')

    def test_audit_search_requires_a_query(self):
        with self.assertRaisesRegex(ValueError, 'search term'):
            self.engine.search_audit('  ')

    def test_external_script_runs_in_saved_working_directory(self):
        self.register_external()
        with patch.object(self.engine, '_launch'):
            queued = self.engine.start('custom-task')
        job_id = queued['id']

        class FakeThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                pass

            def join(self, timeout=None):
                pass

            def is_alive(self):
                return False

        class FakeProcess:
            stdout = []
            pid = 123
            returncode = 0

            @staticmethod
            def poll():
                return 0

        with patch.object(engine_module.threading, 'Thread', FakeThread), \
                patch.object(engine_module, 'psutil', None), \
                patch.object(engine_module.subprocess, 'Popen', return_value=FakeProcess()) as popen:
            self.engine._run(job_id)

        self.assertEqual(popen.call_args.kwargs['cwd'], str(self.external_directory.resolve()))
        self.assertEqual(self.engine.jobs[job_id]['status'], 'completed')
        self.assertIsNone(self.engine.jobs[job_id]['stage_started'])
        self.assertIn('Queued', self.engine.jobs[job_id]['stage_timings'])

    def test_datetime_parse_normalizes_missing_timezone_and_invalid_values(self):
        self.assertEqual(
            engine_module.DateTime_parse('2025-01-02T03:04:05').tzinfo,
            engine_module.timezone.utc,
        )
        self.assertEqual(
            engine_module.DateTime_parse('2025-01-02T03:04:05Z').isoformat(),
            '2025-01-02T03:04:05+00:00',
        )
        with self.assertRaisesRegex(ValueError, 'Invalid ISO timestamp'):
            engine_module.DateTime_parse('not-a-date')


if __name__ == '__main__':
    unittest.main()
