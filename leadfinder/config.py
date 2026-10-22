"""Validated, portable app configuration. Paths do not depend on the shell cwd."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 2,
    "app": {"currency": "USD", "timezone": "Africa/Casablanca", "page_size": 100},
    "fake_domains": [
        "facebook.com", "instagram.com", "google.com", "goo.gl", "g.page",
        "wa.me", "youtube.com", "fb.com", "tiktok.com", "linkedin.com",
    ],
    "free_site_indicators": [
        "business.site", "negocio.site", "entreprise.site", "wixsite.com",
        "wordpress.com", "blogspot.com", "weebly.com", "jimdosite.com",
        "canva.site", "myshopify.com", "godaddysites.com", "webnode.com",
        "site123.me", "notion.site", "sites.google.com",
    ],
    "crm_statuses": ["New", "Contacted", "Interested", "Quoted", "Won", "Lost"],
    "scoring": {
        "no_website": 45, "social_only": 35, "hosted_website": 15,
        "valid_phone": 20, "public_email": 10, "social_presence": 5,
        "has_reviews": 5, "verified_new_business": 15,
        "hot_threshold": 70, "warm_threshold": 40,
    },
    "search_defaults": {
        "target_leads": 25, "max_candidates": 500, "max_minutes": 20,
        "only_without_website": True, "include_hosted_sites": True,
        "require_phone": True, "refresh_existing": False,
        "collect_review_sample": False, "headless": True,
        "delay_seconds": 2, "cache_hours": 168, "timeout_seconds": 30,
    },
}


class ConfigError(ValueError):
    """An actionable configuration validation error."""


@dataclass(frozen=True)
class AppPaths:
    project: Path
    data: Path
    database: Path
    config: Path
    logs: Path
    backups: Path


def app_paths() -> AppPaths:
    project = Path(__file__).resolve().parent.parent
    data = Path(os.environ.get("LEADFINDER_DATA_DIR", str(project))).expanduser().resolve()
    database = Path(os.environ.get("LEADFINDER_DB_PATH", str(data / "leads.db"))).expanduser().resolve()
    config = Path(os.environ.get("LEADFINDER_CONFIG_PATH", str(data / "config.json"))).expanduser().resolve()
    return AppPaths(project, data, database, config, data / "logs", data / "backups")


def _merge(base: dict, supplied: dict) -> dict:
    result = deepcopy(base)
    for key, value in supplied.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def validate_config(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ConfigError("The configuration must be a JSON object.")
    supplied = deepcopy(data)
    if isinstance(supplied.get("scoring"), dict) and any(key in supplied["scoring"] for key in ["mobile", "fixe", "unknown_age", "recent_1_2y"]):
        # Old weights relied on unverified phone types and sampled review ages.
        # Retain them for reference, and use the explicit v2 evidence model.
        supplied["legacy_scoring"] = deepcopy(supplied["scoring"])
        supplied["scoring"] = {
            key: supplied["scoring"][key]
            for key in ["hot_threshold", "warm_threshold"] if key in supplied["scoring"]
        }
    config = _merge(DEFAULT_CONFIG, supplied)
    for group in ["app", "scoring", "search_defaults"]:
        if not isinstance(config.get(group), dict):
            raise ConfigError(f"{group} must be an object.")
    for key in ["fake_domains", "free_site_indicators", "crm_statuses"]:
        values = config.get(key)
        if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
            raise ConfigError(f"{key} must be a list of non-empty text values.")
        config[key] = list(dict.fromkeys(v.strip() for v in values))
    if not config["crm_statuses"]:
        raise ConfigError("At least one CRM status is required.")
    # These states drive conversion reporting and must remain meaningful.
    if not {"New", "Contacted", "Interested", "Quoted", "Won", "Lost"}.issubset(config["crm_statuses"]):
        raise ConfigError("Keep New, Contacted, Interested, Quoted, Won, and Lost; additional statuses are allowed.")
    for key, value in config["scoring"].items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ConfigError(f"scoring.{key} must be a number from 0 to 100.")
    if config["scoring"]["warm_threshold"] >= config["scoring"]["hot_threshold"]:
        raise ConfigError("The warm threshold must be lower than the hot threshold.")
    limits = {
        "target_leads": (1, 10000), "max_candidates": (1, 50000),
        "max_minutes": (1, 1440), "delay_seconds": (0.5, 60),
        "cache_hours": (0, 8760), "timeout_seconds": (5, 120),
    }
    for key, (low, high) in limits.items():
        value = config["search_defaults"].get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ConfigError(f"search_defaults.{key} must be from {low} to {high}.")
        if key in {"target_leads", "max_candidates", "max_minutes", "timeout_seconds"} and int(value) != value:
            raise ConfigError(f"search_defaults.{key} must be a whole number.")
    for key in ["only_without_website", "include_hosted_sites", "require_phone", "refresh_existing", "collect_review_sample", "headless"]:
        if not isinstance(config["search_defaults"][key], bool):
            raise ConfigError(f"search_defaults.{key} must be true or false.")
    currency = str(config["app"].get("currency", "")).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ConfigError("Currency must be a three-letter code, such as USD, MAD, or EUR.")
    config["app"]["currency"] = currency
    tz = config["app"].get("timezone")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ConfigError("Use a valid timezone, such as Africa/Casablanca or Europe/Paris.") from exc
    page_size = config["app"].get("page_size")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 25 <= page_size <= 500:
        raise ConfigError("app.page_size must be a whole number from 25 to 500.")
    config["schema_version"] = 2
    return config


def load_config(path: str | Path | None = None) -> dict:
    path = Path(path) if path is not None else app_paths().config
    if not path.exists():
        return validate_config({})
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read {path.name}: {exc}") from exc
    return validate_config(data)


def save_config(path: str | Path, data: dict) -> dict:
    config = validate_config(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".config-", suffix=".json", delete=False) as handle:
        temp_path = Path(handle.name)
        json.dump(config, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    return config
