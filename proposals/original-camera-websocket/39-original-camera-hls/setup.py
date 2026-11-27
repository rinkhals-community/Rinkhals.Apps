#!/usr/bin/env python3
"""Install/rollback without flashing stock firmware, only while idle."""
import base64, fcntl, json, os, re, socket, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import dependency

HOME=Path('/useremain/home/rinkhals'); APPS=HOME/'apps'
BUILTIN=Path('/useremain/rinkhals/.current/home/rinkhals/apps')
APP='39-original-camera-hls'; STATE=HOME/'original-camera-hls-state/state.json'
CONF=Path('/userdata/app/gk/printer_data/config/moonraker.custom.conf')
MODE=Path('/useremain/dev/remote_ctrl_mode')
ACCOUNT=Path('/userdata/app/gk/config/device_account.json')
CAMERA='[webcam Webcam]\nenabled: True\nservice: hlsstream\nstream_url: /webcam/live.m3u8\nsnapshot_url:\ntarget_fps: 15\ntarget_fps_idle: 5\nrotation: 0\n'
SECTION=re.compile(r'(?im)^[ \t]*\[webcam[ \t]+Webcam\][ \t]*\r?\n.*?(?=^[ \t]*\[|\Z)',re.S)
LOCAL=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def section_replace(text,replacement):
    matches=list(SECTION.finditer(text))
    if len(matches)>1: raise RuntimeError('Duplicate Webcam sections; no changes made')
    if matches:
        m=matches[0]; return text[:m.start()]+(replacement+'\n' if replacement else '')+text[m.end():]
    return text.rstrip()+'\n\n'+replacement if replacement else text

def atomic(path,data,private=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    if data is None: path.unlink(missing_ok=True); return
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.camera-')
    try:
        with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp,0o600 if private else (path.stat().st_mode & 0o777 if path.exists() else 0o644))
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def read(p): return p.read_bytes() if p.exists() else None
def encode(b): return base64.b64encode(b).decode() if b is not None else None
def decode(b): return base64.b64decode(b) if b is not None else None
def root_for(name): return APPS/name if (APPS/name).exists() else BUILTIN/name
def bridge_config(root): return root.parent/(root.name+'.config')
def lan_mode(): return MODE.read_text().strip().lower() in ('lan','1','true')
def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=1): return True
    except OSError: return False

# Rinkhals GoKlipper patch_status mirrors print_stats.state into idle_timeout.
# Only accept explicitly inactive states, including the mirrored representation.
IDLE_PRINT_STATES = frozenset(('standby','complete','cancelled'))

def idle_status(status):
    if not isinstance(status, dict): return False, 'invalid status object'
    ps=status.get('print_stats'); it=status.get('idle_timeout')
    if not isinstance(ps,dict) or not isinstance(it,dict):
        return False, 'missing print_stats or idle_timeout'
    def normalized(obj):
        value=obj.get('state')
        return value.strip().lower() if isinstance(value,str) else ''
    a,b=normalized(ps),normalized(it)
    # Never accept an active/unknown print, or conflicting idle indications.
    ok=a in IDLE_PRINT_STATES and (b in ('idle','ready') or b==a)
    # Only log known state names; never print arbitrary server content.
    known=IDLE_PRINT_STATES | {'idle','ready','printing','paused','onpause','heating','leveling','resonance','error','failed','stoped','shutdown'}
    return ok, 'print_stats='+ (a if a in known else 'unknown') + '; idle_timeout='+ (b if b in known else 'unknown')

def printer_idle():
    try:
        with LOCAL.open('http://127.0.0.1:7125/printer/objects/query?print_stats&idle_timeout',timeout=3) as res:
            state=json.load(res)['result']['status']
        ok,detail=idle_status(state)
        print('Print status: '+detail+'; '+('idle confirmed' if ok else 'blocked'),flush=True)
        return ok
    except Exception as exc:
        print('Print status unavailable: '+type(exc).__name__+'; blocked',flush=True)
        return False

def require_idle():
    if not printer_idle(): raise RuntimeError('Printer not confirmed idle; finish print and retry. No service changes.')

def app_running(root):
    if not (root/'app.sh').is_file(): return False
    p=subprocess.run(['sh',str(root/'app.sh'),'status'],capture_output=True,text=True,timeout=8)
    return 'Status: started' in p.stdout

def camera_enabled(root):
    return ((APPS/(APP+'.enabled')).exists() or (root/'.enabled').exists()) and not ((APPS/(APP+'.disabled')).exists() or (root/'.disabled').exists())

def start_camera(root):
    """Start the committed worker and confirm its own diagnostic endpoint."""
    require_idle()
    if not app_running(root):
        print('Starting Anycubic Original Camera - WebSocket Bridge',flush=True)
        subprocess.run(['sh',str(root/'app.sh'),'start'],check=True,timeout=12,
                       stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+25
    while time.monotonic()<deadline:
        if app_running(root):
            try:
                with LOCAL.open('http://127.0.0.1:8080/status.json',timeout=3) as res:
                    obj=json.load(res)
                if obj.get('version')=='1.0.0-rc2':
                    print('Camera worker and diagnostics confirmed; stream state:',obj.get('state'),flush=True)
                    return
            except (OSError,ValueError,TypeError,AttributeError): pass
        time.sleep(1)
    raise RuntimeError('Camera worker or diagnostic endpoint not confirmed; inspect /tmp/rinkhals/app-original-camera-hls.log')

def restart_moonraker(expected=None):
    expected=expected or CAMERA
    """One restart using the official Rinkhals lifecycle, only while idle."""
    root=root_for('40-moonraker')
    script=root/'app.sh'
    if not script.is_file(): raise RuntimeError('Moonraker app script missing')
    require_idle()
    print('Restarting Moonraker to load camera configuration',flush=True)
    # Rinkhals 20261101_01 app.sh start performs its own graceful stop first.
    subprocess.run(['sh',str(script),'start'],check=True,timeout=30,
                   stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            with LOCAL.open('http://127.0.0.1:7125/server/webcams/list',timeout=3) as res:
                cameras=json.load(res)['result']['webcams']
            if any(c.get('name')=='Webcam' and c.get('enabled') is True
                   and c.get('service')==camera_value(expected,'service') and c.get('stream_url')==camera_value(expected,'stream_url')
                   for c in cameras):
                print('Moonraker restart confirmed: Webcam loaded',flush=True)
                return
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            pass
        time.sleep(2)
    raise RuntimeError('Moonraker restart or loaded webcam not confirmed within 60 seconds')

def start_bridge(root):
    if not app_running(root):
        require_idle()
        subprocess.run(['sh',str(root/'app.sh'),'start'],check=True,timeout=12,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(3)
    for _ in range(10):
        if app_running(root): return
        time.sleep(1)
    raise RuntimeError('Cloud to LAN bridge did not start')

def set_mode(root,target):
    if lan_mode()==target: return
    require_idle(); start_bridge(root)
    time.sleep(2) # give watchdog time to synchronize actual mode
    require_idle()
    p=bridge_config(root)
    cfg=json.loads(read(p) or b'{}'); cfg['lan_mode']='True' if target else 'False'
    atomic(p,(json.dumps(cfg,indent=2)+'\n').encode())
    for _ in range(25):
        if lan_mode()==target: return
        time.sleep(1)
    raise RuntimeError('Bridge did not switch LAN mode; check bridge log')

def enable_changes(name):
    result={APPS/(name+'.enabled'):b'',APPS/(name+'.disabled'):None}
    if (APPS/name).is_dir(): result[APPS/name/'.disabled']=None
    return result

def detect_model(document):
    """Accept only one unambiguous modelId, including nested stock configs."""
    found=set()
    def visit(node):
        if isinstance(node, dict):
            for k,v in node.items():
                if k=='modelId':
                    if isinstance(v,(str,int)) and not isinstance(v,bool) and re.fullmatch(r'[0-9]+',str(v).strip()):
                        found.add(str(v).strip())
                    else: raise RuntimeError('Invalid modelId in api.cfg')
                else: visit(v)
        elif isinstance(node,list):
            for item in node: visit(item)
    visit(document)
    if len(found)!=1: raise RuntimeError('Model detection failed: missing or conflicting modelId in api.cfg; no configuration changed')
    return found.pop()

def compatible():
    model=detect_model(json.loads(Path('/userdata/app/gk/config/api.cfg').read_text()))
    version=Path('/useremain/dev/version').read_text().strip()
    print('Detected model:',model,'firmware:',version,flush=True)
    if model!='20025' or version!='2.7.2.7': raise RuntimeError('This release supports Kobra S1 firmware 2.7.2.7 only')
    if not (BUILTIN/'40-moonraker/app.sh').is_file(): raise RuntimeError('Rinkhals Moonraker not found')
    proxy=(BUILTIN/'25-mainsail/lighttpd.conf').read_text()
    if not re.search(r'"\^/webcam"\s*\{[^}]*"port"\s*=>\s*8080[^}]*"/webcam"\s*=>\s*""',proxy,re.S):
        raise RuntimeError('Expected webcam proxy not found')
    if not os.access('/ac_lib/lib/third_bin/ffmpeg',os.X_OK): raise RuntimeError('Stock FFmpeg missing')
    account=json.loads(ACCOUNT.read_text())
    if not all(account.get(k) for k in ('username','password','deviceId')): raise RuntimeError('Pair printer with Anycubic first')
    import paho.mqtt.client

def camera_value(section,key):
    m=re.search(r'(?m)^'+re.escape(key)+r': *([^\n]*)',section)
    return m.group(1).strip() if m else None

MODE_CONFIG=APPS/(APP+'.config')

def requested_mode():
    obj=json.loads(read(MODE_CONFIG) or b'{}')
    value=obj.get('stream_mode','Low Latency')
    if value not in ('HLS','Low Latency'): raise RuntimeError('Unknown streaming mode')
    return value

def lan_ip():
    # UDP connect selects the route without sending a packet.
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.connect(('192.0.2.1',9)); ip=sock.getsockname()[0]
    if ip.startswith('127.') or ip=='0.0.0.0': raise RuntimeError('No LAN IP available')
    return ip

def selected_camera(mode):
    if mode=='HLS': return CAMERA
    return CAMERA.replace('service: hlsstream','service: jmuxer-stream').replace('/webcam/live.m3u8','ws://'+lan_ip()+':8086/h264').replace('target_fps: 15','target_fps: 5')

def managed_section():
    state=json.loads(STATE.read_text())
    entry=state['files'][str(CONF)]
    after=SECTION.search((decode(entry['after']) or b'').decode())
    if not after: raise RuntimeError('Missing managed webcam backup')
    return state,after.group().strip()

def loaded_camera(expected):
    try:
        with LOCAL.open('http://127.0.0.1:7125/server/webcams/list',timeout=3) as res:
            cams=json.load(res)['result']['webcams']
        return any(c.get('name')=='Webcam' and c.get('enabled') is True and c.get('service')==camera_value(expected,'service') and c.get('stream_url')==camera_value(expected,'stream_url') for c in cams)
    except (OSError,ValueError,KeyError,TypeError,AttributeError): return False

def apply_mode(mode):
    state,previous=managed_section(); target=selected_camera(mode)
    current=(read(CONF) or b'').decode(); matches=list(SECTION.finditer(current))
    if len(matches)!=1 or matches[0].group().strip()!=previous: raise RuntimeError('Managed Webcam changed externally')
    if previous==target.strip():
        if not loaded_camera(target):
            require_idle(); restart_moonraker(target)
        return target
    require_idle()
    updated=section_replace(current,target).encode()
    # Preserve the original pre-installation backup, including other settings.
    state['files'][str(CONF)]['after']=encode(updated)
    atomic(CONF,updated)
    try: save_state(state)
    except BaseException:
        atomic(CONF,current.encode()); raise
    restart_moonraker(target)
    return target

def fallback_hls():
    require_idle()
    cfg=json.loads(read(MODE_CONFIG) or b'{}'); cfg['stream_mode']='HLS'
    atomic(MODE_CONFIG,(json.dumps(cfg,indent=2)+'\n').encode())
    apply_mode('HLS')

def save_state(state): atomic(STATE,json.dumps(state,indent=2).encode(),private=True)

def install():
    if STATE.exists(): raise RuntimeError('Existing camera setup; rollback before installing this beta')
    compatible(); print('Compatibility OK; checking print status',flush=True); require_idle()
    old=read(CONF); new=section_replace((old or b'').decode(),CAMERA).encode()
    bridge,version,added=dependency.install_if_missing(APPS,BUILTIN)
    print('Bridge version',version,'newly installed:',added,flush=True)
    if bridge.parent!=APPS: raise RuntimeError('Unexpected built-in bridge layout; use official user app')
    cfgpath=bridge_config(bridge)
    cfg=json.loads(read(cfgpath) or b'{}'); cfg['lan_mode']='True'
    changes={MODE_CONFIG:read(MODE_CONFIG),CONF:new,cfgpath:(json.dumps(cfg,indent=2)+'\n').encode()}
    for name in ('cloud2lan-bridge',APP): changes.update(enable_changes(name))
    changes[APPS/'30-mjpg-streamer.disabled']=b''; changes[APPS/'30-mjpg-streamer.enabled']=None
    if (APPS/'30-mjpg-streamer').is_dir(): changes[APPS/'30-mjpg-streamer'/'.disabled']=b''
    state={'version':'1.0.0-rc2','phase':'installing','was_lan':lan_mode(),'bridge_was_running':app_running(bridge), 'bridge_added':added,
           'files':{str(p):{'before':encode(read(p)),'after':encode(v)} for p,v in changes.items()}}
    require_idle(); save_state(state)
    try:
        # Boot activation is written last via the phase marker. Never reboot automatically.
        for p,v in changes.items():
            if p not in (cfgpath,CONF): atomic(p,v)
        start_bridge(bridge); set_mode(bridge,True)
        # Preserve current other bridge settings instead of overwriting them.
        current=json.loads(read(cfgpath) or b'{}'); current['lan_mode']='True'
        cfgdata=(json.dumps(current,indent=2)+'\n').encode()
        state['files'][str(cfgpath)]['after']=encode(cfgdata); save_state(state)
        atomic(cfgpath,cfgdata)
        # Use the requested mode on first installation. If the LAN address is
        # not available yet, start with HLS; the worker retries the desired
        # mode after networking is ready. Do not overwrite an explicit choice.
        desired=requested_mode()
        try: target=selected_camera(desired)
        except OSError:
            print('LAN address unavailable; temporary HLS bootstrap',flush=True)
            target=CAMERA
        except RuntimeError as exc:
            if str(exc)!='No LAN IP available': raise
            print('LAN address unavailable; temporary HLS bootstrap',flush=True)
            target=CAMERA
        new=section_replace((old or b'').decode(),target).encode()
        state['files'][str(CONF)]['after']=encode(new); save_state(state)
        require_idle(); atomic(CONF,new)
        state['phase']='ready'; save_state(state)
    except BaseException:
        # Rollback package can recover even if restoration is incomplete.
        try: rollback(force=True)
        except Exception as exc: print('Automatic rollback incomplete:',type(exc).__name__,'Backup retained',flush=True)
        raise
    # Keep the committed setup and its original rollback backup if Moonraker
    # cannot restart; restoring while its print-status API is down is unsafe.
    try:
        restart_moonraker(target)
        start_camera(APPS/APP)
    except Exception as exc:
        print('Configuration saved; Moonraker or camera start not confirmed:',type(exc).__name__,str(exc),flush=True)
        print('Remove USB and reboot manually when idle. Do not reinstall. Backup retained.',flush=True)
        raise SystemExit(2)
    print('Setup complete; camera service started. Bridge port may require manual reboot.',flush=True)

def rollback(force=False):
    if not STATE.exists(): raise RuntimeError('No setup backup found')
    require_idle()
    state=json.loads(STATE.read_text())
    if 'files' not in state: raise RuntimeError('Use the rollback package from version 0.1 first')
    pending=[]; bridge=root_for('cloud2lan-bridge'); cfgpath=bridge_config(bridge)
    for name,s in state['files'].items():
        p=Path(name); current=read(p); after=decode(s['after']); before=decode(s['before'])
        if force or current in (after,before) or p in (APPS/(APP+'.enabled'),APPS/(APP+'.disabled'),APPS/APP/'.disabled'):
            pending.append((p,before)); continue
        if p==MODE_CONFIG:
            obj=json.loads(current or b'{}'); old=json.loads(before or b'{}')
            if 'stream_mode' in old: obj['stream_mode']=old['stream_mode']
            else: obj.pop('stream_mode',None)
            pending.append((p,(json.dumps(obj,indent=2)+'\n').encode() if obj or before is not None else None)); continue
        if p==CONF:
            text=(current or b'').decode(); original=(before or b'').decode()
            active=SECTION.search(text); expected=SECTION.search((after or b'').decode()); previous=SECTION.search(original)
            if active and expected and active.group().strip()==expected.group().strip():
                pending.append((p,section_replace(text,previous.group().strip()+'\n' if previous else '').encode())); continue
        if p==cfgpath:
            obj=json.loads(current or b'{}'); old=json.loads(before or b'{}')
            if str(obj.get('lan_mode')).lower()=='true':
                if 'lan_mode' in old: obj['lan_mode']=old['lan_mode']
                else: obj.pop('lan_mode',None)
                pending.append((p,(json.dumps(obj,indent=2)+'\n').encode())); continue
        raise RuntimeError('Configuration changed since setup: '+name+'; backup retained')
    # Disable new launches and wait until the worker has released its lock.
    state['phase']='rolling_back'; save_state(state)
    subprocess.run(['sh',str(APPS/APP/'app.sh'),'stop'],check=True,timeout=12)
    run=Path('/tmp/rinkhals'); run.mkdir(parents=True,exist_ok=True)
    with open(run/'original-camera-hls.lock','a') as lock:
        for _ in range(50):
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); break
            except BlockingIOError: time.sleep(1)
        else: raise RuntimeError('Camera worker did not stop; backup retained')
    if lan_mode()!=state['was_lan']: set_mode(bridge,state['was_lan'])
    if not state['bridge_was_running'] and app_running(bridge):
        require_idle(); subprocess.run(['sh',str(bridge/'app.sh'),'stop'],check=True,timeout=12)
    for p,v in pending: atomic(p,v)
    STATE.unlink(); print('Previous configuration restored; official dependency retained if newly installed. Reboot manually.')

def repair_dependencies():
    """Repair only when installed and safe; never auto-install/update during runtime."""
    if json.loads(STATE.read_text()).get('phase')!='ready': raise RuntimeError('Camera setup not ready')
    bridge=root_for('cloud2lan-bridge'); dependency.check(bridge)
    if bridge.parent!=APPS: raise RuntimeError('Unexpected bridge layout')
    enabled=(APPS/'cloud2lan-bridge.enabled').exists() or (bridge/'.enabled').exists()
    disabled=(APPS/'cloud2lan-bridge.disabled').exists() or (bridge/'.disabled').exists()
    if not enabled or disabled:
        require_idle()
        for p,v in enable_changes('cloud2lan-bridge').items():
            if read(p)!=v: atomic(p,v)
    if app_running(root_for('30-mjpg-streamer')):
        require_idle()
        subprocess.run(['sh',str(root_for('30-mjpg-streamer')/'app.sh'),'stop'],check=True,timeout=12)
    for p,v in {APPS/'30-mjpg-streamer.disabled':b'',APPS/'30-mjpg-streamer.enabled':None}.items():
        if read(p)!=v: require_idle(); atomic(p,v)
    start_bridge(bridge); set_mode(bridge,True)
    if not port_open(9883): raise RuntimeError('MQTT 9883 unavailable; reboot may be required (no auto reboot)')
    if not port_open(18088): raise RuntimeError('Original camera port 18088 unavailable')
    matches=list(SECTION.finditer((read(CONF) or b'').decode()))
    _,expected=managed_section()
    if len(matches)!=1 or matches[0].group().strip()!=expected:
        raise RuntimeError('Moonraker configuration changed; restore managed section while idle')

if __name__=='__main__':
    if sys.argv[1:]==['install']: install()
    elif sys.argv[1:]==['rollback']: rollback()
    elif sys.argv[1:]==['preflight']:
        compatible()
        if not printer_idle(): raise SystemExit(3)
    else: raise SystemExit('Use install or rollback')
