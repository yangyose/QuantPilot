# WxPusher 微信推送配置指南

> 用途：让 QuantPilot 的交易信号 / 风险告警**推到微信**，而不只是留在站内信里。
> 适用：生产实例（腾讯 43.134.63.13）。本地开发不配也能跑，通知会自动只走站内信。
> 前置阅读：`deployment.md`（生产栈操作规范）、`CLAUDE.md §6` 运维红线 ②（env 双写）

---

## 0. 先搞清楚你现在是什么状态

**2026-09-02 实测：生产从未配置过 WxPusher。** `.env.prod` 里两个键存在但**值为空**，
自 2026-05-18 起 3196 条通知全部只走了站内信，微信推送成功数为 **0**。

所以本文不是"排障"，是**第一次配置**。

判断当前状态（任选其一，**不要靠印象**）：

```bash
# 方式 A：问 API（需先登录拿 token）
curl -s https://quant.portableagi.com/api/v1/notifications/wx-status \
  -H "Authorization: Bearer <你的token>"
# → {"code":0,"data":{"wx_configured":false,"uid_masked":null},"msg":"ok"}

# 方式 B：直接看容器里的环境变量（不打印值）
ssh qp-tencent 'docker exec quantpilot-backend-1 sh -c \
  "for k in WXPUSHER_APP_TOKEN WXPUSHER_UID; do \
     [ -z \"\$(printenv \$k)\" ] && echo \"\$k=<空>\" || echo \"\$k=<已设置>\"; done"'
```

---

## 0.5 先读这节：这个通道的天花板在哪（2026-09-02 实测结论）

**别指望它实时。** WxPusher 走的是微信**订阅号**——微信设计上就把订阅号折叠进一个文件夹、
不给系统级通知。你只会看到一个红点，不会被弹窗提醒。装它的官方客户端（仅 Android/iOS）
才有实时推送。

**但对现有的日次信号，不实时几乎零成本。** 生产近 30 天实测：

| 类型 | 30 天 | 日均 |
|---|---|---|
| `SIGNAL_BUY` | 1103 | **36.8** |
| `RISK_WARN` | 22 | 0.7 |
| `FACTOR_ALERT` / `SIGNAL_SELL` | 4 / 4 | 0.1 |
| `STOP_LOSS_WARN` | 1 | 0.03 |

按小时分布：**17 点 550 条 + 18 点 554 条 = 97%**（每日管线 17:30 跑完），15 点 26 条、20 点 4 条。

即**所有通知都发生在收盘后，距次日 09:30 可行动还有 15.5 小时**——早知道晚知道不改变任何操作。
唯一要求是「次日开盘前你看到了」，订阅号红点足够。

**因此两条结论**：

1. **不要装客户端**。真开了实时推送，你每天 18 点前后会被震 ~37 下（97% 是买入候选清单，
   不是提醒）。这种量级两天就学会无视，然后真要紧的那条也一起被无视。
2. **关掉 `SIGNAL_BUY` 的微信推送**，只留 SELL / STOP_LOSS_WARN / RISK_WARN / FACTOR_ALERT
   —— 合计 **< 1 条/天**。到那个量级，「不实时」更加无所谓。

**它相对直接开网页的唯一价值**：微信你本来一天开几十次，红点自己撞到眼里，省掉「记得去看」
这一步。看完的功夫是一样的。**你若本来就每天开界面，这个通道对你没有意义**，
直接关 `wx_enabled` 即可，站内信照常写、一条不丢。

⚠️ **换 Server酱 / PushPlus 没有意义**——它们与 WxPusher 是**同一种架构**（都靠公众号中转），
天花板完全一样。真要实时弹窗，唯一可行的是**企业微信自建应用 + 微信插件**
（官方免费、不需装企业微信客户端），已登记 roadmap §6 **V1.5-D ⑧ D-PUSH**，
且它只在**日内止损（⑦ D-INTRA）**落地时才有必要——日次信号不需要它。

---

## 1. WxPusher 是什么，为什么要用它

微信官方不允许个人开发者直接给自己发消息。WxPusher 是一个第三方中转：
你在它那儿建一个"应用"，用**微信扫码关注它的公众号**完成订阅，之后你的程序把消息
POST 给 WxPusher，它替你从公众号推给你的微信。

- 官网：`https://wxpusher.zjiecode.com`
- 本项目调用的接口：`POST https://wxpusher.zjiecode.com/api/send/message`
- 你需要拿到**两个值**：`APP_TOKEN`（标识你的应用）+ `UID`（标识收消息的人，也就是你）

> ⚠️ 这是第三方服务，消息内容会经过它的服务器。本项目推的是股票代码、信号类型、
> 盈亏百分比这类内容——自己判断能否接受。不能接受就别配，站内信一样能用。

---

## 2. 申请步骤

> 网站 UI 会改版，下面按**流程**描述，按钮文案以站上实际为准。

### 2.1 拿 APP_TOKEN

1. 打开 `https://wxpusher.zjiecode.com/admin/`，用**微信扫码登录**。
2. 「应用管理」→ 新建应用。表单逐项怎么填见下。
3. 建完后在应用详情里能看到 **APP_TOKEN**，形如 `AT_xxxxxxxxxxxxxxxxxxxx`。
   **这就是 `WXPUSHER_APP_TOKEN`。**

#### 表单逐项

字段名与官方说明来自 `github.com/wxpusher/wxpusher-docs`；「填什么、为什么」一栏
是从本项目代码推出来的，与页面改版无关。

| 字段 | 官方说明 | 填什么 | 理由 |
|---|---|---|---|
| **应用名称** | — | `QuantPilot` | ⚠️ **会出现在你微信收到的每条消息里**。填 `test` / `app1` 之类，以后通知栏里认不出是什么 |
| **说明** | 应用描述 | 「个人量化交易信号提醒」 | 展示给订阅者。⚠️ 别写持仓、账户、真实姓名 |
| **联系方式** | 用户反馈问题的联系途径 | `邮件：yourname@example.com`（**必须带前缀**）| 见下 |
| **回调地址** | 用户关注时回调通知 | **留空** | 本项目**只做单向推送**（代码只调 `POST /api/send/message`，从不接收回调）。填了等于凭空暴露一个公网端点，只有风险没有收益 |
| **设置 URL** | 用户在微信端打开订阅时跳转地址 | **留空** | 同上，本项目无对应页面 |
| **关注提示** | 用户关注时的提示文案 | 留空或一句话 | 只有你自己会关注 |

**联系方式怎么填**：

- 它是**一个自由文本框，没有类型下拉**，但**有内容校验**：只填一个裸邮箱地址会被驳回，
  报错「请描述清楚如何联系你，邮件/qq/微信？」（2026-09-02 实测）。
- 🔴 **长度上限 20 字符**（2026-09-02 实测：`yangyose2@hotmail.com` 21 字，最后一个 `m`
  装不下）。这与上一条**直接打架**——带「邮件：」前缀只剩 **17 格**给地址，
  多数邮箱塞不进去。**所以先挑地址，再谈格式。**
- 可行组合（总长 ≤20）：`邮件：ab@qq.com`(12) / `QQ：123456789`(12)。
- **填邮箱，别填 QQ / 微信**。差别不在"隐私程度"，而在泄露后能干什么：QQ 号与微信号是
  **可直达的身份句柄**（能被直接加、能反查社交资料）；邮箱只能收信，且可用别名地址。
  用一个**非主用**邮箱。
- ⚠️ **两处文档与实现不符**（2026-09-02 实测）：官方文档写此字段「可以不填写」，
  线上是**必填**；且文档只说「告诉用户如何联系你」，未提**存在格式校验**。
  以实际表单为准。

**取名一次到位**：应用名会出现在每条消息里，第一次就填对。

#### 这个应用的配置影响什么、不影响什么

本项目实际发出的请求只有这几个字段（`backend/src/quantpilot/notification/wxpusher.py`）：

```python
payload = {
    "appToken": self._app_token,   # ← 表单产出的 APP_TOKEN
    "content":  body,              # ← 代码生成，与表单无关
    "summary":  title[:20],        # ← 代码生成；微信通知栏预览只有 20 字是这里截的
    "contentType": 1,
    "uids": [self._uid],           # ← 订阅后拿到的 UID
}
```

所以表单里**唯一真正影响推送行为的产出是 APP_TOKEN**；应用名称/简介影响的是
「你在微信里看到它长什么样」；回调地址与主题设置本项目完全不用。

### 2.2 拿 UID

UID 不是你自己填的，是**订阅之后由 WxPusher 分配**的：

1. 在刚建的应用里找到它的**关注二维码**（通常在应用详情页，或「应用二维码 / 关注页面」）。
2. 用**要收消息的那个微信号**扫码，关注公众号并完成订阅。
3. 回到后台「用户管理」，能看到刚订阅的这个用户，对应一串 **UID**，形如 `UID_xxxxxxxxxxxx`。
4. **这就是 `WXPUSHER_UID`。**

> 常见卡点：扫了码但用户列表里没人 → 多半是扫的是**登录二维码**而不是**应用的关注二维码**，
> 两者不同。UID 必须来自"订阅了这个应用"的动作。

---

## 3. 填进生产

⚠️ **这是生产写操作，按 C-1 需要单独确认后再执行。** 下面是完整步骤，不要跳步。

### 3.1 只需要改 `.env.prod`

好消息：`docker-compose.prod.yml` 的 `environment:` 白名单里**已经有这两个键**
（仓库与服务器两侧都有，2026-09-02 核实一致），所以 §6 运维红线 ② 那个"双写漏一半"
的坑这次不存在，**只需要填值**。

```bash
ssh qp-tencent
cd /home/ubuntu/QuantPilot

# 先备份（改任何 .env 之前都先备份）
cp .env.prod .env.prod.bak.$(date +%Y%m%d_%H%M%S)

# 编辑，把两行空值填上
vi .env.prod
#   WXPUSHER_APP_TOKEN=AT_xxxxxxxxxxxxxxxx
#   WXPUSHER_UID=UID_xxxxxxxxxxxx
```

> 值里若含 `$`，用单引号包起来（同 `ADMIN_PASSWORD_HASH` 的处理，见 `CLAUDE.md §4.7`）。

### 3.2 重建容器让它读到新值

环境变量在**容器启动时**注入，改了 `.env.prod` 必须重建，`restart` 不够：

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d backend
```

### 3.3 验证（三步，缺一不可）

```bash
# ① 容器真的拿到值了吗（只看有无）
docker exec quantpilot-backend-1 sh -c \
  'for k in WXPUSHER_APP_TOKEN WXPUSHER_UID; do \
     [ -z "$(printenv $k)" ] && echo "$k=<空>" || echo "$k=<已设置>"; done'

# ② 应用层认不认（wx_configured 必须变 true）
curl -s https://quant.portableagi.com/api/v1/notifications/wx-status \
  -H "Authorization: Bearer <token>"

# ③ 真的收到微信了吗 —— 这一步不能省
```

第 ③ 步是唯一有意义的判据。①② 只证明"配置读到了"，证明不了"消息发得出去"
（token 打错一个字符，①② 照样全过）。等下一次真实通知（信号 / 告警）到达时，
确认微信收到了，才算配成功。

想立刻验而不等信号，就查刚才那条通知在库里的落痕：

```sql
-- wx_pushed = true 才是真发出去了
SELECT created_at, notify_type, wx_pushed, wx_error
FROM in_app_notification ORDER BY id DESC LIMIT 5;
```

---

## 4. 配好之后的行为

| 项 | 值 | 出处 |
|---|---|---|
| 推送时段 | **15:00 ~ 22:00**（Asia/Shanghai）| `NotificationConfig.push_start_hour/end_hour` |
| 时段外 | 只写站内信，不推微信 | `_in_push_window` |
| 去重 | 同类型 + 同 payload，24 小时内只发一次 | `DEDUP_WINDOW` |
| 失败重试 | 3 次，间隔 30s，单次超时 10s | `wxpusher.py` `MAX_ATTEMPTS` 等 |
| 6 类事件开关 | 买入 / 卖出 / 市场状态 / 止损预警 / 风险 / 因子告警，可分别关 | `NotificationConfig` |

**站内信永远都写**，微信推送只是额外通道——即使微信全挂，通知也不会丢。

---

## 5. 不想要微信推送怎么办

把偏好里的 `wx_enabled` 关掉即可，比留着两个空变量干净：

- 界面：通知设置里关闭微信推送
- 效果：`notify()` 直接跳过微信分支，只写站内信

`.env.prod` 里那两个键**留空是安全的**——自 2026-09-02 起，未配置状态不再产生
伪 ERROR 日志、也不再往 `wx_error` 写"重试 3 次均失败"这种假话
（见 `notification_service.py` 的 `configured` 判定与 `CLAUDE.md §4.11` 第 5 例）。

---

## 6. 历史遗留

**3196 行历史通知的 `wx_error` 里存着一句假话**——"WxPusher 重试 3 次均失败"，
而它们其实从未尝试发送（渠道未配置）。代码已于 2026-09-02 修正，但**历史行未订正**：

- 影响：只影响事后统计"发送失败率"，不影响任何交易决策
- 若要清理：`UPDATE in_app_notification SET wx_error = NULL WHERE wx_error LIKE 'WxPusher 重试%' AND wx_pushed = false;`
  —— 这是生产写操作，按 C-1 需单独确认，执行前先 `pg_dump --data-only -t in_app_notification` 留回滚点
- 不清理也可以，但**别拿这批数据算成功率**
