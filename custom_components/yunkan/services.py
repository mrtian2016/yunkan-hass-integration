"""Services for the Yunkan integration: PTZ, TTS broadcast and snapshot.

Services target Yunkan camera entities (directly, by device or by area) and are
resolved to the owning coordinator and backend camera id. Pro-gated actions
(TTS) degrade to a clear error plus a repair issue on the free tier.
"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.service import async_extract_referenced_entity_ids

from .api import YunkanApiError, YunkanProRequiredError
from .const import DOMAIN, PTZ_DIRECTIONS
from .coordinator import YunkanCoordinator
from .issues import async_raise_pro_required

_LOGGER = logging.getLogger(__name__)

SERVICE_PTZ = "ptz"
SERVICE_TTS_BROADCAST = "tts_broadcast"
SERVICE_SNAPSHOT = "snapshot"

ATTR_DIRECTION = "direction"
ATTR_SPEED = "speed"
ATTR_DURATION = "duration"
ATTR_MESSAGE = "message"
ATTR_VOICE = "voice"
ATTR_RATE = "rate"
ATTR_PITCH = "pitch"
ATTR_FILENAME = "filename"

_PTZ_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_DIRECTION): vol.In(PTZ_DIRECTIONS),
        vol.Optional(ATTR_SPEED, default=0.5): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=1.0)
        ),
        vol.Optional(ATTR_DURATION, default=0.5): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=10.0)
        ),
    }
)

_TTS_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_VOICE): cv.string,
        vol.Optional(ATTR_RATE): cv.string,
        vol.Optional(ATTR_PITCH): cv.string,
    }
)

_SNAPSHOT_SCHEMA = cv.make_entity_service_schema(
    {vol.Required(ATTR_FILENAME): cv.template}
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register Yunkan services once for the integration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("services_registered"):
        return

    async def _handle_ptz(call: ServiceCall) -> None:
        direction = call.data[ATTR_DIRECTION]
        speed = call.data[ATTR_SPEED]
        duration = call.data[ATTR_DURATION]
        for coordinator, camera_id in _resolve_targets(hass, call):
            try:
                await coordinator.client.async_ptz(camera_id, direction, speed)
                if duration > 0:
                    await asyncio.sleep(duration)
                    await coordinator.client.async_ptz_stop(camera_id)
            except YunkanApiError as err:
                raise HomeAssistantError(f"PTZ command failed: {err}") from err

    async def _handle_tts(call: ServiceCall) -> None:
        message = call.data[ATTR_MESSAGE]
        voice = call.data.get(ATTR_VOICE)
        rate = call.data.get(ATTR_RATE)
        pitch = call.data.get(ATTR_PITCH)
        for coordinator, camera_id in _resolve_targets(hass, call):
            try:
                await coordinator.client.async_tts_broadcast(
                    camera_id, message, voice=voice, rate=rate, pitch=pitch
                )
            except YunkanProRequiredError as err:
                async_raise_pro_required(hass, err.license_status)
                raise HomeAssistantError(
                    "Voice broadcast is a Pro feature; the server is on the free tier."
                ) from err
            except YunkanApiError as err:
                raise HomeAssistantError(f"Voice broadcast failed: {err}") from err

    async def _handle_snapshot(call: ServiceCall) -> None:
        filename_template = call.data[ATTR_FILENAME]
        for coordinator, camera_id in _resolve_targets(hass, call):
            filename_template.hass = hass
            filename = filename_template.async_render(
                variables={"entity_id": camera_id}, parse_result=False
            )
            if not hass.config.is_allowed_path(filename):
                raise ServiceValidationError(
                    f"Cannot write to {filename}, no access to path; "
                    "add it to allowlist_external_dirs"
                )
            image = await coordinator.client.async_snapshot(camera_id, force=True)
            if image is None:
                raise HomeAssistantError(f"No snapshot available for {camera_id}")

            def _write(path: str, data: bytes) -> None:
                with open(path, "wb") as file:
                    file.write(data)

            await hass.async_add_executor_job(_write, filename, image)

    hass.services.async_register(DOMAIN, SERVICE_PTZ, _handle_ptz, schema=_PTZ_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_TTS_BROADCAST, _handle_tts, schema=_TTS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SNAPSHOT, _handle_snapshot, schema=_SNAPSHOT_SCHEMA
    )
    domain_data["services_registered"] = True


def _resolve_targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[YunkanCoordinator, str]]:
    """Resolve referenced entities to (coordinator, camera_id) pairs."""
    selected = async_extract_referenced_entity_ids(hass, call)
    entity_ids = selected.referenced | selected.indirectly_referenced
    registry = er.async_get(hass)
    seen: set[tuple[str, str]] = set()
    targets: list[tuple[YunkanCoordinator, str]] = []
    for entity_id in entity_ids:
        entry = registry.async_get(entity_id)
        if entry is None or entry.platform != DOMAIN or entry.domain != "camera":
            continue
        config_entry = hass.config_entries.async_get_entry(entry.config_entry_id or "")
        coordinator = getattr(config_entry, "runtime_data", None)
        if coordinator is None:
            continue
        camera_id = entry.unique_id.removeprefix(f"{entry.config_entry_id}_").removesuffix(
            "_camera"
        )
        key = (entry.config_entry_id or "", camera_id)
        if key in seen:
            continue
        seen.add(key)
        targets.append((coordinator, camera_id))
    if not targets:
        raise ServiceValidationError(
            "No Yunkan camera entity was targeted by this service call"
        )
    return targets
