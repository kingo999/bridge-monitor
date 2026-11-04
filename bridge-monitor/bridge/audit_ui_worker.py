"""Ordinary dashboard lifecycle worker for restartable derived audit indexing."""
import logging
import threading

from service.queries.audit import refresh


class AuditIndexer:
    def __init__(self, dsn):
        self.dsn = dsn
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="audit-indexer")
        self.thread.start()

    def run(self):
        while not self.stopping.is_set():
            try:
                refresh(self.dsn)
            except Exception as exc:
                logging.getLogger(__name__).warning("Audit indexing unavailable (%s)", type(exc).__name__)
            self.stopping.wait(5)

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=35)
