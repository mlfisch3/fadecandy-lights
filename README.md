# fadecandy-lights

Controller for a Fadecandy-driven WS2812B installation, running headless on a Raspberry Pi and controlled from Android over the local network.

It is built for everyday apartment lighting that approximates natural light - tunable white from candlelight to daylight, and fades slow enough to be measured in minutes - rather than for a display piece.

This repository holds both sides: the Pi service - the animation engine, the power governor, and the control API - and the [Android app](android/) that drives it over the contract in [docs/api.md](docs/api.md).
Wiring, power distribution and safety are covered in full, with diagrams, in [docs/wiring.md](docs/wiring.md).

## What it is

A Raspberry Pi 3 B+ runs two services.
[`fcserver`](https://github.com/scanlime/fadecandy), the stock upstream Fadecandy server, owns the USB link to the Fadecandy board and listens for Open Pixel Control on localhost.
`fclights`, this project, renders frames and pushes them to fcserver over OPC, and serves the REST and WebSocket API the phones talk to.

```
 Android phones
       |  REST + WebSocket, over the home WiFi
       v
 +---------------------------- Raspberry Pi 3 B+ ------------------------------+
 |                                                                             |
 |   fclights                              fcserver                            |
 |   - effects (numpy, whole-frame)  OPC   - temporal dithering                |
 |   - global brightness            ---->  - interpolation      USB            |
 |   - POWER GOVERNOR (hard clamp)  :7890  - gamma, whitepoint  ---->  Fadecandy
 |   - state, scenes, persistence                                              |
 |   - REST + WebSocket API :7891                                              |
 |                                                                             |
 +-----------------------------------------------------------------------------+
                                                                        |
                                                  8 outputs, 64 pixels each
                                                                        v
                                                            WS2812B strips
                                                     (powered separately, 5 V)
```

Going through fcserver rather than driving the strip directly is deliberate.
Its temporal dithering and interpolation are what make a 5% brightness scene and a slow fade look smooth on an 8-bit WS2812B, and that is the entire reason to use a Fadecandy instead of bit-banging a GPIO.

**Gamma and whitepoint live in fcserver**, in the `color` block of `/etc/fclights/fcserver.json`.
The engine here works in 0..1 display-space values and applies no gamma at all.
If you find yourself wanting to gamma-correct in an effect, don't: it would double-correct and crush exactly the low end the dithering exists to rescue.

## Scale and target

- One Fadecandy board today: 8 outputs of 64 pixels, 512 in total.
- 60 fps target, configurable.
- Raspberry Pi 3 B+ (BCM2837B0, 1 GB RAM, onboard WiFi).

A Fadecandy output is hard-capped at **64 pixels** and a board at **512**, so an installation of around 18 runs needs three boards.
One board is what is built and configured now, but nothing assumes it: the layout file takes a list of devices, each device gets its own OPC channel and a contiguous slice of the frame, and the engine already emits one OPC message per device.
Growing means adding entries to `layout.json` and to fcserver's `map`, not a redesign.

**Target OS: 64-bit Raspberry Pi OS (Bookworm), `arm64`.**
That is the current recommended image for a Pi 3, and aarch64 has the broadest manylinux wheel coverage, so numpy, pydantic-core and uvicorn all install without compiling anything.
32-bit `armhf` should work too - the code is architecture-neutral and the dependencies publish armv7l wheels - but `arm64` is the targeted and documented path.
The service needs Python 3.10 or newer.

Nothing here needs a Pi 4, more than 1 GB of RAM, or a 64-bit-only dependency.
At 512 pixels the render loop is expected to cost a low single-digit percentage of one core, which is what should let the board run this continuously for months and leaves headroom to grow past one Fadecandy later. That figure is reasoned from the frame arithmetic, not measured on a board - `GET /api/status` reports `render_ms` so you can check it on yours.

## Natural light, and its limits

Colour can be given as a temperature in kelvin along the blackbody locus, anywhere from 1800 K candlelight to 6500 K overcast daylight, as well as as plain RGB.
Every colour control accepts both, so the Android app can show a warm-to-cool slider rather than only a colour wheel - and for room lighting, the slider is the control that gets used.

The `slowfade` effect exists for the main use case: a crossfade between two nearby whites over minutes or hours.

**Be aware of what these strips can and cannot do.**
A WS2812B makes white by mixing three narrow-band emitters - a red, a green and a blue LED - rather than by exciting a broad phosphor the way a white LED bulb does.
The result is a spectrum with three spikes and large gaps between them.
It looks like white light, and the colour temperature control here is real in the sense that it tracks the blackbody locus as the eye perceives it, but the **colour rendering is poor**: skin tones read sallow or waxy, wood and fabric lose depth, and food looks unappetising.
Anything whose colour you actually care about will look wrong under it.

This is a property of the hardware, not of this software, and no amount of colour correction fixes it - the missing wavelengths are simply not being emitted.
It is worth knowing before wiring an apartment.
As accent, cove and ambient lighting these are lovely; as the only light in a kitchen or over a mirror they are a poor choice, and a broad-spectrum white fixture belongs there instead.

## Trust model on the LAN

**The control API is unauthenticated, on purpose.**
It binds every interface so phones on the home WiFi can reach it, and anything that can reach port 7891 can change the lights, save and delete scenes, and read the layout. There is no token and no TLS.

That is the right trade for a light fitting on a network you control, and it is why the power governor is a hard clamp rather than something a client can talk its way past: the worst case from a hostile device on the LAN is somebody turning your lights orange, not damaged hardware.

It is only correct under that assumption. **Do not port-forward this, do not put it behind a public reverse proxy, and do not run it on a guest or shared network.** If you want it from outside the house, VPN into the home network rather than opening the port.

## Install

On a fresh Raspberry Pi OS install, on the Pi:

```bash
git clone <this repo> fadecandy-lights
cd fadecandy-lights
sudo ./deploy/setup.sh
```

Then work through [docs/bring-up.md](docs/bring-up.md) with the hardware connected.
Do that before scaling up to the full run; it is written as a checklist that localises failures rather than leaving you guessing.

## The Android app

[android/](android/) holds the native Kotlin app that controls this service.
It builds to a sideloadable APK with `cd android && ./gradlew assembleDebug`; [android/README.md](android/README.md) covers the toolchain, installing it on a phone, and what is in it.

It has never run on a phone.
Its client, socket and state handling have been exercised against a real Pi with a Fadecandy attached; the UI has not been looked at on a device.

## Development without hardware

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/fclights run --simulate --pixels 512
.venv/bin/pytest
```

`--simulate` runs the whole service - engine, governor, API, persistence - with no Fadecandy and no fcserver attached.
The API behaves identically, so the Android app can be built and exercised against it without the rig.

---

# Wiring

This is the short version.
[docs/wiring.md](docs/wiring.md) is the authoritative record, with colour diagrams of the zone topology, the power distribution and the multi-board layout.

## Data

Each of the Fadecandy's eight outputs drives up to 64 pixels.
Per output:

| Fadecandy | Strip | Pigtail colour on these reels |
| --- | --- | --- |
| Output *n* data pin | DI | yellow |
| Output *n* ground pin | GND | black |
| *(not connected)* | 5V, from the supply | red |

These strips have cut pads silkscreened `GND` / `DI` / `5V` on the input side and `GND` / `DO` / `5V` on the output side of **every** LED, with a data-direction arrow between them.
That means you can cut and re-pigtail anywhere, and it also means it is easy to solder onto the wrong side of a pad; check the arrow.

WS2812B strips are directional.
The arrows printed on the strip must point **away** from the Fadecandy.
Wiring to DOUT instead of DIN gives a strip that is powered and completely dark, which looks exactly like a dead strip.

Keep the data lead short - under about 30 cm from the Fadecandy to the first pixel.
If you see noise or flicker on the first few pixels, put a 300 to 500 ohm resistor in series with the data line at the Fadecandy end.

## Ground - read this one

**The Fadecandy and the strip's 5 V supply must share a ground.**

The Fadecandy's data output is a voltage referenced to *its* ground.
If the strip's ground is only connected to its own supply, the strip has no shared reference for that signal and will show garbage, flicker, or nothing at all.

So the strip's ground goes to **both** the strip supply's ground **and** the Fadecandy's ground pin.
This is the most common wiring mistake on a first build, and it presents as "the strip is powered but dark", which sends people looking at the wrong thing.

## Power

**Do not power the strip from the Pi's 5 V pins.** The Pi cannot supply it, and trying will brown out the Pi.
The strip gets its own 5 V supply. The Pi gets its own. They share a ground, as above.

The supplies on hand are a LETOUR S-150-5 (5 V, 30 A, 150 W) and a sompom S-300-5 (5 V, 60 A, 300 W).
Both are open-frame enclosed units with screw terminals and mains input: they are not plug-in bricks, so they want a proper enclosure, strain relief on the mains side, and to be sited where they can breathe.

# Power sizing

This is the arithmetic that decides whether your supply is adequate, and what the governor is set to.

## The numbers

A WS2812B is three constant-current LEDs behind a controller die:

| Quantity | Value |
| --- | --- |
| One colour channel at full | ~20 mA |
| One pixel at full white (R+G+B) | ~60 mA |
| One pixel quiescent (all channels off) | ~1 mA |

For this build:

```
512 pixels x 3 channels x 20 mA  =  30.72 A   at full white
512 pixels x 1 mA                =   0.51 A   idle, everything off
                                    -------
                            total    31.23 A  worst case, 5 V
                                  =  156 W
```

**31 A at 5 V is more than most supplies.**
A common 5 V 10 A brick covers about a third of it.
This is normal for an addressable strip of this length: nobody sizes for full white, because full white is a scene almost nobody runs.

## What the governor does about it

Every frame, after global brightness and before it leaves the engine, the predicted draw is computed from the actual RGB buffer.
If it exceeds the configured ceiling, the whole frame is scaled down to fit.

This is a **hard clamp, not a warning**.
The frame is modified; there is no path from an effect to the strip that skips it.
Scaling the whole frame means the scene dims rather than shifting colour, so hue and the relative brightness of pixels are preserved.

Set the ceiling to the **usable** current of the supply feeding the strip, not its nameplate rating.
Enclosed supplies of this class are rated for intermittent peaks, not for holding nameplate continuously in a warm cupboard for months.
80% of nameplate is the usual allowance for continuous duty, and less if the supply has no fan or poor airflow.

**The shipped default is 24 A**, which is the 5 V 30 A supply derated to 80%:

```json
{ "power": { "limit_amps": 24.0 } }
```

It defaults to the *smaller* of the two supplies on purpose.
If you swap the 60 A unit for the 30 A one and forget to change the config, a ceiling set for the larger supply would quietly overload the smaller; the reverse merely leaves headroom unused.
Running off the 5 V 60 A supply instead, `48.0` is the equivalent figure, and covers 512 pixels at full white outright.

At 24 A, 512 pixels run at about 77% of full white indefinitely - brighter than you are likely to want, since full white on 512 pixels is not something anyone looks at for long.

Check what your configuration implies before connecting anything:

```bash
fclights check --config /etc/fclights/fclights.json
```

```
layout          living room
devices         1
pixels          512
outputs         8
frame rate      60 fps
API             http://0.0.0.0:7891
OPC sink        127.0.0.1:7890

fcserver map    [0, 0, 0, 512]

supply ceiling  24.00 A at 5 V
idle draw       0.512 A (all pixels off)
full white      31.23 A
headroom        frames are clamped at about 77% of full white (120 W)
```

The `fcserver map` lines are the entries `/etc/fclights/fcserver.json` needs for this layout; see [How the layout lines up with fcserver's `map`](#how-the-layout-lines-up-with-fcservers-map).

### Precision, and why we dither

fcserver's OPC input is 8-bit and only 8-bit: its command enum defines `SetPixelColors = 0x00` and `SystemExclusive = 0xFF`, and nothing else, so the 16-bit set-pixels command in the Open Pixel Control specification is not a path that exists here.

For a fast animation that does not matter.
For the slow fades this installation is actually for, it matters a lot.
A fifteen-minute fade from 2700 K to 3400 K moves the blue channel by about 45 codes, so plain rounding holds each code for ten seconds and the result visibly walks up a staircase.
fcserver's interpolation cannot rescue that, because it would be interpolating between two frames we had already rounded to the same value.

So the engine works in float end to end and dithers temporally at the 8-bit encode, carrying each frame's rounding residual into the next.
A channel sitting at 128.3 emits 128 and 129 in the right proportion and averages to 128.3.
The resulting noise is first-order shaped, so it sits up near the frame rate where nothing can see it, and a value that lands exactly on a code - which is every solid colour picked off a wheel - produces no dither at all.

This composes with fcserver rather than competing with it: ours puts the sub-code information into the sequence of frames, and fcserver's interpolation and 400 Hz output dithering smooth that sequence on the way to the LEDs.
Turn it off with `"dither": false` if you ever need to compare.

### The prediction is deliberately conservative

The governor predicts current from the buffer *before* fcserver applies gamma.
Gamma greater than 1 only ever lowers an 8-bit value, so the real draw is **below** the prediction.
That is the safe direction, and it is the default (`power.gamma: 1.0`).

You can set `power.gamma` to match fcserver's own gamma for a tighter estimate, but a mismatch there under-predicts, so the default stays pessimistic.
If you measure current in line with the strip, expect the meter to read below `power.delivered_amps` from `GET /api/status`.
A meter reading *above* it means the current model is wrong for your particular strip - lower the ceiling and find out why before scaling up.

## Power injection

Copper on an LED strip is thin, and 5 V has no headroom to lose.
Voltage drop along a long run shows up as the far end being dimmer and pinker than the near end - the blue channel has the highest forward voltage, so it fades first.

These strips measure **33 mm centre to centre**, so 30.3 LEDs/m and a full 64-pixel output is **2.11 m**.

**Do not use an "inject every 2 m" rule at this density.** It is written for 60 LEDs/m strips, and current, not length, is what sets the drop. `docs/wiring.md` §6.3 works it properly; the results are:

> **Changed:** this section used to say 18 AWG injection wire, 16 AWG if the run is long. That is wrong at this current and length. It is **14 AWG**, and the branch fuse is **6 A**. See `docs/wiring.md` §6.2.


- **At full white, every 64-pixel run is fed at both ends.** That is not a contingency, it is the design. Fed from one end, a 2.11 m run would need a strip rail resistance no flexible PCB achieves; fed from both ends the requirement is four times looser and a decent strip meets it.
- **Feed 5 V and ground at each end**, both legs off that run's single branch fuse. The data line is **not** injected; it continues pixel to pixel as normal.
- **There is one lever that avoids that work, and it is a brightness cap.** These strips have pads at the DI end only, so feeding both ends means opening and resealing about 18 sealed silicone sleeves. Fed from the DI end alone, a **25 % cap asks exactly the same of the strip that full white fed from both ends does** - 0.123 Ω/m either way - and §7 already puts realistic ambient light here at about 25 %. The choice is reversible and changes no gauge and no fuse rating; `docs/wiring.md` §6.3 states both options with the figures.
- **Run injection wire back to the distribution block, not along the strip.** 14 AWG up to 1.87 m per leg is the default. Size each leg for the whole 3.84 A, not half of it.
- Splitting the installation across the Fadecandy's eight outputs still helps: eight 2.11 m runs fed at both ends are a far easier wiring problem than one 512-pixel, 16.9 m chain.

**`docs/wiring.md` §6.2 is the authoritative sizing table** - feeder gauge, main fuse and maximum length for 1 to 8 runs on one supply, the bus specification, and the voltage-drop budget those hold to. Do not size cable from this summary.

## Fusing

**Size fuses from the full-white load, never from the configured software ceiling.**
The governor is software; it stops the *normal* case from overloading the supply.
A fuse covers a short in the wiring, which no amount of correct software prevents, and it has to hold when the software is wrong.

The rule, in full in `docs/wiring.md` §6.2: take 1.25 × the full-white load, round up to the nearest standard fuse rating, and then require the conductor's ampacity to be at or above that **fuse** rating.
For one 64-pixel run that is 3.84 A, a 6 A branch fuse, and 14 AWG.
A fuse is needed wherever the conductor gets smaller: one main at the supply end of each feeder, and one branch fuse per run.

---

# Layout configuration

`/etc/fclights/layout.json` describes where the pixels physically are, so effects can be position-aware rather than working off raw indices.
A gradient sweeping "left to right" then means the same thing whether the strip is one long run or eight parallel bars.

```json
{
  "name": "living room",
  "pixels_per_metre": 30.3,
  "devices": [
    {
      "id": "fc0",
      "opc_channel": 0,
      "outputs": [
        { "index": 0, "count": 64, "name": "run 0", "origin": [0.0, 0.0, 0.0] }
      ]
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `devices[].id` | Any name you like. Must be unique. |
| `devices[].opc_channel` | OPC channel for this board. `0` is broadcast and is what a single-board rig uses. |
| `outputs[].index` | Which of the Fadecandy's eight outputs, `0..7`. |
| `outputs[].count` | Pixels on that output, `1..64`. |
| `pixels_per_metre` | Strip density. Sets the per-pixel spacing for every output that does not state its own. |
| `outputs[].origin` | Position of the first pixel, in metres. |
| `outputs[].step` | Per-pixel offset, in metres. Optional; overrides `pixels_per_metre` for one output, for a mixed-density installation. |
| `outputs[].points` | Explicit per-pixel positions, instead of `origin`/`step`, for anything not a straight line. |
| `outputs[].reverse` | Flip the direction of the run, for a strip physically installed backwards. |

### Measure your strip density

`pixels_per_metre` defaults to **30.3**, measured on these reels at **33 mm centre to centre**.
Nothing in the code assumes it - it is a config value, and it only affects the spatial coordinates effects animate along - but if it is wrong, a "sweep across the room at 0.5 m/s" will not move at the speed it claims and outputs will be placed wrongly relative to each other.

If you use strip of a different density, measure its pitch and put the answer in the layout file.
There is a cut pad at every LED, so measuring across a run of them is the easiest way to get the pitch.

Validate it without starting anything:

```bash
fclights check --layout /etc/fclights/layout.json
```

### How the layout lines up with fcserver's `map`

A Fadecandy addresses output *n* starting at board pixel `64 * n`, whether or not the outputs before it are full.
The render buffer does not work that way - it packs the outputs back to back, so a run of 30 pixels takes 30 slots, not 64.

The engine reconciles the two before anything leaves the process: each board's OPC message is indexed by *board* pixel, with the unused slots of a short output sent as black.
So fcserver's `map` is always the identity over a board's pixels, whatever the runs are:

```json
"map": [ [0, 0, 0, 512] ]
```

The four numbers are `[OPC channel, first OPC pixel, first board pixel, pixel count]`, and the count is `64 * (highest output index) + (pixels on that output)` - 512 for a fully populated board.
`fclights check` prints the exact entries for your layout under `fcserver map`; paste them into `/etc/fclights/fcserver.json`.

Do **not** write one `map` entry per run with running offsets - that describes the packed frame, not what is sent, and would shift every output after the first short one.

## Growing past one Fadecandy

You will need to.
A Fadecandy output is hard-capped at 64 pixels and a board at 512, so at the measured 30.3 LEDs/m one output covers 2.11 m, and around 18 runs comes to roughly 1150 pixels - three boards.

The growth path is not built, but it is not architected shut either.
Additional boards are additional entries in `devices`, each with its own `opc_channel` (1, 2, ... - not 0, which is broadcast and would mirror rather than extend), and a matching entry in fcserver's `map` as printed by `fclights check`; fcserver already supports several devices in one config.
The engine already emits one OPC message per device channel, the layout already lays devices out as contiguous slices of the frame, and the power governor already accounts for the whole installation rather than one board.

What is missing is per-board serial number pinning, so that outputs do not swap when the boards enumerate in a different order after a reboot.
The `serial` field is in the layout schema for that; it is not yet matched against fcserver's device map.

## What is deliberately not here

**Authentication.** See [Trust model on the LAN](#trust-model-on-the-lan). The API trusts the local network, which is a decision rather than an omission; a token or mTLS would add a subsystem here and a matching one in the Android app for no gain on a network you already control.

**Scheduling and circadian automation.** No sunrise ramp, no time-of-day scenes, no astronomical clock. That is separate work, and it is not designed out: an effect is a pure function of time and parameters, a scene is a stored effect plus its parameters and brightness, and both are addressable over the API. A scheduler would sit above this and recall scenes on a clock, without anything here changing.

---

# Repository layout

| Path | |
| --- | --- |
| `src/fclights/engine.py` | The render loop. |
| `src/fclights/power.py` | The power governor. Safety critical. |
| `src/fclights/effects/` | Effect plugins and their parameter schemas. |
| `src/fclights/api/` | REST and WebSocket control surface. |
| `src/fclights/color.py` | Colour temperature and the colour value model. |
| `src/fclights/layout.py` | Layout parsing and the coordinate arrays effects use. |
| `src/fclights/state.py` | State, scenes, and persistence. |
| `src/fclights/opc.py` | Open Pixel Control client. |
| `config/` | Example config, layout, and fcserver config. |
| `deploy/` | systemd units, udev rule, setup script. |
| `docs/api.md` | The control API contract. |
| `docs/wiring.md` | Wiring, power and topology record, with the colour diagrams in `docs/diagrams/`. |
| `docs/bring-up.md` | Hardware bring-up checklist. |
| `tests/opc_sink.py` | Test-double OPC receiver, used in place of fcserver. |
| `tools/` | Development checks that are not part of the service. |
| `android/` | The Android app. Builds independently of the Python service. |

## Testing

```bash
.venv/bin/pytest
```

The engine is exercised end to end against a test-double OPC receiver that validates the frames it is sent, which covers everything up to the USB link.

**Nothing in this repository has been verified on hardware.**
No Fadecandy, no Pi and no LED strip were involved in developing it.
[docs/bring-up.md](docs/bring-up.md) exists because of that: it is the checklist that turns "the tests pass" into "the lights work".
