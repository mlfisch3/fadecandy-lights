# Bring-up checklist

Work through this in order with the real hardware.
Every step says what to verify and what a failure there means, so a problem is localised rather than guessed at.

**None of this has been verified on hardware.**
The controller was developed and tested without a Pi, a Fadecandy or a strip: the test suite exercises the engine end to end against a test-double OPC receiver, which covers everything up to the USB link and nothing beyond it.
Step 5 is the first time real light is involved, and it is the step to be careful at.

Before you start, read the power sizing section of [../README.md](../README.md) and know the actual usable current of the supply you are about to connect.

---

## Step 0 - Bench setup, nothing connected

Have to hand:

- The Pi 3 B+ with a fresh Raspberry Pi OS image, on your WiFi, reachable over SSH.
- The Fadecandy board and a USB cable.
- One short test strip. **Not the full 512-pixel run.** Something under about 30 pixels that a bench supply can drive comfortably.
- The 5 V supply for the strip, and a multimeter if you have one.

Doing the first light-up on a short strip is the whole point of this ordering.
If something is wrong, a short strip fails visibly and harmlessly; 512 pixels fails expensively.

---

## Step 1 - Install

```bash
git clone <this repo> fadecandy-lights
cd fadecandy-lights
sudo ./deploy/setup.sh
```

Partway through it will show you a plan for installing `fcserver` and ask before doing it; say yes.
`fcserver` is not part of this repository, so this step fetches it - from an unmaintained third-party mirror, because the original upstream repository is gone from GitHub.
Read [fcserver.md](fcserver.md) before you answer if you want to know exactly what is being fetched and from where.

**Verify:** the script ends with a "Done" block listing the API URL, config paths and the power ceiling, and the fcserver step ended with "fcserver is installed" after printing a version line like `fcserver-1.04-25-gf911031`.

**If the fcserver step warned instead of finishing:** everything else was installed, so fix just that and rerun `sudo ./deploy/install-fcserver.sh` on its own rather than the whole script.

- *"unsupported architecture"* - you are not on ARM. There is no prebuilt `fcserver` for `amd64`; use a Raspberry Pi OS image.
- *"sha256 mismatch"* - the mirror served bytes we did not expect. Do not work around this by hand; see [fcserver.md](fcserver.md).
- *"download failed"* - network, or the mirror has gone the way of the original. [fcserver.md](fcserver.md) has the manual steps.
- *"did not run"* with `required file not found` - the `armhf` runtime is missing on a 64-bit image. This is the failure the script exists to prevent; rerun it with `sudo`.

**If it warned that `/usr/local/bin/fcserver` is not the pinned build:** you have an `fcserver` from somewhere else - most likely installed by hand before this script existed.
Nothing was changed, and the rest of the install is unaffected.
The warning prints both digests; if you want the pinned build in its place, run `sudo ./deploy/install-fcserver.sh --force`, which re-fetches and re-verifies it before overwriting.
If you would rather keep what you have, keep it - but the binary you are running has not been checked against anything.

**If a pip install fails with "no prebuilt wheel":** you are on an architecture or a Python version without wheels for one of numpy, pydantic-core or uvicorn.
Check `dpkg --print-architecture` (expect `arm64`, or `armhf` on a 32-bit image) and `python3 --version` (needs 3.10 or newer).
On `arm64` you will also see `armhf` in `dpkg --print-foreign-architectures` once `fcserver` is installed; that is deliberate and only affects `fcserver`, never the Python side.
This is the one failure worth stopping on rather than working around; compiling numpy on a Pi 3 takes the better part of an hour and can run the board out of memory.

---

## Step 2 - Service comes up with no hardware attached

Nothing is plugged in yet.

```bash
systemctl status fclights
curl -s http://localhost:7891/api/health
```

**Verify:** `{"ok":true,...,"opc_connected":false}`.

`opc_connected` being false is correct here, because fcserver has nothing to talk to.
The API answering at all proves the service account, the venv, the config file and the layout file are all good.

**If the service is failed:** `journalctl -u fclights -n 50`.
The most likely causes are a syntax error in `/etc/fclights/fclights.json` or `/etc/fclights/layout.json` (the message names the file and the problem), or `/var/lib/fclights` not being writable by the `fclights` user.

**If `curl` is refused:** the service is not listening. Check the journal, then confirm `server.host` in the config is `0.0.0.0` and not `127.0.0.1`.

---

## Step 3 - Reachable from the phone's network, and discoverable

From a laptop on the same WiFi:

```bash
curl -s http://raspberrypi.local:7891/api/health
avahi-browse -rt _fclights._tcp
```

**Verify:** the health response comes back, and `avahi-browse` lists the service with the right port and the `path`, `version` and `pixels` TXT records.

**If the health check works by IP but not by `.local`:** mDNS resolution on your laptop, not a rig problem. The Android app browses for the service type rather than resolving a hostname, so this is not fatal.

**If `avahi-browse` finds nothing:** check `/etc/avahi/services/fclights.service` exists and `systemctl status avahi-daemon` is running. Regenerate with `sudo /opt/fclights/venv/bin/fclights announce`.

**If nothing is reachable from another machine:** the Pi's firewall, or the API bound to localhost. This must work before the Android app can do anything.

---

## Step 4 - Fadecandy detected, fcserver talking

Plug the Fadecandy into the Pi. Leave the LED strip disconnected.

```bash
lsusb | grep -i 1d50
systemctl restart fcserver
journalctl -u fcserver -n 30
curl -s http://localhost:7891/api/health
```

**Verify:** `lsusb` shows `1d50:607a`, the fcserver journal reports a connected Fadecandy device with its serial number, and `/api/health` now reports `"opc_connected":true`.

**If `lsusb` shows nothing:** cable or board. Try another USB cable first; charge-only cables are a common waste of an afternoon.

**If the fcserver unit fails instantly with status 203 or `cannot execute: required file not found`:** the binary is a 32-bit `armhf` executable and this is a 64-bit image without the `armhf` runtime. `sudo ./deploy/install-fcserver.sh` fixes it; [fcserver.md](fcserver.md) explains it.

**If fcserver logs a permissions error opening the device:** the udev rule did not take. Check `/etc/udev/rules.d/99-fadecandy.rules` exists, confirm `fclights` is in `plugdev` (`id fclights`), then unplug and replug the board.

**If `opc_connected` stays false while fcserver is running:** fcserver is not listening where we expect. Confirm `listen` in `/etc/fclights/fcserver.json` is `["127.0.0.1", 7890]` and matches `opc` in `/etc/fclights/fclights.json`.

---

## Step 5 - First light, on the short strip

This is the careful step.

Wire the short test strip:

1. Fadecandy output 0 **data** pin to the strip's DI - the yellow pigtail.
2. Fadecandy output 0 **ground** pin to the strip's ground - the black pigtail.
3. Strip ground **also** to the 5 V supply ground, and strip 5 V - the red pigtail - to the supply's 5 V.
4. **Confirm the Fadecandy and the strip supply share a ground before powering anything on.** Without it the data line has no reference and the strip will show garbage or nothing. This is the single most common wiring mistake.
5. Do not power the strip from the Pi's 5 V pins.

Set the ceiling for the short strip and keep brightness low:

```bash
curl -s -X PUT http://localhost:7891/api/brightness \
     -H 'content-type: application/json' -d '{"brightness":0.1}'
curl -s -X PUT http://localhost:7891/api/effect \
     -H 'content-type: application/json' \
     -d '{"effect":"solid","params":{"color":[255,0,0]}}'
```

Now power the strip supply on.

**Verify:** the first pixels of the strip light dim red.
Only the number of pixels you actually connected will light; the rest of the 512-pixel frame goes nowhere, which is fine.

**If nothing lights:**
- No power at the strip: check the supply and the 5 V and ground connections at the strip end.
- Powered but dark: almost always the missing common ground, or data wired to DOUT instead of DIN. WS2812B strips are directional; the arrows printed on the strip must point away from the Fadecandy.

**If the colours are wrong** (red shows as green, say): your strip is a GRB-order variant. Fix it in fcserver, not here: see the Fadecandy documentation for per-device colour ordering.

**If the first pixel is right and the rest flicker or show noise:** a signal integrity problem. Shorten the data lead, and add a 300 to 500 ohm resistor in series with the data line at the Fadecandy end.

**If the whole strip flickers together:** power. The supply is sagging, or the ground return is too thin.

---

## Step 6 - Effects and the power governor

Still on the short strip.

```bash
for e in solid slowfade gradient breathe wipe rainbow twinkle fire; do
  curl -s -X PUT http://localhost:7891/api/effect \
       -H 'content-type: application/json' -d "{\"effect\":\"$e\"}" >/dev/null
  echo "$e"; sleep 4
done
```

**Verify:** each effect animates smoothly. `slowfade` will look static over four seconds - that is correct, its default cycle is fifteen minutes; check it separately below. Watch particularly at low brightness (`{"brightness":0.05}`): the fades should look smooth rather than stepping between a handful of levels. That smoothness is fcserver's temporal dithering, and it is the reason this rig uses a Fadecandy at all. If low brightness looks steppy and banded, dithering is not happening; check that fcserver is the process driving the board and that nothing has set `dither: false` in its config.

### The natural-light checks

These are what the installation is actually for, so give them more attention than the animated effects.

```bash
# Warm white, then daylight white. This is the control that gets used daily.
for k in 1800 2700 4000 6500; do
  curl -s -X PUT http://localhost:7891/api/effect \
       -H 'content-type: application/json' \
       -d "{\"effect\":\"solid\",\"params\":{\"color\":{\"kelvin\":$k}}}" >/dev/null
  echo "${k}K"; sleep 4
done
```

**Verify:** 1800 K is a deep candle amber, 2700 K reads like a warm bulb, 4000 K neutral, 6500 K a cool near-white. The progression should be smooth and each step obviously warmer or cooler than the last.

**If a temperature looks green or magenta rather than warm-to-cool:** your strip's channel order does not match what fcserver thinks it is, or the whitepoint in `/etc/fclights/fcserver.json` is skewed. Check a pure red, green and blue first (`{"color":[255,0,0]}` and so on) before touching the whitepoint.

Now the slow fade, which is the hardest thing this rig does:

```bash
curl -s -X PUT http://localhost:7891/api/effect \
     -H 'content-type: application/json' \
     -d '{"effect":"slowfade","params":{"color_a":{"kelvin":2700},"color_b":{"kelvin":3400},"period":300,"hold":0}}'
```

Leave it running for five minutes and watch it, at a low brightness (`0.15` or so) where banding is worst.

**Verify:** the colour drifts continuously. There should be no moment where it visibly jumps, and no sense of it sitting still and then stepping.

**If it steps:** this is the one failure mode worth chasing properly.
- Confirm `GET /api/status` reports `"dither": true`. If not, `dither` has been set false in the config.
- Confirm fcserver's own interpolation and dithering are enabled - they are on by default, so check nothing in `/etc/fclights/fcserver.json` has disabled them.
- Confirm `measured_fps` is near the configured rate. The dithering works by spreading a value across successive frames, so a stalled or badly irregular frame rate weakens it.

Then check the governor:

```bash
curl -s -X PUT http://localhost:7891/api/brightness \
     -H 'content-type: application/json' -d '{"brightness":1.0}'
curl -s -X PUT http://localhost:7891/api/effect \
     -H 'content-type: application/json' \
     -d '{"effect":"solid","params":{"color":[255,255,255]}}'
curl -s http://localhost:7891/api/status | python3 -m json.tool
```

**Verify:** with the configured ceiling below full-white draw, `power.clamped` is `true`, `power.delivered_amps` is at or just under `power.limit_amps`, and `power.scale` is below 1.
The strip is visibly dimmer than the raw value would suggest. That is the governor working.

If you have a meter in line with the strip supply, compare the measured current against `power.delivered_amps`.
Expect the measurement to come in **below** the prediction, because fcserver applies gamma downstream and gamma only ever lowers a value.
A measurement *above* the prediction means the current model is wrong for your strip; lower `power.limit_amps` and investigate before scaling up.

**If `clamped` is false when you expected it:** your ceiling is above full-white draw for this pixel count, which for a short test strip is entirely plausible. Set `power.limit_amps` temporarily to something small like `2.0` and repeat. It has to stay above the idle draw of the whole configured pixel count - 0.512 A for 512 pixels - or the service refuses to start and says so; `fclights check` prints that figure as `idle draw`.

---

## Step 7 - Restart and power-cut behaviour

```bash
curl -s -X PUT http://localhost:7891/api/effect \
     -H 'content-type: application/json' -d '{"effect":"fire"}'
curl -s -X POST http://localhost:7891/api/scenes \
     -H 'content-type: application/json' -d '{"name":"Hearth"}'
sleep 3
sudo systemctl restart fclights
sleep 5
curl -s http://localhost:7891/api/state | python3 -m json.tool
```

**Verify:** the effect is still `fire` and the `Hearth` scene is still listed.

Then pull the Pi's power, wait, and plug it back in.

**Verify:** the strip comes back to the same look with no intervention, within a minute of boot.
This is the property that makes it a light fitting rather than a science project.

**If state is lost:** check `/var/lib/fclights/state.json` exists and is owned by `fclights`. State is written about 2 seconds after the last change, so a power cut immediately after a change can legitimately lose that one change.

Also confirm recovery from fcserver dying:

```bash
sudo systemctl restart fcserver
sleep 5
curl -s http://localhost:7891/api/health
```

**Verify:** `opc_connected` returns to `true` on its own and the strip resumes, with no restart of `fclights`.

---

## Step 8 - Scale up to the full run

Only now connect the full 512-pixel installation.

Before powering on:

1. Set `power.limit_amps` in `/etc/fclights/fclights.json` to the real usable current of the supply feeding the strip. The packaged default of 24 A is the 30 A supply derated to 80%; on the 60 A supply the equivalent is 48. Read the sizing section of the README first.
2. **Check your strip density** and set `pixels_per_metre` in `/etc/fclights/layout.json`. The shipped value of 30.3 is the measured pitch of these reels, 33 mm centre to centre. If you are using strip of another density, measure its pitch across a run of cut pads and set the value to match.
3. Check `/etc/fclights/layout.json` matches how the strip is actually split across the Fadecandy's outputs, then run `fclights check` and copy the `fcserver map` entries it prints into `/etc/fclights/fcserver.json`. Each entry is `[OPC channel, first OPC pixel, first board pixel, pixel count]`, and the count covers the board's whole address space - a Fadecandy addresses output *n* from board pixel `64 * n`, so a run shorter than 64 leaves a gap the engine sends as black rather than closing up.
4. Confirm your power injection points, per the README.

```bash
sudo /opt/fclights/venv/bin/fclights check --config /etc/fclights/fclights.json
sudo systemctl restart fclights
```

**Verify:** `check` prints the right pixel count and a full-white figure you recognise, then bring brightness up **gradually** from 0.05 while watching the strip and, if you can, the supply.

**Verify:** the far end of the run is the same colour and brightness as the near end on a white scene. A pink or dim far end is voltage drop, and means you need power injection - see the README.

**Verify:** `GET /api/status` shows `measured_fps` near 60 and `late_frames` not climbing. On a Pi 3 B+ at 512 pixels, `render_ms` should be well under 2 ms.

**If `late_frames` climbs steadily:** something else on the Pi is competing for CPU, or `fps` is set too high. The frame rate is configurable; 30 fps still looks smooth with fcserver's interpolation doing the work.

---

## Step 9 - From the phone

With the Android app, or any REST client on the phone's network:

**Verify:** the app discovers the Pi without being given an address.

**Verify:** changing brightness on one phone updates a second phone within a moment, without either being refreshed.

**Verify:** the effect list and every effect's controls appear without the app having been told what the effects are. That is the parameter schemas doing their job; a new effect on the Pi should show up with working controls and no app update.

---

## Reference

```bash
# Logs, both services
journalctl -u fclights -u fcserver -f

# Validate config without starting anything
sudo /opt/fclights/venv/bin/fclights check --config /etc/fclights/fclights.json

# Run the service by hand with verbose logging, service stopped
sudo systemctl stop fclights
sudo -u fclights /opt/fclights/venv/bin/fclights run \
     --config /etc/fclights/fclights.json --log-level DEBUG

# Run with no hardware at all
/opt/fclights/venv/bin/fclights run --simulate --pixels 512
```

| File | What it is |
| --- | --- |
| `/etc/fclights/fclights.json` | Service config: frame rate, power ceiling, ports, paths. |
| `/etc/fclights/layout.json` | Physical layout: outputs, pixel counts, positions, strip density. |
| `/etc/fclights/fcserver.json` | fcserver config. **Gamma and whitepoint live here.** |
| `/var/lib/fclights/state.json` | Saved state and scenes. Delete to factory reset. |
| `/etc/avahi/services/fclights.service` | mDNS advertisement. |
