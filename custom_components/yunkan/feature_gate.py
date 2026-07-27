"""Expose per-camera entities only while their detection feature is enabled.

A camera with face recognition switched off should not carry a Face occupancy
sensor, a "latest face" image and a "recognised face" sensor that can never
fire. The detection feature switches are live (both the per-camera ones and the
server-wide masters), so this cannot be decided once at setup time: entities are
created when a feature turns on and removed from the entity registry when it
turns off.

Registry removal — rather than leaving the entity behind as unavailable — is
what makes the entity actually disappear from the UI, and it costs the user
nothing: ``async_remove`` moves the entry to the registry's ``deleted_entities``
rather than dropping it, and a later ``async_get_or_create`` with the same
unique id restores the entity id along with the user's customisations (name,
icon, area, labels, aliases). The entry keeps its ``config_entry_id`` while the
entry is loaded, so it is never purged as an orphan. Verified against HA 2026.7
and end-to-end on a live instance; do not "improve" this into leaving stale
unavailable entities behind on the belief that removal loses user data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YunkanConfigEntry
from .const import DOMAIN
from .coordinator import YunkanCoordinator


@dataclass(frozen=True)
class FeatureEntity:
    """An entity that exists only while ``feature`` is enabled on ``camera_id``.

    ``key`` identifies the entity within its platform (e.g. ``"face"`` for the
    face occupancy sensor); ``(camera_id, key)`` is the identity used to track
    what has already been added. ``factory`` builds the entity on demand — it is
    only called when the feature is on.
    """

    camera_id: str
    feature: str
    key: str
    factory: Callable[[], Entity]


@callback
def async_setup_feature_entities(
    hass: HomeAssistant,
    entry: YunkanConfigEntry,
    coordinator: YunkanCoordinator,
    async_add_entities: AddEntitiesCallback,
    platform: str,
    specs: Iterable[FeatureEntity],
) -> None:
    """Add the given entities now and keep the set in sync with the features.

    ``platform`` is the entity domain (``"binary_sensor"`` / ``"image"`` /
    ``"sensor"``), needed to resolve an entity id from a unique id when a
    feature is switched off.
    """
    specs = list(specs)
    # (camera_id, key) -> unique_id of the entity currently added.
    created: dict[tuple[str, str], str] = {}

    @callback
    def _sync() -> None:
        data = coordinator.data
        wanted: dict[tuple[str, str], FeatureEntity] = {}
        for spec in specs:
            ident = (spec.camera_id, spec.key)
            if spec.camera_id not in data.cameras:
                # Camera temporarily absent from the poll (disabled / archived
                # on the server, or a short list hiccup) — neither add nor
                # remove. Removing here would strip half of the device's
                # entities while the always-present ones (online, motion, the
                # camera itself, every switch) merely go unavailable, leaving a
                # visibly torn device page. Whole cameras are cleaned up at
                # setup by _async_prune_stale_devices instead.
                if ident in created:
                    wanted[ident] = spec
                continue
            if data.should_expose_feature(spec.camera_id, spec.feature):
                wanted[ident] = spec

        new_entities: list[Entity] = []
        for ident, spec in wanted.items():
            if ident in created:
                continue
            entity = spec.factory()
            created[ident] = entity.unique_id
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

        stale = [ident for ident in created if ident not in wanted]
        if not stale:
            return
        registry = er.async_get(hass)
        for ident in stale:
            # Drop from `created` whether or not the lookup below finds it:
            # async_add_entities writes the registry entry without yielding, so
            # "not found" means the entity never made it (HA rejected it, e.g.
            # a duplicate unique id) — forgetting it lets the next sync retry,
            # whereas keeping it would strand the slot forever.
            unique_id = created.pop(ident)
            entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
            if entity_id:
                # Removing the registry entry also removes the live entity: the
                # entity platform listens for registry removals.
                registry.async_remove(entity_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))
