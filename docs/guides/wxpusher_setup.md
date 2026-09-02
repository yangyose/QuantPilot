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

1. 打开 `https://wxpusher.zjiecode.com`，进入管理后台，用**微信扫码登录**。
2. 找到「应用管理」→ 新建一个应用。
   - 应用名随便填，例如 `QuantPilot`。
   - 建完后会给你一串 **APP_TOKEN**，形如 `AT_xxxxxxxxxxxxxxxxxxxxxxxxxxxx`。
3. 把它抄下来。**这就是 `WXPUSHER_APP_TOKEN`。**

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
