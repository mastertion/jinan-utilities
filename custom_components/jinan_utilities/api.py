"""山东港华燃气 + 济南水务 API 客户端."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import aiohttp

from .const import (
    GAS_API_BASE,
    GAS_CLIENT_ID,
    GAS_LOGIN_BASE,
    GAS_OAUTH_REDIRECT,
    GAS_ORGS_FALLBACK,
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


def parse_gas_credential(text: str) -> dict[str, str]:
    """把用户粘贴的内容解析成 {code, token, refresh}.

    短信收不到时的兜底入口。支持四种形态：
    1. 微信公众号「复制链接」得到的完整 URL（带 tokenCode / code / token）；
    2. 单独的 tokenCode / code（URL 片段或裸参数串）；
    3. 浏览器 localStorage['token'] 的 JSON：{"access_token": ..., "refresh_token": ...}；
    4. 裸 access_token 字符串。
    """
    raw = (text or "").strip()
    out: dict[str, str] = {"code": "", "token": "", "refresh": ""}
    if not raw:
        return out

    # 1) JSON（localStorage / F12 复制）
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
            out["token"] = str(obj.get("access_token") or obj.get("token") or "")
            out["refresh"] = str(obj.get("refresh_token") or "")
            if out["token"]:
                return out
        except ValueError:
            pass

    # 2) URL / query 片段；微信有时会把参数放在 fragment 后面
    if "://" in raw or "?" in raw or "=" in raw:
        candidate = raw if ("://" in raw or "?" in raw) else "https://x/?" + raw
        parsed = urlparse(candidate)
        qs = parse_qs(parsed.query + "&" + parsed.fragment, keep_blank_values=True)
        for key in ("token", "access_token", "accesstoken"):
            if qs.get(key) and qs[key][0]:
                out["token"] = qs[key][0]
        for key in ("tokenCode", "code"):
            if qs.get(key) and qs[key][0]:
                out["code"] = qs[key][0]
        for key in ("refresh_token", "refreshtoken"):
            if qs.get(key) and qs[key][0]:
                out["refresh"] = qs[key][0]
        if out["token"] or out["code"]:
            return out

    # 3) 裸 token
    out["token"] = raw
    return out


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
        self.refresh_token: str | None = None
        self.kaptcha: str = ""
        # 官网 SPA 用 queryOrgs 的 orgCode 作为 stdCode；实测两个组织
        # 均属「山东港华燃气有限公司」（其一为高新客户服务中心），
        # 由配置流让用户选，不再写死第一个。
        self.std_code: str = "10401_JNGH0105"
        # /login 页面实际渲染出来的 stdCode（必须非空，否则短信不会真发）
        self.page_std_code: str | None = None
        # 本次会话使用的完整 authorize URL，登录换 code 时必须复用它
        self.authorize_url: str | None = None
        self.last_sms_result: dict[str, Any] | None = None
        self._last_sms_at: float = 0.0

    def _get(self, url: str, **kw):
        """aiohttp 的 session.get() 本身是异步上下文管理器，不能再包一层 async."""
        headers = {"User-Agent": UA, **kw.pop("headers", {})}
        return self.session.get(url, headers=headers, **kw)

    def _post(self, url: str, **kw):
        headers = {"User-Agent": UA, **kw.pop("headers", {})}
        return self.session.post(url, headers=headers, **kw)

    async def query_orgs(self) -> list[dict]:
        """列出可选组织（orgCode 即 stdCode）。接口不可用时用实测兜底值."""
        try:
            async with self._get(
                f"{GAS_API_BASE}/openapi/uv1/org/queryOrgs",
                params={"seq": _seq(), "client_id": GAS_CLIENT_ID},
                headers={"Referer": GAS_WEB_REFERER},
            ) as resp:
                data = await resp.json(content_type=None)
            orgs = [
                {"orgCode": str(o["orgCode"]),
                 "orgName": str(o.get("orgName") or o["orgCode"])}
                for o in (data.get("datas") or []) if o.get("orgCode")
            ]
            if orgs:
                return orgs
        except (aiohttp.ClientError, ValueError, TypeError, KeyError) as err:
            _LOGGER.debug("gas queryOrgs failed, use fallback: %s", err)
        return [dict(o) for o in GAS_ORGS_FALLBACK]

    def _authorize_url(self, std_code: str, official: bool = True) -> str:
        """官网 iframe 的真实 authorize；official=False 是旧版直跳网厅的回退链."""
        if official:
            return (
                f"{GAS_LOGIN_BASE}/oauth/authorize?client_id={GAS_CLIENT_ID}"
                "&scope=read%20write&response_type=code"
                f"&redirect_uri={quote(GAS_OAUTH_REDIRECT, safe='')}"
                f"&stdCode={quote(std_code, safe='')}"
            )
        return (
            f"{GAS_LOGIN_BASE}/oauth/authorize?client_id={GAS_CLIENT_ID}"
            f"&redirect_uri={quote(GAS_REDIRECT_URI, safe='')}"
            "&response_type=code&scope=server"
            f"&stdCode={quote(std_code, safe='')}"
        )

    async def prepare_session(self, org_code: str | None = None) -> None:
        """按官网真实跳转链建立会话.

        关键：/oauth/authorize 必须带 &stdCode={orgCode}，服务端才会把组织编码
        写进 JSESSIONID 会话，/login 才会渲染出非空的 var stdCode。
        缺了这个参数页面是 null —— 此时短信接口会返回「发送成功」却静默不投递。
        官网实际用隐藏 iframe 走微信域回调（scope=read write），这里优先复刻，
        若该链拿不到 stdCode 再回退到旧版直跳网厅的 authorize。
        同时必须带网厅域名的 Referer，否则 WAF 返回拦截页。
        """
        headers = {"Referer": GAS_WEB_REFERER}
        if org_code:
            self.std_code = org_code
        else:
            orgs = await self.query_orgs()
            self.std_code = str(orgs[0]["orgCode"])

        last_rendered = "?"
        for official in (True, False):
            authorize = self._authorize_url(self.std_code, official)
            async with self._get(authorize, headers=headers) as resp:
                await resp.read()
                _LOGGER.debug("gas oauth/authorize(official=%s): %s",
                              official, resp.status)
            async with self._get(GAS_LOGIN_BASE + "/login", headers=headers) as resp:
                html = await resp.text()
                status = resp.status
            _LOGGER.debug("gas /login(official=%s): %s (%d bytes)",
                          official, status, len(html))
            m = re.search(r"var\s+stdCode\s*=\s*['\"]?([^;'\"]+)['\"]?\s*;", html)
            last_rendered = m.group(1).strip() if m else "?"
            self.page_std_code = (
                last_rendered
                if last_rendered not in ("?", "null", "undefined") else None
            )
            if self.page_std_code:
                self.authorize_url = authorize
                break
        _LOGGER.debug("gas stdCode: url=%s page=%s", self.std_code, last_rendered)
        if not self.page_std_code:
            raise GasError(
                "登录会话缺少组织编码 stdCode（页面渲染 %s），短信不会真正发出；"
                "请重试、换一个所属公司，或改用「手动填入 access_token」方式"
                % last_rendered
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

    def sms_status(self, phone: str) -> str:
        """给配置界面看的诊断信息；把服务端原文和会话上下文都摊开."""
        r = self.last_sms_result
        if not r:
            return "本次尚未提交短信请求。"
        masked = f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else "***"
        return (
            f"已向 {masked} 提交短信（组织 stdCode={r['std_code']}，"
            f"服务端回复：{r['data']}）。\n\n"
            "注意：实测官网返回「发送成功」并不代表短信真的投递。\n\n"
            "若 1 分钟后仍未收到：①确认这个手机号就是燃气账户登记号码"
            "（户主可能是家人号码，与登记号不一致时服务端仍回成功但不发）；"
            "②检查短信是否被手机安全软件/运营商拦截；"
            "③换一个「所属公司」重试；"
            "④官网按手机号限流，短时间重复点「重新发送」会被拒绝，请等 10~30 分钟；"
            "⑤若官网页面自己登录也收不到，说明是账户/通道问题而非本集成，"
            "请拨 96969 核对登记手机号，或改用「手动填入 access_token」完全绕开短信。"
        )

    async def send_sms(self, phone: str, kaptcha: str | None) -> None:
        """新版官网短信接口.

        wtLogin.js 的 getVcode() 用
        /ignore/sms/getVerificationCode/{phone}/undefined?stdCode={stdCode}
        第二段是图形码位；传 undefined 时服务端**不校验**图形码（实测
        cap/validateVcode 返回 false 也照样「发送成功」），传具体值才校验。
        所以这里把图形码校验改成非阻断：避免因 HA 会话 cookie 与图片不同步
        而把短信请求整个挡掉——那正是「点了发送却什么都没发生」的典型原因。
        """
        if not self.page_std_code:
            raise GasError("登录会话缺少 stdCode，请重新走一遍配置流程")
        # 本地 60 秒限频：官网按手机号计数，反复重发会把当日额度打满
        now = time.monotonic()
        if self._last_sms_at and now - self._last_sms_at < 60:
            wait = int(60 - (now - self._last_sms_at))
            raise GasError(
                f"60 秒内已提交过一次短信，请等待 {wait} 秒再试"
                "（官网按手机号限流，短时间重复发送会被拒绝）"
            )
        if kaptcha:
            try:
                ok = await self.validate_captcha(kaptcha)
                _LOGGER.debug("gas cap/validateVcode: %s (non-blocking)", ok)
            except aiohttp.ClientError as err:
                _LOGGER.debug("gas cap/validateVcode failed (ignored): %s", err)
        self.kaptcha = kaptcha or ""
        url = (
            f"{GAS_LOGIN_BASE}/ignore/sms/getVerificationCode/{phone}/undefined"
            f"?stdCode={quote(self.page_std_code, safe='')}"
        )
        # 与真实浏览器抓包一致的请求头
        headers = {
            "Referer": GAS_LOGIN_BASE + "/login",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/plain, */*; q=0.01",
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
        data = str(obj.get("data") or text[:120])
        self.last_sms_result = {
            "std_code": self.page_std_code,
            "stat": str(obj.get("stat")),
            "data": data,
        }
        if str(obj.get("stat")) != "1":
            if "次数过多" in data:
                raise GasError(
                    f"官网短信限流：{data}。该手机号短时间内发送次数已达上限，"
                    "请等待 10~30 分钟再试，或改用「手动填入 access_token」"
                )
            raise GasError(data)
        self._last_sms_at = time.monotonic()
        _LOGGER.info("gas 短信已提交发送（stdCode=%s, 服务端=%s）",
                     self.page_std_code, data)

    async def _validate_sms(self, phone: str, sms: str) -> bool:
        url = f"{GAS_LOGIN_BASE}/ignore/sms/validateVcode"
        data = {"tel": phone, "vcode": sms}
        async with self._post(url, data=data) as resp:
            text = (await resp.text()).strip()
        _LOGGER.debug("gas validateVcode: %s", text[:200])
        return text.lower() == "true"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        """用微信回调带来的 tokenCode / OAuth code 换 access_token.

        官网真实请求是 **GET**（旧代码用 POST 是错的，Playwright 抓包确认）：
        /openapi/uv1/oauth/token?seq=&client_id=&code=<tokenCode>
          &grant_type=authorization_code&scope=read+write&redirect_uri=<官网回调>
        成功后返回体带 access_token / refresh_token；成功时前端把它存进
        localStorage['token']。code 是一次性的，失效时官网直接返回空响应。
        """
        url = (
            f"{GAS_API_BASE}/openapi/uv1/oauth/token?seq={quote(_seq(), safe='')}"
            f"&client_id={quote(GAS_CLIENT_ID, safe='')}"
            f"&code={quote(code, safe='')}"
            "&grant_type=authorization_code&scope=read+write"
            f"&redirect_uri={quote(GAS_OAUTH_REDIRECT, safe=':')}"
        )
        _LOGGER.debug("gas exchange_code (len=%d)", len(code))
        async with self._get(url, headers={"Referer": GAS_WEB_REFERER}) as resp:
            text = await resp.text()
        _LOGGER.debug("gas exchange_code: %s", text[:300])
        if not text.strip():
            raise GasError(
                "tokenCode 无效或已被使用（官网返回空响应）。请在微信里重新打开"
                "网上营业厅，并立刻点右上角「···」→「复制链接」后粘贴进来。"
            )
        try:
            data = json.loads(text)
        except ValueError as err:
            raise GasError(f"换取 token 返回异常: {text[:120]}") from err
        inner = data.get("datas") if isinstance(data.get("datas"), dict) else {}
        token = (
            inner.get("access_token") or data.get("access_token")
            or inner.get("token") or data.get("token") or ""
        )
        refresh = (
            inner.get("refresh_token") or data.get("refresh_token") or ""
        )
        if not token:
            err = str(data.get("error") or "")
            desc = str(data.get("error_description") or "")
            if err == "invalid_grant" or "Invalid authorization code" in desc:
                raise GasError(
                    "tokenCode 已失效或已被使用（官网返回 invalid_grant）。"
                    "请在微信里重新打开「网上营业厅」，立刻点右上角「···」→「复制链接」，"
                    "把新链接粘贴进来；这个 code 只能用一次且有效期很短。"
                )
            raise GasError(
                f"换取 token 失败: {json.dumps(data, ensure_ascii=False)[:200]}"
            )
        self.token = str(token)
        self.refresh_token = str(refresh) or None
        return {"token": str(token), "refresh": str(refresh), "raw": data}

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
        # OAuth 授权换 code：必须复用建立会话时的完整 authorize URL，
        # 官网 iframe 登录后就是重新请求同一个 URL 拿 302 + code。
        authorize = self.authorize_url or self._authorize_url(self.std_code)
        async with self._get(authorize, allow_redirects=False) as resp:
            loc = resp.headers.get("Location", "")
            if resp.status != 302 or "code=" not in loc:
                raise GasError(f"OAuth 授权失败: HTTP {resp.status} {loc[:80]}")
        m = re.search(r"[?&]code=([^&]+)", loc)
        code = m.group(1)
        # 换 token：官网是 GET + query（见 exchange_code），不能用 POST
        result = await self.exchange_code(code)
        return {
            "token": result["token"],
            "refresh": result["refresh"],
            "raw": result["raw"],
        }

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
