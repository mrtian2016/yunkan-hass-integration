# Yunkan for Home Assistant

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
- **Detection binary sensors** per camera — person, vehicle, animal, package,
  face, fall and baby-cry — updated in real time from the event stream, plus an
  **Online** connectivity sensor.
- **Latest-event image** per camera (with detection boxes drawn) for rich
  notifications.
- **Last-event** timestamp sensor with the event category and summary as
  attributes.
- **AI detection switch** per camera (Pro).
- **Media browser** to page through recordings by day and recent events (with
  thumbnails), all proxied through Home Assistant's own authentication.
- **Services**: `yunkan.ptz`, `yunkan.tts_broadcast` (Pro) and `yunkan.snapshot`.
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

If WebRTC can't be reached (for example behind a tunnel that only carries TCP),
the camera still works over HLS.

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

## Troubleshooting

- **"Server hasn't finished setup"** — open the Yunkan web interface and create
  an admin account first, then add the integration.
- **Live view is black but the camera works** — WebRTC couldn't reach port
  `23515`; the stream falls back to HLS. Check port forwarding / the server's
  public address.
- **Detection switch or voice broadcast fails** — check that your account is an
  admin and that the server is on the Pro tier.

Grab diagnostics from the integration's device page (credentials and stream keys
are redacted) when reporting an issue.

## License

Released under the [MIT License](LICENSE).
