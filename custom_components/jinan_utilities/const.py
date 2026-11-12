"""济南公用事业（山东港华燃气 + 济南水务）常量."""
DOMAIN = "jinan_utilities"

CONF_SERVICE = "service"
SERVICE_GAS = "gas"
SERVICE_WATER = "water"

# 燃气（山东港华 SSO）
GAS_LOGIN_BASE = "https://login.shandongtowngas.com.cn"
GAS_API_BASE = "https://wt.shandongtowngas.com.cn"
GAS_CLIENT_ID = "db196d62f7d211e8a9b2fa163e955d28"
GAS_REDIRECT_URI = "https://wt.shandongtowngas.com.cn/home/"
# 官网首页用隐藏 iframe 发起 OAuth 的真实回调链（Playwright 抓包原样）。
# 与直接跳 /home/ 的区别：scope=read write、回调先落到微信域 vcc-openapi，
# 再由 outhCallBack 二次跳回网厅 loginRedirect。会话上下文不同会影响 SSO。
GAS_OAUTH_REDIRECT = (
    "https://weixin.shandongtowngas.com.cn/vcc-openapi/uv1/sys/outhCallBack"
    "?redirect_url=https%3A%2F%2Fwt.shandongtowngas.com.cn%2FloginRedirect"
    "%3FredirectUrl%3Dhttps%253A%252F%252Fwt.shandongtowngas.com.cn%252Fhome%252F"
)
# queryOrgs 实测返回两个组织；运行时仍动态拉取，这里仅作接口故障时的兜底。
GAS_ORGS_FALLBACK = [
    {"orgCode": "10401_JNGH0105", "orgName": "山东港华燃气（高新客户服务中心）"},
    {"orgCode": "SDJH0105", "orgName": "山东港华燃气有限公司"},
]

# 燃气：微信公众号 H5 营业厅（2026-11-13 用真实微信授权码实测跑通）
# 这条链完全绕开官网 PC 网厅的短信登录，是目前唯一稳定的取数路径。
GAS_H5_BASE = "https://weixin.shandongtowngas.com.cn"
GAS_H5_API = GAS_H5_BASE + "/nv1/vcc-cbs/"
GAS_H5_OAUTH = GAS_H5_BASE + "/vcc-oauth/oauth/authorize2/"
GAS_H5_CLIENT_ID = "jnghwechat"
GAS_H5_REFERER = (
    GAS_H5_BASE + "/h5-gas/pages/gasAirlines/jn-index/index"
    "?c=1&clientid=jnghwechat&sessionid="
)
GAS_H5_ORG_ID = "8ad88b8582f48df601835f6235810065"  # queryBindList 的组织兜底值
GAS_H5_SIGN_KEY = "hbasesoft.com-prod"
GAS_H5_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.40(0x18002824) NetType/WIFI Language/zh_CN"
)
# 用微信扫这个地址授权后，会跳到 __ha_capture.html?code=xxxxx；
# 用户复制整条链接粘贴进 HA 配置流，集成自己把 code 换成 token。
GAS_H5_AUTHORIZE_URL = (
    GAS_H5_BASE + "/vcc-oauth/oauth/authorize2/union?clientid=jnghwechat"
    "&redirectUri=https%3A%2F%2Fweixin.shandongtowngas.com.cn%2Fh5-gas"
    "%2F__ha_capture.html"
)

# 水务（济南水务）
WATER_BASE = "http://wt.jnwater.com.cn"
# 网关必需：实测缺了它所有 /api/ 都返回「请求中未找到任何可用的用户信息」。
# 反倒是 accesstoken / refreshtoken 网关不校验（去掉或填乱码都能查到余额）。
WATER_COOKIE = "UserInfo={%22UserName%22:%22%22%2C%22ApplicationId%22:%22%22}"

CONF_PHONE = "phone"
CONF_SMS_CODE = "smscode"
# 登录方式：仅手机号 / 短信 / 密码 / 手动填入浏览器 token
CONF_LOGIN_METHOD = "login_method"
METHOD_PHONE = "phone_only"    # 仅济南水务：只需注册手机号，无需短信/密码/token
METHOD_SMS = "sms"
METHOD_PASSWORD = "password"   # 仅济南水务：账号密码 + 图形码，无需短信
METHOD_TOKEN = "token"
METHOD_WECHAT = "wechat"           # 燃气微信公众号 H5 授权（无需短信，推荐）
CONF_USER_CODE = "user_code"       # 燃气户号
CONF_ORG_CODE = "org_code"         # 燃气所属组织 stdCode（决定短信通道）
CONF_GS = "gs"                     # 水务给水号
CONF_WECHAT_LINK = "wechat_link"   # 配置流里粘贴的微信回调链接
CONF_GAS_TOKEN = "gas_token"
CONF_GAS_REFRESH = "gas_refresh"
CONF_GAS_SUBS_ID = "gas_subs_id"   # H5 业务接口用的户号内部 ID
CONF_GAS_ORG_ID = "gas_org_id"     # H5 业务接口用的组织内部 ID
CONF_WATER_TOKEN = "water_token"
CONF_WATER_REFRESH = "water_refresh"
CONF_USER_NAME = "user_name"
CONF_ADDRESS = "address"

SCAN_INTERVAL_MIN = 30
