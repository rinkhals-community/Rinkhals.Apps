# Static USB Ethernet MAC

Assign a stable MAC address to a USB Ethernet adapter connected to the printer. This is useful for DHCP reservations, firewall rules or keeping a consistent network identity across reboots.

The app is opt-in: it does nothing until you enable it.

> [!NOTE]
> If your adapter already reports the same MAC address after every reboot, you do not need this app. Create a DHCP reservation for that MAC address on your router instead.

## Compatible adapters

USB Ethernet adapters supported by the stock kernel, for example:

- ASIX AX88179 / AX88179A
- Realtek RTL8153
- ASIX AX88772

See the [USB devices compatibility](https://rinkhals-community.github.io/Rinkhals/printers/usb-compatibility/) page for the current list.

## How it works

On startup, the app looks at `eth*` / `en*` interfaces and checks whether `readlink -f /sys/class/net/<iface>/device` contains `usb`. You can override the detection with the **Interface override** setting if your adapter uses a different name or if you have more than one USB adapter.

The MAC address is resolved in this order:

1. **Custom MAC** — the value of the **MAC address** setting, if set.
2. **Derived MAC** — otherwise, the app hashes `/userdata/ethaddr.txt`, then `$KOBRA_DEVICE_ID`, then `/useremain/dev/device_id`, and uses the first 12 hex digits of the MD5 hash. The first octet is forced to a **locally administered unicast** address (bit 1 = 1, bit 0 = 0), so the second hex digit is always `2`, `6`, `A` or `E`. This guarantees that the result does not collide with a real vendor-assigned OUI.

After resolving the address, the app:

1. brings the interface down,
2. applies the MAC with `ifconfig <iface> hw ether <mac>`,
3. brings the interface back up,
4. restarts the DHCP client on that interface so it gets a lease for the new MAC.

The restart is needed because the stock firmware starts DHCP before Rinkhals apps run. Without it, the printer could keep a lease associated with the old MAC. Disable it with **Restart DHCP** if you configure the interface manually.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| MAC address | *(empty)* | Custom MAC address in `aa:bb:cc:dd:ee:ff` format. If empty, a deterministic MAC is derived from the printer ID. |
| Interface override | *(empty)* | Interface name to use instead of auto-detection (for example `eth1`). The interface must be USB-backed. |
| Restart DHCP after changing the MAC | `True` | Restart `udhcpc` on the interface after applying the MAC. |

## Installation

Install the app from the Rinkhals App Store, or install one of the `app-static-usb-ether-mac-*.swu` packages from the [releases page](https://github.com/rinkhals-community/Rinkhals.Apps/releases), or copy the app directory to `/useremain/home/rinkhals/apps/`.

Enable it from the Rinkhals touch UI (Settings → Rinkhals → Apps), or create the enable marker and reboot:

```bash
touch /useremain/home/rinkhals/apps/static-usb-ether-mac.enabled
```

## Set a custom MAC address

From the touch UI, open the app settings and enter the MAC address.

From SSH:

```bash
source /useremain/rinkhals/.current/tools.sh
set_app_property static-usb-ether-mac mac_address 02:11:22:33:44:55
```

Then restart the app (or reboot):

```bash
/useremain/home/rinkhals/apps/static-usb-ether-mac/app.sh stop
/useremain/home/rinkhals/apps/static-usb-ether-mac/app.sh start
```

Use a **locally administered unicast** address (`02`, `06`, `0A` or `0E` as the first octet) unless you have a good reason to use something else. Do not reuse a real vendor OUI or another device's MAC address.

> [!WARNING]
> Changing the MAC brings the interface down and back up. If you are connected over that USB Ethernet adapter, your SSH session will drop. Keep Wi-Fi enabled as a fallback while testing.

## Verify

Show the interface and its current MAC:

```bash
for i in /sys/class/net/eth*; do
    echo "$i: $(cat $i/address) -> $(readlink -f $i/device)"
done
```

Show the DHCP lease:

```bash
ifconfig
cat /tmp/rinkhals/app-static-usb-ether-mac.log
```

After a reboot, the USB adapter should come back with the same MAC address, and your router's DHCP reservation should hand out the same IP.

## Troubleshooting

- **No USB Ethernet interface found** — check `dmesg` for the adapter driver and confirm the interface name with `ifconfig -a`. Set **Interface override** if auto-detection picks the wrong interface.
- **Failed to set MAC address** — some adapters or drivers reject MAC changes. Check `dmesg`; if the driver does not support it, the adapter cannot be used with this app.
- **Printer gets a different IP after reboot** — make sure the DHCP reservation on the router uses the new MAC (the one shown by the app log), not the adapter's original random MAC.
- **Printer becomes unreachable after enabling** — connect over Wi-Fi or the touchscreen and check `/tmp/rinkhals/app-static-usb-ether-mac.log`.

## Dependencies

`ifconfig`, `udhcpc`, `md5sum` and standard BusyBox tools. No additional packages are required.
