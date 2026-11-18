"""Append only structured evidence. Raw commands and model responses stay out."""
from datetime import datetime, timezone
import json
from uuid import uuid4


class RunLog:
    def __init__(self, folder):
        self.run_id = uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{self.run_id}.jsonl"
        self.file = self.path.open("x", encoding="utf-8", buffering=1)

    def emit(self, event, **fields):
        now = datetime.now(timezone.utc).isoformat()
        row = dict(run_id=self.run_id, time_utc=now, timestamp=now, event=event, **fields)
        self.file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    def close(self):
        self.file.close()
