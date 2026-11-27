"""Transactional upgrade from installed 0.2.x; retain original rollback state."""
import json, fcntl, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import setup as s
VERSION='1.0.0-rc2'
SUPPORTED={'0.2.0-beta','0.2.1-beta','0.2.2-beta','0.2.3-beta','0.2.4-beta','0.2.6-beta','0.2.7-beta','0.2.8-beta','0.2.9-beta','1.0.0-rc1',VERSION}

def stop(root):
    subprocess.run(['sh',str(root/'app.sh'),'stop'],check=True,timeout=12)
    lockpath=Path('/tmp/rinkhals/original-camera-hls.lock'); lockpath.parent.mkdir(parents=True,exist_ok=True)
    with open(lockpath,'a') as lock:
        for _ in range(50):
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); break
            except BlockingIOError: time.sleep(1)
        else: raise RuntimeError('Existing camera worker did not stop')
    for _ in range(10):
        if not s.app_running(root): return
        time.sleep(1)
    raise RuntimeError('Existing camera process did not stop')

def upgrade(stage):
    dest=s.APPS/s.APP
    s.compatible(); s.require_idle()
    state,expected=s.managed_section()
    if state.get('phase')!='ready': raise RuntimeError('Previous setup incomplete; use matching rollback')
    if dest.is_symlink() or not dest.is_dir(): raise RuntimeError('Existing app directory unexpected')
    manifest=json.loads((dest/'app.json').read_text())
    if manifest.get('name') not in ('Anycubic Original Camera - HLS Bridge','Anycubic Original Camera - WebSocket Bridge') or manifest.get('version') not in SUPPORTED: raise RuntimeError('Unknown prior app')
    matches=list(s.SECTION.finditer((s.read(s.CONF) or b'').decode()))
    if len(matches)!=1 or matches[0].group().strip()!=expected: raise RuntimeError('Existing webcam config changed; no update')
    if manifest['version']==VERSION:
        print('Already installed; checking enabled camera service.')
        if s.camera_enabled(dest):
            try: s.start_camera(dest)
            except Exception as exc:
                print('Installed; camera start not confirmed:',str(exc)); raise SystemExit(2)
        return
    was_running=s.app_running(dest)
    should_start=was_running or s.camera_enabled(dest)
    backup=Path(tempfile.mkdtemp(prefix='original-camera-hls-upgrade-',dir=s.HOME))
    oldstate=s.STATE.read_bytes(); (backup/'state.json').write_bytes(oldstate)
    (backup/'state.json').chmod(0o600)
    # Stage complete new directory before stopping the previous service.
    staged=backup/'new'
    shutil.copytree(stage,staged,ignore=shutil.ignore_patterns('__pycache__'))
    for flag in ('.enabled','.disabled'):
        if (dest/flag).is_file(): shutil.copy2(dest/flag,staged/flag)
    state['files'].setdefault(str(s.MODE_CONFIG),{'before':s.encode(s.read(s.MODE_CONFIG)),'after':s.encode(s.read(s.MODE_CONFIG))})
    s.require_idle(); state['phase']='upgrading'; s.save_state(state)
    moved=False
    try:
        stop(dest)
        dest.rename(backup/'app'); moved=True
        staged.rename(dest)
        state['version']=VERSION; state['phase']='ready'; s.save_state(state)
    except BaseException:
        if moved:
            if dest.exists(): shutil.rmtree(dest)
            (backup/'app').rename(dest)
        s.atomic(s.STATE,oldstate,private=True)
        if was_running and not s.app_running(dest): subprocess.run(['sh',str(dest/'app.sh'),'start'],check=False,timeout=12)
        raise
    print('Upgrade complete; previous app and original rollback backup retained:',backup,flush=True)
    # A running worker applies the default or saved mode itself and reloads
    # Moonraker only when idle. Do not concurrently reload the old HLS section
    # while that worker is switching it to WebSocket.
    if should_start:
        try: s.start_camera(dest)
        except Exception as exc:
            print('Upgrade committed; camera start not confirmed:',str(exc),flush=True); raise SystemExit(2)
    if should_start and s.requested_mode() != ('Low Latency' if s.camera_value(expected,'service')=='jmuxer-stream' else 'HLS'):
        print('Updated worker will apply requested mode automatically when idle.',flush=True)
        return
    if not s.loaded_camera(expected):
        try: s.restart_moonraker(expected)
        except Exception as exc:
            print('Upgrade committed; Moonraker reload not confirmed:',type(exc).__name__); raise SystemExit(2)

if __name__=='__main__': upgrade(Path(__file__).resolve().parent)
