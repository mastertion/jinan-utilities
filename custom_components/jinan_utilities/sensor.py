"""济南公用事业传感器."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ADDRESS,
    CONF_GS,
    CONF_PHONE,
    CONF_SERVICE,
    CONF_USER_CODE,
    CONF_USER_NAME,
    DOMAIN,
    SERVICE_GAS,
    SERVICE_WATER,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    if store["service"] == SERVICE_GAS:
        async_add_entities([JinanGasBalanceSensor(hass, entry, store)])
    else:
        # 网厅 GetQFXXByGS 同时给出「余额 yue」和「待交水费 qfje」，拆成两个传感器
        async_add_entities([
            JinanWaterSensor(hass, entry, store),
            JinanWaterArrearsSensor(hass, entry, store),
        ])


class JinanBase(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: dict) -> None:
        super().__init__(store["coordinator"])
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._entry.title,
            manufacturer="济南",
            entry_type=DeviceEntryType.SERVICE,
        )


class JinanGasBalanceSensor(JinanBase, SensorEntity):
    """燃气账户余额."""

    _attr_device_class = None
    _attr_native_unit_of_measurement = "元"
    _attr_icon = "mdi:fire"

    @property
    def unique_id(self) -> str:
        return f"jinan_gas_balance_{self._entry.data[CONF_USER_CODE]}"

    @property
    def native_value(self) -> Any:
        sub = self.coordinator.data.get("sub") or {}
        for key in ("balance", "Balance", "userBalance", "remainMoney", "yue"):
            if sub.get(key) is not None:
                try:
                    return round(float(sub[key]), 2)
                except (TypeError, ValueError):
                    continue
        return None

    @property
    def extra_state_attributes(self) -> dict:
        sub = self.coordinator.data.get("sub") or {}
        # 实测阶段全量输出，便于校准余额字段名
        return {k: v for k, v in sub.items() if not isinstance(v, (list, dict))}

    @property
    def name(self) -> str:
        return "燃气账户余额"


class JinanWaterSensor(JinanBase, SensorEntity):
    """水费账户余额（网厅字段 yue）."""

    _attr_icon = "mdi:water"
    _attr_native_unit_of_measurement = "元"

    @property
    def unique_id(self) -> str:
        return f"jinan_water_{self._entry.data[CONF_GS]}"

    @property
    def native_value(self) -> Any:
        return _pick_money(self.coordinator.data.get("qfxx"),
                           ("yue", "YUE", "balance", "Balance", "YE"))

    @property
    def extra_state_attributes(self) -> dict:
        # GetQFXXByGS 会返回整份用户档案（含身份证号 uidm、详细住址 mp、
        # 户名 hm、联系电话 lxdh），只暴露白名单里的非敏感字段，
        # 避免把个人资料写进 HA 的 state 历史/数据库。
        return _safe_water_attrs(self.coordinator.data.get("qfxx"))

    @property
    def name(self) -> str:
        return "水费账户余额"


class JinanWaterArrearsSensor(JinanBase, SensorEntity):
    """待交水费 / 欠费（网厅字段 qfje）."""

    _attr_icon = "mdi:cash-remove"
    _attr_native_unit_of_measurement = "元"

    @property
    def unique_id(self) -> str:
        return f"jinan_water_arrears_{self._entry.data[CONF_GS]}"

    @property
    def native_value(self) -> Any:
        return _pick_money(self.coordinator.data.get("qfxx"),
                           ("qfje", "QFJE", "Arrears", "arrears", "OweFee"))

    @property
    def name(self) -> str:
        return "待交水费"


# 允许写入 HA 属性的水务字段白名单（其余一律丢弃）
WATER_SAFE_ATTRS = (
    "gs", "bm", "djfl", "dj", "yue", "qfje", "xzje", "wyj", "ycje",
    "jieSuanYUE", "r1", "kj", "sljs", "zq", "qd",
)


def _safe_water_attrs(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        k: data[k] for k in WATER_SAFE_ATTRS
        if k in data and data[k] is not None
        and not isinstance(data[k], (list, dict))
    }


def _pick_money(data: Any, keys: tuple[str, ...]) -> float | None:
    """从兼容结构里取第一个可解析为金额的字段."""
    if not isinstance(data, dict):
        return None
    for key in keys:
        if data.get(key) is not None:
            try:
                return round(float(data[key]), 2)
            except (TypeError, ValueError):
                continue
    return None
