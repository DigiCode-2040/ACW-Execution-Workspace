"""Narrow pywebview bridge: never expose arbitrary shell execution."""
from pathlib import Path
import re

from acw.engine import Engine


class API:
    def __init__(self):
        self.engine = Engine()

    def catalog(self):
        return self.engine.catalog()

    def jobs(self):
        return self.engine.snapshot()

    def audit_current_session(self):
        return self.engine.audit_current_session()

    def search_audit(self, query, limit=100):
        return self.engine.search_audit(query, limit)

    def start_job(self, name, script_args=None):
        return self.engine.start(name, script_args)

    def reload_catalog(self):
        return self.engine.reload_catalog()

    def choose_script_file(self):
        import webview

        if not webview.windows:
            raise RuntimeError('No application window is available.')
        selection = webview.windows[0].create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=('Python files (*.py)',),
        )
        if not selection:
            return None
        path = Path(selection[0]).expanduser()
        stem = re.sub(r'[^a-z0-9]+', '-', path.stem.lower()).strip('-') or 'script'
        if not stem[0].isalpha():
            stem = 'script-' + stem
        return {
            'path': str(path),
            'working_directory': str(path.parent),
            'name': stem,
            'label': path.stem.replace('-', ' ').replace('_', ' ').title(),
        }

    def choose_working_directory(self, directory=None):
        import webview

        if not webview.windows:
            raise RuntimeError('No application window is available.')
        initial_directory = str(Path(directory).expanduser()) if directory else str(Path.home())
        selection = webview.windows[0].create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=initial_directory,
        )
        return selection[0] if selection else None

    def registered_scripts(self):
        return self.engine.list_registered_scripts()

    def register_script(self, name, path, working_directory, label):
        return self.engine.register_script(name, path, working_directory, label)

    def relink_registered_script(self, name, path):
        return self.engine.relink_registered_script(name, path)

    def remove_registered_script(self, name):
        return self.engine.remove_registered_script(name)

    def edit_script(self, name):
        return self.engine.edit_script(name)

    def cancel_job(self, job_id):
        return self.engine.cancel(job_id)

    def open_artifact(self, job_id, artifact):
        return self.engine.open_artifact(job_id, artifact)

    def reveal_artifact(self, job_id, artifact):
        return self.engine.reveal_artifact(job_id, artifact)

    # --- New: concurrency ---
    def get_max_concurrent(self):
        return self.engine.get_max_concurrent()

    def set_max_concurrent(self, value):
        return self.engine.set_max_concurrent(value)

    # --- New: session persistence ---
    def save_session(self, name=None):
        return self.engine.save_session(name)

    def load_session(self, name_or_file):
        return self.engine.load_session(name_or_file)

    def list_sessions(self):
        return self.engine.list_sessions()

    def clear_session(self):
        return self.engine.clear_session()

    # --- New: history / comparison ---
    def get_history(self, script_name=None, limit=50):
        return self.engine.get_history(script_name, limit)

    def compare_script(self, script_name, limit=10):
        return self.engine.compare_script(script_name, limit)

    def exit_app(self):
        # Persist session before exit
        try:
            self.engine._auto_save_session()
        except Exception:
            pass
        import webview
        if not webview.windows:
            raise RuntimeError('No application window is available to close.')
        webview.windows[0].destroy()
