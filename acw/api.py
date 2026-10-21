"""Narrow pywebview bridge: never expose arbitrary shell execution."""
from acw.engine import Engine


class API:
    def __init__(self):
        self.engine = Engine()

    def catalog(self):
        return self.engine.catalog()

    def jobs(self):
        return self.engine.snapshot()

    def start_job(self, name, script_args=None):
        return self.engine.start(name, script_args)

    def reload_catalog(self):
        return self.engine.reload_catalog()

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
