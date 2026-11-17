# LAN Tunnel KS1

This Rinkhals app exposes the printer's main and nozzle MCUs over the printer's Ethernet interface:

| Printer TCP port | MCU device |
| --- | --- |
| `7003` | `/dev/ttyS3` (main MCU) |
| `7005` | `/dev/ttyS5` (nozzle MCU) |

It can also configure a static address on the printer's Ethernet interface and keep Anycubic's private LAN mode enabled with `lanModeWatchdog.sh`.

## Private LAN mode and cloud connectivity

Anycubic's private LAN mode is intended to keep printer access on the local network rather than through the Anycubic cloud. When this mode is active, the printer's cloud connection is disabled.

Some firmware versions can sporadically re-enable the cloud connection by themselves, which disables private LAN mode. `lanModeWatchdog.sh` is a workaround for that behavior: every two minutes it checks the printer's local API and re-enables private LAN mode when necessary.

Enable **Keep LAN print mode enabled** only when this behavior is wanted. The watchdog requires the printer's local API on TCP port `18086` and runs only while this app is running.

> **Warning:** Enabling the static network configuration changes the printer's Ethernet settings. Configure the correct interface, MAC address, address, and netmask before starting the app. A wrong value may make the printer unreachable over Ethernet.

## Install and configure

The app package declares default values in `app.json`, but Rinkhals does **not** create a persistent per-app configuration file until a setting is saved through the UI or `set_app_property`.

After installing the app, but **before starting it for the first time**, connect to the printer shell and create the persistent configuration:

```sh
source /useremain/rinkhals/.current/tools.sh

set_app_property lan-tunneled-klipper network_enabled True
set_app_property lan-tunneled-klipper interface eth1
set_app_property lan-tunneled-klipper mac_address 00:E0:4C:44:4E:50
set_app_property lan-tunneled-klipper ip_address 192.168.10.67
set_app_property lan-tunneled-klipper netmask 255.255.255.0
set_app_property lan-tunneled-klipper watchdog_enabled True
```

Change the example MAC address and IP address to values appropriate for the local network. The commands create the persistent configuration at:

```text
/useremain/home/rinkhals/apps/lan-tunneled-klipper.config
```

Alternatively, change a option in the Rinkhals app UI. Saving a value there creates the same persistent configuration file.

NOTE: Rinkhals App setting framework has currently no (working) textedit fields implemented. So you cannot change the MAC address or IP address in the Rinkhals app UI, you can see what is the current value, but you cannot change it there.

So use the set_app_property instructions above intially to change to change e.g. the MAC address and IP address, afterwards you can also change it in the persistent config file if you prefer.


### Disable static Ethernet configuration

To run only the MCU tunnel without changing Ethernet configuration:

```sh
source /useremain/rinkhals/.current/tools.sh
set_app_property lan-tunneled-klipper network_enabled False
```

### LAN Mode (de)activation workaround

watchdog_enabled property disables/enables workaround to make sure LAN mode stays enabled, even if Anycubic FW sometimes disables it automaticaly by itself. To disable the workaround, use this command in the Rinkhals UI or from a shell:

```sh
source /useremain/rinkhals/.current/tools.sh
set_app_property lan-tunneled-klipper watchdog_enabled False
```

## Start and stop

Once configuration is saved, start or stop the app from the Rinkhals UI. The app starts two `socat` TCP listeners on the configured Ethernet address:

- Main MCU: TCP port `7003`
- Nozzle MCU: TCP port `7005`

Stopping the app stops its tunnel processes and the private LAN-mode watchdog if it was enabled.

Note: On the klippy host a socat instance like this is expected (two times, one for each MCU)
```sh
/usr/bin/socat -d -d \
  PTY,link=/dev/ttyMCU1,raw,echo=0,mode=660,group=dialout,wait-slave,pty-interval=0.1,sitout-eio=5 \
  TCP:<printer ip>:7003,nodelay,keepalive,forever,interval=1

/usr/bin/socat -d -d \
  PTY,link=/dev/ttyMCU2,raw,echo=0,mode=660,group=dialout,wait-slave,pty-interval=0.1,sitout-eio=5 \
  TCP:<printer ip>:7005,nodelay,keepalive,forever,interval=1

```
## External Klipper host setup

For Raspberry Pi, external Klipper-host, `socat`, systemd-service, and `printer.cfg` setup instructions, follow the original [moosbewohner/Kobra-S1-Lan-Tunnel guide](https://github.com/moosbewohner/Kobra-S1-Lan-Tunnel/blob/main/README.md).
