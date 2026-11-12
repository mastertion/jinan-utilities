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
        entities: list[SensorEntity] = [JinanGasBalanceSensor(hass, entry, store)]
        # 微信 H5 网关同时返回余额与待交金额，多给一个实体
        if store.get("h5"):
            entities.append(JinanGasArrearsSensor(hass, entry, store))
        async_add_entities(entities)
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
        # 微信 H5：charge/gasFeeBaseinfo 的 availableBalance 就是账户余额
        fee = self.coordinator.data.get("fee")
        value = _pick_money(fee, ("availableBalance", "balance"))
        if value is not None:
            return value
        sub = self.coordinator.data.get("sub") or {}
        return _pick_money(
            sub,
            ("balance", "Balance", "userBalance", "remainMoney", "yue"),
        )

    @property
    def extra_state_attributes(self) -> dict:
        # H5 返回的户主姓名/地址已脱敏，但仍走白名单，避免把 subsId、
        # orgId 等内部标识写进 HA 的 state 历史。
        sub = self.coordinator.data.get("sub") or {}
        return {
            k: sub[k] for k in GAS_SAFE_ATTRS
            if k in sub and sub[k] is not None
            and not isinstance(sub[k], (list, dict))
        }

    @property
    def name(self) -> str:
        return "燃气账户余额"


class JinanGasArrearsSensor(JinanBase, SensorEntity):
    """待交燃气费（微信 H5 字段 feePayable）."""

    _attr_icon = "mdi:cash-remove"
    _attr_native_unit_of_measurement = "元"

    @property
    def unique_id(self) -> str:
        return f"jinan_gas_arrears_{self._entry.data[CONF_USER_CODE]}"

    @property
    def native_value(self) -> Any:
        return _pick_money(
            self.coordinator.data.get("fee"),
            ("feePayable", "arrears", "qfje"),
        )

    @property
    def name(self) -> str:
        return "待交燃气费"


# 允许写入 HA 属性的燃气字段白名单（其余一律丢弃）
GAS_SAFE_ATTRS = (
    "subsCode", "orgCode", "nickName", "name", "displayAddr",
    "lastMeterReadingDate", "feePayable", "balance", "subsType",
)


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
