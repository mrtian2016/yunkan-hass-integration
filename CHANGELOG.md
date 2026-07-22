# Changelog

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
