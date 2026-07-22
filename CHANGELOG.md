# Changelog

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
