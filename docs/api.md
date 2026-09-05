# fclights control API

This is an interface specification.
The Android app is built against it, so treat a change here as a change to a published contract.

- Base URL: `http://<pi>:7891`
- All REST paths are under `/api`.
- All request and response bodies are JSON, UTF-8.
- The service holds one authoritative state; every mutating call returns the new state, and every state change is also pushed to every connected WebSocket client.

## Contents

- [Discovery](#discovery)
- [Trust model](#trust-model)
- [Conventions](#conventions)
- [Errors](#errors)
- [Colour values](#colour-values)
- [State object](#state-object)
- [Read endpoints](#read-endpoints)
- [Command endpoints](#command-endpoints)
- [Scene endpoints](#scene-endpoints)
- [WebSocket](#websocket)
- [Effect parameter schemas](#effect-parameter-schemas)
- [Client notes](#client-notes)

## Discovery

The Pi is on DHCP, so do not hardcode its address.
It advertises over mDNS:

| Field | Value |
| --- | --- |
| Service type | `_fclights._tcp` |
| Port | the API port, normally `7891` |
| TXT `path` | `/api` |
| TXT `version` | service version, for example `1.0.0` |
| TXT `pixels` | total pixel count, for example `512` |

On Android, browse with `NsdManager` for `_fclights._tcp`.
Resolve the service, then use the resolved host and port as the base URL.
Fall back to a manually entered address if discovery finds nothing; some home routers and some Android battery-saver settings suppress multicast.

## Trust model

**The API is unauthenticated, and that is deliberate.**
There is no token, no TLS and no per-client identity.
It binds `0.0.0.0` and trusts everything on the local network: anything that can reach port 7891 can change the lights, save and delete scenes, and read the layout.

That is the right trade for a light fitting on a home LAN reached by a phone on the same WiFi, and the power governor is a hard clamp regardless of what any client asks for, so the worst a hostile client on the network can do is a nuisance rather than damage.

It is **not** safe outside that assumption:

- Do not port-forward it, and do not put it behind a public reverse proxy.
- Do not expose it on a network you do not control, including a guest or shared WiFi.
- Reach it from outside the house over a VPN into the home network, not by opening the port.

A client should not implement any authentication against it; there is nothing to authenticate to.

## Conventions

- Colours are objects, not bare arrays. See [Colour values](#colour-values).
- Brightness and other normalised values are floats `0.0..1.0`.
- Times are Unix seconds as floats.
- Unknown fields in a request body are rejected with `422`, rather than ignored.
  A typo in a client fails loudly instead of silently doing nothing.
- Booleans must be `true` or `false`. The strings `"true"` and `"false"`, and `0`/`1`, are a `422`, for the same reason.
- `revision` increases on every state change.
  A client that receives a state with a `revision` lower than one it has already applied should discard it; that is the only ordering guarantee needed to keep several phones in step.

## Errors

Every error, including unmatched routes and body validation failures, has the same shape:

```json
{ "error": "not_found", "detail": "no scene with id 'ghost'" }
```

| Status | `error` | Meaning |
| --- | --- | --- |
| `400` | `bad_request` | The request was understood but the value is not usable: a parameter out of range, an unknown parameter name, an empty scene name. |
| `404` | `not_found` | Unknown effect name, unknown scene id, or unknown route. |
| `422` | `unprocessable_entity` | The body did not parse or did not match the schema. `detail` names the offending field. |
| `500` | `internal_error` | A bug. Report it with the journal output. |

`detail` is a human-readable sentence intended to be displayable.

## Colour values

This installation is apartment lighting meant to approximate natural light, so a colour temperature in kelvin is a first-class way to name a colour - not a derived convenience.
A colour value therefore remembers *how* it was chosen, so the app can put a warm-to-cool slider back where the user left it rather than silently demoting a temperature to a swatch.

A colour is always **returned** as one of:

```json
{ "mode": "rgb", "rgb": [255, 170, 80] }
{ "mode": "kelvin", "kelvin": 2700, "rgb": [255, 173, 89] }
```

`rgb` is always present, including for a kelvin colour, so a client can draw the swatch without reimplementing the blackbody conversion.
For a kelvin colour it is derived and read-only; the temperature is the value that matters.

On **input** any of these is accepted and normalised to the above:

| Form | Example |
| --- | --- |
| RGB array | `[255, 170, 80]` |
| Hex string | `"#ffaa50"` or `"#fa5"` |
| Kelvin | `{"kelvin": 2700}` |
| A canonical object | either form above, verbatim |

Kelvin must be in `1800..6500`; outside that is a `400`.
1800 K is candlelight, 2700 K a warm domestic bulb, 4000 K neutral, 6500 K overcast daylight.

Every colour parameter supports this. Its schema entry says so:

```json
{
  "name": "color", "type": "color", "label": "Colour",
  "default": { "mode": "kelvin", "kelvin": 2700, "rgb": [255, 173, 89] },
  "supports_kelvin": true,
  "kelvin_range": [1800.0, 6500.0],
  "kelvin_default": 2700.0
}
```

So one control can offer both a colour wheel and a warm-to-cool slider, and the `mode` of the current value says which to show.
Given the intended use, the temperature slider is the one to make prominent.

## State object

Returned by most endpoints, wrapped as `{"type": "state", "state": {...}}`.

```json
{
  "power": true,
  "brightness": 0.35,
  "effect": "solid",
  "params": { "color": { "mode": "kelvin", "kelvin": 2700, "rgb": [255, 173, 89] } },
  "scenes": [],
  "active_scene": null,
  "revision": 7
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `power` | bool | Master switch. When false the strip is black but the service keeps running. |
| `brightness` | float `0..1` | Global master brightness, applied before the power governor. |
| `effect` | string | Name of the active effect. Always one of the names from `GET /api/effects`. |
| `params` | object | Complete parameter set for the active effect. Every parameter the effect declares is present, never a partial set. |
| `scenes` | array | Saved scenes, in creation order. See [Scene endpoints](#scene-endpoints). |
| `active_scene` | string or null | Id of the scene currently showing. Cleared as soon as the effect, its parameters or the brightness are changed, so a client can highlight the selected scene and un-highlight it when the user edits away from it. Master power does not clear it. |
| `revision` | integer | Monotonically increasing. |

## Read endpoints

### `GET /api/health`

A liveness probe. Cheap enough to poll.

```json
{ "ok": true, "version": "1.0.0", "simulated": false, "opc_connected": true }
```

`opc_connected` false means the render loop is running but fcserver is not reachable, so the strip is not being updated.
The client reconnects on its own; surface it, do not treat it as fatal.

### `GET /api/state`

The state object. This plus `GET /api/effects` and `GET /api/layout` is everything a client needs on a cold start, though a WebSocket connection delivers all three in one message.

### `GET /api/effects`

```json
{ "effects": [ { "name": "...", "display_name": "...", "description": "...", "params": [ ... ] } ] }
```

See [Effect parameter schemas](#effect-parameter-schemas).
Build the UI from this. Do not hardcode the effect list; effects added on the Pi appear here without a client change.

### `GET /api/layout`

```json
{
  "name": "living room",
  "pixel_count": 512,
  "pixels_per_metre": 30.3,
  "devices": [
    {
      "id": "fc0",
      "opc_channel": 0,
      "serial": null,
      "pixel_count": 512,
      "outputs": [ { "index": 0, "count": 64, "name": "run 0", "reverse": false } ]
    }
  ],
  "bounds": { "min": [0.0, 0.0, 0.0], "max": [16.8647, 0.0, 0.0] }
}
```

Only the first of the eight outputs is shown above; the real response for this 512-pixel board lists all eight.
`devices[].pixel_count` is the sum of that board's `outputs[].count`, so it is the number of pixels the board actually drives rather than the 512 it can address.

`bounds` are metres, derived from `pixels_per_metre` and the layout's per-output positions.
Useful for drawing a preview to scale.

`pixels_per_metre` is configured on the Pi and may be any value; do not assume a density.
`devices` may hold more than one entry: a Fadecandy output is hard-capped at 64 pixels and a board at 512, so an installation larger than that spans several boards. A client should treat the device list as a list.

### `GET /api/status`

Engine and power telemetry. Also pushed over the WebSocket every 2 seconds.

```json
{
  "fps_target": 60.0,
  "pixel_count": 512,
  "connected": true,
  "dither": true,
  "sink": "127.0.0.1:7890",
  "engine": {
    "frames_rendered": 214980,
    "frames_sent": 214980,
    "frames_dropped": 0,
    "measured_fps": 60.0,
    "render_ms": 0.31,
    "late_frames": 0
  },
  "power": {
    "requested_amps": 31.232,
    "delivered_amps": 23.9998,
    "limit_amps": 24.0,
    "headroom_amps": 0.0002,
    "scale": 0.764576,
    "clamped": true
  },
  "power_model": {
    "limit_amps": 24.0,
    "full_white_amps": 31.232,
    "idle_amps": 0.512,
    "ma_per_channel": 20.0,
    "gamma": 1.0
  }
}
```

`power.clamped` true means the requested frame exceeded the supply ceiling and was scaled down.
This is normal and expected on a 512-pixel run, not an error.
Showing it, for instance as "limited by power budget", is genuinely useful: it explains why dragging brightness past a point stops making the strip brighter.

### `GET /api/info`

Service metadata, layout and status in one response. Convenient for a settings screen.

## Command endpoints

Each returns the full state object.

### `PUT /api/power`

```json
{ "on": false }
```

### `PUT /api/brightness`

```json
{ "brightness": 0.4 }
```

`brightness` must be `0.0..1.0`; outside that is `422`.

### `PUT /api/effect`

Select an effect and set its parameters. Anything omitted from `params` takes the effect's declared default, so `{"effect": "rainbow"}` alone is valid.

```json
{ "effect": "rainbow", "params": { "speed": 0.3, "cycles": 2.0 } }
```

- Unknown effect name: `404`.
- Unknown parameter name, or a value outside the declared range: `400`, with the offending name in `detail`.

### `PATCH /api/effect/params`

Merge a partial parameter update into the current effect. This is the call to make while a slider is being dragged.

```json
{ "params": { "speed": 0.7 } }
```

Parameters not mentioned keep their current values.

## Scene endpoints

A scene stores an effect, its complete parameter set, and the brightness to show it at.
It does not store master power.

```json
{
  "id": "a1b2c3d4e5f6",
  "name": "Hearth",
  "effect": "fire",
  "params": { "cooling": 0.8, "sparking": 0.5, "speed": 1.0, "hue": 0.05, "per_segment": true, "seed": 0 },
  "brightness": 0.6,
  "created_at": 1767225600.0,
  "updated_at": 1767225600.0
}
```

| Method and path | Body | Returns |
| --- | --- | --- |
| `GET /api/scenes` | | `{"scenes": [...], "active_scene": null}` |
| `POST /api/scenes` | `{"name": "Hearth"}` | `201` with `{"scene": {...}, "state": {...}}`. Captures the live look. |
| `GET /api/scenes/{id}` | | `{"scene": {...}}` |
| `PUT /api/scenes/{id}` | `{"name": "Fireplace"}` and/or `{"capture": true}` | `{"scene": {...}, "state": {...}}` |
| `DELETE /api/scenes/{id}` | | The state object. |
| `POST /api/scenes/{id}/recall` | | The state object, with the scene applied. |

Notes:

- `POST /api/scenes` sets `active_scene` to the new scene, because the live look now is that scene.
- `PUT` with only `name` renames without changing what the scene shows.
  `PUT` with `capture: true` overwrites the scene's effect, parameters and brightness with the live look, keeping its id and `created_at`.
  Sending neither is `400`.
- Names are trimmed, must be non-empty, and at most 120 characters.
- At most 256 scenes.

## WebSocket

`ws://<pi>:7891/api/ws`

Connect and you immediately receive a `hello` carrying everything needed to render the UI, with no separate REST calls:

```json
{
  "type": "hello",
  "version": "1.0.0",
  "state": { ... },
  "layout": { ... },
  "effects": [ ... ],
  "status": { ... }
}
```

Then:

| `type` | When | Payload |
| --- | --- | --- |
| `state` | Immediately on any state change, from any client | `{"type": "state", "state": {...}}` |
| `telemetry` | Every 2 seconds, while at least one client is connected | `{"type": "telemetry", "status": {...}}` |
| `pong` | In reply to a `ping` | `{"type": "pong"}` |

The socket is push-only apart from `ping`.
Send commands over REST; the resulting `state` message arrives on the socket, including to the client that sent the command.

Send the text `ping` to keep an idle connection alive.
Reconnect with backoff on disconnect, and treat the `hello` as a full resync rather than trying to reconcile what was missed.

## Effect parameter schemas

A parameter schema entry:

```json
{
  "name": "speed",
  "type": "float",
  "default": 0.15,
  "label": "Speed",
  "description": "Cycles per second. 0 freezes the animation.",
  "minimum": 0.0,
  "maximum": 5.0,
  "step": 0.01,
  "unit": "Hz"
}
```

`name`, `type`, `default`, `label` and `description` are always present.
The rest depend on the type:

| `type` | Extra fields | Suggested control | Value on the wire |
| --- | --- | --- | --- |
| `float` | `minimum`, `maximum`, `step`, sometimes `unit` | Slider | JSON number |
| `int` | `minimum`, `maximum`, sometimes `step` | Stepper or slider | JSON integer |
| `bool` | | Switch | `true` / `false` |
| `color` | `supports_kelvin`, `kelvin_range`, `kelvin_default` | Colour wheel **and** a warm-to-cool slider | a [colour value](#colour-values) |
| `enum` | `choices` | Segmented control or dropdown | one of `choices` |

`minimum` and `maximum` are always present for `float` and `int`, so a slider can always be built.
`label` is display-ready; `name` is the wire key.

Booleans are not accepted where a number is expected: sending `true` for `speed` is a `400`, not `1.0`.

Neither are the non-finite values JSON parsers accept as an extension, nor integer literals too large to be a `double`. For an effect parameter or a colour component they are a `400`; for `brightness` they fail the body schema first and are a `422`, like any other out-of-range brightness.

### v1 effects

| `name` | `display_name` | Parameters |
| --- | --- | --- |
| `solid` | Solid Colour | `color` |
| `slowfade` | Slow Fade | `color_a`, `color_b`, `period`, `hold`, `easing` |
| `gradient` | Gradient Sweep | `color_a`, `color_b`, `speed`, `cycles`, `axis` |
| `breathe` | Breathe | `color`, `speed`, `minimum`, `maximum` |
| `wipe` | Colour Wipe | `color`, `background`, `speed`, `softness`, `bounce`, `axis` |
| `rainbow` | Rainbow | `speed`, `cycles`, `saturation`, `axis` |
| `twinkle` | Twinkle | `color`, `background`, `density`, `decay`, `color_jitter`, `seed` |
| `fire` | Fire | `cooling`, `sparking`, `speed`, `hue`, `per_segment`, `seed` |

`twinkle.density` is a rate **per run**, not across the installation: each of the layout's outputs gets that many sparks per second, so a strip twinkles at the same rate whether it is the only one connected or one of twenty-four.
Every effect parameter is defined this way - per run, never as a whole-installation total - so adding a Fadecandy does not change how the strips already up look.

`slowfade.hold` is the fraction of the cycle spent parked at **each** end, not the two ends combined, so its maximum is `0.5`: at `0.5` the two dwells already fill the cycle and the crossfade has no time left to run in.
With the default `period` of 900 s and `hold` of 0.2, each end is held for 180 s and each crossfade takes 270 s.

This table is documentation, not contract.
`GET /api/effects` is the contract; fetch it rather than embedding this.

`axis` selects what the animation runs along: `run` follows strip order, `x`/`y`/`z` follow the spatial layout.
On a single straight run they are equivalent.

## Client notes

**Throttle drags.** `PATCH /api/effect/params` at roughly 10 Hz while a slider moves, and send a final value on release. The engine renders at 60 fps regardless; there is nothing to gain from matching it.

**Do not echo-suppress.** Apply the `state` messages you receive, including the ones caused by your own commands. That is what keeps two phones in agreement.

**Brightness is not linear in perceived light,** and above the power ceiling it stops being linear in output at all. `GET /api/status` reports `power.clamped` and `power.scale` if you want to show where the ceiling bites.

**Gamma is not your problem.** It is applied by fcserver on the Pi. Send the colour the user picked.

**Slow fades are the point.** `slowfade` with a `period` of many minutes between two nearby colour temperatures is the primary use case for this installation. Offer it prominently, and let `period` reach into the hours; the API allows up to six.

**Colour rendering is limited.** These are RGB pixels synthesising white from three narrow emitters, so whites are tunable and pleasant but render skin tones and food poorly. Worth saying once in the app rather than leaving the user to wonder.

**The service restores its last state on boot,** so a client should render whatever it is told rather than pushing a remembered state on connect.
