"""济南公用事业集成初始化."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GasClient, GasH5Client, WaterClient

_LOGGER = logging.getLogger(__name__)
from .const import (
    CONF_GAS_ORG_ID,
    CONF_GAS_REFRESH,
    CONF_GAS_SUBS_ID,
    CONF_GAS_TOKEN,
    CONF_GS,
    CONF_LOGIN_METHOD,
    CONF_PHONE,
    CONF_SERVICE,
    CONF_USER_CODE,
    CONF_WATER_REFRESH,
    CONF_WATER_TOKEN,
    DOMAIN,
    METHOD_WECHAT,
    SCAN_INTERVAL_MIN,
    SERVICE_GAS,
    SERVICE_WATER,
)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    service = entry.data[CONF_SERVICE]
    session = async_get_clientsession(hass)
    gas: GasClient | None = None
    h5: GasH5Client | None = None
    water: WaterClient | None = None

    if service == SERVICE_GAS:
        if entry.data.get(CONF_LOGIN_METHOD) == METHOD_WECHAT:
            # 微信公众号 H5：token 每 2 小时过期，refresh_token 一次性轮换，
            # 所以刷新后必须把新 token 立刻写回 config entry 才能跨重启续期。
            h5 = GasH5Client(
                session,
                token=entry.data.get(CONF_GAS_TOKEN) or "",
                refresh=entry.data.get(CONF_GAS_REFRESH) or "",
            )
            h5.org_id = entry.data.get(CONF_GAS_ORG_ID) or ""
            h5.subs_id = entry.data.get(CONF_GAS_SUBS_ID) or ""
            h5.subs_code = entry.data.get(CONF_USER_CODE) or ""

            async def _persist_gas_token(token: str, refresh: str) -> None:
                hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_GAS_TOKEN: token,
                        CONF_GAS_REFRESH: refresh,
                    },
                )

            h5.on_rotated = _persist_gas_token

            async def async_update_gas() -> dict:
                try:
                    return await h5.async_get_data()
                except Exception as err:  # noqa: BLE001
                    raise UpdateFailed(f"燃气数据更新失败: {err}") from err
        else:
            gas = GasClient(session)
            gas.token = entry.data.get(CONF_GAS_TOKEN) or ""
            gas.refresh_token = entry.data.get(CONF_GAS_REFRESH)

            async def async_update_gas() -> dict:
                try:
                    subs = await gas.query_subs()
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
        "gas": gas,
        "h5": h5,
        "water": water,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload
