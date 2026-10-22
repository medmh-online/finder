"""Launch the standalone collection worker without tying it to a UI session."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def start_worker(database: str | Path, config: str | Path, log_dir: str | Path | None = None) -> int:
    """Launch a collection process for this local installation."""
    database = Path(database).resolve()
    config = Path(config).resolve()
    project = Path(__file__).resolve().parent.parent
    log_dir = Path(log_dir) if log_dir else database.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONUNBUFFERED"] = "1"
    command = [sys.executable, "-m", "leadfinder.worker", "--db", str(database), "--config", str(config)]
    kwargs = {"cwd": str(project), "env": env, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    # The repository lease fences concurrent launch attempts. No PID-based killing.
    with (log_dir / "worker.log").open("ab") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    return process.pid
