"""Launch AI gateway, WhatsApp Node process, and the ORIGINAL Streamlit launcher."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent

if __name__=='__main__':
    if not shutil.which('node'):
        sys.exit('Node.js 20+ is required. Install Node.js and rerun INSTALL.bat.')
    if not (ROOT/'whatsapp'/'node_modules'/'whatsapp-web.js').exists():
        sys.exit('WhatsApp dependencies missing: run INSTALL.bat first.')
    from leadfinder.config import app_paths
    from leadfinder.ai_runtime import bridge_token,crypt,Registry
    root_data=app_paths().data
    bridge_token(root_data)
    crypt(root_data)
    Registry(root_data)
    env=os.environ.copy()
    env['MEDMH_BRIDGE_TOKEN_FILE']=str(root_data/'ai_bridge_token.secret')
    env['MEDMH_QUEUE_EXTERNAL']='1'  # prevent two dispatchers for the same batch
    programs=[]
    try:
        programs.append(subprocess.Popen([sys.executable,'-u',str(ROOT/'ai_server.py')],cwd=ROOT,env=env))
        programs.append(subprocess.Popen([sys.executable,'-u',str(ROOT/'queue_runner.py')],cwd=ROOT,env=env))
        programs.append(subprocess.Popen(['node',str(ROOT/'whatsapp'/'server.js')],cwd=ROOT,env=env))
        print('AI gateway, WhatsApp manager and Streamlit are launching.',flush=True)
        programs.append(subprocess.Popen([sys.executable,str(ROOT/'launch.py')],cwd=ROOT,env=env))
        code=programs[-1].wait()
        raise SystemExit(code)
    finally:
        for proc in reversed(programs):
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=6)
                except subprocess.TimeoutExpired:proc.kill()
