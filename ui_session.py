"""Per-browser-session view history and raw working drafts. No disk/browser storage."""
from copy import deepcopy
from threading import Lock


class WorkspaceSession:
    def __init__(self):
        self.lock = Lock()
        self.active_source = 'Your document'
        self.stages = {}
        self.drafts = {}

    def __deepcopy__(self, memo):
        # Gradio creates an isolated workspace for each new browser session.
        return WorkspaceSession()

    def select_source(self, source):
        with self.lock:
            self.active_source = source
            return self.stages.get(source, 'upload')

    def remember_stage(self, source, stage):
        if stage not in {'upload', 'review', 'results'}:
            raise ValueError('Unknown workflow stage.')
        with self.lock:
            self.stages[source] = stage
        return stage

    def showing(self, source):
        with self.lock:
            return self.active_source == source

    def save_draft(self, source, data):
        with self.lock:
            self.drafts[source] = deepcopy(data)

    def draft(self, source):
        with self.lock:
            return deepcopy(self.drafts.get(source, {}))

    def reset(self, source):
        with self.lock:
            self.drafts.pop(source, None)
            self.stages[source] = 'upload'
