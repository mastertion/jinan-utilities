# 济南公用事业 · Home Assistant 集成

`jinan_utilities` 是一个 Home Assistant 自定义集成，用于在 HA 中显示**济南水务**水费账户余额 / 待交水费，以及**山东港华（济南港华）**燃气余额 / 待交燃气费。数据来自两家网上营业厅的公开查询接口，每 30 分钟自动刷新。

> **2026-11-13 更新：燃气已完整跑通**。不再依赖收不到的短信验证码。改用**微信公众号 H5 授权**：微信扫码一次 → 复制回调链接 → 粘贴进 HA，集成自动换取 token、查询余额并每 30 分钟刷新；token 每 2 小时自动续期，续期结果写回配置条目，无需重复扫码。实测某绑定户返回余额 **30.50 元**、待交 **0 元**、上次抄表日 `2026-11-13`（户号已脱敏）。

> 本项目为个人存档与自用集成，非官方产品，与济南水务、山东港华无关。接口为非公开的网页端接口，随时可能变更或失效。

## 安装

### 通过 HA 市场 / HACS（推荐）

1. HA **设置 → 市场**（2026.11 起 HACS 已并入内核并改名「市场」；旧版走 **HACS → 集成**）。
2. 右上角菜单 → **自定义仓库**（Custom repositories）。
3. 仓库地址填 `https://github.com/mastertion/jinan-utilities`，类别选 **集成 / Integration**。
4. 添加后在市场里搜索「济南公用事业」→ **下载 / 安装**。
5. **重启 Home Assistant**，然后到 **设置 → 设备与服务 → 添加集成 → 济南公用事业** 完成配置。

### 手动安装

把仓库里的 `custom_components/jinan_utilities/` 整个目录复制到 HA 的 `/config/custom_components/`，重启 HA，再添加集成即可。

## 配置方式

在添加集成时选择服务与登录方式：

| 服务 | 推荐方式 | 说明 |
| --- | --- | --- |
| 水费（济南水务） | **仅手机号直连** | 填网厅注册手机号即可查出绑定户号与余额，无需短信 / 密码 / token |
| 水费 | 手机号 + 短信验证码 | 已调通，作为备用；水务不需要图形码 |
| 水费 | 手动填入 access_token | 兜底 |
| 水费 | 密码登录 | 网厅图形验证码服务当前故障，暂不可用 |
| 燃气（山东港华 / 济南港华） | **微信扫码授权** | 已实测跑通：微信扫码 → 复制 `__ha_capture.html?code=...` 链接 → 粘贴进 HA。集成自动换 token、自动续期、查余额，完全不用短信 |
| 燃气 | 粘贴 PC 网厅链接 / 手动 token | 旧版备用路径（`tokenCode` / localStorage JSON / 裸 `access_token`） |
| 燃气 | 短信验证码 | 官网页面自己也收不到短信，实际不可用；保留代码与诊断回显供排查 |
| 水费 | 短信验证码 / 密码 / token | 备用路径 |

配置时会列出该微信 / 手机号绑定的户号，选中后创建条目。

### 燃气短信收不到时（已实测结论）

> **现已绕开**：下面记录的是排查过程。燃气取数已改为微信公众号 H5 授权路径（见上文），不再需要短信，以下内容仅作为历史排查资料保留。

**系统归属已核实**：`wt.shandongtowngas.com.cn` / `login.shandongtowngas.com.cn` 就是山东港华燃气集团（含济南港华）的官方网厅；`jinantowngas.com.cn` 旧域名目前已不解析。`queryOrgs` 返回的两个 orgCode **同属一家公司**：`10401_JNGH0105`（高新客户服务中心）与 `SDJH0105`（总公司，备注济南市），并非两家不同公司、也不是两条不同短信通道。

实测结论（2026-11）：

- 插件请求与真实浏览器逐项一致（URL / Referer / X-Requested-With / Cookie / stdCode / 图形码），两个组织、以及把正确图形码放进 URL 的变体，服务端一律回 `{"stat":1,"data":"发送成功"}`，但**均未投递到手机**；
- **用户在官网页面自己登录也收不到短信** → 已排除本集成的请求问题，属于**燃气账户登记手机号 / 短信通道**层面的问题；
- 燃气网关**真校验 token**（假 token 一律 `access token 过期`），不存在水费那种「只凭手机号直连」的捷径；
- 官网 PC 登录页只有「手机号 + 图形码 + 短信码」一种方式，密码登录的图形码服务端报 `Gdip` 异常，微信 UA 也不会自动静默授权（回调不带 code 返回 `系统错误`）。

因此排查顺序：

1. **确认登记手机号**：燃气户号绑定的可能是家人号码或旧号，与当前手机号不一致时服务端仍回「发送成功」但不发。
2. 检查手机安全软件 / 运营商是否拦截了 106 下行短信。
3. 官网**按手机号限流**，短时间重复「重新发送」会返回「发送次数过多」；集成内置 60 秒保护，被限流请等 10~30 分钟。
4. 换另一个 orgCode 重试（两个组织实测都试过，成功率不高，但成本很低）。
5. **拨打 96969**（济南能源 24 小时热线）核对 / 变更登记手机号 —— 这是让短信链路恢复的办法；但目前无需再走这条路，直接用「微信扫码授权」即可。

### 只给「燃气户号」能不能查余额？（已实测：不能）

这是最容易被问到的捷径，2026-11 已做完整对照实验，**结论是否定的**：港华网关在鉴权层就拦掉请求，户号根本没有机会被处理。

| 实验 | 结果 |
| --- | --- |
| 未登录打开官网首页 / `/pay` / `/query` / `/business/pay` | 只放行 3 个组织配置接口（`groupFuncList` / `queryOrgs` / `queryServicePhone`）；业务页一律弹「您当前尚未登录，请先登录后再进行操作」 |
| `groupFuncList` 全部 77 个功能项 | 全是登录后的 SPA 路由（我要缴费 / 户号管理 / 用气记录 …），**没有任何「免登录输户号查余额」入口** |
| `subs/querySubs`、`user/queryBindSubsLimitServer`、`oauth/user/me`、`pay/queryFee`、`user/queryBindSubs` 等，带 9 种户号参数名（`userCode` / `subCode` / `custCode` / `accountNo` …） | 无 token 或空 token 一律 `{"resultCode":"40058","resultMsg":"token不能为空"}`；假 token 一律 `{"resultCode":"20001","resultMsg":"access token 过期"}` |
| 猜测的 `subs/querySubByCode`、`subs/queryBalance`、`charge/queryBalance` 等 12 个接口 | 404，接口不存在 |
| 模仿水费那套 `Cookie: UserInfo` + `openid` 头 | 同样 `token不能为空`，燃气不认这套 |
| 微信域 `weixin.shandongtowngas.com.cn/vcc-openapi/uv1/...`、`vcc-wx/member/...` | 同一套网关，仍返回 `token不能为空`；`_APP_ID` 非微信环境为空，网页授权只在微信内生效 |

**所以「户号」在本集成里的角色只是：拿到有效 token 之后，从 `querySubs` 返回的户号列表里选中具体哪一户。** 单独一个户号换不出任何数据，也不等价于登录凭据。户号真正能用的地方是官方渠道（支付宝 / 微信「生活缴费 → 燃气费」输户号查余额、NB-IoT 表长按看余额、拨 96969 报户号核对登记手机号），这些是人工查询，HA 无法自动调用。

### 推荐：微信扫码授权（燃气已跑通，2026-11-13）

PC 网厅的短信链路既然在官网自己也收不到，本项目改走**微信公众号 H5 营业厅**的独立网关，用真实微信授权码完整跑通了取数链路。

**操作步骤**

1. 添加集成时，服务选「燃气（山东港华）」，登录方式选「**微信扫码授权**」。
2. 配置页会给出授权链接（`.../vcc-oauth/oauth/authorize2/union?clientid=jnghwechat&redirectUri=...__ha_capture.html`）。也可以用项目 `outputs/燃气微信授权_扫码.png` 的二维码，在**手机微信**里打开授权页。
3. 完成微信授权后，浏览器最终停在打不开的 `__ha_capture.html?code=xxxxx`（404 页面是正常的，它只负责把 code 留在地址栏）。
4. **立刻复制地址栏里的整条链接**，粘贴到 HA 配置框提交。code 一次性、几分钟内有效，过期就重新扫码。
5. HA 列出该微信绑定的燃气户号，选中即完成。之后自动生成「燃气账户余额」「待交燃气费」两个传感器。

也可以直接把 `{"access_token":"...","refresh_token":"..."}`（浏览器 localStorage）粘进同一个输入框。

**链路与接口（2026-11-13 实测）**

| 步骤 | 请求 | 返回 |
| --- | --- | --- |
| 换 token | `POST /vcc-oauth/oauth/authorize2/accessToekn?authCode=<code>` | `access_token` / `refresh_token` / `expires_in=7200` |
| 自动续期 | `POST /vcc-oauth/oauth/authorize2/refreshToken?refreshToken=<rt>` | **全新** token 对 |
| 查户号 | `GET /nv1/vcc-cbs/usersubs/queryBindList?orgId=&clientid=&timestamp=&sign=` | `subsId` / `subsCode` / `orgId` / `displayAddr` |
| 查余额 | `GET /nv1/vcc-cbs/charge/gasFeeBaseinfo?orgId=&subsId=&clientid=&timestamp=&sign=` | `availableBalance` / `feePayable` / `lastMeterReadingDate` |

业务接口需要 `Authorization: Bearer <access_token>`、`clientid: jnghwechat` 以及签名：

```python
sign = md5(
    "".join(k + str(v) for k, v in sorted(params.items())
            if k != "sign" and v not in (None, ""))
    + "hbasesoft.com-prod"
).hexdigest().upper()
```

**关于长期运行**：`access_token` 有效期 7200 秒；`refresh_token` 是**一次性轮换**的，刷新成功后旧的 access / refresh 会立刻失效（分别返回 `20001 access token 过期` / `90143 refreshToken已失效`）。因此集成在每次续期后都会把新 token 写回 HA 的配置条目，重启也不会丢。只有在刷新链被别的客户端抢先消费、或微信侧撤销授权时，才需要重新扫码授权一次。H5 token 与 PC 网厅 token **不通用**。

### 旧版备用：PC 网厅 tokenCode 链接 / 手动 token

运行时抓包确认了官网的真实登录态机制：

- 微信里打开网上营业厅会带 `tokenCode` 跳到 `https://wt.shandongtowngas.com.cn/home/?tokenCode=...`；
- 前端用 **GET** `/openapi/uv1/oauth/token?seq=&client_id=&code=<tokenCode>&grant_type=authorization_code&scope=read+write&redirect_uri=<官网回调>` 换出 `access_token` / `refresh_token`，存进 `localStorage['token']`；
- 后续所有接口都靠 URL 上的 `?token=<access_token>` 鉴权。

所以只需：

1. 手机微信打开「山东港华燃气集团有限公司」公众号 → **网上营业厅**；
2. 点右上角「···」→「**复制链接**」；
3. 在 HA 添加本集成时，登录方式选「**粘贴 PC 网厅链接 / 手动 token**」（旧版路径），把整条链接粘进输入框并提交。

集成会自动识别并接受四种格式：带 `tokenCode` / `code` / `token` 的完整链接（有 code 就自动调官网换 token）、单独的 `tokenCode=xxx`、`{"access_token":"...","refresh_token":"..."}`（浏览器 localStorage 的 `token`）、裸 `access_token`。换到的 token 会存入配置条目，之后 30 分钟自动刷新余额，终身不再需要短信。

> `tokenCode` 是一次性的且前端可能很快把 URL 参数清掉，所以**复制后请尽快粘贴**；若提示「tokenCode 无效或已被使用」，在微信里重新打开网上营业厅再复制一次即可。若链接里已经没有参数，改用下方 F12 方式取 `access_token`。

**找不到公众号 / 找不到「网上营业厅」菜单时**：官网 `queryServicePhone` 返回的公众号二维码指向 `http://weixin.qq.com/r/PUiMlIHEzNyzrcX39x0v`（官网首页右下角二维码是 `http://weixin.qq.com/q/zkhN_N3lIuJb4R58kWRG`），用微信扫一下即可关注「山东港华燃气集团有限公司」。底部菜单通常叫「**网上营业厅**」或「**燃气服务 → 网上营业厅**」，进去后**立刻**点右上角「···」→「复制链接」；如果只拿到 `.../home/` 没有参数，说明前端已把 `tokenCode` 清掉，退出菜单重新点一次、落地后马上复制。用 **PC 版微信**打开公众号菜单时可以直接右键「复制链接」，比手机更容易抄到完整 URL。

**其他免短信渠道**（官方公开信息）：

- 微信公众号「山东港华燃气集团有限公司」→ 网上营业厅 → 我要缴费：微信内授权登录，页面直接显示燃气余额；绑定户号支持「精准户号 / 手机验证 / 证件验证 / 地址查询」四种方式。
- 支付宝 / 微信「生活缴费 → 燃气费」：输入燃气户号即可查询与缴费。
- NB-IoT 智能燃气表：长按表具功能键，液晶屏直接显示余额。
- **电脑浏览器 F12**：登录 https://wt.shandongtowngas.com.cn/ → Network → 任意 `/openapi/uv1/...` 请求 → Query String 里复制 `token`。

> 注意：旧版本把换 token 写成了 POST，官网实际是 GET（本次已修正）。

## 生成的实体

| 实体 | 含义 | 单位 |
| --- | --- | --- |
| `sensor.ji_nan_shui_wu_<户号>_shui_fei_zhang_hu_yu_e` | 水费账户余额 | 元 |
| `sensor.ji_nan_shui_wu_<户号>_dai_jiao_shui_fei` | 待交水费 | 元 |
| 燃气余额传感器 | 燃气账户余额（`availableBalance`） | 元 |
| 燃气待交传感器 | 待交燃气费（`feePayable`） | 元 |

传感器属性只保留给水号、单价、用水性质、违约金等非敏感字段；网厅返回的身份证号、详细住址、联系电话不会写入 HA 状态。

## 隐私与安全提示

- 水费网厅的查询鉴权较弱，主要由**注册手机号**决定能查到哪些户档案。请妥善保管自己的手机号，不要把它写进公开配置或截图。
- 本集成只在你的 HA 本机与网厅之间通信，不向任何第三方上传数据；仓库内不含任何真实手机号、token、密码或户主信息。
- 若网厅调整鉴权或接口，集成可能报错或数值不可用，届时请更新版本或改用手动抓包方式。

## 版本

- `0.1.0`：水费「仅手机号直连」、短信 / 密码 / token 四种登录路径，余额与待交双传感器；燃气短信与手动 token 路径（暂停维护）。
- `0.1.1`：恢复燃气维护。对齐官网隐藏 iframe 的真实 OAuth 链（`scope=read write` + 微信域 `outhCallBack`）；组织可选而非写死第一个；图形码校验改为非阻断（实测官网在 `undefined` 变体下不校验）；补齐浏览器同款请求头；新增 60 秒限频保护、服务端原文/组织/脱敏号码诊断回显、手动 token 兜底指引。水费功能不变。
- `0.1.1+`（main）：核实系统归属与两个 orgCode 同属一家公司，修正「不同短信通道」的误导性文案；补充官网自己也收不到短信的结论、96969 与微信公众号 / 生活缴费 / 智能表等免短信渠道说明。
- `0.1.2`：新增「粘贴微信链接自动换 token」免短信路径。运行时抓包还原官网真实登录态机制（`tokenCode` → **GET** `/openapi/uv1/oauth/token` → `localStorage['token']`），修正旧代码用 POST 换 token 的错误；配置流支持粘贴链接 / tokenCode / localStorage JSON / 裸 token 四种格式；保存 `refresh_token` 到配置条目；界面与四语文案改为引导微信复制链接。水费功能不变。
- `0.1.2+`（main）：完整实测「只用户号能否查余额」并给出否定结论（网关鉴权层拦截、功能清单无免登录入口、微信域同一套网关）；补充公众号二维码与 PC 微信复制链接的操作指引。功能不变。
- `0.2.0`：**燃气完整跑通**。新增微信公众号 H5 授权路径：配置流「微信扫码授权」支持粘贴回调链接 / code / localStorage JSON，自动 `accessToekn` 换 token；新增 `GasH5Client`（Bearer + MD5 签名 + 自动续期）；验证 `usersubs/queryBindList` → `charge/gasFeeBaseinfo` 取到 `availableBalance` / `feePayable`；针对 `refresh_token` 一次性轮换，把续期后的新 token 持久化回配置条目；新增「待交燃气费」传感器，燃气属性改白名单脱敏；四语文案与界面同步更新。水费功能不变。
