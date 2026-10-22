"""Single-computer localhost workspace. No accounts or login UI.

Intentionally binds the dashboard and helper services to loopback only. Anyone
with access to this Windows account and local application can control all data.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from .config import app_paths, AppPaths, DEFAULT_CONFIG, load_config, save_config
from .storage import Repository

class WorkspaceError(RuntimeError):
    pass

class PermissionDenied(WorkspaceError):
    pass

class SessionExpired(WorkspaceError):
    pass

class LocalRepository:
    def __init__(self, access):
        self.access=access
        self.raw=Repository(access.paths().database,config=access.load_config())
    def initialize(self):
        self.raw.initialize()
        return self
    def authorize(self):
        return True
    def __getattr__(self, name):
        return getattr(self.raw,name)

class LocalWorkspace:
    def __init__(self, *_):
        self.base=app_paths()
    def require(self):
        return {'mode':'local'}
    def paths(self):
        return self.base
    def load_config(self):
        p=self.base.config
        p.parent.mkdir(parents=True,exist_ok=True)
        if not p.exists():
            try:
                with p.open('x',encoding='utf-8') as f:
                    json.dump(DEFAULT_CONFIG,f,ensure_ascii=False,indent=2)
            except FileExistsError: pass
        return load_config(p)
    def save_config(self,config):
        return save_config(self.base.config,config)
    def repository(self):
        return LocalRepository(self)
    def launch_worker(self):
        from .runner import start_worker
        p=self.paths()
        return start_worker(p.database,p.config,p.logs)
    def create_backup(self):
        p=self.paths()
        p.backups.mkdir(parents=True,exist_ok=True)
        self.repository().initialize()
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        name=f'leads_{stamp}_{uuid.uuid4().hex[:12]}.db'
        self.repository().raw.backup(p.backups/name)
        return name
    def read_backup(self,name):
        if not re_full_backup(name):
            raise ValueError('Invalid backup filename')
        return (self.paths().backups/name).read_bytes()

def re_full_backup(name):
    import re
    return bool(re.fullmatch(r'leads_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{12}\.db',str(name)))
