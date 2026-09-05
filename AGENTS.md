# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

## What this is

A Fadecandy-driven WS2812B controller for a Raspberry Pi 3 B+, serving a REST and WebSocket API to a native Android app (a separate project).
`README.md` covers wiring and power sizing, `docs/api.md` is the API contract, `docs/bring-up.md` is the hardware checklist.

## Working here

```bash
uv venv --python 3.11 .venv && VIRTUAL_ENV=.venv uv pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/fclights run --simulate --pixels 512    # whole service, no hardware
.venv/bin/fclights check --simulate --pixels 512  # validate config, print power arithmetic
```

## Sharp edges

**Nothing here has been verified on hardware.** No Fadecandy, Pi or strip has ever been attached. Do not claim otherwise, in commits, docs or CI. `docs/bring-up.md` exists because of that gap.

**Gamma lives in fcserver, not here.** Engine values are 0..1 *display* space, not linear light. Applying gamma in an effect or in the encoder would double-correct. `config/fcserver.json` `color` block is the one place it belongs.

**The power governor is a hard clamp and must stay one.** 512 pixels at full white is ~31 A. Every path from an effect to the wire goes through `PowerGovernor.apply`, which mutates the frame. Do not add a path that skips it, and do not soften it to a warning. `tests/test_power.py` and the end-to-end assertions in `tests/test_engine.py` guard this.

**fcserver's OPC input is 8-bit only.** Its command enum has `SetPixelColors = 0x00` and `SystemExclusive = 0xFF`; the 16-bit OPC command 2 is not implemented, so do not reach for it. Precision on slow fades is recovered by `TemporalDither` in `src/fclights/opc.py`, which is load-bearing for the project's main use case and not an optimisation to drop.

**Effect parameters are per-run, never whole-installation.** A rate, count or length that divides across the whole layout - "sparks per second across the installation", cooling scaled by total pixel count - silently changes how an existing strip looks when a board is added, and becomes meaningless once an effect runs on a subset. A zone model grouping the runs into rooms is queued as the next piece of work, so an effect must already behave the same on a run whether it is one of eight or one of twenty-four. Scale by `layout.segment` and the run's own length; spatial parameters that read the coordinate arrays (`u`, `normalized`) are fine, because those follow whatever layout the effect is handed.

**Do not hardcode strip density or a single Fadecandy.** `pixels_per_metre` defaults to 30.3, *measured* at 33 mm centre to centre, and the real installation (~18 runs, ~1150 pixels) needs three boards. A full 64-pixel output is 2.11 m, so it needs power injection at both ends. Layout, pixel addressing and the OPC client all take a device list; keep it that way. A Fadecandy output is hard-capped at 64 pixels, a board at 512.

**Colour values are objects, not arrays.** `{"mode": "kelvin", "kelvin": 2700, "rgb": [...]}` or `{"mode": "rgb", "rgb": [...]}`. A kelvin colour keeps its temperature so the phone's warm-to-cool slider can be restored, and is re-derived in float rather than read back from the 8-bit `rgb`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
