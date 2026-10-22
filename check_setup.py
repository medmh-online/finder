"""Check installed dependencies and Chromium without starting any collection."""
from __future__ import annotations

from pathlib import Path
import sys

from leadfinder.runtime import inspect_runtime


def main() -> int:
    print("Universal Lead Finder — local setup check")
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.")
        return 1
    print(f"Python: {sys.version.split()[0]}")
    runtime = inspect_runtime()
    for package, installed_version in runtime.get("versions", {}).items():
        print(f"{package}: {installed_version}")
    if runtime.get("status") != "ok":
        for issue in runtime.get("issues", []):
            print(f"Environment: {issue}")
        print("\nUse a compatible existing environment or install the project requirements with INSTALL.bat.")
        return 1
    from leadfinder.ui_assets import inspect_streamlit_assets
    assets = inspect_streamlit_assets()
    print(f"Streamlit frontend assets: {assets.get('status')} ({assets.get('files_checked', 0)} files checked)")
    if assets.get("status") != "ok":
        for field in ["errors", "missing", "mismatched", "unverifiable"]:
            for item in (assets.get(field) or [])[:8]:
                print(f"  {field}: {item}")
        print("Run re-run INSTALL.bat to reinstall and verify Streamlit's files.")
        return 1
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.is_file():
            print("Chromium is missing. Use this same Python environment to run: python -m playwright install chromium")
            return 1
        print(f"Chromium executable: {executable}")
    from leadfinder.config import app_paths, load_config
    from leadfinder.quality import classify_website, country_info
    paths = app_paths()
    from leadfinder.local_workspace import LocalWorkspace
    LocalWorkspace().load_config()
    assert country_info("MA")["country_code"] == "MA"
    assert classify_website("https://example.co.uk")["website_type"] == "Real"
    print("Configuration, country metadata and domain parsing: OK")
    print("No browser was launched and no database or website was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
