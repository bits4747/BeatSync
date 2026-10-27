"""系统托盘支持：关闭窗口时收进托盘，而不是退出程序。

为什么要单独一个模块（而不是塞进 app / gui 里）：
  * pywebview 桌面版与 tkinter 兼容界面**都要**这个能力，两边界面代码完全不共享，
    只有这一层是共用的；
  * pystray / Pillow 是**可选依赖**——装不上、导入失败、平台不支持时，行为必须
    安静地退回「关闭 = 真退出」，绝不能因为托盘把主程序带崩。把探测集中在这里，
    上层只管问 `probe_available()` 和 `should_hide_to_tray()`。

设计取舍（记下来免得以后改错）：
  * 托盘图标**按需创建**（第一次真的收进托盘时才建），不做成启动即显示。原因：启动
    就挂一个托盘图标，在「用户根本没打算收托盘」的机器上是噪音；而且 tkinter 冒烟
    测试只开窗不显示，启动即建会在测试里留下托盘图标。
  * `pystray.Icon.stop()` 是必须调用的：win32 后端的消息循环跑在**非 daemon** 线程
    里，不 stop 进程退不掉（任务管理器里会残留）。
  * 菜单回调一律丢到独立线程执行：回调是在 pystray 自己的消息循环线程里跑的，
    在里面直接做 Show/Hide/Close 会跟 pywebview 的 WinForms 消息循环互等。
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import threading

# 偏好文件名：和转换报告、app_error.log 放在同一处（可写的工作目录）。
PREFS_FILE = "ui_prefs.json"

# 默认值刻意是 True：用户提这个需求的原话是「关闭软件后收到系统托盘，不完全退出」，
# 也就是他期望的默认行为。仍然给开关，方便想「点 × 就退出」的人关掉。
DEFAULT_PREFS = {"close_to_tray": True}


def _default_importer(name: str):
    return importlib.import_module(name)


def probe_available(importer=None) -> tuple[bool, str]:
    """探测托盘功能能不能用。返回 (可用?, 不可用原因)。

    走 importer 注入而不是直接 `import pystray`：单元测试要能在没装 pystray 的机器
    上验证「缺依赖时优雅降级」这条分支。

    Pillow 也一起探：pystray 在 win32 上要把图标序列化成 ICO，内部就是
    `PIL.Image.save`，没 Pillow 的话要到真正显示图标时才炸——那时已经在收托盘的
    路径上了，炸了就等于窗口没了、程序也没退。
    """
    if sys.platform != "win32":
        return False, "only Windows is supported"
    imp = importer or _default_importer
    for mod, label in (("pystray", "pystray"), ("PIL.Image", "Pillow"),
                       ("PIL.ImageDraw", "Pillow")):
        try:
            imp(mod)
        except Exception as e:  # noqa: BLE001 - 任何导入失败都降级
            return False, f"{label} unavailable: {type(e).__name__}: {e}"
    return True, ""


def should_hide_to_tray(close_to_tray, tray_available) -> bool:
    """点 × 时该不该收进托盘（而不是退出）。

    单独抽成纯函数是为了可测：真正的分支逻辑是「用户希望收托盘 **且** 这台机器
    真的能收」，两者缺一就必须老老实实退出——收不了还不退，用户会以为程序卡死。
    """
    return bool(close_to_tray) and bool(tray_available)


def _candidate_dirs() -> list[Path]:
    out: list[Path] = []
    try:
        if getattr(sys, "frozen", False):
            out.append(Path(sys.executable).parent)
        else:
            out.append(Path(__file__).resolve().parent)
    except Exception:  # noqa: BLE001
        pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "BeatSync")
    return out


def prefs_path(app_dir=None) -> Path:
    """偏好文件路径。`app_dir` 显式给了就用它（测试/打包时确定位置）。

    没给时按「程序目录优先、%APPDATA%\\BeatSync 兜底」挑一个可写的：安装到
    Program Files 之类只读位置时程序目录不可写，退回用户目录仍能记住设置。
    """
    if app_dir is not None:
        return Path(app_dir) / PREFS_FILE
    for d in _candidate_dirs():
        try:
            if d.is_dir() and os.access(d, os.W_OK):
                return d / PREFS_FILE
        except OSError:
            continue
    dirs = _candidate_dirs()
    return (dirs[-1] if dirs else Path.cwd()) / PREFS_FILE


def load_prefs(app_dir=None) -> dict:
    """读偏好；文件不存在/损坏/类型不对时回到默认值，绝不抛异常。

    文件内容是用户可编辑的 JSON，写坏了是常态（手动改、断电写一半、同步盘冲突），
    这里一律当作「没设置过」，而不是让程序启动失败。
    """
    out = dict(DEFAULT_PREFS)
    try:
        raw = json.loads(prefs_path(app_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    for key, default in DEFAULT_PREFS.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(default, bool):
            # 只认真正的布尔；字符串 "false" 之类容易被误解成 True，直接忽略。
            if isinstance(val, bool):
                out[key] = val
        elif isinstance(val, type(default)):
            out[key] = val
    return out


def save_prefs(patch: dict, app_dir=None) -> dict:
    """把 patch 里的已知键并进偏好文件，返回合并后的完整偏好。

    先写临时文件再 `os.replace`：直接覆盖时如果程序在这一刻被杀掉，留下的是半个
    JSON，下次启动就成了「设置丢了」。replace 在 Windows 上也是原子的。
    """
    cur = load_prefs(app_dir)
    for key in DEFAULT_PREFS:
        if key in (patch or {}):
            default = DEFAULT_PREFS[key]
            val = patch[key]
            if isinstance(default, bool):
                if isinstance(val, (bool, int)):
                    cur[key] = bool(val)
            elif isinstance(val, type(default)):
                cur[key] = val
    target = prefs_path(app_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass
    return cur


class TrayCradle:
    """一个托盘图标的生命周期包装（按需创建、可反复启停）。

    上层只需要三件事：
      * `ensure(on_show, on_quit)` —— 收进托盘前确保图标在；
      * `notify(msg)` —— 第一次收起时提示「还在后台运行」；
      * `stop()` —— 真退出时收干净，否则进程残留。

    所有异常都在内部吞掉并记进 `self.last_error`：托盘只是「顺手多给的一条退路」，
    它失败不能影响主窗口的关闭语义（调用方的兜底是「直接退出」）。
    """

    def __init__(self, icon_path=None, lang: str = "zh", importer=None):
        self.icon_path = str(icon_path) if icon_path else None
        self.lang = "en" if str(lang).lower().startswith("en") else "zh"
        self._importer = importer
        self._icon = None
        self._lock = threading.Lock()
        self.last_error = ""

    # ---------- 文案 ----------
    def _t(self, zh: str, en: str) -> str:
        return en if self.lang == "en" else zh

    # ---------- 状态 ----------
    @property
    def running(self) -> bool:
        return self._icon is not None

    # ---------- 图标 ----------
    def _load_image(self, modules):
        Image = modules["Image"]
        ImageDraw = modules["ImageDraw"]
        if self.icon_path:
            try:
                return Image.open(self.icon_path).convert("RGBA")
            except Exception:  # noqa: BLE001 - 图标坏了不该让托盘不可用
                pass
        # 兜底画一个纯色圆点：总比没有图标好（Windows 会拒绝空图标）。
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((3, 3, 28, 28), fill=(64, 158, 255, 255))
        draw.ellipse((11, 11, 20, 20), fill=(255, 255, 255, 255))
        return img

    def _modules(self) -> dict:
        imp = self._importer or _default_importer
        return {
            "pystray": imp("pystray"),
            "Image": imp("PIL.Image"),
            "ImageDraw": imp("PIL.ImageDraw"),
        }

    def ensure(self, on_show, on_quit) -> bool:
        """确保托盘图标已在运行；成功返回 True。已运行则直接返回 True。"""
        with self._lock:
            if self._icon is not None:
                return True
            try:
                mods = self._modules()
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                return False
            pystray = mods["pystray"]
            try:
                image = self._load_image(mods)

                def _spawn(fn):
                    # 回调跑在 pystray 的消息循环线程里，必须甩给独立线程，
                    # 否则 Show/Hide/Close 会和 pywebview 的 WinForms 循环互等。
                    def _cb(icon=None, item=None):
                        threading.Thread(target=_safe_call, args=(fn,),
                                         daemon=True).start()
                    return _cb

                menu = pystray.Menu(
                    pystray.MenuItem(self._t("显示主窗口", "Show window"),
                                     _spawn(on_show), default=True),
                    pystray.MenuItem(self._t("退出", "Quit"),
                                     _spawn(on_quit)),
                )
                icon = pystray.Icon("BeatSync", image, "BeatSync", menu)
                icon.run_detached()
                self._icon = icon
                self.last_error = ""
                return True
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                self._icon = None
                return False

    def notify(self, message: str, title: str = "BeatSync") -> bool:
        """弹一个气泡提示（不支持则静默返回 False）。"""
        icon = self._icon
        if icon is None:
            return False
        try:
            icon.notify(str(message), str(title))
            return True
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            return False

    def stop(self) -> None:
        """停掉托盘图标并等消息循环收尾。可重复调用。"""
        with self._lock:
            icon = self._icon
            self._icon = None
        if icon is None:
            return
        try:
            icon.stop()
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
        try:
            # 顺带摘掉残留的气泡，避免托盘里留一条注销通知。
            icon.remove_notification()
        except Exception:  # noqa: BLE001
            pass


def _safe_call(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - 托盘回调里的异常没有人能接
        pass
