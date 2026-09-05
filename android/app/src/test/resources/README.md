These files are verbatim responses captured from the real controller running
`fclights run --simulate --pixels 512`, not hand-written examples. They are
here so a change to the wire format shows up as a failing test rather than as
an app that renders nothing on the phone.

Regenerate with the service running:

    curl -s localhost:7891/api/state  > fixture_state.json
    curl -s localhost:7891/api/layout > fixture_layout.json
    curl -s localhost:7891/api/status > fixture_status.json
    curl -s localhost:7891/api/effects > fixture_effects.json

`fixture_hello.json` is the first frame from `ws://localhost:7891/api/ws`.
