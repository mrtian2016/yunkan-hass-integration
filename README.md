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
  face, fall, baby-cry and gesture. They turn on the instant an event fires
  (carrying the recognised name / licence plate / gesture as attributes) and
  stay on while the object is present (live tracking; gesture pulses only),
  plus a generic **Motion** and an **Online** sensor.
- **Object-count** sensors (live person / vehicle / animal / package counts) and
  **Recognised face** / **Recognised plate** / **Recognised gesture** sensors
  per camera.
- **Only the features you actually run** get entities: a camera with face
  recognition switched off carries no Face sensor, no "latest face" image and no
  "recognised face" sensor. Switch the feature back on (per camera or
  server-wide) and its entities come back within one poll — same entity ids,
  customisations intact, no reload needed. Motion, Online, the camera and the
  switches are always present.
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
- **One-tap controls on the camera device page**: a **Refresh snapshot**
  button, **up / down / left / right** and **Stop** buttons for PTZ cameras
  (a direction starts a continuous move that Stop — or an 8-second safety
  timer — ends), a **PTZ preset** dropdown listing the camera's own presets,
  and a **Broadcast message** text field paired with a **Voice broadcast**
  button. Anything needing free-form input beyond that stays a service.
- **Birdseye** overview camera (when enabled on the server).
- **Media browser** to page through recordings by day, recent events and a
  snapshot gallery (with thumbnails), all proxied through Home Assistant's own
  authentication.
- **Device triggers** — "person detected", "vehicle detected", etc. — for
  building automations from the UI, plus a ready-made notification blueprint.
- **Device actions** — move the camera, go to a preset, broadcast a message,
  take a snapshot or export a clip — pickable per camera device in the
  automation editor's *then do* step.
- **Services**: `yunkan.ptz` (direction or preset), `yunkan.tts_broadcast`
  (Pro), `yunkan.snapshot` and `yunkan.export` (clip, realtime or timelapse).
- **Server update** entity that reflects the server's available version.
- **Sidebar panel** (optional): embed the full Yunkan web console in the Home
  Assistant sidebar — see [Sidebar panel](#sidebar-panel-embedded-web-console).

## Requirements

- Home Assistant **2024.12** or newer. Native WebRTC cameras arrived in
  2024.11, but the integration's options flow needs the config entry on the
  base options flow class, which landed in 2024.12.
- A reachable Yunkan server. Use its main entry URL (the nginx entry, usually
  port `23406`), for example `http://192.168.1.10:23406` — **not** an internal
  port.
- A Yunkan account. An **admin** account is required for PTZ, the detection
  switch and voice broadcast; viewing and browsing work with any account.

## Installation

### HACS (recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mrtian2016&repository=yunkan-hass-integration&category=integration)

Yunkan isn't in the default HACS store yet, so it installs as a **custom
repository** — the button above sets that up for you: click it to open the repo
in HACS, install **Yunkan**, then restart Home Assistant. Or add it manually:

1. In HACS, open the three-dot menu → **Custom repositories**, add
   `https://github.com/mrtian2016/yunkan-hass-integration` with category
   **Integration**.
2. Search for **Yunkan**, install it, and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration → Yunkan** and enter
   your server address. Home Assistant then opens the Yunkan sign-in page in a
   new window; sign in the way you normally do and press **Authorize**. Yunkan
   hands Home Assistant an API token, so your password stays on the server and
   an account with two-factor authentication works like any other.

   Older Yunkan servers have no authorization page and ask for a username and
   password instead, exactly as before. Those accounts cannot have two-factor
   authentication turned on — update the server first.

   If your browser cannot open the authorization page at all — a certificate it
   refuses, or a Yunkan server it cannot reach from where you are — tick **Sign
   in with a username and password instead** on the first step to go straight to
   the password form.

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

## Sidebar panel (embedded web console)

The options flow (Settings → Devices & services → Yunkan → Configure) can add
the full Yunkan web console to the Home Assistant sidebar. The panel is a plain
iframe pointing at your Yunkan server — Home Assistant does **not** proxy the
traffic — so a few things follow from that:

- **Your browser must reach the Yunkan address directly** (same LAN, or
  Yunkan's own public HTTPS access). The panel does not work through Nabu Casa
  remote access on its own; Yunkan itself must be reachable from wherever you
  are browsing.
- **HTTPS Home Assistant needs an HTTPS panel URL.** Browsers block a
  plain-HTTP frame inside an HTTPS page as mixed content. Yunkan has built-in
  public HTTPS access (single public port + automatic Let's Encrypt
  certificates); put that address into the *Web console address* option.
- **Signing in inside the panel needs a server-side switch — the integration
  flips it for you.** Enabling the panel automatically switches on *Allow
  embedding in Home Assistant* on the server (needs the integration to be
  configured with an admin account). If the account is not an admin, or the
  server predates the setting, a repair issue explains what to do — you can
  always enable it manually in the web console (*Settings → remote live view →
  advanced*). While the panel option is on, the integration keeps the switch
  enabled (re-asserting it on reloads); it never turns it off — disable the
  panel option first, then turn the switch off in the web console. Embedded
  sign-in only works when the panel URL is HTTPS — over plain HTTP, modern
  browsers refuse the session cookie inside a frame regardless of the switch.
- **Two-way talk stays in the full tab.** The sidebar frame is not granted
  microphone permission, so use the *open in browser* path for talkback.
  Browsers that block third-party cookies entirely (Safari, Chrome's Incognito
  mode) also refuse the embedded sign-in — open the console in its own tab
  there.

Leave the *Web console address* option empty to reuse the connection address;
set it when the browser should use a different one (typically your HTTPS
domain while the integration talks to the LAN address).

## Options

Settings → Devices & services → Yunkan → **Configure**. Changes apply
immediately (the entry reloads itself).

| Option | Default | What it does |
|---|---|---|
| Draw detection boxes on event images | on | The server draws the detection boxes onto the *latest event* / per-category images, as in the event centre. |
| Crop event images around the detected object | off | Zooms the same images in on the detected object with a little context — useful for notification previews. |
| Show the Yunkan web console in the sidebar | off | See [Sidebar panel](#sidebar-panel-embedded-web-console). |
| Sidebar panel title | Yunkan | Name shown in the sidebar. |
| Web console address for the panel | *(connection address)* | Address the browser opens for the panel, when it differs from the one the integration talks to. |

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

### Device actions

The same actions are available per camera device in the automation editor
(*then do* → pick the camera device):

| Device action | Service behind it |
|---|---|
| Move the camera | `yunkan.ptz` (direction, speed, duration) |
| Go to a preset | `yunkan.ptz` (preset, speed) |
| Voice broadcast | `yunkan.tts_broadcast` (message, voice, rate, pitch) |
| Take a snapshot | `yunkan.snapshot` (filename) |
| Export a clip | `yunkan.export` (start, end, playback factor) |

PTZ actions only appear on cameras that support PTZ, and voice broadcast only
on cameras with a speaker.

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
