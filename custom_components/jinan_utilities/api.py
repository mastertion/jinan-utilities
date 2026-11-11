"""山东港华燃气 + 济南水务 API 客户端."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import (
    GAS_API_BASE,
    GAS_CLIENT_ID,
    GAS_LOGIN_BASE,
    GAS_REDIRECT_URI,
    WATER_BASE,
    WATER_COOKIE,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_LOGGER = logging.getLogger(__name__)
_ocr = None


def ocr_image(data: bytes) -> str:
    """用 ddddocr 识别图形验证码（可选依赖，musl 容器里不可用）."""
    global _ocr
    if _ocr is None:
        try:
            import ddddocr  # noqa: PLC0415
        except ImportError as err:
            raise GasError("本环境无 ddddocr，无法自动识别图形验证码") from err
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr.classification(data)


def _seq() -> str:
    """燃气网关要求的 seq 参数：随机+日期+序号，保证唯一即可."""
    t = time.localtime()
    head = "".join(str(random.randint(0, 9)) for _ in range(5))
    return (
        head
        + time.strftime("%Y%m%d%H%M%S", t)
        + str(random.randint(1000000, 9999999)).zfill(7)
    )


class GasError(Exception):
    """燃气流程错误."""


class WaterError(Exception):
    """水务流程错误."""


GAS_WEB_REFERER = "https://wt.shandongtowngas.com.cn/"


class GasClient:
    """山东港华燃气网上营业厅客户端."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.token: str | None = None
        self.kaptcha: str = ""
        # 官网 SPA 用 queryOrgs 返回的第一个 orgCode 作为 stdCode
        self.std_code: str = "10401_JNGH0105"
        # /login 页面实际渲染出来的 stdCode（必须非空，否则短信不会真发）
        self.page_std_code: str | None = None

    def _get(self, url: str, **kw):
        """aiohttp 的 session.get() 本身是异步上下文管理器，不能再包一层 async."""
        headers = {"User-Agent": UA, **kw.pop("headers", {})}
        return self.session.get(url, headers=headers, **kw)

    def _post(self, url: str, **kw):
        headers = {"User-Agent": UA, **kw.pop("headers", {})}
        return self.session.post(url, headers=headers, **kw)

    async def prepare_session(self) -> None:
        """按官网真实跳转链建立会话.

        关键：/oauth/authorize 必须带 &stdCode={orgCode}，服务端才会把组织编码
        写进 JSESSIONID 会话，/login 才会渲染 var stdCode = '10401_JNGH0105'。
        缺了这个参数页面是 var stdCode = null —— 此时短信接口会返回
        {"stat":1,"data":"发送成功"} 但静默不投递（服务端按会话里的
        组织编码选短信通道，会话里是 null 就没有通道）。
        同时必须带网厅域名的 Referer，否则 WAF 返回 403 拦截页。
        """
        headers = {"Referer": GAS_WEB_REFERER}
        # 1) 先取组织编码（不同子公司短信通道不同，不能只写死）
        try:
            async with self._get(
                f"{GAS_API_BASE}/openapi/uv1/org/queryOrgs",
                params={"seq": _seq(), "client_id": GAS_CLIENT_ID},
                headers=headers,
            ) as resp:
                orgs = (await resp.json(content_type=None)).get("datas") or []
            if orgs and orgs[0].get("orgCode"):
                self.std_code = str(orgs[0]["orgCode"])
        except (aiohttp.ClientError, ValueError, TypeError) as err:
            _LOGGER.debug("gas queryOrgs failed, keep default: %s", err)

        # 2) 带 stdCode 建立 OAuth 会话
        authorize = (
            f"{GAS_LOGIN_BASE}/oauth/authorize?client_id={GAS_CLIENT_ID}"
            f"&redirect_uri={quote(GAS_REDIRECT_URI, safe='')}"
            "&response_type=code&scope=server"
            f"&stdCode={quote(self.std_code, safe='')}"
        )
        async with self._get(authorize, headers=headers) as resp:
            await resp.read()
            _LOGGER.debug("gas oauth/authorize: %s", resp.status)
        async with self._get(GAS_LOGIN_BASE + "/login", headers=headers) as resp:
            html = await resp.text()
            status = resp.status
        _LOGGER.debug("gas /login: %s (%d bytes)", status, len(html))

        # 3) 校验会话里确实注入了组织编码
        m = re.search(r"var\s+stdCode\s*=\s*['\"]?([^;'\"]+)['\"]?\s*;", html)
        rendered = m.group(1).strip() if m else "?"
        self.page_std_code = (
            rendered if rendered not in ("?", "null", "undefined") else None
        )
        _LOGGER.debug("gas stdCode: url=%s page=%s", self.std_code, rendered)
        if not self.page_std_code:
            raise GasError(
                "登录会话缺少组织编码 stdCode（页面渲染 %s），短信不会真正发出；"
                "请重试，或改用「手动填入 access_token」方式" % rendered
            )

    async def get_captcha(self) -> bytes:
        """取新版登录页的图形码；响应会种下 kaptchaCode cookie（5 分钟有效）."""
        async with self._get(
            GAS_LOGIN_BASE + "/kaptcha-image?time=%d" % int(time.time() * 1000),
            headers={"Referer": GAS_LOGIN_BASE + "/login"},
        ) as resp:
            data = await resp.read()
        _LOGGER.debug("gas kaptcha image: %d bytes", len(data))
        return data

    async def validate_captcha(self, kaptcha: str) -> bool:
        """新版官网：图形码先经 /ignore/cap/validateVcode 单独校验."""
        async with self._post(
            GAS_LOGIN_BASE + "/ignore/cap/validateVcode",
            data={"vcode": kaptcha},
            headers={
                "Referer": GAS_LOGIN_BASE + "/login",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
        ) as resp:
            text = (await resp.text()).strip()
        _LOGGER.debug("gas cap/validateVcode: %s", text[:80])
        return text.lower() == "true"

    async def send_sms(self, phone: str, kaptcha: str | None) -> None:
        """新版官网短信接口.

        wtLogin.js 里 getVcode() 用的是
        /ignore/sms/getVerificationCode/{phone}/undefined?stdCode={stdCode}
        图形码不放在路径里，而是先调用 cap/validateVcode 校验。
        旧版接口（路径里带图形码）已废弃：会返回「发送成功」但不发短信。
        """
        if not self.page_std_code:
            raise GasError("登录会话缺少 stdCode，请重新走一遍配置流程")
        if kaptcha and not await self.validate_captcha(kaptcha):
            raise GasError("图形验证码不正确，请按刷新后的新图片重新输入")
        self.kaptcha = kaptcha or ""
        # 不再尝试「不带 stdCode」的变体：那个变体会返回假成功且消耗发送次数
        url = (
            f"{GAS_LOGIN_BASE}/ignore/sms/getVerificationCode/{phone}/undefined"
            f"?stdCode={quote(self.page_std_code, safe='')}"
        )
        headers = {
            "Referer": GAS_LOGIN_BASE + "/login",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }
        _LOGGER.debug("gas send_sms %s****%s stdCode=%s",
                      phone[:3], phone[-4:], self.page_std_code)
        async with self._get(url, headers=headers) as resp:
            text = await resp.text()
        _LOGGER.debug("gas send_sms: %s", text[:400])
        try:
            obj = json.loads(text)
        except ValueError as err:
            raise GasError(f"短信接口返回异常: {text[:120]}") from err
        if str(obj.get("stat")) != "1":
            raise GasError(str(obj.get("data") or text[:120]))
        _LOGGER.info("gas 短信已提交发送（会话 stdCode=%s）", self.page_std_code)

    async def _validate_sms(self, phone: str, sms: str) -> bool:
        url = f"{GAS_LOGIN_BASE}/ignore/sms/validateVcode"
        data = {"tel": phone, "vcode": sms}
        async with self._post(url, data=data) as resp:
            text = (await resp.text()).strip()
        _LOGGER.debug("gas validateVcode: %s", text[:200])
        return text.lower() == "true"

    async def login(self, phone: str, sms: str) -> dict[str, Any]:
        """完整登录：校验短信→SSO 登录→换 token."""
        if not await self._validate_sms(phone, sms):
            raise GasError("短信验证码不正确")
        # 新版登录页表单：POST /loginByPhone（username/picCode/smscode）
        async with self._post(
            f"{GAS_LOGIN_BASE}/loginByPhone",
            data={"username": phone, "picCode": self.kaptcha, "smscode": sms},
            headers={"Referer": GAS_LOGIN_BASE + "/login"},
            allow_redirects=False,
        ) as resp:
            status = resp.status
            await resp.read()
        _LOGGER.debug("gas loginByPhone: HTTP %s", status)
        if status >= 400:
            # 兼容旧版登录表单
            async with self._post(
                f"{GAS_LOGIN_BASE}/login",
                data={"tel": phone, "vcode": sms},
                headers={"Referer": GAS_LOGIN_BASE + "/login"},
                allow_redirects=False,
            ) as resp:
                if resp.status >= 400:
                    raise GasError(f"登录失败 HTTP {resp.status}")
        # OAuth 授权换 code
        async with self._get(
            f"{GAS_LOGIN_BASE}/oauth/authorize",
            params={
                "client_id": GAS_CLIENT_ID,
                "response_type": "code",
                "scope": "server",
                "redirect_uri": GAS_REDIRECT_URI,
            },
            allow_redirects=False,
        ) as resp:
            loc = resp.headers.get("Location", "")
            if resp.status != 302 or "code=" not in loc:
                raise GasError(f"OAuth 授权失败: HTTP {resp.status} {loc[:80]}")
        m = re.search(r"[?&]code=([^&]+)", loc)
        code = m.group(1)
        # 换 token
        async with self._post(
            f"{GAS_API_BASE}/openapi/uv1/oauth/token",
            json={
                "code": code,
                "grant_type": "authorization_code",
                "scope": "server",
                "redirect_uri": GAS_REDIRECT_URI,
            },
        ) as resp:
            data = await resp.json(content_type=None)
        token = (
            data.get("datas", {}).get("access_token")
            or data.get("access_token")
            or data.get("datas", {}).get("token")
        )
        if not token:
            raise GasError(f"token 换取失败: {json.dumps(data, ensure_ascii=False)[:200]}")
        self.token = token
        return {"token": token, "raw": data}

    async def _api(self, path: str, token: str | None = None, **params) -> Any:
        token = token or self.token
        params.setdefault("seq", _seq())
        params["token"] = token or ""
        url = f"{GAS_API_BASE}/openapi/uv1/{path}"
        async with self._get(url, params=params) as resp:
            return await resp.json(content_type=None)

    async def query_subs(self, token: str | None = None) -> list[dict]:
        """查询名下户号列表."""
        data = await self._api("subs/querySubs", token, noBuildingLimit="Y")
        if isinstance(data, dict):
            return data.get("datas") or []
        return data or []

    async def query_user_detail(self, token: str | None = None) -> Any:
        return await self._api("oauth/user/me", token)

    async def query_pay_info(self, token: str | None, org_code: str, user_code: str) -> Any:
        """查询缴费页数据（含账户余额）."""
        url = f"{GAS_API_BASE}/business/pay/price/{org_code}/{user_code}"
        # 页面数据接口未知时，先返回占位
        return await self._api("user/queryBindSubsLimitServer", token, isPayOrReport="Y")


class WaterClient:
    """济南水务网上营业厅客户端.

    实测（2026-11-12，用真实注册手机号抓包校准）：
    - SPA 入口 http://wt.jnwater.com.cn/shoufeizjj3/WT/index.html
    - 网关真正校验的是 Cookie UserInfo + 请求头 openid(=注册手机号)。
      accesstoken / refreshtoken 实测可去掉或填乱码，网关一律不校验。
    - 缺 UserInfo Cookie 或手机号未注册时，返回
      「请求中未找到任何可用的用户信息」。
    - GetQFXXByGS 返回的是**裸对象**（没有 State/data 包装）：
      yue=账户余额, qfje=待交水费, xzje, wyj=违约金, dj=单价, djfl=用水性质。
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.captcha_token: str = ""

    @staticmethod
    def _headers(phone: str | None = None, at: str | None = None, rt: str | None = None) -> dict:
        return {
            "User-Agent": UA,
            "Content-Type": "application/json;charset=utf-8",
            "accesstoken": at or "",
            "refreshtoken": rt or "",
            "openid": phone or "",
            "Accept": "application/json, text/plain, */*",
            "Origin": WATER_BASE,
            "Referer": WATER_BASE + "/shoufeizjj3/WT/index.html",
            # 网关靠这个 Cookie 判定为 Web 客户端，缺了直接拒绝
            "Cookie": WATER_COOKIE,
        }

    @staticmethod
    def _check(data: Any) -> dict:
        """统一校验网厅响应；失败时透传服务端原文."""
        if not isinstance(data, dict):
            raise WaterError(f"接口返回异常: {str(data)[:120]}")
        state = data.get("State", data.get("state", False))
        if isinstance(state, str):
            state = state.strip().lower() in ("true", "1", "ok")
        if not state:
            msg = (data.get("Message") or data.get("MessageText")
                   or data.get("messageText") or data.get("message") or "请求失败")
            raise WaterError(str(msg))
        return data

    @staticmethod
    def _payload(data: dict) -> Any:
        """业务数据可能在 Data/data/EntityList，也可能直接挂在顶层."""
        return (data.get("Data") or data.get("data")
                or data.get("EntityList") or data.get("entityList") or {})

    async def _capture_tokens(self, resp: aiohttp.ClientResponse) -> None:
        at = (resp.headers.get("access_token") or resp.headers.get("accesstoken")
              or resp.headers.get("Access_Token"))
        rt = resp.headers.get("refreshtoken") or resp.headers.get("refresh_token")
        if at:
            self.access_token = at
        if rt:
            self.refresh_token = rt

    async def _post(self, path: str, phone: str, payload: Any = None) -> dict:
        url = WATER_BASE + "/shoufeizjj3/api/" + path
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False)
        async with self.session.post(
            url,
            headers=self._headers(phone, self.access_token, self.refresh_token),
            data=body,
        ) as resp:
            await self._capture_tokens(resp)
            try:
                data = await resp.json(content_type=None)
            except (ValueError, aiohttp.ClientError) as err:
                raise WaterError(f"接口返回异常: {err}") from err
        return self._check(data)

    def _extract_tokens(self, data: dict) -> None:
        """登录响应：token 可能在 Data、顶层或响应头（响应头已在 _post 抓取）."""
        sources = [data]
        p = self._payload(data)
        if isinstance(p, dict):
            sources.append(p)
        for src in sources:
            at = (src.get("accesstoken") or src.get("access_token")
                  or src.get("AccessToken"))
            rt = (src.get("refreshtoken") or src.get("refresh_token")
                  or src.get("RefreshToken"))
            if at and not self.access_token:
                self.access_token = at
            if rt and not self.refresh_token:
                self.refresh_token = rt

    async def get_captcha(self, phone: str) -> bytes:
        """密码登录图形码：ImgCode 返回 {img, token}，token 要回传给登录接口."""
        url = WATER_BASE + "/shoufeizjj3/api/HomeApi/ImgCode"
        async with self.session.post(
            url, headers=self._headers(phone), data="{}"
        ) as resp:
            raw = await resp.read()
            ctype = resp.headers.get("Content-Type", "")
        if "json" in ctype or raw[:1] == b"{":
            try:
                obj = json.loads(raw)
            except ValueError as err:
                raise WaterError(f"图形验证码响应异常: {err}") from err
            try:
                self._check(obj)
            except WaterError as err:
                # 服务端 Linux 缺 libgdiplus，ImgCode 直接 500
                if "Gdip" in str(err) or "type initializer" in str(err):
                    raise WaterError(
                        "济南水务网厅图形验证码服务当前不可用（服务端 GDI+ 初始化失败）；"
                        "请改用「仅手机号直连」或「手机号 + 短信验证码」"
                    ) from err
                raise
            d = self._payload(obj)
            if not isinstance(d, dict):
                d = obj
            img = (d.get("img") or d.get("Img") or d.get("picPath")
                   or d.get("PicPath") or d.get("image") or "")
            self.captcha_token = str(d.get("token") or d.get("Token") or "")
            if not img:
                raise WaterError("图形验证码接口未返回图片，请改用「手动填入 access_token」")
            if img.startswith("data:"):
                import base64  # noqa: PLC0415
                return base64.b64decode(img.split(",", 1)[-1])
            if img.startswith("http"):
                async with self.session.get(img) as r:
                    return await r.read()
            if img.startswith("/"):
                async with self.session.get(WATER_BASE + img) as r:
                    return await r.read()
            import base64  # noqa: PLC0415
            try:
                return base64.b64decode(img)
            except Exception as err:
                raise WaterError("图形验证码格式无法识别") from err
        return raw

    async def send_sms(self, phone: str) -> None:
        await self._post(f"WxProgramApi/SendCheckCode?PhoneNum={phone}", phone)

    async def login(self, phone: str, sms: str) -> dict[str, Any]:
        """短信登录：FSOU="PC" + FAPPID="1" + checkCode.

        FSOU 取自登录页 loginForm.mode（实测为字符串 "PC"，不是数字 1）。
        2026-11-12 实测：SendCheckCode 返回「验证码发送成功！」，
        错码时 CommonCheckSign 返回「验证码错误」（而非网关拒绝），链路已通。
        """
        data = await self._post(
            "WxProgramApi/CommonCheckSign", phone,
            {"FMOBILE": phone, "FSOU": "PC", "FAPPID": "1", "checkCode": sms},
        )
        self._extract_tokens(data)
        return data

    async def login_with_password(self, phone: str, password: str, kaptcha: str) -> dict:
        """密码登录（已注册用户，无需短信）：FSOU=2 + FPASSWORD + 图形码.

        FREMARK 是 ImgCode 返回的 token，服务端用它校验图形码。
        """
        data = await self._post(
            "WxProgramApi/CommonCheckSign", phone,
            {
                "FMOBILE": phone,
                "FPASSWORD": password,
                "FSOU": 2,
                "FAPPID": kaptcha,
                "FUNIONID": "",
                "FREMARK": self.captcha_token,
            },
        )
        self._extract_tokens(data)
        return data

    async def get_bind_list(self, phone: str) -> list[dict]:
        data = await self._post(
            f"WxProgramApi/GetBangDingListByWebWT?PhoneNum={phone}", phone
        )
        d = self._payload(data)
        if isinstance(d, list):
            return d
        if isinstance(d, dict):
            return (d.get("list") or d.get("List") or d.get("entityList")
                    or d.get("EntityList") or [])
        return []

    async def get_qfxx(self, phone: str, gs: str) -> dict:
        """查询给水号余额，兼容裸对象与标准 {state,data} 两种包装.

        实测 GetQFXXByGS 直接返回用户信息对象本身（顶层就是 yue/qfje），
        没有 State 字段，所以不能走 _check()，否则会被误判为请求失败。
        """
        url = WATER_BASE + f"/shoufeizjj3/api/WxProgramApi/GetQFXXByGS?GS={gs}"
        async with self.session.post(
            url,
            headers=self._headers(phone, self.access_token, self.refresh_token),
            data="{}",
        ) as resp:
            await self._capture_tokens(resp)
            try:
                raw = await resp.json(content_type=None)
            except (ValueError, aiohttp.ClientError) as err:
                raise WaterError(f"余额接口返回异常: {err}") from err
        if not isinstance(raw, dict):
            raise WaterError(f"余额接口返回异常: {str(raw)[:120]}")
        state = raw.get("State", raw.get("state"))
        if state is not None and (state is False or str(state).lower() in ("false", "0")):
            raise WaterError(str(raw.get("Message") or raw.get("messageText")
                                or "查询余额失败"))
        d = raw.get("Data") or raw.get("data")
        if isinstance(d, dict) and d:
            return {**raw, **d}
        if not any(k in raw for k in ("yue", "YUE", "qfje", "QFJE")):
            raise WaterError("余额接口未返回金额字段，请确认手机号是该户号的注册手机号")
        return raw


async def run_sync(fn, *args):
    """在线程池里跑同步阻塞调用."""
    return await asyncio.to_thread(fn, *args)
