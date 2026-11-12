"""济南公用事业配置流程."""
from __future__ import annotations

import os
import time
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GasClient, GasError, GasH5Client, WaterClient, WaterError,
    parse_gas_credential,
)
from .const import (
    CONF_ADDRESS,
    CONF_GAS_ORG_ID,
    CONF_GAS_REFRESH,
    CONF_GAS_SUBS_ID,
    CONF_GAS_TOKEN,
    CONF_GS,
    CONF_LOGIN_METHOD,
    CONF_ORG_CODE,
    CONF_PHONE,
    CONF_SERVICE,
    CONF_USER_CODE,
    CONF_USER_NAME,
    CONF_WATER_REFRESH,
    CONF_WATER_TOKEN,
    CONF_WECHAT_LINK,
    DOMAIN,
    GAS_H5_AUTHORIZE_URL,
    METHOD_PASSWORD,
    METHOD_PHONE,
    METHOD_SMS,
    METHOD_TOKEN,
    METHOD_WECHAT,
    SERVICE_GAS,
    SERVICE_WATER,
)

SERVICE_SCHEMA = vol.Schema({
    vol.Required(CONF_SERVICE, default=SERVICE_GAS): vol.In(
        {SERVICE_GAS: "燃气（山东港华）", SERVICE_WATER: "水费（济南水务）"}),
})

# 登录方式按服务类型分开显示。HA 配置流不支持同一表单内字段联动，
# 所以拆成「先选服务 → 再选该服务专属的登录方式」两步。
GAS_METHODS = {
    METHOD_WECHAT: "微信扫码授权（推荐·不用短信·自动续期）",
    METHOD_TOKEN: "粘贴 PC 网厅链接 / 手动 token（旧版）",
    METHOD_SMS: "手机号 + 短信验证码（官网也收不到，仅供参考）",
}
WATER_METHODS = {
    METHOD_PHONE: "仅手机号直连（推荐）",
    METHOD_SMS: "手机号 + 短信验证码",
    METHOD_PASSWORD: "密码登录（网厅图形码故障，暂不可用）",
    METHOD_TOKEN: "手动填入 access_token",
}
GAS_DEFAULT_METHOD = METHOD_WECHAT
WATER_DEFAULT_METHOD = METHOD_PHONE


def _method_schema(service: str) -> vol.Schema:
    """按所选服务返回专属登录方式下拉框."""
    if service == SERVICE_GAS:
        options, default = GAS_METHODS, GAS_DEFAULT_METHOD
    else:
        options, default = WATER_METHODS, WATER_DEFAULT_METHOD
    return vol.Schema({
        vol.Required(CONF_LOGIN_METHOD, default=default): vol.In(options)
    })
PHONE_SCHEMA = vol.Schema({vol.Required(CONF_PHONE): str})
SMS_SCHEMA = vol.Schema({
    vol.Optional("smscode", default=""): str,
    vol.Optional("resend", default=False): bool,
})
CAPTCHA_SCHEMA = vol.Schema({vol.Required("kaptcha"): str})
WATER_CAPTCHA_SCHEMA = vol.Schema({
    vol.Required("password"): str,
    vol.Required("kaptcha"): str,
})
MANUAL_SCHEMA = vol.Schema({
    vol.Required("access_token"): str,
    vol.Optional("refresh_token", default=""): str,
})
WECHAT_SCHEMA = vol.Schema({vol.Required(CONF_WECHAT_LINK): str})


class JinanUtilitiesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """济南公用事业配置流程."""

    VERSION = 1

    def __init__(self) -> None:
        self.service: str | None = None
        self.login_method: str = METHOD_SMS
        self.phone: str | None = None
        self.gas: GasClient | None = None
        self.h5: GasH5Client | None = None
        self.water: WaterClient | None = None
        self.subs: list[dict] = []
        self.gs_list: list[dict] = []
        self.orgs: list[dict] = []
        self.org_code: str | None = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        """第一步：只选服务类型."""
        if user_input is not None:
            self.service = user_input[CONF_SERVICE]
            return await self.async_step_method()
        return self.async_show_form(step_id="user", data_schema=SERVICE_SCHEMA)

    async def async_step_method(self, user_input=None) -> FlowResult:
        """第二步：只列出当前服务支持的登录方式."""
        if user_input is not None:
            allowed = (
                GAS_METHODS if self.service == SERVICE_GAS else WATER_METHODS
            )
            default = (
                GAS_DEFAULT_METHOD if self.service == SERVICE_GAS
                else WATER_DEFAULT_METHOD
            )
            # 防御：旧流程或手工提交里带了不匹配的登录方式时回退到推荐项
            method = user_input.get(CONF_LOGIN_METHOD, default)
            self.login_method = method if method in allowed else default
            if self.login_method == METHOD_WECHAT:
                return await self.async_step_wechat()
            if self.service == SERVICE_GAS and self.login_method in (
                METHOD_PASSWORD, METHOD_PHONE
            ):
                self.login_method = METHOD_SMS
            return await self.async_step_phone()
        service_label = (
            "燃气（山东港华）" if self.service == SERVICE_GAS
            else "水费（济南水务）"
        )
        return self.async_show_form(
            step_id="method",
            data_schema=_method_schema(self.service),
            description_placeholders={"service": service_label},
        )

    # ---------------- 手机号 ----------------
    async def async_step_phone(self, user_input=None) -> FlowResult:
        errors = {}
        last_err = ""
        if user_input is not None:
            self.phone = user_input[CONF_PHONE].strip()
            if self.login_method == METHOD_TOKEN:
                return await self.async_step_manual()
            session = async_get_clientsession(self.hass)
            try:
                if self.service == SERVICE_GAS:
                    self.gas = GasClient(session)
                    # queryOrgs 返回两个组织（同属山东港华燃气集团），
                    # 不再写死第一个，让用户按缴费单上的服务中心选。
                    self.orgs = await self.gas.query_orgs()
                    if len(self.orgs) > 1:
                        return await self.async_step_org()
                    self.org_code = str(self.orgs[0]["orgCode"])
                    await self.gas.prepare_session(self.org_code)
                    return await self.async_step_captcha()
                self.water = WaterClient(session)
                if self.login_method == METHOD_PHONE:
                    # 实测：网关只认 UserInfo Cookie + openid(手机号)，
                    # 不需要短信/密码/token，直接就能查绑定户号与余额
                    self.gs_list = await self.water.get_bind_list(self.phone)
                    if not self.gs_list:
                        return self.async_abort(reason="water_no_bind")
                    return await self.async_step_pick_gs()
                if self.login_method == METHOD_PASSWORD:
                    return await self.async_step_water_captcha()
                await self.water.send_sms(self.phone)
                return await self.async_step_sms()
            except (GasError, WaterError) as err:
                errors["base"] = "send_sms_failed"
                last_err = str(err)
            except aiohttp.ClientError as err:
                errors["base"] = "network"
                last_err = str(err)
        return self.async_show_form(
            step_id="phone", data_schema=PHONE_SCHEMA, errors=errors,
            description_placeholders={"error": last_err}
        )

    # ---------------- 燃气选择所属公司（stdCode / 短信通道） ----------------
    async def async_step_org(self, user_input=None) -> FlowResult:
        """两个 orgCode 同属山东港华燃气集团，按缴费单上的服务中心选."""
        errors = {}
        last_err = ""
        options = {
            str(o["orgCode"]): f"{o.get('orgName') or o['orgCode']} ({o['orgCode']})"
            for o in self.orgs
        }
        if user_input is not None:
            try:
                self.org_code = user_input[CONF_ORG_CODE]
                await self.gas.prepare_session(self.org_code)
                return await self.async_step_captcha()
            except (GasError, aiohttp.ClientError) as err:
                errors["base"] = "query_failed"
                last_err = str(err)
        return self.async_show_form(
            step_id="org",
            data_schema=vol.Schema({
                vol.Required(CONF_ORG_CODE,
                             default=str(self.orgs[0]["orgCode"])): vol.In(options)
            }),
            errors=errors,
            description_placeholders={"error": last_err},
        )

    # ---------------- 燃气：微信公众号 H5 授权（推荐） ----------------
    async def async_step_wechat(self, user_input=None) -> FlowResult:
        """粘贴微信回调链接（带 code=...），集成自动换 token 并续期.

        这条链完全绕开 PC 网厅的短信登录：微信 OAuth 回调 code →
        accessToekn → access_token / refresh_token → 业务接口取余额。
        """
        errors = {}
        last_err = ""
        if user_input is not None:
            raw = (user_input.get(CONF_WECHAT_LINK) or "").strip()
            if not raw:
                errors["base"] = "need_token"
                last_err = (
                    "请粘贴微信授权后浏览器地址栏里的整条链接（需带 code=...）"
                )
            else:
                session = async_get_clientsession(self.hass)
                try:
                    cred = parse_gas_credential(raw)
                    h5 = GasH5Client(session)
                    if cred["code"]:
                        await h5.exchange_code(cred["code"])
                    elif cred["token"]:
                        h5.token = cred["token"]
                        h5.refresh_token = cred["refresh"]
                    else:
                        raise GasError(
                            "粘贴内容里没有找到 code 或 access_token。"
                            "请把微信授权后地址栏里的整条链接原样复制进来。"
                        )
                    if cred["refresh"] and not h5.refresh_token:
                        h5.refresh_token = cred["refresh"]
                    self.subs = await h5.query_bind_list()
                    if not self.subs:
                        raise GasError("token 有效，但未查到该微信绑定的燃气户号")
                    self.h5 = h5
                    self.phone = ""
                    return await self.async_step_pick_user()
                except (GasError, WaterError) as err:
                    errors["base"] = "manual_failed"
                    last_err = str(err)
                except aiohttp.ClientError as err:
                    errors["base"] = "network"
                    last_err = str(err)
        return self.async_show_form(
            step_id="wechat", data_schema=WECHAT_SCHEMA, errors=errors,
            description_placeholders={
                "error": last_err,
                "authorize_url": GAS_H5_AUTHORIZE_URL,
            },
        )

    # ---------------- 手动填入 token（收不到短信时的兜底） ----------------
    async def async_step_manual(self, user_input=None) -> FlowResult:
        """粘贴微信链接 / tokenCode / access_token，完全绕开短信."""
        errors = {}
        last_err = ""
        if user_input is not None:
            raw = (user_input.get("access_token") or "").strip()
            refresh_in = (user_input.get("refresh_token") or "").strip()
            if not raw:
                errors["base"] = "need_token"
                last_err = "请粘贴微信链接 / tokenCode / access_token"
            else:
                session = async_get_clientsession(self.hass)
                try:
                    if self.service == SERVICE_GAS:
                        self.gas = GasClient(session)
                        cred = parse_gas_credential(raw)
                        token = cred["token"]
                        refresh = cred["refresh"] or refresh_in
                        # 微信链接里带 tokenCode / code 时，直接调官网 GET 换 token
                        if not token and cred["code"]:
                            exchanged = await self.gas.exchange_code(cred["code"])
                            token = exchanged["token"]
                            refresh = refresh or exchanged["refresh"]
                        if not token:
                            raise GasError(
                                "没能从粘贴内容里找到 tokenCode / access_token。"
                                "请在微信里打开公众号网上营业厅，点右上角「···」→ "
                                "「复制链接」，把完整链接粘贴进来（链接需带 "
                                "tokenCode 或 token 参数）；若页面已把参数清掉，"
                                "可改用浏览器 F12 复制 access_token。"
                            )
                        self.gas.token = token
                        self.gas.refresh_token = refresh or None
                        self.subs = await self.gas.query_subs()
                        if not self.subs:
                            raise GasError("token 无效或已过期，未查到户号")
                        return await self.async_step_pick_user()
                    self.water = WaterClient(session)
                    self.water.access_token = parse_gas_credential(raw)["token"] or raw
                    self.water.refresh_token = refresh_in
                    self.gs_list = await self.water.get_bind_list(self.phone)
                    if not self.gs_list:
                        return self.async_abort(reason="water_no_bind")
                    return await self.async_step_pick_gs()
                except (GasError, WaterError) as err:
                    errors["base"] = "manual_failed"
                    last_err = str(err)
                except aiohttp.ClientError as err:
                    errors["base"] = "network"
                    last_err = str(err)
        return self.async_show_form(
            step_id="manual", data_schema=MANUAL_SCHEMA, errors=errors,
            description_placeholders={"error": last_err},
        )

    # ---------------- 水务密码登录（图形码） ----------------
    async def async_step_water_captcha(self, user_input=None) -> FlowResult:
        """已注册用户用手机号 + 密码 + 图形码登录，全程不需要短信."""
        errors = {}
        last_err = ""
        if user_input is not None:
            pwd = (user_input.get("password") or "").strip()
            kap = (user_input.get("kaptcha") or "").strip()
            if not pwd or not kap:
                errors["base"] = "need_kaptcha"
                last_err = "请填写登录密码和图形验证码"
            else:
                try:
                    await self.water.login_with_password(self.phone, pwd, kap)
                    self.gs_list = await self.water.get_bind_list(self.phone)
                    if not self.gs_list:
                        return self.async_abort(reason="water_no_bind")
                    return await self.async_step_pick_gs()
                except WaterError as err:
                    errors["base"] = "login_failed"
                    last_err = str(err)
                except aiohttp.ClientError as err:
                    errors["base"] = "network"
                    last_err = str(err)
        # 每次都换一张新图（服务端图形码一次性有效）
        try:
            cap = await self.water.get_captcha(self.phone)
            if len(cap) < 200:
                raise WaterError("图形验证码图片为空")
            path = self.hass.config.path("www", "jinan_water_kaptcha.jpg")
            await self.hass.async_add_executor_job(self._write_captcha, path, cap)
            url = "/local/jinan_water_kaptcha.jpg?t=%d" % int(time.time() * 1000)
        except (WaterError, aiohttp.ClientError, OSError) as err:
            errors.setdefault("base", "captcha_failed")
            last_err = str(err)
            url = ""
        return self.async_show_form(
            step_id="water_captcha", data_schema=WATER_CAPTCHA_SCHEMA, errors=errors,
            description_placeholders={"error": last_err, "captcha_url": url},
        )

    # ---------------- 燃气图形验证码 ----------------
    @staticmethod
    def _write_captcha(path: str, data: bytes) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as file:
            file.write(data)

    async def async_step_captcha(self, user_input=None) -> FlowResult:
        """把图形验证码图片放到 /local/ 供前端显示，由用户手动输入."""
        errors = {}
        last_err = ""
        if user_input is not None:
            kap = user_input["kaptcha"].strip()
            if not kap:
                errors["base"] = "need_kaptcha"
                last_err = "请输入图形验证码"
            else:
                try:
                    await self.gas.send_sms(self.phone, kap)
                    return await self.async_step_sms()
                except (GasError, aiohttp.ClientError) as err:
                    errors["base"] = "send_sms_failed"
                    last_err = str(err)
        # 每次进入/提交失败都重新取一张图，避免旧图失效
        try:
            # JSESSIONID 可能已过期（例如点了「重新发送」），重建带 stdCode 的会话
            if not self.gas.page_std_code:
                await self.gas.prepare_session(self.org_code)
            cap = await self.gas.get_captcha()
            if len(cap) < 500:
                raise GasError("图形验证码图片为空，请重试")
            path = self.hass.config.path("www", "jinan_kaptcha.jpg")
            await self.hass.async_add_executor_job(self._write_captcha, path, cap)
            url = "/local/jinan_kaptcha.jpg?t=%d" % int(time.time() * 1000)
        except (GasError, aiohttp.ClientError, OSError) as err:
            errors.setdefault("base", "captcha_failed")
            last_err = str(err)
            url = ""
        return self.async_show_form(
            step_id="captcha", data_schema=CAPTCHA_SCHEMA, errors=errors,
            description_placeholders={"error": last_err, "captcha_url": url},
        )

    # ---------------- 短信验证码 ----------------
    async def async_step_sms(self, user_input=None) -> FlowResult:
        errors = {}
        last_err = ""
        if user_input is not None:
            if user_input.get("resend"):
                # 水务短信登录没有图形码步骤，之前直接跳到燃气 captcha 会崩
                if self.service == SERVICE_GAS:
                    return await self.async_step_captcha()
                try:
                    await self.water.send_sms(self.phone)
                    last_err = "已重新发送短信验证码，请查收后输入"
                except (WaterError, aiohttp.ClientError) as err:
                    errors["base"] = "send_sms_failed"
                    last_err = str(err)
                return self.async_show_form(
                    step_id="sms", data_schema=SMS_SCHEMA, errors=errors,
                    description_placeholders={
                        "error": last_err, "sms_status": self._sms_status(),
                    },
                )
            sms = (user_input.get("smscode") or "").strip()
            if not sms:
                errors["base"] = "need_sms"
                last_err = "请输入短信验证码"
            else:
                try:
                    if self.service == SERVICE_GAS:
                        await self.gas.login(self.phone, sms)
                        self.subs = await self.gas.query_subs()
                        return await self.async_step_pick_user()
                    await self.water.login(self.phone, sms)
                    self.gs_list = await self.water.get_bind_list(self.phone)
                    if not self.gs_list:
                        return self.async_abort(reason="water_no_bind")
                    return await self.async_step_pick_gs()
                except (GasError, WaterError) as err:
                    errors["base"] = "login_failed"
                    last_err = str(err)
                except aiohttp.ClientError as err:
                    errors["base"] = "network"
                    last_err = str(err)
        return self.async_show_form(
            step_id="sms", data_schema=SMS_SCHEMA, errors=errors,
            description_placeholders={
                "error": last_err, "sms_status": self._sms_status(),
            },
        )

    def _sms_status(self) -> str:
        """把服务端原文与会话上下文回显到界面，便于自助定位收不到短信."""
        if self.service == SERVICE_GAS and self.gas:
            return self.gas.sms_status(self.phone or "")
        return (
            "水务短信登录：若未收到，勾选「重新发送短信」。服务端提示"
            "「当前手机号存在未失效的验证码」时，直接输入最新那条即可。"
        )
    # ---------------- 燃气选户号 ----------------
    @staticmethod
    def _sub_code(sub: dict) -> str:
        """PC 网厅与 H5 网关的户号字段名不同."""
        return str(
            sub.get("subsCode") or sub.get("userCode")
            or sub.get("UserCode") or sub.get("user_code") or ""
        )

    @staticmethod
    def _sub_label(sub: dict, code: str) -> str:
        name = (sub.get("name") or sub.get("userName")
                or sub.get("nickName") or code)
        addr = sub.get("displayAddr") or sub.get("address") or ""
        return f"{name} / {addr} ({code})" if addr else f"{name} ({code})"

    async def async_step_pick_user(self, user_input=None) -> FlowResult:
        errors = {}
        options = {}
        for sub in self.subs:
            code = self._sub_code(sub)
            if code:
                options[code] = self._sub_label(sub, code)
        if user_input is not None:
            code = user_input[CONF_USER_CODE]
            sub = next(
                (s for s in self.subs if self._sub_code(s) == code), None
            ) or {}
            await self.async_set_unique_id(f"gas_{code}")
            self._abort_if_unique_id_configured()
            data = {
                CONF_SERVICE: SERVICE_GAS,
                CONF_PHONE: self.phone or "",
                CONF_USER_CODE: code,
                CONF_USER_NAME: (sub.get("name") or sub.get("userName") or ""),
                CONF_ADDRESS: (sub.get("displayAddr") or sub.get("address") or ""),
            }
            if self.h5:
                # 微信 H5：保存轮换式 token 与业务接口所需内部 ID
                data.update({
                    CONF_LOGIN_METHOD: METHOD_WECHAT,
                    CONF_GAS_TOKEN: self.h5.token,
                    CONF_GAS_REFRESH: self.h5.refresh_token,
                    CONF_GAS_SUBS_ID: str(sub.get("subsId") or ""),
                    CONF_GAS_ORG_ID: str(sub.get("orgId") or ""),
                })
            else:
                data.update({
                    CONF_GAS_TOKEN: self.gas.token,
                    CONF_GAS_REFRESH: self.gas.refresh_token,
                })
            return self.async_create_entry(title=f"济南燃气 {code}", data=data)
        if not options:
            errors["base"] = "no_subs"
            return self.async_show_form(step_id="pick_user", errors=errors)
        return self.async_show_form(
            step_id="pick_user",
            data_schema=vol.Schema({vol.Required(CONF_USER_CODE): vol.In(options)}),
            errors=errors,
        )

    # ---------------- 水务选给水号 ----------------
    async def async_step_pick_gs(self, user_input=None) -> FlowResult:
        options = {}
        for g in self.gs_list:
            gs = str(g.get("GS") or g.get("gs") or g.get("给水号"))
            addr = g.get("Address") or g.get("address") or g.get("地址") or ""
            options[gs] = f"{addr} ({gs})" if addr else gs
        if user_input is not None:
            await self.async_set_unique_id(f"water_{user_input[CONF_GS]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"济南水务 {user_input[CONF_GS]}",
                data={
                    CONF_SERVICE: SERVICE_WATER,
                    CONF_PHONE: self.phone,
                    CONF_GS: user_input[CONF_GS],
                    CONF_WATER_TOKEN: self.water.access_token or "",
                    CONF_WATER_REFRESH: self.water.refresh_token or "",
                },
            )
        return self.async_show_form(
            step_id="pick_gs",
            data_schema=vol.Schema({vol.Required(CONF_GS): vol.In(options)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow(config_entry)


class OptionsFlow(config_entries.OptionsFlow):
    """重新登录（token 失效时）."""

    def __init__(self, config_entry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data={})
        return self.async_show_form(step_id="init")
