import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import acw.engine as engine_module
from acw.engine import Engine


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
        )

    def test_registration_persists_path_and_working_directory(self):
        self.register_external()

        restored = Engine()
        entry = next(item for item in restored.catalog() if item['name'] == 'custom-task')
        self.assertEqual(entry['label'], 'Custom Task')
        registered = restored.list_registered_scripts()[0]
        self.assertEqual(registered['path'], str(self.external_script.resolve()))
        self.assertEqual(registered['working_directory'], str(self.external_directory.resolve()))
        self.assertTrue(registered['available'])

    def test_invalid_script_and_working_directory_are_rejected(self):
        not_python = self.external_directory / 'notes.txt'
        not_python.write_text('not a script', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Python'):
            self.engine.register_script(
                'bad-script', str(not_python), str(self.external_directory), 'Bad Script'
            )
        with self.assertRaisesRegex(ValueError, 'working directory'):
            self.engine.register_script(
                'custom-task', str(self.external_script), str(self.root / 'missing'), 'Custom Task'
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


if __name__ == '__main__':
    unittest.main()
