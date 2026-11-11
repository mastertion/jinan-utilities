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
CONF_USER_CODE = "user_code"       # 燃气户号
CONF_GS = "gs"                     # 水务给水号
CONF_GAS_TOKEN = "gas_token"
CONF_WATER_TOKEN = "water_token"
CONF_WATER_REFRESH = "water_refresh"
CONF_USER_NAME = "user_name"
CONF_ADDRESS = "address"

SCAN_INTERVAL_MIN = 30
