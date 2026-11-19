# HelixScreen

[HelixScreen](https://github.com/prestonbrown/helixscreen) is a touch screen
interface for Klipper printers. This app runs it on the printer's own screen,
in place of Anycubic's: print files, temperatures and fans, the ACE Pro with
its dryer, the chamber light, bed mesh, macros and more.

It talks to Moonraker, so it needs real Klipper behind it.

## What you need

- **A Kobra S1.** That is the only printer this has been run on. On other
  models the app installs but doesn't start. The Kobra 3 family has a smaller,
  rotated, resistive screen that needs its own settings and a tester.
- **Klipper (vanilla), or a tunneled Klipper** (tunneled-klipper or
  lan-tunneled-klipper), installed and enabled. Rinkhals won't install or
  enable HelixScreen without one of them, and won't let you remove the last
  one while HelixScreen is installed.
- Rinkhals newer than `20261117_01_test` if you want the chamber light button
  to work, and Klipper (vanilla) 1.4.0 or later, which adds the light switch.

Anycubic's own firmware (GoKlipper) is not supported. HelixScreen expects real
Klipper, and nobody has checked what works without it. So the app never puts
HelixScreen on the screen while GoKlipper is running the printer:

- If you stop, disable or remove your Klipper app, GoKlipper comes back, and
  within about ten seconds so does Anycubic's screen.
- The same happens if Klipper (vanilla) can't start and falls back to GoKlipper.
- HelixScreen returns by itself once a Klipper app is running again.

This doesn't depend on your Rinkhals version. Newer Rinkhals also refuses to
disable or remove the last Klipper app while HelixScreen is enabled or
installed, but on older ones nothing stops you, and this is what keeps the
printer usable when you do.

## What happens to Anycubic's screen

Only one program can own the screen. While this app is running, Anycubic's
interface (K3SysUi) is stopped. That has consequences:

- **Wi-Fi setup, Anycubic firmware updates and the Rinkhals touch menu are not
  reachable** while HelixScreen is up. Use the Rinkhals web portal
  (`http://<printer-ip>:8090`) for apps and updates.
- **To get Anycubic's screen back**, run the **ANYCUBIC_SCREEN** macro from
  HelixScreen (see below), or stop this app from the web portal. K3SysUi
  starts again within a few seconds. When it starts it re-checks the network,
  so expect a short network blip.
- If HelixScreen can't stay up (three quick exits in a row), the app gives the
  screen back to K3SysUi by itself and says so in the Rinkhals log.
- At boot, Anycubic's screen shows for a few seconds first. HelixScreen takes
  over once Klipper is in charge.

Memory is not a concern: HelixScreen uses about 20 MB where K3SysUi uses 40 MB
or more.

## Switching back to Anycubic's screen

With Klipper (vanilla), the app gives Klipper a macro called
**ANYCUBIC_SCREEN**. Running it stops HelixScreen and brings Anycubic's screen
back, within about ten seconds.

- It is in HelixScreen's macro list. To get a button for it on the home
  screen, long-press the home screen, add a macro button and pick
  ANYCUBIC_SCREEN.
- The macro appears after Klipper has restarted once with the app installed.
  The first time the app starts, it adds the macro to your Klipper config and
  says so in the Rinkhals log; restart Klipper (or power cycle) to load it.
- **To return to HelixScreen**, start the app again: on the printer under
  Settings > Rinkhals > Apps > HelixScreen, or from the web portal. It also
  comes back after a reboot, as long as the app is enabled.

It costs nothing while you aren't using it. A Klipper macro can't run a
program, so the macro saves a count with Klipper's `SAVE_VARIABLE`, and the app
only looks at whether that file on the printer has changed. Nothing asks
Moonraker or Klipper anything.

With a tunneled Klipper there is no such button: Klipper's files are on your
other machine, where the app can't see them. Stop the app from the web portal
instead.

## First start

HelixScreen opens its setup wizard: language, printer, sensors. The printer is
recognized as a Kobra S1 on its own. The ACE Pro and the chamber light are
found without any setup.

Touch needs no calibration.

## Using it with a tunneled Klipper

With tunneled-klipper, Klipper and Moonraker run on another machine. Point
HelixScreen at it in the app's settings in Rinkhals:

- **Moonraker host**: that machine's address (default `127.0.0.1`, the printer)
- **Moonraker port**: default `7125`

Restart the app after changing them. This setup hasn't been tested by us.

## Where things are

| | |
|---|---|
| Settings | `/useremain/home/rinkhals/helixscreen/` (kept across app updates) |
| Log | `/tmp/rinkhals/app-helixscreen.log` |

To start over, stop the app and delete the settings folder.

HelixScreen's own updater is switched off. New versions come with this app.

## Where it comes from

`helixscreen/` is HelixScreen 1.0.10, unmodified:

- The data files are from HelixScreen's release package for the FlashForge
  AD5M, a fully static ARMv7 build for a framebuffer screen.
- `bin/helix-screen` is compiled from the same tag with one build option
  changed, `HELIX_HAS_ACE=1`. Upstream builds its small printer packages
  without Anycubic ACE support.

`get-helixscreen.sh` rebuilds all of it, and is the pointer to the exact
sources. HelixScreen is licensed under the GPL-3.0; so is this packaging.

Three things in this app are ours: `app.sh`, `helixscreen.cfg` with the
ANYCUBIC_SCREEN macro, and `rinkhals-kobra.json`, which teaches HelixScreen's
printer detection the host names Rinkhals gives the printers
(`kobra-ks1-xxxx` and so on).

## Not checked yet

- A whole print with HelixScreen on the screen, and how much CPU it takes
  then. The printers have one core, shared with Klipper.
- The backlight going to sleep and waking.
- Sound. It is switched off.
- Any printer other than the Kobra S1.
