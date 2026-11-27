#!/usr/bin/env python3
import fcntl
import html
import setup as config
import low_latency as low
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
import uuid
import urllib.request
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

RUN = Path('/tmp/rinkhals')
FFMPEG = '/ac_lib/lib/third_bin/ffmpeg'
ACCOUNT = Path('/userdata/app/gk/config/device_account.json')
SOURCE = 'http://127.0.0.1:18088/flv'
PORT = 8080
MAX_BYTES = 32 * 1024 * 1024
MIN_RAM_KB = 24 * 1024
HEALTH = {"version":"1.0.0-rc2", "state":"starting", "detail":"Kameradienst startet", "retry_seconds":0}
HEALTH_LOCK = threading.Lock()
CPU_PREV=None
CPU_LOCK=threading.Lock()

def cpu_usage():
    global CPU_PREV
    try:
        nums=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        now=(sum(nums),nums[3]+nums[4])
        with CPU_LOCK:
            old=CPU_PREV; CPU_PREV=now
        if old is None or now[0]<=old[0]: return None
        return round(max(0,min(100,100*(1-(now[1]-old[1])/(now[0]-old[0])))),1)
    except (OSError,ValueError,IndexError): return None


def health(state, detail, retry=0):
    with HEALTH_LOCK:
        HEALTH.update(state=state,detail=detail,retry_seconds=retry,updated=time.strftime("%Y-%m-%d %H:%M:%S"))

def available_ram():
    try:
        values={line.split(":",1)[0]:int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line and len(line.split())>=2}
        return values.get("MemAvailable",values.get("MemFree",0)+values.get("Buffers",0)+values.get("Cached",0))
    except (OSError,ValueError): return None

STOP = threading.Event()
LOG = logging.getLogger('original-camera-hls')

class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    slots = threading.BoundedSemaphore(4)
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request); return
        try: super().process_request(request,address)
        except BaseException:
            self.slots.release(); raise
    def process_request_thread(self, request, address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()
    allow_reuse_address = True
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address
    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(3)
        return sock, addr

class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()
    def guess_type(self, path):
        if path.endswith('.m3u8'): return 'application/vnd.apple.mpegurl'
        if path.endswith('.ts'): return 'video/mp2t'
        return super().guess_type(path)
    def allowed(self):
        return bool(re.fullmatch(r'/(?:live\.m3u8|seg-[0-9]+\.ts)', urlsplit(self.path).path))
    def diagnostic(self, head=False):
        path=urlsplit(self.path).path
        if path not in ('/status.json','/status.html'): return False
        with HEALTH_LOCK: obj=dict(HEALTH)
        try: obj['lan_mode']=config.lan_mode()
        except OSError: obj['lan_mode']=None
        obj['available_ram_kib']=available_ram()
        obj['cpu_percent']=cpu_usage()
        clients,frames,last=low.HUB.status()
        obj['websocket_clients']=clients; obj['h264_frames']=frames
        obj['last_h264_frame_age_s']=round(time.monotonic()-last,1) if last else None
        try: obj['requested_mode']=config.requested_mode()
        except Exception: obj['requested_mode']='unknown'
        try: _,section=config.managed_section(); obj['moonraker_service']=config.camera_value(section,'service'); obj['moonraker_loaded']=config.loaded_camera(section)
        except Exception: obj['moonraker_loaded']=False
        # Port check is recorded by the worker, not by opening camera connections on every status refresh.
        if path.endswith('.json'):
            data=json.dumps(obj,ensure_ascii=False,indent=2).encode(); mime='application/json; charset=utf-8'
        else:
            rows=''.join('<tr><th>'+html.escape(str(k))+'</th><td>'+html.escape(str(v))+'</td></tr>' for k,v in obj.items())
            data=('<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="refresh" content="5"><title>Anycubic Original Camera - WebSocket Bridge</title><style>body{font:16px sans-serif;max-width:740px;margin:2em auto;padding:0 1em}th,td{text-align:left;padding:.6em;border-bottom:1px solid #ccc}</style><h1>Anycubic Original Camera - WebSocket Bridge</h1><p>Diagnose (aktualisiert alle 5 Sekunden)</p><table>'+rows+'</table></html>').encode(); mime='text/html; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data))); self.end_headers()
        if not head: self.wfile.write(data)
        return True
    def do_GET(self):
        if self.diagnostic(): return
        if not self.allowed(): self.send_error(404); return
        super().do_GET()
    def do_HEAD(self):
        if self.diagnostic(head=True): return
        if not self.allowed(): self.send_error(404); return
        super().do_HEAD()

# Use the same command, credentials and TLS settings as the successful S1 test.
# Only connect to loopback; never log credentials and never publish stopCapture.
def activate():
    import paho.mqtt.client as mqtt
    account = json.loads(ACCOUNT.read_text())
    ready = threading.Event()
    kwargs={'client_id':'hls-' + uuid.uuid4().hex[:12]}
    if hasattr(mqtt,'CallbackAPIVersion'): kwargs['callback_api_version']=mqtt.CallbackAPIVersion.VERSION2
    client = mqtt.Client(**kwargs)
    client.connect_timeout=5
    client.username_pw_set(account['username'], account['password'])
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    def connected(client, userdata, flags, rc, *args):
        if rc == 0: ready.set()
    client.on_connect = connected
    client.connect('127.0.0.1', 9883, 10)
    client.loop_start()
    try:
        if not ready.wait(8): raise RuntimeError('MQTT connection not accepted')
        topic = 'anycubic/anycubicCloud/v1/web/printer/20025/' + str(account['deviceId']) + '/video'
        payload = {'type':'video','action':'startCapture','timestamp':int(time.time()*1000),'msgid':str(uuid.uuid4()),'data':None}
        info = client.publish(topic, json.dumps(payload), qos=1)
        info.wait_for_publish(5)
        if not info.is_published(): raise RuntimeError('MQTT publish timeout')
    finally:
        client.disconnect()
        client.loop_stop()


def command(folder, ffmpeg=FFMPEG, source=SOURCE, basic=False):
    options=[] if basic else ['-rw_timeout','15000000']
    flags='delete_segments' if basic else 'delete_segments+temp_file'
    return [ffmpeg,'-nostdin','-hide_banner','-loglevel','error',*options,'-i',source,'-an','-c:v','copy','-f','hls','-hls_time','2','-hls_list_size','4','-start_number',str(int(time.time())),'-hls_flags',flags,'-hls_segment_filename',str(folder/'seg-%d.ts'),str(folder/'live.m3u8')]


def stop_child(proc):
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=2)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()


class LowServer(Server):
    slots=threading.BoundedSemaphore(4)

def active_mode():
    _,section=config.managed_section()
    return 'Low Latency' if config.camera_value(section,'service')=='jmuxer-stream' else 'HLS'

def mode_change_needed(mode):
    try:
        _,section=config.managed_section()
        desired=config.requested_mode()
        return (desired!=mode or config.selected_camera(mode).strip()!=section) and config.printer_idle()
    except Exception: return False

def run_low():
    ip=config.lan_ip()
    with LowServer((ip,8086),low.WSHandler) as server:
        server.timeout=.5
        serving=threading.Thread(target=server.serve_forever,daemon=True); serving.start()
        try:
            with config.LOCAL.open(SOURCE,timeout=8) as response:
                check=time.monotonic(); last_frame=check
                for key,data in low.access_units(response):
                    if STOP.is_set(): return
                    low.HUB.send(key,data); last_frame=time.monotonic()
                    if last_frame-check>=5:
                        check=last_frame
                        free=available_ram()
                        if free is not None and free<MIN_RAM_KB: raise RuntimeError('Zu wenig freier RAM')
                        if mode_change_needed('Low Latency'): return
                        health('ready','Originalkamera liefert H264 WebSocket')
        finally:
            low.HUB.reset(); server.shutdown(); serving.join(timeout=3)

def supervise(folder):
    delay = 3
    basic = False
    low_failures=0
    last_reload=-120.0
    while not STOP.is_set():
        for p in folder.iterdir():
            if p.is_file(): p.unlink(missing_ok=True)
        mode='HLS'
        try:
            health('checking','Bridge, LAN-Modus und Kameraports werden geprüft')
            config.compatible()
            config.repair_dependencies()
            with HEALTH_LOCK: HEALTH.update(mqtt_port_open=True,camera_port_open=True)
            mode=active_mode()
            if mode_change_needed(mode):
                config.apply_mode(config.requested_mode()); mode=active_mode()
            # Recover a previous incomplete Moonraker reload, only while idle.
            _,section=config.managed_section()
            if not config.loaded_camera(section) and time.monotonic()-last_reload>=120 and config.printer_idle():
                last_reload=time.monotonic()
                try: config.restart_moonraker(section)
                except Exception: LOG.warning('Moonraker reload not confirmed; see diagnostics')
            with HEALTH_LOCK: HEALTH['active_mode']=mode
            if STOP.is_set(): break
            free=available_ram()
            if free is not None and free < MIN_RAM_KB: raise RuntimeError('Zu wenig freier RAM; Kamerastream pausiert')
            health('starting','Originalkamera wird per MQTT aktiviert')
            activate()
            health('buffering','Warte auf HLS-Videosegmente')
            if STOP.wait(1): break
            if mode=='Low Latency':
                started=time.monotonic()
                try: run_low()
                finally:
                    if time.monotonic()-started>60: low_failures=0
                low_failures=0
                continue
            # No re-encoding; stderr is drained with a bounded tail, never credentials.
            proc = subprocess.Popen(command(folder,basic=basic), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            errors=[]
            def drain():
                for line in iter(proc.stderr.readline,b''):
                    errors.append(line[:400].decode('utf-8','replace').strip()); del errors[:-4]
            reader=threading.Thread(target=drain,daemon=True); reader.start()
            born = time.monotonic(); next_check=born+5
            try:
                while not STOP.wait(1):
                    if time.monotonic()>=next_check:
                        next_check=time.monotonic()+5
                        if mode_change_needed(mode): break
                    if proc.poll() is not None:
                        reader.join(timeout=1)
                        LOG.warning('FFmpeg exit %s: %s',proc.returncode,' | '.join(errors))
                        if not basic and any(x in ' '.join(errors).lower() for x in ('unrecognized option','option not found','undefined constant','error setting option','invalid argument')):
                            basic=True
                            LOG.info('Retrying with basic HLS options')
                        raise RuntimeError('FFmpeg beendet: '+str(proc.returncode))
                    free=available_ram()
                    if free is not None and free < MIN_RAM_KB: raise RuntimeError('Zu wenig freier RAM; Kamerastream pausiert')
                    files = list(folder.iterdir())
                    size = 0
                    for p in files:
                        try: size += p.stat().st_size
                        except FileNotFoundError: pass
                    if size > MAX_BYTES: raise RuntimeError('HLS buffer limit exceeded')
                    playlist = folder/'live.m3u8'
                    try: fresh=playlist.exists() and time.time()-playlist.stat().st_mtime < 20 and any(folder.glob('seg-*.ts'))
                    except FileNotFoundError: fresh=False
                    if fresh:
                        health('ready','Originalkamera liefert HLS-Videodaten')
                        if time.monotonic()-born > 30: delay=3
                    elif time.monotonic()-born > 45: raise RuntimeError('No fresh HLS playlist')
            finally:
                stop_child(proc); reader.join(timeout=2); proc.stderr.close()
        except Exception as exc:
            with HEALTH_LOCK: HEALTH.update(mqtt_port_open=None,camera_port_open=None)
            if mode=='Low Latency':
                low_failures+=1
                if low_failures>=3:
                    try:
                        config.fallback_hls(); low_failures=0
                        health('retry','Low Latency fehlgeschlagen; HLS wieder aktiviert',0)
                        continue
                    except Exception: LOG.warning('HLS fallback deferred until idle or configuration repair')
            detail= str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
            health('retry',detail,delay)
            LOG.warning('Retry in %ss (%s)',delay,detail)
        if STOP.wait(delay): break
        delay = min(delay*2, 60)


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    if json.loads(config.STATE.read_text()).get('phase')!='ready': return
    lock = open(RUN/'original-camera-hls.lock','a')
    try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: return
    if json.loads(config.STATE.read_text()).get('phase')!='ready': return
    LOG.setLevel(logging.INFO)
    log = RotatingFileHandler(RUN/'app-original-camera-hls.log', maxBytes=128*1024, backupCount=1)
    log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    LOG.addHandler(log)
    pidfile = RUN/'original-camera-hls.pid'
    pidfile.write_text(str(os.getpid()))
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig, lambda *_: STOP.set())
    try:
        with tempfile.TemporaryDirectory(prefix='original-camera-hls-', dir='/tmp') as tmp:
            folder = Path(tmp)
            with Server(('127.0.0.1', PORT),partial(Handler,directory=tmp)) as server:
                server.timeout=.5
                worker=threading.Thread(target=supervise,args=(folder,))
                worker.start()
                LOG.info('HTTP ready on loopback port %d', PORT)
                try:
                    while not STOP.is_set(): server.handle_request()
                finally:
                    STOP.set(); worker.join()
    except Exception:
        LOG.exception('Service stopped')
    finally: pidfile.unlink(missing_ok=True)

if __name__ == '__main__': main()
