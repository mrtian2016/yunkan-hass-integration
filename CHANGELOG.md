# Changelog

## 0.4.0

Event snapshot rendering options (requests from the field — thanks Benjamin!).

- **Options flow** (Settings → Integrations → Yunkan → Configure): two new
  toggles for how event images are rendered, applied to all latest-event /
  per-category image entities and the snapshot proxy view.
  - **Draw detection boxes** (default on) — drawn server-side, same rendering
    as the event center.
  - **Crop around the detected object** (default off) — square crop centred on
    the detection with context margin, ideal for notification previews.
    Mirrors Frigate's `bounding_box` / `crop` snapshot options.
- The snapshot proxy view (`/api/yunkan/{entry_id}/event_snapshot`) accepts
  `crop=1` alongside the existing `event_id` / `w` parameters.

Note: bounding boxes on image entities require Yunkan server >= 0.9.32 — older
servers returned the plain thumbnail when a width cap was requested together
with box drawing (server-side ordering bug, fixed in 0.9.32). Crop also needs
>= 0.9.32; on older servers the parameter is ignored and the full frame is
served, nothing breaks.

## 0.3.0

Full parity pass against the Frigate integration.

- **Generic motion** binary sensor per camera (on while anything is tracked or a
  detection event fires).
- **Object-count** sensors per camera (person / vehicle / animal / package),
  live from the tracks stream.
- **Recognised face** and **recognised plate** sensors per camera.
- **Latest snapshot per category** image entities (person, vehicle, animal,
  package, face, fall, cry) alongside the "latest event" one.
- **Detection feature switches** per camera — object, audio, face, plate, pose,
  gesture, package — backed by `detection_overrides` — plus a **PTZ
  auto-tracking** switch (person/vehicle/pet together).
- **PTZ presets**: the `yunkan.ptz` service now takes a `preset` (name or token)
  in addition to `direction`.
- **Timelapse export**: `yunkan.export` gains a `playback_factor` (realtime or
  25x timelapse).
- Full 5-language translations for every new entity/field.

Note: Frigate's motion/snapshots/improve_contrast switches have no Yunkan backend
equivalent; the richer per-feature detection switches above replace them.

## 0.2.0

Feature additions (parity with common Frigate-integration capabilities) and a
deep review pass.

- **Device triggers** per camera ("person detected", "vehicle detected", …) for
  building automations from the UI, plus a notification blueprint (import by URL).
- **Live occupancy**: detection sensors now stay on while the object is present
  (driven by the per-camera live-tracks stream), not just a momentary pulse.
- **Recording switch** per camera (continuous/disabled).
- **Birdseye** overview camera when the server has it enabled.
- **Snapshot gallery** in the media browser.
- **`yunkan.export`** service to export a recording clip.
- Richer occupancy/event attributes carry the recognised name and licence plate.

Review fixes:

- Device triggers wrap the action in a `HassJob` (they previously raised at fire
  time and never ran).
- live-tracks stream authenticates with an sse-ticket (was returning 401/422).
- Background SSE/tracks loops use `async_create_background_task` and propagate
  cancellation, so they shut down cleanly (no "task could not be canceled").
- Live-tracks clients are reconciled when the camera set changes (no leaked
  task for a removed camera, occupancy for an added one).
- Bounded, lock-coalesced 401 re-auth in the snapshot paths (no recursion storm).
- WebRTC grant is warmed on add so the first session carries the server ICE
  servers; `verify_ssl` now defaults to on; proxy views verify the entry domain
  and the event-snapshot path; diagnostics redaction gained suffix patterns.
- Full 5-language translations for all new entities/services/triggers.

## 0.1.1

Real-world fixes from a live run against Home Assistant 2026.7 and a live server.

- Compatibility with newer cores: import `RTCIceServer` from `webrtc_models` and
  implement the WebRTC client configuration as the synchronous
  `_async_get_webrtc_client_configuration` hook (fixes live view failing with a
  "coroutine was never awaited" error).
- Fix the event stream returning 401 behind a reverse proxy by sending the bearer
  token on the SSE request (an aiohttp client, unlike a browser, can set headers).
- Only expose enabled, non-archived cameras, and prune devices for cameras that
  are no longer present.
- Register the server (hub) device so per-camera devices have a valid `via_device`.
- Pre-import platform modules off the event loop to avoid a blocking-import warning.
- Richer event attributes: recognised face name, licence plate (+ region/color),
  match similarity, detected objects, zone and VLM summary.
- Map `package_arrival` / `package_removal` to the package sensor.

## 0.1.0

Initial release. A thin Home Assistant client for a self-hosted Yunkan server.

- Config flow with login and re-authentication (single server).
- Camera entities: native WebRTC (WHEP) with HLS fallback and JPEG snapshots.
- Real-time detection binary sensors (person, vehicle, animal, package, face,
  fall, baby-cry) plus an online connectivity sensor, driven by the event
  stream — no MQTT broker required.
- Latest-event image and last-event timestamp sensor per camera.
- AI detection switch per camera (Pro).
- Media browser for recordings-by-day and recent events, proxied through Home
  Assistant authentication.
- Services: `yunkan.ptz`, `yunkan.tts_broadcast` (Pro) and `yunkan.snapshot`.
- Informational server update entity.
- Repair issue when a Pro-only action is used on a free-tier server.
- Translations: English, Simplified Chinese, Traditional Chinese, Japanese and
  French.
