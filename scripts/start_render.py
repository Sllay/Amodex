"""Candidate startup. Production migrations require a separately authorized flag."""
import os
import subprocess
import sys
from amodex.deployment.supervised_runtime import status


def main():
    check=status()
    if check['enabled'] and not check['ready']:
        print(check['code'],flush=True)
        return 2
    if os.environ.get('AMODEX_APPLY_MIGRATIONS_ON_START')=='1':
        subprocess.run([sys.executable,'scripts/migrate_production.py'],check=True)
    os.execv(sys.executable,[sys.executable,'-m','uvicorn','amodex.api.workspace_app:app',
        '--host','0.0.0.0','--port',os.environ.get('PORT','10000'),'--workers','1'])


if __name__=='__main__':raise SystemExit(main())
