"""Standalone queue dispatcher for the local MedMH Windows application.

Run by run_ai.py as its own process. Does not start a second WhatsApp session,
change Gemini settings, or contact Leads. A single polling thread serializes
standalone queue items; the main thread publishes a heartbeat even while a
WhatsApp network operation is in flight.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock, Thread
import time

from leadfinder.config import app_paths
from leadfinder.ai_outreach import GlobalContactLedger
from leadfinder.ai_workers import WorkerDB
from leadfinder.message_queue import tick


def start():
    paths = app_paths()
    db_path = Path(paths.database)
    status_path = paths.data / 'queue_dispatcher_status.json'
    paths.data.mkdir(parents=True, exist_ok=True)
    started = time.time()
    lock = Lock()
    state = {
        'pid': os.getpid(),
        'db_path': str(db_path),
        'started_at': started,
        'heartbeat_at': started,
        'last_poll_at': 0,
        'phase': 'starting',
        'last_error': '',
    }
    print(f'[queue dispatcher] separate process started PID={os.getpid()}', flush=True)
    print(f'[queue dispatcher] database: {db_path}', flush=True)
    ledger = GlobalContactLedger(paths.data)

    def poll():
        last_info = 0.0
        while True:
            now = time.time()
            if not db_path.is_file():
                with lock:
                    state['phase'] = 'waiting_for_database'
                if now - last_info > 30:
                    print(f'[queue dispatcher] waiting for database: {db_path}', flush=True)
                    last_info = now
            else:
                try:
                    with lock:
                        state['phase'] = 'polling'
                    tick(WorkerDB(db_path), ledger)
                    with lock:
                        state['last_poll_at'] = time.time()
                        state['phase'] = 'idle'
                        state['last_error'] = ''
                except Exception as exc:
                    detail = f'{type(exc).__name__}: {str(exc)[:260]}'
                    with lock:
                        state['phase'] = 'error'
                        state['last_error'] = detail
                    print(f'[queue dispatcher] POLL ERROR: {detail}', flush=True)
            time.sleep(3)

    Thread(target=poll, daemon=True, name='medmh-queue-poller').start()
    while True:
        with lock:
            state['heartbeat_at'] = time.time()
            snapshot = dict(state)
        try:
            tmp = status_path.with_name(f'{status_path.name}.{os.getpid()}.tmp')
            tmp.write_text(json.dumps(snapshot), encoding='utf-8')
            os.replace(tmp, status_path)
        except OSError as exc:
            print(f'[queue dispatcher] HEARTBEAT ERROR: {exc}', flush=True)
        time.sleep(3)


if __name__ == '__main__':
    start()
