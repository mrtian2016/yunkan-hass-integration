# Yunkan for Home Assistant

**English** | [中文](README.zh-CN.md)

A [Home Assistant](https://www.home-assistant.io/) custom integration for a
self-hosted **Yunkan** camera server ([yun-kan.com](https://yun-kan.com)).

It is a thin client: it talks to your Yunkan server over its regular REST and
event (SSE) API and maps it onto native Home Assistant entities, a media browser
and a few services. All recording, detection and account logic stays on the
server — the integration stores nothing but the connection details.

> Prefer a zero-integration setup? Yunkan can also publish cameras to Home
> Assistant over MQTT discovery. This integration is the richer, Pro-oriented
> alternative and does **not** require an MQTT broker.

## Features

- **Cameras** with native WebRTC (low-latency, HLS fallback) and JPEG snapshots.
  This unlocks the rest of the HA camera ecosystem: picture-glance cards, area
  dashboards, HomeKit export and `camera.snapshot`.
- **Detection occupancy sensors** per camera — person, vehicle, animal, package,
  face, fall and baby-cry. They turn on the instant an event fires (carrying the
  recognised name / licence plate as attributes) and stay on while the object is
  present (live tracking), plus a generic **Motion** and an **Online** sensor.
- **Object-count** sensors (live person / vehicle / animal / package counts) and
  **Recognised face** / **Recognised plate** sensors per camera.
- **Latest-event image** per camera plus a **latest snapshot per category**
  (with detection boxes drawn) for rich notifications.
- **Last-event** timestamp sensor with the event category, name, plate and
  summary as attributes.
- **Switches** per camera: AI detection, recording, per-feature detection
  toggles (object / audio / face / plate / pose / gesture / package) and PTZ
  auto-tracking.
- **Server-wide detection switches** on the Yunkan server device — one master
  toggle per detection feature (object / audio / face / plate / pose / gesture /
  package) plus **motion**, the same control as the Web Admin detection page. A
  feature turned off here is off for every camera; the per-camera switches sit
  under it. Motion is server-wide only (per-camera motion is tuned by
  sensitivity / region, not an on/off switch).
- **Birdseye** overview camera (when enabled on the server).
- **Media browser** to page through recordings by day, recent events and a
  snapshot gallery (with thumbnails), all proxied through Home Assistant's own
  authentication.
- **Device triggers** — "person detected", "vehicle detected", etc. — for
  building automations from the UI, plus a ready-made notification blueprint.
- **Services**: `yunkan.ptz` (direction or preset), `yunkan.tts_broadcast`
  (Pro), `yunkan.snapshot` and `yunkan.export` (clip, realtime or timelapse).
- **Server update** entity that reflects the server's available version.

## Requirements

- Home Assistant **2024.11** or newer (for native WebRTC cameras).
- A reachable Yunkan server. Use its main entry URL (the nginx entry, usually
  port `23406`), for example `http://192.168.1.10:23406` — **not** an internal
  port.
- A Yunkan account. An **admin** account is required for PTZ, the detection
  switch and voice broadcast; viewing and browsing work with any account.

## Installation

### HACS (recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=yunkan&repository=yunkan-hass-integration&category=integration)

Click the button above to open this repository in HACS, install **Yunkan** and
restart Home Assistant. Or add it manually:

1. In HACS, add this repository as a **custom repository** (category
   *Integration*).
2. Install **Yunkan** and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Yunkan** and enter
   your server URL, username and password.

### Manual

Copy `custom_components/yunkan` into your Home Assistant `config/custom_components`
directory and restart.

## Live view and WebRTC reachability

Live view prefers WebRTC and automatically falls back to HLS when WebRTC can't be
established. For WebRTC to connect, the machine running Home Assistant must be
able to reach the Yunkan media plane (UDP/TCP port `23515`) on the server host:

- Running Yunkan as a Home Assistant add-on (host networking) — reachable out of
  the box.
- Remote / reverse-proxied deployments — forward UDP+TCP `23515` and set the
  server's public address so it advertises a reachable candidate. See the
  Yunkan Home Assistant documentation for the reverse-proxy checklist.

Live view in the dashboard is WebRTC. Snapshots, recording, casting and still
previews use HLS and keep working regardless; if the WebRTC media plane can't be
reached the live card shows an error, so make sure port `23515` is reachable.

## Free tier vs Pro

Cameras, snapshots, events, sensors, the media browser and PTZ work on any
Yunkan server. **AI detection** and **voice broadcast** are Pro features; on a
free-tier server those actions surface a repair issue explaining that an upgrade
is required, instead of failing silently.

## Coexisting with MQTT discovery

If you also use Yunkan's MQTT discovery, you'll get two sets of camera entities.
This integration uses distinct entity IDs so nothing collides, but you may want
to disable the MQTT-provided camera/event entities to avoid duplicates. The two
paths are independent and neither disables the other automatically.

## Services

| Service | Description | Tier |
|---|---|---|
| `yunkan.ptz` | Move a pan/tilt/zoom camera; auto-stops after a short duration | Free (admin) |
| `yunkan.tts_broadcast` | Speak a message on a camera speaker | Pro (admin) |
| `yunkan.snapshot` | Capture a fresh snapshot to a file | Free |
| `yunkan.export` | Export a recording for a time range to a downloadable clip | Free |

Example:

```yaml
service: yunkan.ptz
target:
  entity_id: camera.front_door
data:
  direction: left
  speed: 0.5
  duration: 1
```

## Automations & notifications

Each camera device exposes **device triggers** ("Person detected", "Vehicle
detected", …) — pick one when creating an automation and select the camera. The
trigger data includes the recognised name / licence plate.

A ready-made notification blueprint is included. It is not auto-installed, so
import it once by URL (My Home Assistant → Blueprints → Import, or **Settings →
Automations & scenes → Blueprints → Import blueprint**) using the raw URL of
`blueprints/automation/yunkan/camera_notification.yaml` in this repository. It
sends a mobile notification with a snapshot when the selected camera detects
something.

## Troubleshooting

- **"Server hasn't finished setup"** — open the Yunkan web interface and create
  an admin account first, then add the integration.
- **Live view is black but snapshots work** — WebRTC couldn't reach the media
  plane on port `23515`. Check port forwarding / the server's public address.
- **Detection switch or voice broadcast fails** — check that your account is an
  admin and that the server is on the Pro tier.
- **A per-feature detection switch (face/plate/…) won't turn on** — the
  per-*camera* switches are overrides on top of the server-wide setting. You can
  turn a feature *off* for one camera, but a feature that is disabled server-wide
  can't be enabled per camera. Turn it on first with the matching **server-wide**
  switch on the Yunkan server device (or in Web Admin), then adjust per camera.

Grab diagnostics from the integration's device page (credentials and stream keys
are redacted) when reporting an issue.

## License

Released under the [MIT License](LICENSE).
