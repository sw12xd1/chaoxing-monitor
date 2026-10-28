# 学习通签到 / 通知 实时监控告警器

实时盯着学习通，一旦出现 **签到活动** 或 **新通知**，立刻：

- 🔴 弹出**置顶警告窗口**（大红/大蓝卡片 + 提示音，抢你注意力）
- 📱 通过 **PushPlus 推送到你的微信**
- ✅ （可选）对「普通 / 拍照 / 位置」签到**自动完成**

---

## 一、安装

**最省事：直接双击 `启动监听.bat`。**

- **首次运行**会自动在本目录创建虚拟环境 `.venv` 并装好依赖（约十几秒，只需一次）
- **之后每次**双击即刻开始监听，无需任何手动操作
- 任何报错都会**停在窗口里等你按键**，不会一闪而过

> 前提：本机已装 Python 3.8+（安装时记得勾选 **Add Python to PATH**）。

删掉 `.venv` 目录即可让它重新自动准备一遍（换电脑 / 换 Python 版本时用得上）。

---

如果你想**手动**管理环境，也可以：

```bash
pip install -r requirements.txt
```

只有两个依赖：`requests`、`pycryptodome`。

---

## 二、配置 `config.json`

仓库里只放了**模板** `config.example.json`（所有敏感字段都是空的）。第一次用先复制一份：

```bash
copy config.example.json config.json     # Windows
cp config.example.json config.json       # macOS / Linux
```

> `config.json` 已被 `.gitignore` 排除，你的 Cookie 和 token **不会**被误提交。

### 1. 登录（推荐用 Cookie，不用给程序账号密码）

1. 浏览器打开并登录 <https://i.chaoxing.com/>
2. 按 `F12` 打开开发者工具 → `Application`（应用）→ 左侧 `Cookies` → `https://i.chaoxing.com`
3. 找到并复制这些 Cookie 的值，拼成一行填进 `config.json` 的 `cookie` 字段：

```
"_uid=xxxx; _d=xxxx; vc3=xxxx; fid=xxxx; uf=xxxx"
```

> 最省事的做法：在 `Network`（网络）面板随便点一个请求 → `Request Headers` → 找到 `Cookie:` 那一整行，**整行复制**粘进去即可（程序会自动解析）。

> Cookie 一般能撑挺久；失效后重新复制一次即可。程序检测到失效会在日志里提示。

**如果你不想手动抓 Cookie**，也可以改成填 `phone` + `password`（程序用学习通的 AES 加密自动登录，并会把拿到的 Cookie 回写到配置文件，下次就不用再登录了）。

### 2. 微信推送（PushPlus）

1. 微信扫码登录 <https://www.pushplus.plus/>
2. 在「一对一推送」页面复制你的 **用户Token**
3. 填进 `config.json` 的 `pushplus_token`

> ⚠️ **PushPlus 现在要求先完成实名认证才能发消息**（否则接口返回 `905 账户未进行实名认证`）。
> 去 <https://www.pushplus.plus/center/real-auth> 完成认证后再用。

#### 不想实名？用群机器人 webhook（同样推到微信，不用注册）

把 `config.json` 的 `webhook_url` 填成下面任意一种，程序会自动识别并推送：

- **企业微信**：建一个群 → 群设置 → 群机器人 → 添加 → 复制 Webhook 地址
- **钉钉**：群设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 Webhook
- **飞书**：群设置 → 群机器人 → 添加 → 自定义机器人 → 复制 Webhook

两个渠道可以同时填，会都发。

不想用任何推送就都留空，程序只弹窗。

### 3. 自动签到（可选）

```jsonc
"auto_sign": true,
"auto_sign_types": ["general", "photo", "location"]
```

- `general`：普通签到（全自动）
- `photo`：拍照签到（需要你提前在**超星云盘根目录**放一张 `0.jpg` 或 `0.png`）
- `location`：位置签到（需要在 `location` 里填 `纬度,经度,地址`，如 `"34.817,113.516,河南科技大学"`）
- **手势 / 签到码 / 二维码**签到需要老师当场给的码，无法自动，程序会**弹窗+推送提醒你手动签**。

> ⚠️ 自动签到可能触发平台风控。想全程只提醒、不自动操作，把 `auto_sign` 改成 `false`。

---

## 三、运行

```bash
python chaoxing_monitor.py              # 常驻监听（推荐）
python chaoxing_monitor.py --once       # 只检测一次，用来验证配置是否 OK
python chaoxing_monitor.py --test-push  # 只测推送是否通
python chaoxing_monitor.py --no-sign    # 本次只提醒、不自动签到
python chaoxing_monitor.py --no-popup   # 本次不弹窗（只推送/写日志）
python chaoxing_monitor.py --dump       # 打印接口原始返回，排查问题
```

Windows 推荐直接双击 **`启动监听.bat`**（首次会自动建环境，之后秒开）。

想让 bat 常驻时**只提醒、不自动签到**，可以在 bat 后面追加参数（命令行运行，或给快捷方式的「目标」加参数）：

```bash
启动监听.bat --no-sign
```

日志写在 `monitor.log`，已处理过的签到/通知记录在 `state.json`（可删除以重置）。

### ⚠️ 让监控真正「一直在跑」（最容易翻车的地方）

**监控不灵，九成是程序根本没在轮询。** 请注意这几点：

| 事项 | 说明 |
|---|---|
| **别关那个黑窗口** | 关掉窗口 = 停止监控，不会有任何提示 |
| **别在窗口里拖选文字** | Windows 控制台一进入"选择"状态，整个程序会被**卡死**（日志停更、签到无响应），看着就像没在监听。程序启动时已自动关闭这个模式，但仍别去点选 |
| **看心跳确认活着** | 日志里每 5 分钟会有一行 `[i] 心跳：已连续运行 N 分钟，完成 M 轮扫描（正常）`。**超过 10 分钟没心跳 = 已停止** |
| **电脑睡眠会暂停** | 合盖/休眠期间不轮询。睡眠中开出的签到如果已经结束，就补不回来了 |
| **每天开机要重开** | 不配开机自启的话，重启电脑后不会自动跑 |

**判断程序是否还活着，最快的办法：**

```bash
tasklist | findstr python
```
或直接看 `monitor.log` 最后一行时间戳——距今超过 5 分钟基本就是停了。

### 开机自启（Windows，推荐）

1. `Win + R` 输入 `shell:startup` 回车，打开「启动」文件夹
2. 把 `启动监听.bat` 的**快捷方式**放进去即可

---

## 四、工作原理（接口备忘）

| 功能 | 接口 |
|------|------|
| 登录 | `POST passport2.chaoxing.com/fanyalogin`（AES-128-CBC，key=iv=`u2oh6Vu^HWe4_AES`） |
| 课程列表 | `GET mooc1-api.chaoxing.com/mycourse/backclazzdata?rss=1` |
| 签到检测 | `GET mobilelearn.chaoxing.com/v2/apis/active/student/activelist`（`status==1` 即进行中） |
| 预签到 | `newsign/preSign` → `pptSign/analysis` → `pptSign/analysis2` |
| 提交签到 | `GET mobilelearn.chaoxing.com/pptSign/stuSignajax` |
| 通知中心 | `POST notice.chaoxing.com/pc/notice/getNoticeList`（游标翻页 `lastGetId`） |

签到类型由 `otherId` 区分：`0`=普通/拍照 `2`=二维码 `3`=手势 `4`=位置 `5`=签到码。

### 接口知识来自哪里

学习通**没有公开 API 文档**，上面的接口全部来自社区逆向成果。本项目的协议细节参考了这些开源项目，特此致谢：

| 项目 | 提供的线索 |
|---|---|
| [HEDBS/chaoxing-sign](https://github.com/HEDBS/chaoxing-sign) | `fanyalogin` 登录加密、签到主流程 `preSign → analysis → analysis2 → stuSignajax` |
| [Past-Tang/chaoxing-sign](https://github.com/Past-Tang/chaoxing-sign) | 各签到类型（普通/位置/拍照/二维码）的参数拼法 |
| [yuban10703/chaoxing_sign](https://github.com/yuban10703/chaoxing_sign) | 签到实现的补充细节 |
| [DanMo661/Xuexitong-mcp](https://github.com/DanMo661/Xuexitong-mcp) | 通知中心 `getNoticeList` 的参数与返回结构 |

> 这些项目之间也存在实现分歧（例如登录加密有 DES-ECB 与 AES-CBC 两种说法），本项目以 AES-CBC 为主、DES 兜底。
> **接口随时可能被官方改动**。失效时用 `python chaoxing_monitor.py --dump` 打印原始返回对照排查。

> 本仓库的 `chaoxing_monitor.py` 为原创实现，除接口协议知识外未复制上述项目的代码。

---

## 五、免责声明

本项目仅供**学习网络通信与接口编程**。请只监控和操作**你自己的账号**。
自动签到属模拟客户端行为，可能触发平台风控，**一切后果自负**。
请勿用于代签、刷课或任何违反学校规定与平台条款的用途。
