import os
import sys
import socket
import configparser
import subprocess
import hashlib
import json
import paho.mqtt.client as mqtt
import urllib
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
        print(datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ' ' + message, flush = True)
def md5(input: str) -> str:
    return hashlib.md5(input.encode('utf-8')).hexdigest()
def now() -> int:
    return round(time.time() * 1000)


def get_remote_mode() -> str:
    # Same default as rinkhals-ui.py: no file means the printer is in cloud mode
    if os.path.isfile('/useremain/dev/remote_ctrl_mode'):
        with open('/useremain/dev/remote_ctrl_mode', 'r') as f:
            return f.read().strip()
    return 'cloud'


def wait_for_file(path: str, timeout: float = 120.0, poll_interval: float = 1.0) -> bool:
    """
    Block until `path` exists. Returns True if it appeared within `timeout`
    seconds, False on timeout.

    Apps can start before the stock Anycubic firmware has finished laying
    down its config files in /userdata/app/gk/config and the boot-time
    descriptors in /useremain/dev/. Reading them eagerly in __init__ races
    that boot sequence and was the main reason cloud2lan-bridge "didn't
    start sometimes."
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

    Used to gate the LAN MQTT connect on gklib's local broker actually being
    up. Connecting before the broker has bound the port produces a
    ConnectionRefusedError that, under the previous code, propagated out to
    main() and SIGKILLed the process with no respawn.
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


class Program:

    # Configuration
    cloud_config = None
    api_config = None

    # Environment
    firmware_version = None
    model_id = None
    cloud_device_id = None
    lan_device_id = None

    # MQTT
    cloud_client = None
    lan_client = None

    def __init__(self):
        # Wait for the boot-time files we depend on. The stock Anycubic
        # firmware writes these as part of its startup; if Rinkhals brings
        # this app up before that's done, eager reads explode.
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

        self.cloud_config = self.get_cloud_config()
        self.api_config = self.get_api_config()

        self.firmware_version = self.get_firmware_version()
        self.model_id = self.api_config['cloud']['modelId']
        self.cloud_device_id = self.cloud_config['deviceUnionId']
        self.lan_device_id = self.get_lan_device_id()

    def get_cloud_config(self):
        config = configparser.ConfigParser()
        config.read('/userdata/app/gk/config/device.ini')

        environment = config['device']['env']
        zone = config['device']['zone']

        if zone == 'cn':
            section_name = f'cloud_{environment}'
        else:
            section_name = f'cloud_{zone}_{environment}'

        cloud_config = config[section_name]
        return cloud_config
    def get_api_config(self):
        with open('/userdata/app/gk/config/api.cfg', 'r') as f:
            return json.loads(f.read())

    def get_ssl_context(self) -> ssl.SSLContext:
        cert_path = self.cloud_config['certPath']
        cert_file = f'{cert_path}/deviceCrt'
        key_file = f'{cert_path}/devicePk'
        ca_file = f'{cert_path}/caCrt'

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.set_ciphers(('ALL:@SECLEVEL=0'),)
        if cert_file and key_file:
            ssl_context.load_cert_chain(cert_file, key_file, None)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        if ca_file:
            ssl_context.load_verify_locations(ca_file)

        return ssl_context
    def get_firmware_version(self) -> str:
        with open('/useremain/dev/version', 'r') as f:
            return f.read().strip()
    def get_lan_device_id(self) -> str:
        with open('/useremain/dev/device_id', 'r') as f:
            return f.read()

    def get_cloud_mqtt_credentials(self):
        device_key = self.cloud_config['deviceKey']
        cert_path = self.cloud_config['certPath']

        command = f'printf "{device_key}" | openssl rsautl -encrypt -inkey {cert_path}/caCrt -certin -pkcs | base64 -w 0'
        encrypted_device_key = subprocess.check_output(['sh', '-c', command])
        encrypted_device_key = encrypted_device_key.decode('utf-8').strip()

        taco = f'{self.cloud_device_id}{encrypted_device_key}{self.cloud_device_id}'
        username = f'dev|fdm|{self.model_id}|{md5(taco)}'

        return (username, encrypted_device_key)
    def get_lan_mqtt_credentials(self):
        with open('/userdata/app/gk/config/device_account.json', 'r') as f:
            json_data = f.read()
            data = json.loads(json_data)

            mqtt_username = data['username']
            mqtt_password = data['password']

        return (mqtt_username, mqtt_password)

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

        if not topic.endswith('/response'):
            self.send_message(self.lan_client, topic.replace(self.cloud_device_id, self.lan_device_id), payload)
    def on_lan_message(self, topic, payload):
        log(LOG_DEBUG, f'[lan] Received {topic} = {str(payload)}')

        if not topic.endswith('/response'):
            if topic.endswith('/report'):
                log(LOG_INFO, f'[lan] Received report for {payload.get("type")}/{payload.get("action")}')
            else:
                log(LOG_INFO, f'[lan] Received {payload.get("type")}/{payload.get("action")}')

        if topic.endswith('/report') or topic.endswith('/response'):
            self.send_message(self.cloud_client, topic.replace(self.lan_device_id, self.cloud_device_id), payload)

    def connect_cloud_mqtt(self):
        mqtt_broker = self.cloud_config['mqttBroker']
        mqtt_username, mqtt_password = self.get_cloud_mqtt_credentials()

        def mqtt_on_connect(client, userdata, connect_flags, reason_code, properties):
            log(LOG_INFO, '[cloud] Connected')
            self.cloud_client.subscribe(f'anycubic/anycubicCloud/v1/+/printer/{self.model_id}/{self.cloud_device_id}/#')
        def mqtt_on_connect_fail(client, userdata):
            log(LOG_WARNING, '[cloud] Failed to connect')
        def mqtt_on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
            log(LOG_WARNING, f'[cloud] Disconnected (reason: {reason_code}); paho will retry')
        def mqtt_on_log(client, userdata, level, buf):
            #log(LOG_DEBUG, buf)
            pass
        def mqtt_on_message(client, userdata, msg):
            # Wrap in try/except so a single malformed payload doesn't kill
            # the callback path. paho catches the exception and continues,
            # but the message is lost; logging it makes debugging tractable.
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                self.on_cloud_message(msg.topic, payload)
            except Exception as e:
                log(LOG_ERROR, f'[cloud] Failed to handle {msg.topic}: {e}')

        mqtt_broker_endpoint = urllib.parse.urlparse(mqtt_broker)

        # The callbacks above use paho's VERSION2 signatures. Without asking for
        # it, paho 2.x falls back to VERSION1, calls on_disconnect with one
        # argument fewer, and the TypeError kills the network thread on the
        # first disconnect, so the client never reconnects.
        self.cloud_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5, client_id=self.cloud_device_id)
        self.cloud_client.enable_logger()

        if mqtt_broker_endpoint.scheme == 'ssl':
            self.cloud_client.tls_set_context(self.get_ssl_context())
            self.cloud_client.tls_insecure_set(True)

        self.cloud_client.on_connect = mqtt_on_connect
        self.cloud_client.on_connect_fail = mqtt_on_connect_fail
        self.cloud_client.on_disconnect = mqtt_on_disconnect
        self.cloud_client.on_message = mqtt_on_message
        self.cloud_client.on_log = mqtt_on_log

        self.cloud_client.username_pw_set(mqtt_username, mqtt_password)

        # Retry with exponential backoff. Network may not be up yet at boot,
        # DNS may not be ready, the cert may not be loaded, etc. Fail loudly
        # only after multiple attempts.
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

        # Bounded wait for CONNACK so we don't hang forever on a half-open
        # connection where TCP completed but the broker never replied.
        deadline = time.time() + 30
        while not self.cloud_client.is_connected():
            if time.time() >= deadline:
                raise RuntimeError('Cloud MQTT TCP connected but never got CONNACK')
            time.sleep(0.25)
    def connect_lan_mqtt(self):
        # Wait for gklib's local broker to be reachable. This is by far the
        # most common race at boot: the bridge starts in parallel with the
        # Anycubic stack and gklib hasn't bound port 9883 yet.
        log(LOG_INFO, '[lan] Waiting for local MQTT broker on 127.0.0.1:9883...')
        if not wait_for_tcp('127.0.0.1', 9883, timeout=120):
            raise RuntimeError('Timed out waiting for local MQTT broker (gklib not up?)')
        log(LOG_INFO, '[lan] Local MQTT broker is reachable')

        mqtt_username, mqtt_password = self.get_lan_mqtt_credentials()

        def mqtt_on_connect(client, userdata, connect_flags, reason_code, properties):
            log(LOG_INFO, '[lan] Connected')
            self.lan_client.subscribe(f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.lan_device_id}/#')
        def mqtt_on_connect_fail(client, userdata):
            log(LOG_WARNING, '[lan] Failed to connect')
        def mqtt_on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
            log(LOG_WARNING, f'[lan] Disconnected (reason: {reason_code}); paho will retry')
        def mqtt_on_log(client, userdata, level, buf):
            #log(LOG_DEBUG, buf)
            pass
        def mqtt_on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                self.on_lan_message(msg.topic, payload)
            except Exception as e:
                log(LOG_ERROR, f'[lan] Failed to handle {msg.topic}: {e}')

        self.lan_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5, client_id=self.lan_device_id)
        self.lan_client.enable_logger()

        self.lan_client.tls_set_context(self.get_ssl_context())
        self.lan_client.tls_insecure_set(True)

        self.lan_client.on_connect = mqtt_on_connect
        self.lan_client.on_connect_fail = mqtt_on_connect_fail
        self.lan_client.on_disconnect = mqtt_on_disconnect
        self.lan_client.on_message = mqtt_on_message
        self.lan_client.on_log = mqtt_on_log

        self.lan_client.username_pw_set(mqtt_username, mqtt_password)

        # Retry the actual connect even though we just confirmed the port
        # is open. There's a small window where gklib has the TCP socket
        # listening but isn't fully processing CONNECT packets yet.
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

        self.connect_cloud_mqtt()
        self.connect_lan_mqtt()

        payload = {
            'type': 'lastWill',
            'action': 'onlineReport',
            'timestamp': now(),
            'msgid': str(uuid.uuid4()),
            'state': 'online',
            'code': 200,
            'msg': 'device online',
            'data': None
        }
        self.send_message(self.cloud_client, f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.cloud_device_id}/lastWill/report', payload)

        payload = {
            'type': 'status',
            'action': 'workReport',
            'timestamp': now(),
            'msgid': str(uuid.uuid4()),
            'state': 'free',
            'code': 200,
            'msg': '',
            'data': None
        }
        self.send_message(self.cloud_client, f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.cloud_device_id}/status/report', payload)

        payload = {
            'type': 'ota',
            'action': 'reportVersion',
            'timestamp': now(),
            'msgid': str(uuid.uuid4()),
            'state': 'done',
            'code': 200,
            'msg': 'done',
            'data': {
                'device_unionid': self.cloud_device_id,
                'machine_version': '1.1.0',
                'peripheral_version': '',
                'firmware_version': self.firmware_version,
                'model_id': self.model_id
            }
        }
        self.send_message(self.cloud_client, f'anycubic/anycubicCloud/v1/printer/public/{self.model_id}/{self.cloud_device_id}/ota/report', payload)

        # Heartbeat loop. Logging the connection state every few minutes
        # makes it possible to tell from the app log whether the bridge is
        # actually alive and which side dropped if something stops working.
        # Also watch for a switch back to cloud mode: the firmware then logs in
        # to the cloud with the same client id, and the two would keep knocking
        # each other off the broker.
        HEARTBEAT_INTERVAL = 300
        MODE_CHECK_INTERVAL = 5
        last_heartbeat = time.time()
        while True:
            time.sleep(MODE_CHECK_INTERVAL)

            mode = get_remote_mode()
            if mode != 'lan':
                log(LOG_WARNING, f'Printer switched to {mode} mode, stopping. Start the app again or reboot once LAN mode is back on')
                self.cloud_client.disconnect()
                self.lan_client.disconnect()
                return

            if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                last_heartbeat = time.time()
                cloud_ok = self.cloud_client.is_connected() if self.cloud_client else False
                lan_ok = self.lan_client.is_connected() if self.lan_client else False
                log(LOG_INFO, f'[heartbeat] cloud={"up" if cloud_ok else "DOWN"} lan={"up" if lan_ok else "DOWN"}')


if __name__ == "__main__":
    # In cloud mode the firmware is already logged in to the cloud as this
    # printer and there is no local broker to bridge to. Exit 0 so the
    # supervisor doesn't respawn us
    mode = get_remote_mode()
    if mode != 'lan':
        log(LOG_WARNING, f'Printer is in {mode} mode, the bridge only runs in LAN mode. Enable LAN mode, then start the app again or reboot')
        sys.exit(0)

    try:
        program = Program()
        program.main()
    except Exception as e:
        log(LOG_ERROR, str(e))
        log(LOG_ERROR, traceback.format_exc())
        # sys.exit so the supervisor wrapper sees a non-zero exit and
        # respawns. The previous os.kill(SIGKILL) bypassed even our own
        # atexit handlers and looked indistinguishable from an external
        # kill - the supervisor couldn't tell whether to restart.
        sys.exit(1)
