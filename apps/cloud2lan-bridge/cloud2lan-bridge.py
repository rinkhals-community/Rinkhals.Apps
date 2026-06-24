import os
import sys
import socket
import configparser
import subprocess
import hashlib
import signal
import json
import paho.mqtt.client as mqtt
import urllib.parse
import time
import ssl
import uuid
import traceback
from datetime import datetime

LOG_DEBUG = 0
LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3

LOG_LEVEL = LOG_DEBUG if os.getenv('DEBUG') else LOG_INFO

def log(level, message):
    if level >= LOG_LEVEL:
        print(datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ' ' + message, flush=True)

def md5(input_str: str) -> str:
    return hashlib.md5(input_str.encode('utf-8')).hexdigest()

def now() -> int:
    return round(time.time() * 1000)

def wait_for_file(path: str, timeout: float = 120.0, poll_interval: float = 1.0) -> bool:
    """
    Block until `path` exists. Returns True if it appeared within `timeout`
    seconds, False on timeout.
    """
    deadline = time.time() + timeout
    while not os.path.exists(path):
        if time.time() >= deadline:
            return False
        time.sleep(poll_interval)
    return True

def wait_for_tcp(host: str, port: int, timeout: float = 120.0, poll_interval: float = 1.0) -> bool:
    """
    Block until host:port accepts a TCP connection. Returns True if reachable
    within `timeout` seconds, False on timeout.
    """
    deadline = time.time() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except (OSError, socket.timeout):
            if time.time() >= deadline:
                return False
            time.sleep(poll_interval)

def is_lan_mode() -> bool:
    mode_file = "/useremain/dev/remote_ctrl_mode"
    if os.path.exists(mode_file):
        try:
            with open(mode_file, "r") as f:
                val = f.read().strip().lower()
            return val in ["1", "lan", "true"]
        except Exception:
            pass
    return False

# ==============================================================================
# DOCUMENTED CLOUD-TO-LAN TELEMETRY RELAY & CAMERA CORE ACTIVATOR
# ==============================================================================
class Program:
    cloud_config = None
    api_config = None
    firmware_version = None
    model_id = None
    cloud_device_id = None
    lan_device_id = None
    cloud_client = None
    lan_client = None
    section_name = None
    area_code = None

    def __init__(self):
        # Clean up any lingering or orphaned instances from previous runs to prevent OOM/resource leaks
        log(LOG_INFO, "[SYSTEM] Cleaning up any stale bridge processes from previous runs...")
        for pattern in ['auto_stream.sh', 'agora_pusher', 'ffmpeg -nostdin -loglevel quiet -i http://127.0.0.1:18088/flv', 'nc 127.0.0.1 18086']:
            try:
                subprocess.run(['pkill', '-f', pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        # Wait for the boot-time files we depend on to prevent boot races.
        required_files = [
            '/userdata/app/gk/config/device.ini',
            '/userdata/app/gk/config/api.cfg',
            '/userdata/app/gk/config/device_account.json',
            '/useremain/dev/version',
            '/useremain/dev/device_id',
        ]
        for path in required_files:
            log(LOG_INFO, f'Waiting for {path}...')
            if not wait_for_file(path, timeout=120):
                raise RuntimeError(f'Timed out waiting for {path}')

        self.cloud_config, self.section_name = self.get_cloud_config()
        self.api_config = self.get_api_config()
        self.firmware_version = self.get_firmware_version()
        self.model_id = self.api_config['cloud']['modelId']
        self.cloud_device_id = self.cloud_config['deviceUnionId']
        self.lan_device_id = self.get_lan_device_id()
        self.area_code = self.get_area_code()

    def get_cloud_config(self):
        config = configparser.ConfigParser()
        config.read('/userdata/app/gk/config/device.ini')
        environment = config['device'].get('env', 'prod').strip()
        zone = config['device'].get('zone', 'global').strip().lower()
        if not zone:
            zone = 'global'
        
        section_name = f'cloud_{environment}' if (zone == 'cn' or zone == 'china') else f'cloud_{zone}_{environment}'
        
        # Robust fallback logic if section name doesn't match expected formats
        if section_name not in config:
            fallback = f'cloud_{environment}'
            if fallback in config:
                section_name = fallback
            else:
                fallback_global = f'cloud_global_{environment}'
                if fallback_global in config:
                    section_name = fallback_global
                    
        return config[section_name], section_name

    def get_area_code(self) -> str:
        if 'global' in self.section_name.lower():
            return "0xFFFFFFFF"  # AREA_CODE_GLOB
        return "1"  # AREA_CODE_CN

    def get_api_config(self):
        with open('/userdata/app/gk/config/api.cfg', 'r') as f:
            return json.loads(f.read())

    def get_ssl_context(self) -> ssl.SSLContext:
        cert_path = self.cloud_config['certPath']
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.set_ciphers(('ALL:@SECLEVEL=0'),)
        if cert_path:
            ssl_context.load_cert_chain(f'{cert_path}/deviceCrt', f'{cert_path}/devicePk', None)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        if os.path.exists(f'{cert_path}/caCrt'):
            ssl_context.load_verify_locations(f'{cert_path}/caCrt')
        return ssl_context

    def get_firmware_version(self) -> str:
        with open('/useremain/dev/version', 'r') as f: 
            return f.read().strip()

    def get_lan_device_id(self) -> str:
        with open('/useremain/dev/device_id', 'r') as f: 
            return f.read().strip()

    def get_cloud_mqtt_credentials(self):
        device_key = self.cloud_config['deviceKey']
        cert_path = self.cloud_config['certPath']
        command = f'printf "{device_key}" | openssl rsautl -encrypt -inkey {cert_path}/caCrt -certin -pkcs | base64 -w 0'
        encrypted_device_key = subprocess.check_output(['sh', '-c', command]).decode('utf-8').strip()
        taco = f'{self.cloud_device_id}{encrypted_device_key}{self.cloud_device_id}'
        return (f'dev|fdm|{self.model_id}|{md5(taco)}', encrypted_device_key)

    def get_lan_mqtt_credentials(self):
        with open('/userdata/app/gk/config/device_account.json', 'r') as f:
            data = json.loads(f.read())
        return (data['username'], data['password'])

    def send_message(self, client, topic, payload):
        mode = 'cloud' if client == self.cloud_client else 'lan'
        log(LOG_DEBUG, f'[{mode}] Sent {topic} = {str(payload)}')
        
        response = topic.endswith('/response')
        report = topic.endswith('/report')

        if not response:
            if report:
                log(LOG_INFO, f'[{mode}] Sent report for {payload.get("type")}/{payload.get("action")}')
            else:
                log(LOG_INFO, f'[{mode}] Sent {payload.get("type")}/{payload.get("action")}')

        client.publish(topic, json.dumps(payload))

    def on_cloud_message(self, topic, payload):
        log(LOG_DEBUG, f'[cloud] Received {topic} = {str(payload)}')

        if not topic.endswith('/response'):
            if topic.endswith('/report'):
                log(LOG_INFO, f'[cloud] Received report for {payload.get("type")}/{payload.get("action")}')
            else:
                log(LOG_INFO, f'[cloud] Received {payload.get("type")}/{payload.get("action")}')
        
        # Intercept stream start event to boot the local bridge pipeline
        if isinstance(payload, dict) and payload.get('action') == 'startCapture':
            log(LOG_INFO, f"[ROUTER] Intercepted startCapture payload: {json.dumps(payload)}")
            shengwang_data = payload.get('data', {}).get('shengwang', {})
            
            # Ignore simple trailing heartbeat acknowledgments
            if not shengwang_data:
                log(LOG_DEBUG, "[ROUTER] Dropping join status message echo.")
            else:
                log(LOG_INFO, "[ROUTER] Intercepted stream request payload. Passing core configuration signals down...")
                try:
                    # Map the local profile to LAN target to spin up camera pipeline
                    local_video_payload = {
                        "type": "video",
                        "action": "startCapture",
                        "timestamp": now(),
                        "msgid": str(payload.get('msgid', uuid.uuid4())),
                        "data": None
                    }
                    
                    self.send_message(
                        self.lan_client, 
                        f"anycubic/anycubicCloud/v1/web/printer/20025/{self.lan_device_id}/video", 
                        local_video_payload
                    )
                    log(LOG_INFO, "[ROUTER] Dispatched internal camera sensor activation safely.")
                except Exception as e:
                    log(LOG_ERROR, f"[ROUTER] Internal camera bootstrap call failed: {str(e)}")

        if not topic.endswith('/response'):
            self.send_message(self.lan_client, topic.replace(self.cloud_device_id, self.lan_device_id), payload)

    def on_lan_message(self, topic, payload):
        log(LOG_DEBUG, f'[lan] Received {topic} = {str(payload)}')
        
        if not topic.endswith('/response'):
            if topic.endswith('/report'):
                log(LOG_INFO, f'[lan] Received report for {payload.get("type")}/{payload.get("action")}')
            else:
                log(LOG_INFO, f'[lan] Received {payload.get("type")}/{payload.get("action")}')

        # Intercept telemetry info query packet to patch video endpoint mappings
        if topic.endswith('/info/report') and isinstance(payload, dict):
            data_block = payload.get('data', {})
            if data_block and 'urls' in data_block:
                log(LOG_INFO, "[ROUTER] Patching video endpoint configuration mappings within system query packet.")
                local_ip = data_block.get('ip', '127.0.0.1')
                data_block['urls']['rtspUrl'] = f"http://{local_ip}:18088/flv"

        if topic.endswith('/report') or topic.endswith('/response'):
            self.send_message(self.cloud_client, topic.replace(self.lan_device_id, self.cloud_device_id), payload)

    def connect_cloud_mqtt(self):
        mqtt_broker = self.cloud_config['mqttBroker']
        mqtt_username, mqtt_password = self.get_cloud_mqtt_credentials()
        
        def mqtt_on_connect(client, userdata, flags, rc, *args, **kwargs):
            log(LOG_INFO, '[cloud] Connected / Handshake established with upstream cluster endpoint.')
            self.cloud_client.subscribe(f'anycubic/anycubicCloud/v1/+/printer/{self.model_id}/{self.cloud_device_id}/#')
        def mqtt_on_connect_fail(client, userdata, *args, **kwargs):
            log(LOG_WARNING, '[cloud] Failed to connect')
        def mqtt_on_disconnect(client, userdata, rc, *args, **kwargs):
            log(LOG_WARNING, f'[cloud] Disconnected (reason: {rc}); paho will retry')
        def mqtt_on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                self.on_cloud_message(msg.topic, payload)
            except Exception as e:
                log(LOG_ERROR, f'[cloud] Failed to handle message on {msg.topic}: {e}')

        mqtt_broker_endpoint = urllib.parse.urlparse(mqtt_broker)

        self.cloud_client = mqtt.Client(protocol=mqtt.MQTTv5, client_id=self.cloud_device_id)
        if mqtt_broker_endpoint.scheme == 'ssl':
            self.cloud_client.tls_set_context(self.get_ssl_context())
            self.cloud_client.tls_insecure_set(True)
        self.cloud_client.on_connect = mqtt_on_connect
        self.cloud_client.on_connect_fail = mqtt_on_connect_fail
        self.cloud_client.on_disconnect = mqtt_on_disconnect
        self.cloud_client.on_message = mqtt_on_message
        self.cloud_client.username_pw_set(mqtt_username, mqtt_password)

        last_err = None
        for attempt in range(8):
            try:
                self.cloud_client.connect(mqtt_broker_endpoint.hostname, mqtt_broker_endpoint.port or 1883)
                self.cloud_client.loop_start()
                break
            except Exception as e:
                last_err = e
                wait = min(60, 2 ** attempt)
                log(LOG_WARNING, f'[cloud] Connect attempt {attempt+1} failed: {e}; retrying in {wait}s')
                time.sleep(wait)
        else:
            raise RuntimeError(f'Could not connect to cloud MQTT after 8 attempts: {last_err}')

        deadline = time.time() + 30
        while not self.cloud_client.is_connected():
            if time.time() >= deadline:
                raise RuntimeError('Cloud MQTT TCP connected but never got CONNACK')
            time.sleep(0.25)

    def connect_lan_mqtt(self):
        log(LOG_INFO, '[lan] Waiting for local MQTT broker on 127.0.0.1:9883...')
        if not wait_for_tcp('127.0.0.1', 9883, timeout=120):
            raise RuntimeError('Timed out waiting for local MQTT broker (gklib not up?)')
        log(LOG_INFO, '[lan] Local MQTT broker is reachable')

        mqtt_username, mqtt_password = self.get_lan_mqtt_credentials()

        def mqtt_on_connect(client, userdata, flags, rc, *args, **kwargs):
            log(LOG_INFO, '[lan] Handshake established with localized host execution loop / Connected.')
            self.lan_client.subscribe(f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.lan_device_id}/#')
            
            # Deploy state notification reports
            for rtype, raction in [('lastWill', 'onlineReport'), ('status', 'workReport')]:
                self.send_message(
                    self.cloud_client, 
                    f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.cloud_device_id}/{rtype}/report', 
                    {
                        'type': rtype, 'action': raction, 'timestamp': now(), 
                        'msgid': str(uuid.uuid4()), 'state': 'online' if rtype=='lastWill' else 'free', 
                        'code': 200, 'msg': 'device online' if rtype=='lastWill' else '', 'data': None
                    }
                )
            
            # Deploy ota notification report
            self.send_message(
                self.cloud_client,
                f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.cloud_device_id}/ota/report',
                {
                    'type': 'ota', 'action': 'reportVersion', 'timestamp': now(),
                    'msgid': str(uuid.uuid4()), 'state': 'done', 'code': 200, 'msg': 'done',
                    'data': {
                        'device_unionid': self.cloud_device_id,
                        'machine_version': '1.1.0',
                        'peripheral_version': '',
                        'firmware_version': self.firmware_version,
                        'model_id': self.model_id
                    }
                }
            )

        def mqtt_on_connect_fail(client, userdata, *args, **kwargs):
            log(LOG_WARNING, '[lan] Failed to connect')
        def mqtt_on_disconnect(client, userdata, rc, *args, **kwargs):
            log(LOG_WARNING, f'[lan] Disconnected (reason: {rc}); paho will retry')
        def mqtt_on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                self.on_lan_message(msg.topic, payload)
            except Exception as e:
                log(LOG_ERROR, f'[lan] Failed to handle message on {msg.topic}: {e}')

        self.lan_client = mqtt.Client(protocol=mqtt.MQTTv5, client_id=self.lan_device_id + "-bridge")
        self.lan_client.tls_set_context(self.get_ssl_context())
        self.lan_client.tls_insecure_set(True)
        self.lan_client.on_connect = mqtt_on_connect
        self.lan_client.on_connect_fail = mqtt_on_connect_fail
        self.lan_client.on_disconnect = mqtt_on_disconnect
        self.lan_client.on_message = mqtt_on_message
        self.lan_client.username_pw_set(mqtt_username, mqtt_password)

        last_err = None
        for attempt in range(8):
            try:
                self.lan_client.connect('127.0.0.1', 9883)
                self.lan_client.loop_start()
                break
            except Exception as e:
                last_err = e
                wait = min(60, 2 ** attempt)
                log(LOG_WARNING, f'[lan] Connect attempt {attempt+1} failed: {e}; retrying in {wait}s')
                time.sleep(wait)
        else:
            raise RuntimeError(f'Could not connect to local MQTT after 8 attempts: {last_err}')

        deadline = time.time() + 30
        while not self.lan_client.is_connected():
            if time.time() >= deadline:
                raise RuntimeError('Local MQTT TCP connected but never got CONNACK')
            time.sleep(0.25)

    def main(self):
        # Setup signal handler to ensure finally block runs on SIGTERM/SIGINT
        def sig_handler(signum, frame):
            log(LOG_INFO, f"[SYSTEM] Received signal {signum}, exiting...")
            raise SystemExit(0)
            
        signal.signal(signal.SIGTERM, sig_handler)
        signal.signal(signal.SIGINT, sig_handler)

        self.connect_cloud_mqtt()
        self.connect_lan_mqtt()

        # Start auto_stream.sh in the background with AGORA_AREA_CODE environment variable
        script_dir = os.path.dirname(os.path.realpath(__file__))
        auto_stream_path = os.path.join(script_dir, 'auto_stream.sh')
        log(LOG_INFO, f"[SYSTEM] Launching {auto_stream_path} with AGORA_AREA_CODE={self.area_code}...")
        self.auto_stream_proc = None
        try:
            env = os.environ.copy()
            env['AGORA_AREA_CODE'] = self.area_code
            # Redirect stdout/stderr of auto_stream.sh to a diagnostic log file
            try:
                log_file = open('/tmp/rinkhals/auto_stream.log', 'a', buffering=1)
            except Exception:
                log_file = subprocess.DEVNULL
            self.auto_stream_proc = subprocess.Popen(
                ['/bin/sh', auto_stream_path],
                cwd=script_dir,
                env=env,
                stdout=log_file,
                stderr=log_file,
                preexec_fn=os.setsid
            )
            log(LOG_INFO, f"[SYSTEM] auto_stream.sh started with PID {self.auto_stream_proc.pid}")
        except Exception as e:
            log(LOG_ERROR, f"[SYSTEM] Failed to start auto_stream.sh: {str(e)}")

        # Watchdog loop
        log(LOG_INFO, "[SYSTEM] Entering main watchdog loop...")
        last_heartbeat = time.time()
        disconnect_start = None
        max_disconnect_duration = 30.0 # Exit if disconnected for more than 30 seconds
        
        try:
            while True:
                if not is_lan_mode():
                    log(LOG_INFO, "[SYSTEM] remote_ctrl_mode is cloud. Exiting normal runtime...")
                    break

                time.sleep(5)
                
                # Check connection status
                cloud_ok = self.cloud_client.is_connected() if self.cloud_client else False
                lan_ok = self.lan_client.is_connected() if self.lan_client else False
                
                # Check thread liveness to detect crashed loop threads
                cloud_thread_ok = self.cloud_client._thread.is_alive() if (self.cloud_client and getattr(self.cloud_client, '_thread', None)) else True
                lan_thread_ok = self.lan_client._thread.is_alive() if (self.lan_client and getattr(self.lan_client, '_thread', None)) else True
                
                if not cloud_thread_ok:
                    log(LOG_ERROR, "[watchdog] Cloud MQTT loop thread is dead!")
                    cloud_ok = False
                if not lan_thread_ok:
                    log(LOG_ERROR, "[watchdog] LAN MQTT loop thread is dead!")
                    lan_ok = False
                
                # Heartbeat logging every 5 minutes
                if time.time() - last_heartbeat >= 300:
                    log(LOG_INFO, f'[heartbeat] cloud={"up" if cloud_ok else "DOWN"} lan={"up" if lan_ok else "DOWN"}')
                    last_heartbeat = time.time()
                
                if not cloud_ok or not lan_ok:
                    if disconnect_start is None:
                        disconnect_start = time.time()
                        log(LOG_WARNING, f"[watchdog] Detected disconnection (cloud_ok={cloud_ok}, lan_ok={lan_ok}). Starting recovery window...")
                    else:
                        duration = time.time() - disconnect_start
                        if duration >= max_disconnect_duration:
                            raise RuntimeError(f"Connection lost or loop thread crashed for {duration:.1f}s (cloud_ok={cloud_ok}, lan_ok={lan_ok}). Restarting app...")
                else:
                    if disconnect_start is not None:
                        log(LOG_INFO, "[watchdog] Connection recovered cleanly.")
                        disconnect_start = None
        finally:
            if self.auto_stream_proc:
                log(LOG_INFO, "[SYSTEM] Terminating auto_stream.sh process group...")
                try:
                    pgid = os.getpgid(self.auto_stream_proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    self.auto_stream_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log(LOG_WARNING, "[SYSTEM] Process group did not terminate. Killing it...")
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                        self.auto_stream_proc.wait(timeout=2)
                    except Exception:
                        pass
                except Exception as e:
                    log(LOG_ERROR, f"[SYSTEM] Failed to cleanly terminate process group: {e}")
                    try:
                        self.auto_stream_proc.kill()
                    except Exception:
                        pass
            
            # Cleanly disconnect MQTT clients so they release local ports and client IDs
            if self.cloud_client:
                try:
                    self.cloud_client.disconnect()
                    self.cloud_client.loop_stop()
                except Exception:
                    pass
            if self.lan_client:
                try:
                    self.lan_client.disconnect()
                    self.lan_client.loop_stop()
                except Exception:
                    pass

if __name__ == "__main__":
    log(LOG_INFO, "[SYSTEM] Starting remote_ctrl_mode watchdog...")
    while not is_lan_mode():
        time.sleep(15)

    log(LOG_INFO, "[SYSTEM] Printer is in LAN Mode. Starting normal runtime...")
    program = None
    try:
        program = Program()
        program.main()
    except Exception as e:
        log(LOG_ERROR, str(e))
        log(LOG_ERROR, traceback.format_exc())
        if program and hasattr(program, 'auto_stream_proc') and program.auto_stream_proc:
            log(LOG_INFO, "[SYSTEM] Exception cleanup: Terminating auto_stream.sh process group...")
            try:
                pgid = os.getpgid(program.auto_stream_proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                program.auto_stream_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except Exception:
                    pass
            except Exception:
                try:
                    program.auto_stream_proc.kill()
                except Exception:
                    pass
        sys.exit(1)