"""Narrow pywebview bridge: never expose arbitrary shell execution."""
from .engine import Engine

class API:
    def __init__(self):
        self.engine = Engine()

    def catalog(self): return self.engine.catalog()
    def jobs(self): return self.engine.snapshot()
    def start_job(self, name): return self.engine.start(name)
    def cancel_job(self, job_id): return self.engine.cancel(job_id)
