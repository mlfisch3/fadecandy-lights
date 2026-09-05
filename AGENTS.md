# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

## What this is

A Fadecandy-driven WS2812B controller for a Raspberry Pi 3 B+, serving a REST and WebSocket API to the native Android app in `android/`.
`docs/wiring.md` is the authoritative wiring, power and topology record, `README.md` summarises wiring and power sizing, `docs/api.md` is the API contract, `docs/bring-up.md` is the hardware checklist.

## Working here

```bash
uv venv --python 3.11 .venv && VIRTUAL_ENV=.venv uv pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests tools
.venv/bin/fclights run --simulate --pixels 512    # whole service, no hardware
.venv/bin/fclights check --simulate --pixels 512  # validate config, print power arithmetic
```

The Android app is a separate build under `android/`, sharing nothing but `docs/api.md`.
It needs a JDK 17 and Android SDK 36; `android/README.md` has the commands and the version pins' reasoning.

```bash
cd android && ./gradlew assembleDebug testDebugUnitTest lintDebug
FCLIGHTS_TEST_HOST=localhost:7891 ./gradlew testDebugUnitTest  # opt-in, against a running service
```

## Sharp edges

**The rig exists now; be exact about what that has and has not proved.** The service runs on the Pi (hostname `fadecandy`, DHCP - do not hardcode the address) with a genuine Fadecandy attached: `/api/health` reports `simulated: false` and `opc_connected: true`, and `/api/status` shows a steady 60 fps with no dropped frames. That covers the render loop, the OPC link and the whole control API, and the Android client has been run against it. It does not cover how any effect actually looks on a strip, and the Android UI has never run on a phone. Claim the first, not the second, and keep working through `docs/bring-up.md` for the rest.

**fcserver's upstream is gone and it cannot be built from source.** `scanlime/fadecandy` is 404, and so are the three `scanlime/*` submodules its build needs, so the binary comes from an unmaintained mirror via `deploy/install-fcserver.sh` - pinned by commit, digest-verified. It is 32-bit `armhf`, so `arm64` (the target OS) needs `libc6:armhf` and `libstdc++6:armhf` beside it. `docs/fcserver.md` has the evidence and the assessment; do not re-derive it, and do not paste a `scanlime` URL back into anything - `tests/test_install_fcserver.py` fails if you do.

**Gamma lives in fcserver, not here.** Engine values are 0..1 *display* space, not linear light. Applying gamma in an effect or in the encoder would double-correct. `config/fcserver.json` `color` block is the one place it belongs.

**The power governor is a hard clamp and must stay one.** 512 pixels at full white is ~31 A. Every path from an effect to the wire goes through `PowerGovernor.apply`, which mutates the frame. Do not add a path that skips it, and do not soften it to a warning. `tests/test_power.py` and the end-to-end assertions in `tests/test_engine.py` guard this.

**fcserver's OPC input is 8-bit only.** Its command enum has `SetPixelColors = 0x00` and `SystemExclusive = 0xFF`; the 16-bit OPC command 2 is not implemented, so do not reach for it. Precision on slow fades is recovered by `TemporalDither` in `src/fclights/opc.py`, which is load-bearing for the project's main use case and not an optimisation to drop.

**Effect parameters are per-run, never whole-installation.** A rate, count or length that divides across the whole layout - "sparks per second across the installation", cooling scaled by total pixel count - silently changes how an existing strip looks when a board is added, and becomes meaningless once an effect runs on a subset. A zone model grouping the runs into rooms is queued as the next piece of work, so an effect must already behave the same on a run whether it is one of eight or one of twenty-four. Scale by `layout.segment` and the run's own length; spatial parameters that read the coordinate arrays (`u`, `normalized`) are fine, because those follow whatever layout the effect is handed.

**Do not hardcode strip density or a single Fadecandy.** `pixels_per_metre` defaults to 30.3, *measured* at 33 mm centre to centre, and the real installation (~18 runs, ~1150 pixels) needs three boards. A full 64-pixel output is 2.11 m, so it needs power injection at both ends. Layout, pixel addressing and the OPC client all take a device list; keep it that way. A Fadecandy output is hard-capped at 64 pixels, a board at 512.

**Nothing on the request path may wait on a phone's network.** `Controller.commit` awaits `Broadcaster.broadcast`, so the hub queues per client and never writes inline; a client is dropped only when its own bounded queue overflows, and a dropped client always has its connection ended rather than being quietly forgotten. `src/fclights/api/hub.py` explains why in full and `docs/api.md` promises it to clients - keep the two in step. The sharp edge underneath: nothing, not even a close frame, can be written to a peer that has stopped reading, and asyncio's `transport.close()` waits for the send buffer to drain, so only an abort actually releases such a socket. `tests/test_hub.py` covers this with a fake socket; the real behaviour needs a peer that genuinely stops reading, which the unit tests cannot fake.

**The app builds its controls from the schema, never from a list.** `android/` renders the effect picker and every parameter control from `GET /api/effects` at runtime, so an effect added on the Pi appears on the phone with no app change. A hardcoded effect name or a switch on a parameter name in the app is a bug, not a shortcut. The same goes for the blackbody conversion in `app/.../model/Blackbody.kt`: it is a port of `fclights.color.kelvin_to_rgb` and is tested against values that implementation produced, because the slider is drawn from one and the strip is lit by the other.

**Colour values are objects, not arrays.** `{"mode": "kelvin", "kelvin": 2700, "rgb": [...]}` or `{"mode": "rgb", "rgb": [...]}`. A kelvin colour keeps its temperature so the phone's warm-to-cool slider can be restored, and is re-derived in float rather than read back from the 8-bit `rgb`.

## Hardware facts

`docs/wiring.md` is the authoritative record of the confirmed hardware, the verified Fadecandy
output pinout and logic levels, and the power/topology arithmetic. Read it before writing anything
that assumes strip geometry, channel numbering, current, or supply behaviour. Its §12 lists the
primary sources and, separately, everything that is explicitly unverified.

Two facts that are easy to get wrong and are settled there:

- The strips are 30 LEDs/m (33 mm pitch), so a 64-pixel run is 2.11 m and draws 3.84 A at full
  white. Rules of thumb quoted for 60 LEDs/m strips do not transfer; compare in amp-metres.
- The Fadecandy output header carries GND and DATA only. There is no +5 V pin on it, and strip
  power must come from the external supply.

Power sizing lives in §6.2 and is anchored to named NEC tables (310.16 and 402.5 for ampacity,
Chapter 9 Table 8 for resistance, 240.6(A) for fuse ratings, 210.19(A) Informational Note No. 4 for
the drop budget). Do not reintroduce uncited "typical chassis-wiring" figures, and do not patch a
number in isolation: the section was corrected in four consecutive rounds that way, and each round's
fix contained the next round's bug. The two traps that caused them: **4.5 V is a floor at the dimmest
LED, not at the strip's solder pads** (confusing the two makes §6.3 optimistic by 2x), and a fuse is
sized against the *conductor's ampacity* and 1.25x the load in one direction, never as a range
between them.

Diagrams live in `docs/diagrams/*.svg` as hand-written SVG so they stay diffable. They render with
an explicit light panel background so they stay legible on dark documentation backgrounds, label
every conductor in text as well as colour, and use `xml:space="preserve"` on monospace rows to keep
tabular alignment. SVG text does not wrap and is not clipped, so overruns are silent: run
`python3 tools/check-svg-text-fit.py` after any edit, which renders each diagram in headless Chrome
and fails on a label that crosses its panel border or the canvas, and on a diagram it could not
measure at all.
`tests/test_svg_text_fit.py` runs it over every committed diagram, but the whole module skips when
no Chrome or Chromium binary is on PATH, so a green pytest run there is not proof the drawings were
checked.
Look at the drawing too (`google-chrome --headless --screenshot`); the check catches collisions,
not ugliness.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
