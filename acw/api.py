"""Narrow pywebview bridge: never expose arbitrary shell execution."""
from acw.engine import Engine

class API:
    def __init__(self):
        self.engine = Engine()

    def catalog(self): return self.engine.catalog()
    def jobs(self): return self.engine.snapshot()
    def start_job(self, name, script_args=None): return self.engine.start(name, script_args)
    def reload_catalog(self): return self.engine.reload_catalog()
    def edit_script(self, name): return self.engine.edit_script(name)
    def cancel_job(self, job_id): return self.engine.cancel(job_id)
    def open_artifact(self, job_id, artifact): return self.engine.open_artifact(job_id, artifact)
    def reveal_artifact(self, job_id, artifact): return self.engine.reveal_artifact(job_id, artifact)

    def exit_app(self):
        import webview

        if not webview.windows:
            raise RuntimeError('No application window is available to close.')
        webview.windows[0].destroy()
