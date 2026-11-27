"""Install the exact upstream bridge release if absent; never run its SWU updater."""
import hashlib, io, json, os, shutil, tarfile, tempfile, urllib.request, zipfile
from pathlib import Path, PurePosixPath

URL = 'https://github.com/rinkhals-community/Rinkhals.Apps/releases/download/20261123_01/app-cloud2lan-bridge-ks1.swu'
SHA256 = '6ff2361cca6a20d098ab2fc492491b06e22fbca63f01da9f3e3c7280b05cf8fd'
# Public KS1 SWU packaging password from upstream build/tools.sh, not a user credential.
SWU_PASSWORD = b'U2FsdGVkX1+lG6cHmshPLI/LaQr9cZCjA8HZt6Y8qmbB7riY'
LIMIT = 2 * 1024 * 1024
NAME = 'cloud2lan-bridge'

def check(root):
    obj = json.loads((root/'app.json').read_text())
    version = str(obj.get('version',''))
    try: version_tuple = tuple(int(x) for x in version.split('.'))
    except ValueError: raise RuntimeError('Unrecognized bridge version; update using App Store')
    if version_tuple < (1,2) or 'lan_mode' not in obj.get('properties',{}):
        raise RuntimeError('Bridge too old; update to 1.2+ in Rinkhals App Store')
    for name in ('app.sh','mode_watchdog.py','cloud2lan-supervisor.sh'):
        if not (root/name).is_file(): raise RuntimeError('Bridge incomplete: '+name)
    return version

def unpack(data, destination, expected_sha=SHA256):
    if len(data)>LIMIT or hashlib.sha256(data).hexdigest()!=expected_sha:
        raise RuntimeError('Official bridge SHA256 mismatch; no files installed')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        member=z.getinfo('update_swu/setup.tar.gz')
        if member.file_size>LIMIT: raise RuntimeError('Bridge archive too large')
        packed=z.read(member,pwd=SWU_PASSWORD)
    total=0
    with tarfile.open(fileobj=io.BytesIO(packed),mode='r:gz') as tar:
        for item in tar:
            path=PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts: raise RuntimeError('Unsafe archive path')
            if not path.parts or path.parts[0]!=NAME: continue
            rel=path.parts[1:]
            out=destination.joinpath(*rel)
            if item.isdir(): out.mkdir(parents=True,exist_ok=True); continue
            if not item.isfile() or not rel: raise RuntimeError('Unexpected bridge archive entry')
            total+=item.size
            if total>LIMIT: raise RuntimeError('Bridge payload too large')
            out.parent.mkdir(parents=True,exist_ok=True)
            with tar.extractfile(item) as src, open(out,'xb') as f: shutil.copyfileobj(src,f)
            out.chmod(0o755 if item.mode & 0o111 or out.suffix=='.sh' else 0o644)
    check(destination)

def install_if_missing(apps, builtin):
    for root in (apps/NAME,builtin/NAME):
        if root.exists(): return root, check(root), False
    offline=Path('/mnt/udisk/app-cloud2lan-bridge-ks1.swu')
    if offline.is_file():
        with offline.open('rb') as f: data=f.read(LIMIT+1)
    else:
        print('Downloading pinned official Cloud to LAN bridge',flush=True)
        req=urllib.request.Request(URL,headers={'User-Agent':'OriginalCameraHLS/0.2.2'})
        with urllib.request.urlopen(req,timeout=20) as res:
            if not res.geturl().startswith('https://'): raise RuntimeError('Insecure download redirect')
            data=res.read(LIMIT+1)
    apps.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.bridge-stage-',dir=apps) as tmp:
        dest=Path(tmp)/NAME; dest.mkdir()
        unpack(data,dest)
        version=check(dest)
        if (apps/NAME).exists(): raise RuntimeError('Bridge installed concurrently; retry')
        os.rename(dest,apps/NAME)
    return apps/NAME, version, True
