"""济南公用事业集成初始化."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GasClient, WaterClient

_LOGGER = logging.getLogger(__name__)
from .const import (
    CONF_GAS_TOKEN,
    CONF_GS,
    CONF_PHONE,
    CONF_SERVICE,
    CONF_USER_CODE,
    CONF_WATER_REFRESH,
    CONF_WATER_TOKEN,
    DOMAIN,
    SCAN_INTERVAL_MIN,
    SERVICE_GAS,
    SERVICE_WATER,
)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    service = entry.data[CONF_SERVICE]
    session = async_get_clientsession(hass)

    if service == SERVICE_GAS:
        gas = GasClient(session)
        gas.token = entry.data[CONF_GAS_TOKEN]

        async def async_update_gas() -> dict:
            try:
                subs = await gas.query_subs()
                # 找到当前户号
                user_code = entry.data[CONF_USER_CODE]
                sub = next(
                    (s for s in subs if str(
                        s.get("userCode") or s.get("user_code")) == user_code),
                    {},
                )
                return {"sub": sub, "subs": subs}
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(f"燃气数据更新失败: {err}") from err

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"jinan_gas_{entry.data[CONF_USER_CODE]}",
            update_method=async_update_gas,
            update_interval=timedelta(minutes=SCAN_INTERVAL_MIN),
        )
    else:
        water = WaterClient(session)
        water.access_token = entry.data.get(CONF_WATER_TOKEN)
        water.refresh_token = entry.data.get(CONF_WATER_REFRESH)

        async def async_update_water() -> dict:
            try:
                gs = entry.data[CONF_GS]
                phone = entry.data[CONF_PHONE]
                data = await water.get_qfxx(phone, gs)
                return {"qfxx": data}
            except Exception as err:  # noqa: BLE001
                raise UpdateFailed(f"水务数据更新失败: {err}") from err

        coordinator = DataUpdateCoordinator(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"jinan_water_{entry.data[CONF_GS]}",
            update_method=async_update_water,
            update_interval=timedelta(minutes=SCAN_INTERVAL_MIN),
        )

    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "service": service,
        "gas": gas if service == SERVICE_GAS else None,
        "water": water if service == SERVICE_WATER else None,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload
