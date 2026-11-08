"""Rebuildable Render assets. Never downloads at request time or stores authority."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import zipfile

from amodex.runtime.hermes_worker import HERMES_COMMIT

LUAU_VERSION = '0.746'
ARCHIVE_SHA = '30f11c9cde8be2dc0e3066d210705532aac3726a2db89a6c5078080b6faa6cf8'
ANALYZER_SHA = '151d8490740c7afc10638ef6e396fd50ab6f4b02a4ddf3b924abdea30f0e9820'


def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL,text=True,timeout=120).strip()


def runtime_root():
    return Path(os.environ.get('AMODEX_SUPERVISED_RUNTIME_ROOT','.amodex-runtime')).resolve()


def require(condition):
    if not condition:
        raise RuntimeError("RUNTIME_INTEGRITY_REJECTED")


def verify(root=None):
    root = root or runtime_root()
    manifest = json.loads((root/'manifest.json').read_text())
    require(git(root/'hermes','rev-parse','HEAD') == HERMES_COMMIT)
    require(not (root/'hermes'/'.env').exists())
    require(git(root/'project','rev-parse','HEAD') == manifest['release_sha'])
    expected = os.environ.get('RENDER_GIT_COMMIT') or os.environ.get('AMODEX_RELEASE_SHA')
    require(not expected or expected == manifest['release_sha'])
    analyzer = root/'luau-analyze'
    require(analyzer.is_file() and os.access(analyzer,os.X_OK))
    require(hashlib.sha256(analyzer.read_bytes()).hexdigest() == ANALYZER_SHA)
    return manifest


def status():
    if os.environ.get('AMODEX_SUPERVISED_ENABLED') != '1':
        return {'enabled':False,'ready':False,'code':'SUPERVISED_DISABLED'}
    try:
        manifest=verify()
        # Validate config without reading credentials or mutating the database.
        from amodex.runtime.supervised_execution import grants_from_environment
        grants=grants_from_environment()
        if not grants:
            return {'enabled':True,'ready':False,'code':'HOST_PROJECT_GRANT_REQUIRED'}
        for grant in grants: grant.validate_host()
        return {'enabled':True,'ready':True,'code':None,'hermes_commit':HERMES_COMMIT,
                'luau_version':LUAU_VERSION,'analyzer_sha256':ANALYZER_SHA,'release_sha':manifest['release_sha']}
    except Exception:
        return {'enabled':True,'ready':False,'code':'SUPERVISED_RUNTIME_INVALID'}


def expand_grants(items):
    if not any(str(v).startswith('@') for item in items for v in item.values()):
        return items
    manifest=verify()
    root=runtime_root()
    replacements={'repository_root':('@runtime/project',str(root/'project')),
                  'hermes_root':('@runtime/hermes',str(root/'hermes')),
                  'analyzer':('@runtime/luau-analyze',str(root/'luau-analyze')),
                  'base_sha':('@release',manifest['release_sha']),
                  'analyzer_sha256':('@pinned',ANALYZER_SHA)}
    return [{k:(replacements[k][1] if k in replacements and v==replacements[k][0] else v)
             for k,v in item.items()} for item in items]


def build(*, project, root, archive=None, hermes_source=None):
    if platform.system() != 'Linux' or platform.machine() not in {'x86_64','AMD64'}:
        raise RuntimeError('UNSUPPORTED_RUNTIME_PLATFORM')
    project,root=project.resolve(),root.resolve()
    if root==project or root in project.parents:
        raise RuntimeError('INVALID_RUNTIME_ROOT')
    root.mkdir(parents=True,exist_ok=True)
    (root/'manifest.json').unlink(missing_ok=True)  # Partial build cannot become ready.
    for name in ('hermes','project'): shutil.rmtree(root/name,ignore_errors=True)
    if hermes_source:
        subprocess.run(['git','clone','--quiet','--no-hardlinks',str(hermes_source),str(root/'hermes')],check=True,timeout=120)
    else:
        subprocess.run(['git','init','-q',str(root/'hermes')],check=True)
        git(root/'hermes','fetch','--quiet','--depth','1','https://github.com/NousResearch/hermes-agent.git',HERMES_COMMIT)
    git(root/'hermes','checkout','--quiet','--detach',HERMES_COMMIT)
    subprocess.run(['git','clone','--quiet','--no-hardlinks','--no-checkout',str(project),str(root/'project')],check=True,timeout=120)
    sha=git(project,'rev-parse','HEAD')
    git(root/'project','checkout','--quiet','--detach',sha)
    with tempfile.TemporaryDirectory() as temp:
        asset=Path(archive) if archive else Path(temp)/'luau.zip'
        if archive is None:
            import httpx
            url=f'https://github.com/luau-lang/luau/releases/download/{LUAU_VERSION}/luau-ubuntu.zip'
            with httpx.stream('GET',url,follow_redirects=True,timeout=60) as response:
                response.raise_for_status()
                with asset.open('wb') as out:
                    size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>32_000_000: raise RuntimeError('ASSET_SIZE_LIMIT')
                        out.write(chunk)
        if hashlib.sha256(asset.read_bytes()).hexdigest()!=ARCHIVE_SHA:
            raise RuntimeError('LUAU_ARCHIVE_HASH_MISMATCH')
        with zipfile.ZipFile(asset) as z:
            binary=z.read('luau-analyze')  # Never extract arbitrary archive paths.
        if hashlib.sha256(binary).hexdigest()!=ANALYZER_SHA:
            raise RuntimeError('LUAU_BINARY_HASH_MISMATCH')
        (root/'luau-analyze').write_bytes(binary)
        (root/'luau-analyze').chmod(0o755)
    manifest={'release_sha':sha,'hermes_commit':HERMES_COMMIT,'luau_version':LUAU_VERSION,
              'archive_sha256':ARCHIVE_SHA,'analyzer_sha256':ANALYZER_SHA}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    verify(root)
    return manifest


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path,default=Path.cwd());p.add_argument('--root',type=Path,default=runtime_root());p.add_argument('--archive',type=Path);p.add_argument('--hermes-source',type=Path)
    args=p.parse_args()
    try:
        print(json.dumps(build(project=args.project,root=args.root,archive=args.archive,hermes_source=args.hermes_source)))
    except Exception:
        print('{"status":"BUILD_BLOCKED"}')
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
