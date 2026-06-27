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

def wait_for_tcp(host: str, port: int, timeout: float = 120.0, poll_interval: float = 1.0) -> bool:
    """Block until host:port accepts a TCP connection."""
    deadline = time.time() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=2.0):
                return True
        except (OSError, socket.timeout):
            if time.time() >= deadline:
                return False
            time.sleep(poll_interval)

# ==============================================================================
# CLOUD-TO-LAN MQTT BRIDGE
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
    agora_proc = None  # ffmpeg | agora_pusher pipeline

    def __init__(self):
        required_files = [
            '/userdata/app/gk/config/device.ini',
            '/userdata/app/gk/config/api.cfg',
            '/userdata/app/gk/config/device_account.json',
            '/useremain/dev/version',
            '/useremain/dev/device_id',
        ]
        for path in required_files:
            log(LOG_INFO, f'Waiting for {path}...')
            deadline = time.time() + 120
            while not os.path.exists(path):
                if time.time() >= deadline:
                    raise RuntimeError(f'Timed out waiting for {path}')
                time.sleep(1)

        self.cloud_config, self.section_name = self.get_cloud_config()
        self.api_config = self.get_api_config()
        self.firmware_version = self.get_firmware_version()
        self.model_id = self.api_config['cloud']['modelId']
        self.cloud_device_id = self.cloud_config['deviceUnionId']
        self.lan_device_id = self.get_lan_device_id()
        self.area_code = self.get_area_code()

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # MQTT message handling
    # ------------------------------------------------------------------
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
        
        # Intercept stream start event — launch agora_pusher
        if isinstance(payload, dict) and payload.get('action') == 'startCapture':
            log(LOG_INFO, f"[ROUTER] Intercepted startCapture payload: {json.dumps(payload)}")
            shengwang_data = payload.get('data', {}).get('shengwang', {})
            if not shengwang_data:
                log(LOG_DEBUG, "[ROUTER] Dropping join status message echo.")
                return
            else:
                log(LOG_INFO, "[ROUTER] Intercepted stream request. Starting agora_pusher...")
                self.launch_agora_pusher(shengwang_data)
                
                report_topic = topic.replace('/web/', '/app/')
                if report_topic.endswith('/control') or report_topic.endswith('/request'):
                    report_topic = report_topic.rsplit('/', 1)[0]
                if not report_topic.endswith('/report'):
                    report_topic += '/report'

                report_payload = {
                    "type": "video",
                    "action": "startCapture",
                    "msgid": payload.get("msgid", ""),
                    "state": "joinSuccess",
                    "timestamp": now(),
                    "code": 200,
                    "msg": ""
                }
                self.send_message(self.cloud_client, report_topic, report_payload)
                
                # Wake up the local camera pipeline by sending startCapture to LAN.
                # We use dummy credentials so gkapi doesn't crash (which happens if data is None)
                # and so the native agora_pusher fails instantly instead of competing with ours.
                local_video_payload = {
                    "action": "startCapture",
                    "type": "video",
                    "msgid": str(payload.get('msgid', uuid.uuid4())),
                    "data": {
                        "shengwang": {
                            "appid": "dummy",
                            "channel": "dummy",
                            "rtc_token": "dummy",
                            "uid": 123,
                            "license": "dummy",
                            "encryption_mode": "",
                            "encryption_key": "",
                            "encryption_kdf_salt": ""
                        }
                    }
                }
                self.send_message(
                    self.lan_client, 
                    f"anycubic/anycubicCloud/v1/web/printer/20025/{self.lan_device_id}/video", 
                    local_video_payload
                )
                log(LOG_INFO, "[ROUTER] Dispatched startCapture with dummy credentials to wake up gkcam.")
                return

        # Intercept stop event — kill agora_pusher
        elif isinstance(payload, dict) and payload.get('action') == 'stopCapture':
            log(LOG_INFO, "[ROUTER] Intercepted stopCapture. Stopping agora_pusher...")
            self.stop_agora_pusher()
            
            report_topic = topic.replace('/web/', '/app/')
            if report_topic.endswith('/control') or report_topic.endswith('/request'):
                report_topic = report_topic.rsplit('/', 1)[0]
            if not report_topic.endswith('/report'):
                report_topic += '/report'

            report_payload = {
                "type": "video",
                "action": "stopCapture",
                "msgid": payload.get("msgid", ""),
                "state": "leaveSuccess",
                "timestamp": now(),
                "code": 200,
                "msg": ""
            }
            self.send_message(self.cloud_client, report_topic, report_payload)
            
            # Put the local camera pipeline to sleep using the standard command
            local_video_payload = {
                "action": "stopCapture",
                "type": "video",
                "msgid": str(payload.get('msgid', uuid.uuid4())),
                "data": None
            }
            self.send_message(
                self.lan_client, 
                f"anycubic/anycubicCloud/v1/web/printer/20025/{self.lan_device_id}/video", 
                local_video_payload
            )
            log(LOG_INFO, "[ROUTER] Dispatched stopCapture to LAN to sleep gkcam.")
            return

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

    # ------------------------------------------------------------------
    # Agora pusher lifecycle
    # ------------------------------------------------------------------
    def launch_agora_pusher(self, shengwang_data):
        """Launch the ffmpeg | agora_pusher streaming pipeline."""
        self.stop_agora_pusher()  # kill any existing pipeline first

        script_dir = os.path.dirname(os.path.realpath(__file__))
        pusher_path = os.path.join(script_dir, 'agora_pusher')

        appid = shengwang_data.get('appid', '')
        channel = shengwang_data.get('channel', '')
        token = shengwang_data.get('rtc_token', '') or shengwang_data.get('token', '')
        license_key = shengwang_data.get('license', '')
        uid = str(shengwang_data.get('uid', 0))
        enc_mode = shengwang_data.get('encryption_mode', '') or shengwang_data.get('mode', '')
        enc_key = shengwang_data.get('encryption_key', '') or shengwang_data.get('key', '')
        enc_salt = shengwang_data.get('encryption_kdf_salt', '') or shengwang_data.get('salt', '')

        if not appid or not channel:
            log(LOG_WARNING, "[AGORA] Missing appid or channel, cannot start pusher.")
            return

        log(LOG_INFO, f"[AGORA] Credentials: appid={appid[:8]}... channel={channel[:8]}... uid={uid} enc_mode={enc_mode}")

        # Build: ffmpeg ... | agora_pusher ...
        # Add nobuffer, low_delay, and analyzeduration 0 to completely eliminate ffmpeg startup lag
        cmd = (
            f"ffmpeg -nostdin -loglevel quiet "
            f"-fflags nobuffer -flags low_delay -analyzeduration 0 -probesize 32 "
            f"-i http://127.0.0.1:18088/flv "
            f"-vcodec copy -f h264 - | "
            f"'{pusher_path}' '{appid}' '{channel}' '{token}' '{license_key}' '{uid}' -1 "
            f"'{enc_mode}' '{enc_key}' '{enc_salt}'"
        )

        log(LOG_INFO, f"[AGORA] Launching pipeline: ffmpeg | agora_pusher (channel={channel})")
        try:
            self.agora_proc = subprocess.Popen(
                cmd, shell=True,
                cwd=script_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            log(LOG_INFO, f"[AGORA] Pipeline started (PID {self.agora_proc.pid})")
        except Exception as e:
            log(LOG_ERROR, f"[AGORA] Failed to start pipeline: {e}")

    def stop_agora_pusher(self):
        """Kill the ffmpeg | agora_pusher pipeline if running."""
        if self.agora_proc is not None:
            log(LOG_INFO, f"[AGORA] Stopping pipeline (PID {self.agora_proc.pid})...")
            try:
                pgid = os.getpgid(self.agora_proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
            try:
                self.agora_proc.wait(timeout=2)
            except Exception:
                pass
            self.agora_proc = None
            log(LOG_INFO, "[AGORA] Pipeline stopped.")

    # ------------------------------------------------------------------
    # MQTT connections
    # ------------------------------------------------------------------
    def connect_cloud_mqtt(self):
        mqtt_broker = self.cloud_config['mqttBroker']
        mqtt_username, mqtt_password = self.get_cloud_mqtt_credentials()
        
        def mqtt_on_connect(client, userdata, flags, rc, *args, **kwargs):
            log(LOG_INFO, '[cloud] Connected.')
            self.cloud_client.subscribe(f'anycubic/anycubicCloud/v1/+/printer/{self.model_id}/{self.cloud_device_id}/#')
        def mqtt_on_connect_fail(client, userdata, *args, **kwargs):
            log(LOG_WARNING, '[cloud] Failed to connect')
        def mqtt_on_disconnect(client, userdata, rc, *args, **kwargs):
            log(LOG_WARNING, f'[cloud] Disconnected (reason: {rc})')
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
            log(LOG_INFO, '[lan] Connected.')
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
            log(LOG_WARNING, f'[lan] Disconnected (reason: {rc})')
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

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------
    def main(self):
        self.connect_cloud_mqtt()
        self.connect_lan_mqtt()

        log(LOG_INFO, "[SYSTEM] MQTT bridge is running. Waiting for messages...")

        # Block forever — paho runs in background threads.
        # The mode_watchdog will SIGKILL us if the mode changes.
        # The supervisor will restart us if we crash.
        while True:
            time.sleep(60)

    def cleanup(self):
        """Best-effort cleanup on graceful shutdown."""
        self.stop_agora_pusher()
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
    program = None
    
    def sig_handler(signum, frame):
        log(LOG_INFO, f"[SYSTEM] Received signal {signum}, cleaning up...")
        if program:
            program.cleanup()
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    try:
        program = Program()
        program.main()
    except SystemExit:
        raise
    except Exception as e:
        log(LOG_ERROR, f"[SYSTEM] Fatal: {e}")
        log(LOG_ERROR, traceback.format_exc())
        if program:
            program.cleanup()
        sys.exit(1)