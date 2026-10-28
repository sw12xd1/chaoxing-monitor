# -*- coding: utf-8 -*-
"""
学习通（超星）签到 / 通知  实时监控告警器
====================================================================
功能
----
1. 实时轮询学习通，检测【进行中的签到活动】和【通知中心】的新消息
2. 命中后弹出一个置顶的红色/蓝色警告窗口（带提示音），抢你的注意力
3. 同时通过 PushPlus 把消息推送到你的微信
4. 可选：对「可自动化」的签到类型自动完成签到（普通 / 拍照 / 位置）

登录方式
--------
默认使用「手动 Cookie」登录（在 config.json 里填 cookie 字段）。
也支持填 手机号 + 密码 自动登录（需要 pycryptodome）。

依赖
----
    pip install requests pycryptodome

用法
----
    python chaoxing_monitor.py              # 常驻监听（默认）
    python chaoxing_monitor.py --once       # 只检测一次就退出（用来验证配置）
    python chaoxing_monitor.py --test-push  # 只测 PushPlus 微信推送通不通
    python chaoxing_monitor.py --dump       # 打印接口原始返回，排查问题用
    python chaoxing_monitor.py --no-sign    # 临时关闭自动签到，只提醒

免责声明
--------
本程序仅用于学习网络协议 / 接口编程。请只监控与操作你自己的账号。
自动签到可能触发平台风控，一切后果自负。
"""

import argparse
import base64
import json
import os
import queue
import random
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime

# ----------------------------------------------------------------------------
# 常量 / 接口地址
# ----------------------------------------------------------------------------
try:
    import requests
except ImportError:
    sys.exit("缺少依赖 requests，请先执行:  pip install requests pycryptodome")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
STATE_FILE = os.path.join(APP_DIR, "state.json")
LOG_FILE = os.path.join(APP_DIR, "monitor.log")

URL_LOGIN = "https://passport2.chaoxing.com/fanyalogin"
URL_COURSES = "https://mooc1-api.chaoxing.com/mycourse/backclazzdata"
URL_ACTIVELIST = "https://mobilelearn.chaoxing.com/v2/apis/active/student/activelist"
URL_PRESIGN = "https://mobilelearn.chaoxing.com/newsign/preSign"
URL_ANALYSIS = "https://mobilelearn.chaoxing.com/pptSign/analysis"
URL_ANALYSIS2 = "https://mobilelearn.chaoxing.com/pptSign/analysis2"
URL_SIGNAJAX = "https://mobilelearn.chaoxing.com/pptSign/stuSignajax"
URL_CHECKSIGNCODE = ("https://mobilelearn.chaoxing.com/widget/sign/"
                     "pcStuSignController/checkSignCode")
URL_ACTIVEINFO = "https://mobilelearn.chaoxing.com/v2/apis/active/getPPTActiveInfo"
URL_NOTICELIST = "https://notice.chaoxing.com/pc/notice/getNoticeList"
URL_PAN_INDEX = "https://pan-yz.chaoxing.com"
URL_PAN_LIST = "https://pan-yz.chaoxing.com/opt/listres"
URL_PUSHPLUS = "https://www.pushplus.plus/send"

AES_KEY = b"u2oh6Vu^HWe4_AES"          # 学习通登录参数加密密钥（key == iv）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# otherId -> 签到类型说明
SIGN_TYPES = {
    0: "普通/拍照签到",
    2: "二维码签到",
    3: "手势签到",
    4: "位置签到",
    5: "签到码签到",
}

DEFAULT_CONFIG = {
    "cookie": "",
    "phone": "",
    "password": "",
    "pushplus_token": "",
    "webhook_url": "",
    "monitor_sign": True,
    "monitor_notice": True,
    "auto_sign": True,
    "auto_sign_types": ["general", "photo", "location"],
    "location": "",
    "sign_interval": 30,
    "notice_interval": 180,
    "heartbeat_interval": 300,
    "sign_workers": 4,
    "popup": True,
    "popup_seconds": 30,
    "sound": True,
    "course_filter": [],
    "push_on_first_run": False,
}


# ----------------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------------
_log_lock = threading.Lock()


def disable_quickedit() -> None:
    """关掉控制台的「快速编辑」模式。

    Windows 控制台一旦被鼠标点一下进入"选择文字"状态，所有输出会被挂起，
    整个 Python 进程跟着冻结——症状就是日志不再更新、签到完全没反应，
    看起来像"程序没在监听"，其实是被控制台卡住了。
    """
    try:
        k32 = ctypes.windll.kernel32
        k32.GetStdHandle.restype = ctypes.c_void_p
        k32.GetStdHandle.argtypes = [ctypes.c_uint]
        k32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        k32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint]

        handle = k32.GetStdHandle(-10)          # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if handle and k32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_EXTENDED_FLAGS = 0x0080
            ENABLE_QUICK_EDIT_MODE = 0x0040
            new_mode = (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE
            k32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    with _log_lock:
        try:
            print(line, flush=True)
        except Exception:
            pass
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 配置 / 状态
# ----------------------------------------------------------------------------
def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log(f"[!] config.json 解析失败，改用默认配置: {e}")
    else:
        save_config(cfg)
        log(f"[i] 未找到 config.json，已生成默认配置: {CONFIG_FILE}")
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_state() -> dict:
    st = {"signed": [], "seen_notices": [], "first_notice_done": False}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                st.update(json.load(f))
        except Exception:
            pass
    return st


def save_state(st: dict) -> None:
    # 只保留最近的记录，避免文件无限膨胀
    st["signed"] = list(dict.fromkeys(st.get("signed", [])))[-500:]
    st["seen_notices"] = list(dict.fromkeys(st.get("seen_notices", [])))[-800:]
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        log(f"[!] 保存 state.json 失败: {e}")


# ----------------------------------------------------------------------------
# 登录
# ----------------------------------------------------------------------------
def aes_encrypt(text: str) -> str:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_KEY)
    return base64.b64encode(cipher.encrypt(pad(text.encode("utf-8"), 16))).decode()


def _des_encrypt_hex(text: str) -> str:
    """备用加密方案（部分版本用 DES-ECB，取密钥前 8 字节）。"""
    from Crypto.Cipher import DES
    from Crypto.Util.Padding import pad
    cipher = DES.new(AES_KEY[:8], DES.MODE_ECB)
    return cipher.encrypt(pad(text.encode("utf-8"), 8)).hex()


def parse_cookie_string(cookie: str) -> dict:
    jar = {}
    for part in re.split(r"[;\n]", cookie or ""):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        jar[k.strip()] = v.strip()
    return jar


def build_session(cfg: dict) -> requests.Session:
    """构建带登录态的 Session：优先用 cookie，其次用账号密码登录。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    cookie = (cfg.get("cookie") or "").strip()
    if cookie:
        jar = parse_cookie_string(cookie)
        for k, v in jar.items():
            s.cookies.set(k, v)
        log(f"[i] 使用手工 Cookie 登录（{len(jar)} 项）")
        return s

    phone = (cfg.get("phone") or "").strip()
    password = cfg.get("password") or ""
    if not phone or not password:
        sys.exit("[x] 未配置登录信息：请在 config.json 里填写 cookie，或填写 phone + password")

    data = {
        "fid": "-1",
        "uname": aes_encrypt(phone),
        "password": aes_encrypt(password),
        "refer": "https%3A%2F%2Fi.chaoxing.com",
        "t": "true",
        "forbidotherlogin": "0",
        "validate": "",
        "doubleFactorLogin": "0",
        "independentId": "0",
    }
    r = s.post(URL_LOGIN, data=data, timeout=20)
    try:
        body = r.json()
    except Exception:
        sys.exit(f"[x] 登录响应异常: {r.text[:200]}")

    if body.get("status") is not True:
        # 尝试备用加密方案
        data["uname"] = phone
        data["password"] = _des_encrypt_hex(password)
        r = s.post(URL_LOGIN, data=data, timeout=20)
        try:
            body = r.json()
        except Exception:
            body = {}

    if body.get("status") is not True:
        sys.exit(f"[x] 登录失败: {body.get('msg2') or body.get('msg') or body}")

    log(f"[i] 账号登录成功: {body.get('name') or phone}")
    try:
        cfg["cookie"] = "; ".join(f"{k}={v}" for k, v in
                                  {c.name: c.value for c in s.cookies}.items())
        save_config(cfg)
        log("[i] 已把登录 Cookie 回写到 config.json，下次无需再登录")
    except Exception:
        pass
    return s


def get_identity(s: requests.Session) -> tuple:
    uid = s.cookies.get("_uid") or s.cookies.get("UID") or ""
    fid = s.cookies.get("fid") or "-1"
    return str(uid), str(fid)


# ----------------------------------------------------------------------------
# 弹窗（独立 UI 线程 + 队列，线程安全）
# ----------------------------------------------------------------------------
class PopupManager:
    COLORS = {"sign": "#c62828", "notice": "#1565c0",
              "ok": "#2e7d32", "error": "#8e24aa", "info": "#37474f"}

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.q = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="popup-ui")
        self._thread.start()

    def notify(self, title: str, text: str, kind: str = "info",
               seconds: int = None) -> None:
        if not self.cfg.get("popup", True):
            return
        self.q.put((title, text, kind, seconds))

    # ---------------- 以下运行在 UI 线程 ----------------
    def _run(self):
        """优先用 tkinter 做漂亮的自定义弹窗；不可用时退回 Windows 原生弹窗。"""
        if self._start_tk():
            return
        self._run_win32()

    def _start_tk(self) -> bool:
        try:
            import tkinter as tk
        except Exception:
            log("[i] 未检测到 tkinter，改用 Windows 原生弹窗")
            return False
        try:
            self.tk = tk
            self.root = tk.Tk()
        except Exception as e:
            log(f"[i] tkinter 初始化失败（{e}），改用 Windows 原生弹窗")
            return False
        self.mode = "tk"
        try:
            self.root.withdraw()
            self.root.after(100, self._pump)
            self.root.mainloop()
        except Exception as e:
            log(f"[!] 弹窗线程异常: {e}")
        return True

    def _run_win32(self):
        """零依赖兜底方案：用 Windows 原生 MessageBox（始终置顶、带系统提示音）。"""
        self.mode = "win32"
        try:
            import ctypes
        except Exception:
            log("[!] 连 ctypes 都不可用，弹窗功能关闭（消息仍会打印/推送）")
            return
        MB_OK = 0x0
        MB_ICONWARNING = 0x30
        MB_ICONINFORMATION = 0x40
        MB_TOPMOST = 0x00040000
        MB_SETFOREGROUND = 0x00010000
        MB_SYSTEMMODAL = 0x00001000
        flags = MB_OK | MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL
        # MessageBoxTimeoutW 是未公开但全平台可用的接口，支持自动关闭
        has_timeout = hasattr(ctypes.windll.user32, "MessageBoxTimeoutW")
        log("[i] 弹窗模式：Windows 原生"
            + (f"（{self.cfg.get('popup_seconds', 30)} 秒后自动关闭）"
               if has_timeout else "（点「确定」关闭）"))
        while True:
            title, text, kind, sec = self.q.get()
            icon = MB_ICONWARNING if kind in ("sign", "error") else MB_ICONINFORMATION
            if self.cfg.get("sound", True):
                try:
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
            ms = int((sec or self.cfg.get("popup_seconds", 30)) or 30) * 1000
            try:
                if has_timeout:
                    ctypes.windll.user32.MessageBoxTimeoutW(
                        0, str(text), str(title), flags | icon, 0, ms)
                else:
                    ctypes.windll.user32.MessageBoxW(
                        0, str(text), str(title), flags | icon)
            except Exception as e:
                log(f"[!] 原生弹窗失败: {e}")

    def _pump(self):
        try:
            while True:
                title, text, kind, sec = self.q.get_nowait()
                try:
                    self._show(title, text, kind, sec)
                except Exception as e:
                    log(f"[!] 弹窗显示失败: {e}")
        except queue.Empty:
            pass
        try:
            self.root.after(120, self._pump)
        except Exception:
            pass

    def _show(self, title, text, kind, sec):
        tk = self.tk
        bg = self.COLORS.get(kind, self.COLORS["info"])
        seconds = sec or int(self.cfg.get("popup_seconds", 30))

        win = tk.Toplevel(self.root)
        win.title("学习通提醒")
        win.configure(bg=bg)
        win.attributes("-topmost", True)
        try:
            win.attributes("-toolwindow", False)
        except Exception:
            pass

        w, h = 560, 340
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x, y = (sw - w) // 2, int(sh * 0.22)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.protocol("WM_DELETE_WINDOW", lambda: self._close(win))

        tk.Label(win, text=title, bg=bg, fg="white",
                 font=("Microsoft YaHei", 20, "bold")).pack(pady=(22, 6))
        tk.Frame(win, bg="white", height=2, width=w - 80).pack(pady=6)

        body = tk.Label(win, text=text, bg=bg, fg="white", justify="left",
                        wraplength=w - 70, font=("Microsoft YaHei", 13))
        body.pack(padx=26, pady=8, fill="both", expand=True)

        tk.Button(win, text="我知道了", font=("Microsoft YaHei", 12, "bold"),
                  bg="white", fg=bg, relief="flat", padx=24, pady=6,
                  command=lambda: self._close(win)).pack(pady=(4, 18))

        win.after(seconds * 1000, lambda: self._close(win))

        # 抢焦点 + 提示音
        try:
            win.lift()
            win.focus_force()
        except Exception:
            pass
        if self.cfg.get("sound", True):
            threading.Thread(target=self._beep, args=(seconds, win),
                             daemon=True).start()

    @staticmethod
    def _beep(seconds, win):
        try:
            import winsound
        except Exception:
            return
        end = time.time() + min(seconds, 12)
        while time.time() < end:
            try:
                if not win.winfo_exists():
                    return
            except Exception:
                return
            try:
                winsound.Beep(1200, 250)
                winsound.Beep(900, 250)
            except Exception:
                return
            time.sleep(0.5)

    @staticmethod
    def _close(win):
        try:
            win.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# 微信推送（PushPlus）
# ----------------------------------------------------------------------------
def pushplus_send(token: str, title: str, content: str) -> bool:
    if not token:
        return False
    try:
        r = requests.post(URL_PUSHPLUS, timeout=15, json={
            "token": token, "title": title,
            "content": content, "template": "html"}, )
        j = r.json()
        if j.get("code") == 200:
            return True
        log(f"[!] PushPlus 推送失败: {j}")
    except Exception as e:
        log(f"[!] PushPlus 推送异常: {e}")
    return False


def webhook_send(url: str, title: str, content: str) -> bool:
    """备用推送渠道：企业微信 / 钉钉 / 飞书 群机器人 webhook（无需注册，按域名自动适配）。"""
    if not url:
        return False
    plain = re.sub(r"<br\s*/?>", "\n", str(content))
    plain = re.sub(r"<[^>]+>", "", plain).strip()
    try:
        if "qyapi.weixin.qq.com" in url:
            body = {"msgtype": "markdown",
                    "markdown": {"content": f"**{title}**\n{plain}"}}
        elif "dingtalk.com" in url:
            body = {"msgtype": "markdown",
                    "markdown": {"title": title, "text": f"### {title}\n\n{plain}"}}
        elif "feishu" in url or "lark" in url:
            body = {"msg_type": "text", "content": {"text": f"{title}\n{plain}"}}
        else:
            body = {"msgtype": "text", "text": {"content": f"{title}\n{plain}"}}
        r = requests.post(url, json=body, timeout=15)
        j = r.json() if r.text.strip().startswith("{") else {}
        if j.get("errcode") in (0,) or j.get("code") in (0, 200) or \
                j.get("StatusCode") == 0:
            return True
        log(f"[!] webhook 推送返回异常: {j or r.text[:120]}")
    except Exception as e:
        log(f"[!] webhook 推送异常: {e}")
    return False


# ----------------------------------------------------------------------------
# 学习通客户端
# ----------------------------------------------------------------------------
class Chaoxing:
    def __init__(self, session: requests.Session, cfg: dict):
        self.s = session
        self.cfg = cfg
        self.uid, self.fid = get_identity(session)
        self.name = ""
        self._courses = None

    # ---------- 课程 ----------
    def courses(self, refresh=False):
        if self._courses is not None and not refresh:
            return self._courses
        r = self.s.get(URL_COURSES, params={"rss": "1", "view": "json"}, timeout=20)
        data = r.json()
        out, seen = [], set()
        for ch in data.get("channelList", []):
            content = ch.get("content") or {}
            class_id = content.get("id")
            if not class_id:
                continue
            for c in (content.get("course") or {}).get("data", []) or []:
                key = (c.get("id"), class_id)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "courseId": str(c.get("id")),
                    "classId": str(class_id),
                    "courseName": c.get("name", ""),
                    "className": content.get("name", ""),
                })
        flt = self.cfg.get("course_filter") or []
        if flt:
            out = [c for c in out
                   if any(k in (c["courseName"] + c["className"]) for k in flt)]
        self._courses = out
        return out

    # ---------- 签到检测 ----------
    def check_activity(self, course_id: str, class_id: str):
        url = (f"{URL_ACTIVELIST}?fid=0&courseId={course_id}&classId={class_id}"
               f"&_={int(time.time() * 1000)}")
        try:
            j = self.s.get(url, timeout=15).json()
        except Exception:
            return None
        d = j.get("data") if isinstance(j.get("data"), dict) else j
        lst = (d or {}).get("activeList") or []
        for a in lst:
            other_id = int(a.get("otherId", -1))
            if a.get("status") != 1 or not (0 <= other_id <= 5):
                continue
            start = a.get("startTime", 0) or 0
            if start and time.time() - start / 1000 > 7200:   # 开始超 2 小时忽略
                continue
            return {
                "activeId": str(a["id"]),
                "name": a.get("nameOne") or "签到",
                "otherId": other_id,
                "courseId": course_id,
                "classId": class_id,
            }
        return None

    def pre_sign(self, act: dict) -> None:
        try:
            self.s.get(URL_PRESIGN, params={
                "courseId": act["courseId"], "classId": act["classId"],
                "activePrimaryId": act["activeId"], "general": 1, "sys": 1,
                "ls": 1, "appType": 15, "tid": "", "uid": self.uid, "ut": "s",
            }, timeout=15)
            r = self.s.get(URL_ANALYSIS, params={
                "vs": 1, "DB_STRATEGY": "RANDOM", "aid": act["activeId"]}, timeout=15)
            m = re.search(r"code='\+'([^']*)", r.text)
            if m:
                self.s.get(URL_ANALYSIS2, params={
                    "DB_STRATEGY": "RANDOM", "code": m.group(1)}, timeout=15)
            time.sleep(0.4)
        except Exception as e:
            log(f"[!] 预签到请求异常: {e}")

    def is_photo_sign(self, active_id: str) -> bool:
        try:
            j = self.s.get(URL_ACTIVEINFO, params={"activeId": active_id},
                           timeout=15).json()
            return ((j.get("data") or {}).get("ifphoto") == 1)
        except Exception:
            return False

    def get_object_id(self):
        try:
            html = self.s.get(URL_PAN_INDEX, timeout=15).text
        except Exception:
            return None
        m_enc = re.search(r'enc ="(.*?)"', html) or re.search(r'enc="(.*?)"', html)
        m_root = re.search(r'_rootdir = "(.*?)"', html) or \
            re.search(r'rootdir = "(.*?)"', html)
        if not (m_enc and m_root):
            return None
        params = (f"puid=0&shareid=0&parentId={m_root.group(1)}"
                  f"&page=1&size=50&enc={m_enc.group(1)}")
        try:
            j = self.s.post(URL_PAN_LIST + "?" + params, data=params, timeout=15).json()
            for f in j.get("list", []):
                if str(f.get("name", "")).lower() in ("0.jpg", "0.png"):
                    return f.get("objectId") or f.get("id")
        except Exception:
            pass
        return None

    # ---------- 各类签到提交 ----------
    def _sign_general(self, act):
        return self.s.get(URL_SIGNAJAX, params={
            "activeId": act["activeId"], "uid": self.uid, "clientip": "",
            "latitude": -1, "longitude": -1, "appType": 15,
            "fid": self.fid, "name": self.name}, timeout=15).text

    def _sign_with_code(self, act, code):
        try:
            j = self.s.get(URL_CHECKSIGNCODE, params={
                "activeId": act["activeId"], "signCode": code},
                timeout=15).json()
        except Exception as e:
            return f"校验接口异常: {e}"
        if j.get("result") != 1:
            return f"码校验失败: {j.get('errorMsg') or j}"
        time.sleep(0.2)
        return self.s.get(URL_SIGNAJAX, params={
            "activeId": act["activeId"], "uid": self.uid, "clientip": "",
            "latitude": "", "longitude": "", "appType": 15,
            "fid": self.fid, "name": self.name, "signCode": code},
            timeout=15).text

    def _sign_location(self, act, lat, lon, address):
        return self.s.get(URL_SIGNAJAX, params={
            "name": self.name, "address": address, "activeId": act["activeId"],
            "uid": self.uid, "clientip": "", "latitude": lat, "longitude": lon,
            "fid": self.fid, "appType": 15, "ifTiJiao": 1}, timeout=15).text

    def _sign_photo(self, act, object_id):
        return self.s.get(URL_SIGNAJAX, params={
            "activeId": act["activeId"], "uid": self.uid, "clientip": "",
            "useragent": "", "latitude": -1, "longitude": -1, "appType": 15,
            "fid": self.fid, "objectId": object_id, "name": self.name},
            timeout=15).text

    def try_auto_sign(self, act: dict) -> tuple:
        """尝试自动签到。返回 (是否已自动处理, 结果描述)。"""
        types = [t.lower() for t in (self.cfg.get("auto_sign_types") or [])]
        oid = act["otherId"]
        self.pre_sign(act)

        if oid == 0:
            if self.is_photo_sign(act["activeId"]):
                if "photo" not in types:
                    return False, "拍照签到未开启自动"
                oid_file = self.get_object_id()
                if not oid_file:
                    return False, "拍照签到：云盘根目录没有 0.jpg/0.png，需手动"
                msg = self._sign_photo(act, oid_file)
            else:
                if "general" not in types:
                    return False, "普通签到未开启自动"
                msg = self._sign_general(act)
        elif oid == 4:
            if "location" not in types:
                return False, "位置签到未开启自动"
            loc = (self.cfg.get("location") or "").strip()
            if not loc:
                return False, "位置签到未配置 location（纬度,经度,地址），需手动"
            parts = loc.split(",", 2)
            if len(parts) < 3:
                return False, "location 格式应为 '纬度,经度,地址'"
            msg = self._sign_location(act, parts[0], parts[1], parts[2])
        else:
            return False, f"{SIGN_TYPES.get(oid, '该类型')}需要验证码，无法自动，请手动"

        ok = "success" in str(msg)
        return ok, ("签到成功" if ok else f"签到返回: {str(msg)[:120]}")

    # ---------- 通知 ----------
    def fetch_notices(self, limit=20) -> list:
        payload = {
            "type": 0, "notice_type": 0, "lastValue": "", "sort": 0,
            "folderUUID": "", "kw": "", "startTime": "", "endTime": "",
            "gKw": "", "gName": "", "year": "", "tag": "", "fidsCode": "",
            "filterSenderPuids": "", "filterTags": "",
        }
        out, last_id = [], ""
        for _ in range(3):
            payload["lastValue"] = last_id
            r = self.s.post(URL_NOTICELIST, data=payload, timeout=20)
            j = r.json()
            if not j.get("status"):
                raise RuntimeError(f"通知接口失败: {j.get('msg') or j}")
            d = j.get("notices") or {}
            items = d.get("list") or []
            out.extend(items)
            last_id = d.get("lastGetId") or ""
            if not items or d.get("lastPage") or len(out) >= limit:
                break
        return out[:limit]


# ----------------------------------------------------------------------------
# 监控主逻辑
# ----------------------------------------------------------------------------
class Monitor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = load_state()
        self.popup = PopupManager(cfg)
        self.push_token = cfg.get("pushplus_token") or ""
        self.webhook = cfg.get("webhook_url") or ""
        self.session = build_session(cfg)
        self.cx = Chaoxing(self.session, cfg)
        self._init_user()

    def _init_user(self):
        # 取真实姓名（自动签到的 name 参数需要）
        for url, pat in (
            ("https://passport2.chaoxing.com/mooc/accountManage",
             r'class="fr colorBlue"[^>]*>\s*([^<\s]+)\s*</p>'),
            ("https://i.chaoxing.com/base", r'aria-label="账号[：:]\s*([^"]+)"'),
            ("https://i.chaoxing.com/base", r'账号[：:]\s*([^"<\s]{1,20})'),
        ):
            try:
                h = self.session.get(url, timeout=15).text
                mm = re.search(pat, h)
                if mm:
                    self.cx.name = mm.group(1).strip()
                    break
            except Exception:
                continue
        if not self.cx.uid:
            log("[!] 未从 Cookie 中读到 _uid，签到可能需要它。请确认 Cookie 完整。")
        log(f"[i] 当前身份: uid={self.cx.uid or '?'} fid={self.cx.fid} "
            f"name={self.cx.name or '?'}")

    def alert(self, title, text, kind, push_content=None):
        log(f"[!] {title} | {text.replace(chr(10), ' / ')}")
        self.popup.notify(title, text, kind)
        if self.push_token:
            pushplus_send(self.push_token, title, push_content or text)
        if self.webhook:
            webhook_send(self.webhook, title, push_content or text)

    # ---------- 签到 ----------
    def _scan_activities(self, courses):
        """并发扫描所有课程的签到活动（sign_workers=1 时退化为顺序+抖动）。"""
        workers = max(1, int(self.cfg.get("sign_workers", 4) or 1))
        if workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as ex:
                return list(ex.map(
                    lambda c: (c, self.cx.check_activity(c["courseId"], c["classId"])),
                    courses))
        out = []
        for c in courses:
            out.append((c, self.cx.check_activity(c["courseId"], c["classId"])))
            time.sleep(random.uniform(0.4, 1.2))    # 顺序模式加抖动，降低风控
        return out

    def check_signs(self):
        try:
            courses = self.cx.courses()
        except Exception as e:
            log(f"[!] 获取课程列表失败（Cookie 可能已失效）: {e}")
            return
        if not courses:
            return
        t0 = time.time()
        results = self._scan_activities(courses)
        cost = time.time() - t0
        if cost > max(10, int(self.cfg.get("sign_interval", 30))):
            log(f"[!] 扫描 {len(courses)} 门课耗时 {cost:.1f}s，已超过轮询间隔，"
                f"建议调大 sign_interval 或 sign_workers")

        for c, act in results:
            if not act:
                continue
            if act["activeId"] in self.state["signed"]:
                continue

            tname = SIGN_TYPES.get(act["otherId"], "未知")
            where = f"{c['courseName']} / {c['className']}"
            head = f"【签到】{tname}"
            body = (f"课程: {where}\n"
                    f"类型: {tname}\n"
                    f"活动: {act['name']}\n"
                    f"时间: {datetime.now().strftime('%H:%M:%S')}")

            handled = False
            if self.cfg.get("auto_sign", True):
                handled, result = self.cx.try_auto_sign(act)
                log(f"[i] {where} 自动签到: {result}")
                if handled:
                    self.state["signed"].append(act["activeId"])
                    self.alert(f"✅ 已自动签到：{tname}",
                               f"{where}\n{act['name']}\n{result}", "ok",
                               push_content=f"<b>{where}</b><br>{tname}<br>{result}")
                    save_state(self.state)
                    continue
                body += f"\n\n自动处理未完成：{result}\n👉 请尽快手动签到！"
            else:
                body += "\n\n👉 请尽快打开学习通手动签到！"

            self.alert(head, body, "sign",
                       push_content=(f"<b>{head}</b><br>课程: {where}<br>"
                                     f"类型: {tname}<br>活动: {act['name']}<br>"
                                     f"<span style='color:red'>请尽快手动签到！</span>"))
            self.state["signed"].append(act["activeId"])
            save_state(self.state)

    # ---------- 通知 ----------
    def check_notices(self):
        try:
            notices = self.cx.fetch_notices()
        except Exception as e:
            log(f"[!] 获取通知失败: {e}")
            return
        if not notices:
            return

        seen = set(self.state["seen_notices"])
        first = not self.state.get("first_notice_done")

        new_items = []
        for n in notices:
            nid = self._notice_id(n)
            if nid in seen:
                continue
            seen.add(nid)
            # 只看未读，已经读过的历史通知不打扰
            if str(n.get("isread", "0")) in ("1", "true", "True"):
                continue
            new_items.append(n)

        self.state["seen_notices"] = list(seen)
        if first:
            self.state["first_notice_done"] = True
            save_state(self.state)
            log(f"[i] 首次运行，已记录 {len(notices)} 条历史通知（不打扰）")
            if not self.cfg.get("push_on_first_run", False):
                return

        for n in new_items:
            title = (n.get("title") or "新通知").strip()
            content = re.sub(r"<[^>]+>", "", str(n.get("content") or ""))
            content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
            sender = n.get("createrName") or ""
            when = n.get("completeTime") or ""
            body = f"{title}\n\n{content[:220]}"
            if sender or when:
                body += f"\n\n—— {sender} {when}"
            self.alert(f"📢 学习通通知：{title}", body, "notice",
                       push_content=(f"<b>{title}</b><br>{sender} {when}<br><br>"
                                     f"{content[:400]}"))
        save_state(self.state)

    @staticmethod
    def _notice_id(n: dict) -> str:
        for k in ("idCode", "id", "noticeId", "uuid"):
            if n.get(k):
                return str(n[k])
        return f"{n.get('title','')}|{n.get('completeTime','')}"

    # ---------- 主循环 ----------
    def run_forever(self):
        si = max(10, int(self.cfg.get("sign_interval", 30)))
        ni = max(30, int(self.cfg.get("notice_interval", 180)))
        log("=" * 60)
        log(f"[i] 开始监听：签到每 {si}s，通知每 {ni}s；"
            f"自动签到={'开' if self.cfg.get('auto_sign', True) else '关'}；"
            f"推送={'PushPlus ' if self.push_token else ''}"
            f"{'Webhook ' if self.webhook else ''}"
            f"{'关' if not (self.push_token or self.webhook) else ''}")
        log("=" * 60)
        log("[i] 提醒：别关这个窗口，也别在窗口里用鼠标拖选文字（会卡死程序）")
        last_notice = 0.0
        started = time.time()
        last_hb = started
        hb = max(60, int(self.cfg.get("heartbeat_interval", 300)))
        rounds = 0
        while True:
            try:
                if self.cfg.get("monitor_sign", True):
                    self.check_signs()
                if self.cfg.get("monitor_notice", True) and \
                        time.time() - last_notice >= ni:
                    self.check_notices()
                    last_notice = time.time()

                rounds += 1
                if time.time() - last_hb >= hb:
                    mins = int((time.time() - started) / 60)
                    log(f"[i] 心跳：已连续运行 {mins} 分钟，完成 {rounds} 轮扫描（正常）")
                    last_hb = time.time()
            except KeyboardInterrupt:
                log("[i] 已手动退出")
                break
            except Exception as e:
                log(f"[!] 本轮检测异常: {e}")
            time.sleep(si + random.uniform(0, 5))

    def once(self):
        if self.cfg.get("monitor_sign", True):
            self.check_signs()
        if self.cfg.get("monitor_notice", True):
            self.check_notices()
        log("[i] 单次检测完成")


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    disable_quickedit()

    ap = argparse.ArgumentParser(description="学习通签到/通知 实时监控告警器")
    ap.add_argument("--once", action="store_true", help="只检测一次就退出")
    ap.add_argument("--test-push", action="store_true", help="只测试 PushPlus 微信推送")
    ap.add_argument("--dump", action="store_true", help="打印接口原始返回，排查问题")
    ap.add_argument("--no-sign", action="store_true", help="本次运行关闭自动签到（只提醒）")
    ap.add_argument("--no-popup", action="store_true", help="本次运行关闭弹窗（只推送/打印）")
    ap.add_argument("--interval", type=int, help="覆盖签到轮询间隔（秒）")
    args = ap.parse_args()

    cfg = load_config()
    if args.no_sign:
        cfg["auto_sign"] = False
    if args.no_popup:
        cfg["popup"] = False
    if args.interval:
        cfg["sign_interval"] = args.interval

    if args.test_push:
        token = (cfg.get("pushplus_token") or "").strip()
        hook = (cfg.get("webhook_url") or "").strip()
        if not token and not hook:
            sys.exit("[x] 请先在 config.json 里填写 pushplus_token 或 webhook_url")
        title = "学习通监控-推送测试"
        body = "如果你收到这条消息，说明推送配置成功 ✅"
        print(f"[PushPlus] {'已配置' if token else '未配置'}")
        if token:
            print("  PushPlus " + ("成功 -> 请查看微信"
                                   if pushplus_send(token, title, body)
                                   else "失败（常见原因：905 未实名认证 / 903 token 不正确）"))
        print(f"[Webhook]  {'已配置' if hook else '未配置'}")
        if hook:
            print("  Webhook " + ("成功" if webhook_send(hook, title, body) else "失败"))
        return

    if args.dump:
        s = build_session(cfg)
        uid, fid = get_identity(s)
        print(f"uid={uid}  fid={fid}")
        print("\n--- backclazzdata ---")
        print(s.get(URL_COURSES, params={"rss": "1", "view": "json"},
                    timeout=20).text[:1500])
        print("\n--- getNoticeList ---")
        print(s.post(URL_NOTICELIST, data={
            "type": 0, "notice_type": 0, "lastValue": "", "sort": 0},
            timeout=20).text[:1500])
        return

    banner = (
        "=" * 54 + "\n"
        "   学习通 签到 / 通知 实时监控告警器\n"
        "   关闭本窗口即可停止监控（Ctrl+C 亦可）\n"
        + "=" * 54 + "\n"
    )
    print(banner)

    mon = Monitor(cfg)
    if args.once:
        mon.once()
    else:
        mon.run_forever()


if __name__ == "__main__":
    main()
