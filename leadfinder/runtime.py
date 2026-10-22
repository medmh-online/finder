"""Read-only checks of the interpreter and the project's declared dependencies.

This checks real distribution versions and imports. It does not install packages,
start Streamlit, open a browser, or read the lead database and configuration.
"""
from __future__ import annotations

import importlib
from importlib import metadata
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo


PROJECT = Path(__file__).resolve().parent.parent
MINIMUM_PYTHON = (3, 11)
REQUIRED_IMPORTS = (
    "streamlit", "pandas", "playwright.sync_api", "phonenumbers",
    "tldextract", "pycountry", "tzdata", "packaging",
)


def inspect_runtime(requirements_path=None, *, distribution_version=None,
                    import_loader=None, python_version=None, executable=None,
                    zoneinfo_factory=None) -> dict:
    """Return ``ok`` only for a compatible, importable application runtime.

    The optional callbacks are explicit test boundaries for package metadata,
    import execution and timezone availability; production defaults always use
    the actual interpreter. Version parsing uses the real ``packaging`` library.
    A successful result is an environment check, not an application/browser test.
    """
    version_info = tuple(python_version or sys.version_info[:3])
    report = {
        "status": "failed", "python": ".".join(str(v) for v in version_info[:3]),
        "executable": str(executable or sys.executable), "issues": [], "versions": {},
    }
    if version_info[:2] < MINIMUM_PYTHON:
        report["issues"].append(
            f"Python {report['python']} is installed, but this project requires Python 3.11 or newer."
        )
    get_version = distribution_version or metadata.version
    load_module = import_loader or importlib.import_module
    get_timezone = zoneinfo_factory or ZoneInfo
    requirements_path = Path(requirements_path) if requirements_path is not None else PROJECT / "requirements.txt"

    # Import checks need not populate bytecode caches while selecting runtimes.
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        try:
            from packaging.markers import default_environment
            from packaging.requirements import InvalidRequirement, Requirement
            from packaging.version import InvalidVersion, Version
        except Exception as exc:
            report["issues"].append(
                f"The packaging dependency cannot load ({type(exc).__name__}: {exc}). "
                "Run INSTALL.bat to install the project requirements."
            )
            return report

        try:
            lines = requirements_path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            report["issues"].append(f"Cannot read project requirements at {requirements_path}: {exc}")
            return report
        marker_environment = default_environment()
        marker_environment["python_version"] = ".".join(str(v) for v in version_info[:2])
        marker_environment["python_full_version"] = report["python"]
        checked = 0
        for number, raw in enumerate(lines, 1):
            line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
            if not line or line.startswith("#"):
                continue
            try:
                requirement = Requirement(line)
            except InvalidRequirement as exc:
                report["issues"].append(f"Invalid project requirement on line {number}: {exc}")
                continue
            if requirement.marker and not requirement.marker.evaluate(marker_environment):
                continue
            checked += 1
            if requirement.url:
                report["issues"].append(
                    f"Cannot verify a direct-URL requirement for {requirement.name}; use a package version requirement."
                )
                continue
            try:
                installed_text = str(get_version(requirement.name))
                report["versions"][requirement.name] = installed_text
                installed = Version(installed_text)
            except metadata.PackageNotFoundError:
                report["issues"].append(f"Missing dependency: {requirement.name}{requirement.specifier}.")
                continue
            except InvalidVersion as exc:
                report["issues"].append(f"Invalid installed version for {requirement.name}: {exc}")
                continue
            except Exception as exc:
                report["issues"].append(
                    f"Cannot inspect {requirement.name} metadata ({type(exc).__name__}: {exc})."
                )
                continue
            if installed not in requirement.specifier:
                report["issues"].append(
                    f"Incompatible dependency: {requirement.name} {installed_text} is installed; "
                    f"this project requires {requirement.name}{requirement.specifier}."
                )
        if not checked:
            report["issues"].append("The project requirements file contains no applicable package requirements.")

        for module_name in REQUIRED_IMPORTS:
            try:
                load_module(module_name)
            except Exception as exc:
                report["issues"].append(
                    f"Cannot import {module_name} ({type(exc).__name__}: {exc})."
                )
        for timezone in ("UTC", "Africa/Casablanca"):
            try:
                get_timezone(timezone)
            except Exception as exc:
                report["issues"].append(
                    f"Timezone data for {timezone} is unavailable ({type(exc).__name__}: {exc}). "
                    "The project requires the tzdata package."
                )
    finally:
        sys.dont_write_bytecode = previous_bytecode
    report["status"] = "failed" if report["issues"] else "ok"
    return report
