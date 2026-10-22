"""Start this project's Streamlit app after checking its browser assets.

Only this command's child process is stopped on failure or Ctrl+C. Existing
servers, the database, the configuration and collection workers are not killed
or replaced. The fresh loopback origin avoids reusing the old :8501 page.
"""
from __future__ import annotations

import argparse
import http.client
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from leadfinder import __version__
from leadfinder.config import app_paths, load_config
from leadfinder.local_workspace import LocalWorkspace
from leadfinder.runtime import inspect_runtime
from leadfinder.ui_assets import inspect_streamlit_assets, verify_http_assets

PROJECT = Path(__file__).resolve().parent
DEFAULT_PORT = 8502


def show_asset_report(report: dict) -> None:
    print(f"Streamlit: {report.get('version') or 'not installed'}")
    print(f"Browser assets: {report.get('status', 'unknown')} ({report.get('files_checked', 0)} files checked)")
    for key in ("errors", "missing", "mismatched"):
        for item in (report.get(key) or [])[:8]:
            print(f"  {key}: {item}")


def wait_for_health(process, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"The app server exited with code {process.returncode}. Read the error printed above.")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
        try:
            connection.request("GET", "/_stcore/health")
            response = connection.getresponse()
            body = response.read(100)
            if response.status == 200 and body.strip().lower() == b"ok":
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.2)
    raise RuntimeError("The app server did not become ready within 30 seconds.")


def stop_owned_process(process) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def check_port(port: int) -> None:
    # A recently closed socket can still be in TIME_WAIT without an active
    # server. Check for an actual listener; Streamlit will report any later
    # binding race or other failure through its process exit.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        if connection.connect_ex(("127.0.0.1", port)) == 0:
            raise OSError(f"Port {port} already has a listening server.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Print the URL without opening the default browser.")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        print("Choose a local port from 1024 to 65535.")
        return 2
    print(f"Universal Lead Finder {__version__}")
    print(f"Python: {sys.executable}")
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.")
        return 2
    assets = inspect_streamlit_assets()
    show_asset_report(assets)
    if assets.get("status") == "missing_package":
        print("Streamlit is not installed in this project's environment. Run INSTALL.bat.")
        return 2
    if assets.get("status") == "failed":
        print("The frontend installation needs repair. Run re-run INSTALL.bat.")
        return 2
    if assets.get("status") != "ok":
        print("The package metadata is incomplete. This launch cannot fully verify installed or served JavaScript.")
    runtime = inspect_runtime()
    if runtime.get("status") != "ok":
        for issue in runtime.get("issues", []):
            print(f"Environment: {issue}")
        print("Use your working Python environment, or run INSTALL.bat to install the project requirements with an existing Python.")
        return 2
    paths = app_paths()
    LocalWorkspace().load_config()
    try:
        load_config(paths.config)
        check_port(args.port)
    except OSError:
        print(f"Port {args.port} is already in use or unavailable. Existing processes were left alone.")
        print("Close the old app server, or choose another port: close the old START.bat session before retrying")
        return 2
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2
    url = f"http://127.0.0.1:{args.port}"
    command = [sys.executable, "-m", "streamlit", "run", str(PROJECT / "app.py"),
               "--server.address=127.0.0.1", f"--server.port={args.port}",
               "--browser.serverAddress=127.0.0.1", "--server.headless=true",
               "--server.fileWatcherType=none", "--server.enableCORS=true",
               "--server.enableXsrfProtection=true", "--server.enableStaticServing=false",
               "--client.showErrorDetails=false"]
    process = None
    try:
        process = subprocess.Popen(command, cwd=PROJECT)
        wait_for_health(process, args.port)
        served = verify_http_assets(url, assets)
        if served.get("status") == "failed":
            for error in (served.get("errors") or [])[:10]:
                print(f"Browser asset response: {error}")
            print("The server's JavaScript checks failed. Re-run INSTALL.bat and review the output.")
            return 3
        if served.get("status") != "ok":
            print("The sampled HTTP assets were not fully verified. Re-run INSTALL.bat if widgets fail.")
        print(f"\nOpen this new address: {url}", flush=True)
        print("Close the old localhost:8501 tab. Leave this terminal open while using the app.", flush=True)
        if not args.no_browser:
            try:
                webbrowser.open(url, new=2)
            except webbrowser.Error:
                print("Open the printed address manually.")
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping the app server started by this launcher.")
        return 0
    except (OSError, RuntimeError, http.client.HTTPException) as exc:
        print(f"Startup failed: {exc}")
        return 3
    finally:
        if process is not None:
            stop_owned_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
