# fclights for Android

The phone app for the controller in this repository.
It talks to the Pi over the contract in [docs/api.md](../docs/api.md): REST for commands, a WebSocket for live state, mDNS for discovery, and no authentication of any kind.

**This app has never run on a phone.**
No APK has been installed on a device and no screen in it has ever been looked at.
Treat the first install as bring-up, not as a release.

What *has* been verified is everything below the UI: the client, the socket and the state handling were run against the real Pi with a genuine Fadecandy attached, not only against a simulator.
See [Tests](#tests).

## Build an APK

You need a JDK 17 and an Android SDK with platform 36.
Nothing else; the Gradle wrapper fetches its own Gradle.

```bash
cd android
echo "sdk.dir=$HOME/Android/Sdk" > local.properties   # or set ANDROID_HOME
./gradlew assembleDebug
```

The APK lands at:

```
android/app/build/outputs/apk/debug/app-debug.apk
```

If `java -version` is not already 17 or newer, point the build at one:

```bash
JAVA_HOME=/path/to/jdk-17 ./gradlew assembleDebug
```

## Put it on the phone

Either way works; the app is debug-signed, so Android will ask you to allow installing from whatever app is handing it over.

**By file.** Copy `app-debug.apk` to the phone - a cable, a share sheet, a cloud drive - and tap it in the phone's file manager.
The phone will offer "install unknown apps" for that file manager the first time; allow it.

**By adb**, with USB debugging turned on:

```bash
~/Android/Sdk/platform-tools/adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

`-r` replaces an already-installed copy, keeping the remembered controller address.

## First run

The app opens the address sheet when it has no controller.
Type the Pi's address - `192.168.1.164`, or `fadecandy.local` if the phone resolves mDNS - and connect.
Port 7891 is assumed unless you add one.

The address is remembered, so this is a one-time step per phone.
Discovery runs alongside it and lists anything advertising `_fclights._tcp`, but it is a shortcut rather than the way in: plenty of home routers drop multicast and Android's battery saver suppresses it, which is why the typed address is the first-class path.

## What is in it

| | |
| --- | --- |
| Master power and brightness | The two controls used most. |
| Colour temperature | A warm-to-cool slider along the blackbody locus, with the track painted in the colours it selects. |
| Effects | Listed from the controller, with controls built from the parameter schemas it publishes. |
| Scenes | Save what is showing, recall, delete. |
| Live state | A change made on one phone appears on another; the socket reconnects on its own. |

Deliberately absent, for now: zones or room grouping, scheduling, widgets, and any settings beyond the controller address.

## Choices worth knowing

**minSdk 26** (Android 8.0, 2017).
It covers every phone this is likely to be used from, and gets `NsdManager`'s modern behaviour and `java.time` without support-library contortions.
`compileSdk` and `targetSdk` are 36.

**No hardcoded effects.**
The effect list and every control in it are built at runtime from `GET /api/effects`.
An effect added on the Pi appears here after a reconnect, with working sliders, and with no change to this app.
Anything that would break that - a table of effect names, a switch on a parameter name - is a bug.

**The dependency list is short on purpose**: Compose, OkHttp, kotlinx-serialization.
No dependency injection framework, no networking wrapper, no navigation library; there is one screen.

**Versions are pinned to what AGP 8.13 and API 36 support.**
The newer AndroidX releases require compiling against API 37 and AGP 9, which is a toolchain upgrade rather than a dependency bump.
`app/lint.xml` silences the resulting "a newer version is available" warnings so the rest of the lint report stays worth reading.

## Tests

```bash
cd android
./gradlew testDebugUnitTest lintDebug
```

What is covered: the REST client and the exact bodies it sends, decoding of every message the controller emits, the state reduction that keeps two phones in step, the schema-to-control translation, address parsing, reconnect backoff, and the blackbody conversion checked against values produced by the Pi's own `fclights.color`.

The JSON in `app/src/test/resources/` is captured verbatim from a running controller rather than written by hand, so a change to the wire format fails a test here instead of showing up as an app that renders nothing.

There is also an opt-in pass against a controller that is really running, skipped unless you name one:

```bash
# a Pi, or a laptop running: fclights run --simulate --pixels 512
FCLIGHTS_TEST_HOST=192.168.1.164 ./gradlew testDebugUnitTest
```

It is the closest thing to end-to-end verification available without a phone, and it checks the things a fixture cannot:

- A cold start renders from the WebSocket `hello` alone, with a schema for the effect the controller is actually running.
- Every effect the controller publishes can be selected, reports a complete parameter set, and takes its own declared defaults back.
- A colour temperature survives the round trip as a temperature, and the swatch this app would draw matches the `rgb` the controller reports - which is the port of `fclights.color.kelvin_to_rgb` checked against the implementation lighting the strip, rather than against numbers copied out of it.
- The socket resyncs on its own after the connection is really dropped mid-stream.

It restores exactly what was showing when it finishes, including a scene that was recalled, because the thing it points at is somebody's lighting.

This pass has been run against the real installation: a Raspberry Pi 3 B+ driving a genuine Fadecandy over USB, `simulated: false`, 60 fps with the OPC link up.
Every check above passed there, and the error handling was confirmed against it too - an unknown effect is a 404, an out-of-range colour temperature and an unknown parameter name are 400s, an unknown request field is a 422, and none of the four changed the controller's state.

## Layout

| Path | |
| --- | --- |
| `app/src/main/java/com/fclights/model/` | Wire types, the blackbody conversion, schema-to-control translation, and the state reduction. No Android, no OkHttp. |
| `app/src/main/java/com/fclights/api/` | REST client, WebSocket with reconnect, mDNS discovery, remembered address. |
| `app/src/main/java/com/fclights/ui/` | Compose screen and the view model that drives it. |
| `app/src/test/` | Unit tests and the captured controller responses they run against. |
