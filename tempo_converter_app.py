#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BeatSync —— 桌面应用（pywebview 原生窗口 + HTML 前端）。

    python tempo_converter_app.py          # 启动桌面应用
    python tempo_converter_app.py --smoke  # 无交互冒烟测试（自动开窗、跑一次转换、退出）

界面：pywebview 原生窗口 + `frontend/` 那套 HTML（Windows 上用 WebView2）。
文件/目录选择走**原生资源管理器对话框**（`window.create_file_dialog`）。

依赖 pythonnet/.NET。本机（用户机器）实测可用；若某台机器上 pythonnet 初始化失败
（某些 Windows 安全策略会拒绝 `WindowsLoader.GetAllModules()` 所需的进程句柄访问，
报 `Win32Exception: 拒绝访问`），启动会失败而不是静默降级，错误由 `_report_fatal()`
写进 `app_error.log` 并弹窗。此时可改用 tkinter 界面：`python tempo_converter_gui.py`
（功能一致、不依赖 .NET），或设环境变量 `TMC_FORCE_TK=1` 让本程序改走 tkinter。
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# numba 缓存目录：**必须在 import librosa/numba 之前**设置。
#
# 为什么必须有这段：numba 首次使用要把 JIT 编译结果写进缓存目录。打包后的 exe
# 不带 NUMBA_CACHE_DIR，numba 会退回默认位置（用户目录/临时目录），而在开启了
# 某些 Windows 安全策略的机器上，那个写入会**永久挂起**——表现为点「开始处理」
# 后卡在第一首歌、毫无输出（实测：不设 >10 分钟无响应；设了 16.7 秒正常完成）。
# 这里强制指向程序自己可写的目录（exe 同级的 _ncache），彻底绕开该问题。
# ---------------------------------------------------------------------------
def _setup_numba_cache() -> None:
    try:
        if os.environ.get("NUMBA_CACHE_DIR"):
            return
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        cache = base / "_ncache"
        cache.mkdir(parents=True, exist_ok=True)
        # 先验证真的可写，写不了就换临时目录
        probe = cache / ".write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            import tempfile
            cache = Path(tempfile.gettempdir()) / "tmc_numba_cache"
            cache.mkdir(parents=True, exist_ok=True)
        os.environ["NUMBA_CACHE_DIR"] = str(cache)
    except Exception:  # noqa: BLE001
        pass


def _pick_ascii_temp_root() -> "Path | None":
    """挑一个"路径纯 ASCII 且实测可写"的目录作为 CLR 包的中转根目录。

    为什么不用 `tempfile.gettempdir()`：它返回的是缓存值，候选列表的最后一项是
    `os.getcwd()`。打包后的进程从中文目录启动时，实测它给出的就是那个中文工作
    目录——中转目标本身非 ASCII，搬过去毫无意义。见坑 #50。

    为什么要在候选**下面再建子目录**再写探针，而不是在候选根上直接写：某些机器
    上临时目录的根允许列目录但不允许建文件（受限令牌 / 企业策略），子目录却可以。
    更麻烦的是刚打包出来的 exe 常被系统按"陌生程序"启发式拦截，写 TEMP 根会直接
    吃到 `[Errno 13] Permission denied`——而这个 exe 一旦跑过一次（或被加入排除项）
    就恢复正常。所以这里是"多候选逐个实测"，任何一个成功就收工，全败再硬降级。
    """
    candidates = []
    for var in ("TMP", "TEMP", "TMPDIR", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA"):
        val = os.environ.get(var)
        if val:
            candidates.append(Path(val))
    candidates.append(Path(os.path.expanduser("~")))

    first_ascii = None
    for cand in candidates:
        if not str(cand).isascii():
            continue
        if first_ascii is None:
            first_ascii = cand
        sub = cand / "BeatSyncRT"
        try:
            sub.mkdir(parents=True, exist_ok=True)
            probe = sub / ".beat_sync_wtest"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return cand
        except Exception:  # noqa: BLE001
            continue
    # 所有候选都不可写：硬降级给第一个纯 ASCII 的候选，让后续流程照原样产出处，
    # 总好过返回 None 让整个迁移静默跳过。
    return first_ascii


def _relocate_clr_packages_to_ascii() -> None:
    """把 pythonnet / clr_loader / webview 三个包搬到纯 ASCII 目录再导入。

    为什么必须有这段：pythonnet 在 Windows 上默认走 .NET Framework（netfx），
    netfx 用 `Assembly.LoadFrom` 加载程序集，而 .NET Framework 会把**含非 ASCII
    字符的路径当成"网络位置"**，直接以 CAS 策略拒绝加载：

        System.NotSupportedException: 尝试从一个网络位置加载程序集，在早期版本的
        .NET Framework 中，这会导致对该程序集进行沙盒处理。此发行版的 .NET Framework
        默认情况下不启用 CAS 策略，因此，此加载可能会很危险。如果此加载不是要对
        程序集进行沙盒处理，请启用 loadFromRemoteSources 开关。

    于是只要程序放在中文目录下（例如 `C:\\Users\\xxx\\Desktop\\音乐速度转换器\\`），
    启动必定失败——先是 `Python.Runtime.dll` 加载不了（报 `Failed to resolve
    Python.Runtime.Loader.Initialize`），就算绕过这步，下一步 pywebview 加载
    `webview\\lib\\Microsoft.Web.WebView2.Core.dll` 时会再撞同一个 CAS 拒绝
    （`System.IO.FileLoadException ... 不支持操作 (0x80131515)`）。

    这不是打包缺陷，也不是本文件代码的问题——源码环境一直正常，只因 pythonnet
    装在纯英文的 `AppData\\Roaming\\Python\\...` 下。

    试过并否掉的两条路（都实测过，留作记录）：
      * 改走 coreclr：`set_runtime('coreclr')` + `runtime_spec=Microsoft.WindowsDesktop.App`
        确实能让 `System.Windows.Forms` 出来，但 pywebview 的 edgechromium 后端要
        `Microsoft.Win32.SystemEvents` / `System.Windows.Forms.ContextMenu` 这些
        .NET Framework 专属类型，coreclr 下装不上（`Failed to create Python type for
        Microsoft.Web.WebView2.WinForms.WebView2`）。
      * 给 netfx 传 `config_file=<开启 loadFromRemoteSources>`：只对非 root AppDomain
        生效，且实测仍无法改变 Python.Runtime.dll 的加载结果。

    可行解法（本函数）：这三个包加起来只有约 2.7 MB，启动时整体复制到系统临时目录
    （`%TEMP%\\BeatSyncRT\\<path-hash>\\`，路径恒为 ASCII），再把它插到 `sys.path`
    最前面。`webview\\lib\\` 里的 WebView2 dll 随包一起走，路径同样变 ASCII，
    于是 CAS 判定通过。实测中文路径下 `webview.create_window()` 可正常返回。

    幂等：目录已存在且 marker 文件存在就跳过复制（第二次启动近乎瞬时）。
    全程异常静默：搬不动就保留原路径，由启动流程照常报错或走 tkinter 回退。
    """
    if os.name != "nt":
        return
    try:
        import importlib.util
        import shutil

        # 只在真的落在非 ASCII 路径下时才搬
        targets = []
        for name in ("pythonnet", "clr_loader", "webview"):
            spec = importlib.util.find_spec(name)
            if spec is None or not spec.origin:
                continue
            pkg_dir = Path(spec.origin).parent
            if not str(pkg_dir).isascii():
                targets.append((name, pkg_dir))
        if not targets:
            return

        # 中转目录**必须**逐个候选实测：不能直接用 tempfile.gettempdir()。
        # 实测打包后的进程里它可能返回当前工作目录（tempfile 候选列表的最后
        # 一项是 os.getcwd()），而工作目录往往正是那棵含中文的路径——那样搬
        # 过去的目标本身就不是 ASCII，等于白复制一遍还会白留一堆垃圾。
        import hashlib

        tmp_root = _pick_ascii_temp_root()
        if tmp_root is None:
            return
        tmp_root = tmp_root / "BeatSyncRT"
        # 用 md5 而非 hash()：内置 hash() 对 str 是随机化的（PYTHONHASHSEED），
        # 每次启动都会算出不同目录名，导致白复制一遍并堆积垃圾目录。
        stamp = hashlib.md5(str(targets[0][1].parent).encode("utf-8")).hexdigest()[:16]
        dest = tmp_root / stamp
        marker = dest / ".beat_sync_rt_ok"

        if not marker.exists():
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)
            for name, pkg_dir in targets:
                shutil.copytree(pkg_dir, dest / name, dirs_exist_ok=True)
            marker.write_text("ok", encoding="utf-8")

        sys.path.insert(0, str(dest))
    except Exception:  # noqa: BLE001
        pass


_setup_numba_cache()
_relocate_clr_packages_to_ascii()

import numpy as np
import soundfile as sf
import webview

import tempo_converter as tc


def _resource_dir() -> Path:
    """只读资源目录（frontend 等）：打包后为 _MEIPASS，源码运行时为脚本目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent


def _work_dir() -> Path:
    """可写工作目录（报告/日志）：打包后为 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


if getattr(sys, "frozen", False):
    # --windowed 打包后 stdout/stderr 为 None，print 会静默；此处兜底避免极端情况报错
    import os as _os
    if sys.stdout is None:
        sys.stdout = open(_os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(_os.devnull, "w", encoding="utf-8")

APP_DIR = _work_dir()
FRONTEND = _resource_dir() / "frontend" / "index.html"


def _pct(value, fallback: float) -> float:
    """把前端传来的百分数（如 25 表示 25%）转成小数（0.25），并夹到 0~100%。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    if v != v:                      # NaN
        return fallback
    return max(0.0, min(1.0, v / 100.0))


def _num(value, fallback: float) -> float:
    """把前端传来的数字转成 float；缺失/不可解析/NaN 时回落 fallback。

    为什么要单独写：前端偶尔会传空串或 undefined（例如输入框被清空），
    直接 float() 会抛 ValueError 把整个 start() 带崩，pywebview 那边只表现为
    "点了开始处理没反应"。这里和 _pct 一样做容错，不让坏值毁掉整批任务。
    """
    if value is None or value == "":
        return fallback
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback
    if v != v:                      # NaN
        return fallback
    return v


def _webview_available() -> tuple[bool, str]:
    """探测 pywebview 能否真正用起来（关键在 pythonnet/.NET 是否能初始化）。

    为什么不能只写 `import webview`：pythonnet 的失败发生在**加载 CLR 时**，
    而 webview 的 Windows 后端要到 `webview.start()` 才 `import clr`，那时窗口
    已经建了一半、报错也来不及回退。这里提前做一次等价探测——真的去碰一下
    `webview.platforms.winforms` 里的 `import clr`，把失败挡在回退之前。

    返回 (可用?, 说明文本)。
    """
    if os.environ.get("TMC_FORCE_TK"):
        return False, tc.tr("已通过 TMC_FORCE_TK 强制使用 tkinter 界面",
                            "tkinter interface forced via TMC_FORCE_TK", tc.get_lang())
    try:
        import webview  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, tc.tr(f"pywebview 导入失败：{e}", f"pywebview import failed: {e}", tc.get_lang())
    try:
        import clr  # noqa: F401
        # 真的用一下 CLR：仅 import clr 在部分环境下是延迟失败的
        import System  # type: ignore  # noqa: F401
        from System.Diagnostics import Process  # type: ignore  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, tc.tr(f"pythonnet / .NET 不可用：{type(e).__name__}: {e}",
                            f"pythonnet / .NET unavailable: {type(e).__name__}: {e}", tc.get_lang())
    try:
        import webview.platforms.winforms  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, tc.tr(f"pywebview 的 Windows 后端不可用：{type(e).__name__}: {e}",
                            f"the Windows backend of pywebview is unavailable: {type(e).__name__}: {e}", tc.get_lang())
    return True, ""


def _chinese_path_hint() -> str:
    """当程序路径含非 ASCII 字符时，给出用户能自己动手的自救指引。

    为什么需要：Windows 上的 .NET Framework 会把含非 ASCII 字符的路径当成"网络
    位置"，用 CAS 策略拒绝 `Assembly.LoadFrom`（报 `Failed to resolve
    Python.Runtime.Loader.Initialize`）。代码里已有一段会自动把 CLR 相关包搬到
    纯 ASCII 临时目录的兜底（`_relocate_clr_packages_to_ascii`），但那需要写权限；
    在临时目录被安全策略封锁的机器上仍可能搬不动。这时与其让用户对着崩溃干瞪眼，
    不如直接告诉他怎么绕：把整个文件夹挪到纯英文路径下再双击。
    """
    try:
        exe_dir = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).parent
        bad = None
        for p in (exe_dir, Path(getattr(sys, "_MEIPASS", exe_dir))):
            if not str(p).isascii():
                bad = p
                break
        if bad is None:
            return ""
        _l = tc.get_lang()
        return (
            tc.tr("检测到程序所在的路径含非 ASCII 字符（中文等）：\n",
                  "The program's path contains non-ASCII characters (e.g. Chinese):\n", _l)
            + f"  {bad}\n\n"
            + tc.tr(
                "Windows 的 .NET Framework 会拒绝从这种路径加载组件，而本机临时目录"
                "又无法写入、自动中转没能生效。\n"
                "请把整个程序文件夹移动到**纯英文路径**下再双击运行，例如：\n"
                "  C:\\BeatSync\\  （或 D:\\Tools\\BeatSync\\）",
                "Windows .NET Framework refuses to load components from such paths, and the "
                "local temp directory is not writable, so automatic relocation failed.\n"
                "Move the entire program folder to a **purely English (ASCII) path** and "
                "double-click again, for example:\n"
                "  C:\\BeatSync\\  (or D:\\Tools\\BeatSync\\)", _l)
        )
    except Exception:  # noqa: BLE001
        return ""


def _run_tk_fallback(reason: str) -> int:
    """改用 tkinter 界面继续（纯 Python，不依赖 .NET）。

    触发场景：pythonnet/.NET 初始化失败（某些 Windows 安全策略会拒绝
    `WindowsLoader.GetAllModules()` 所需的进程句柄访问，报 `Win32Exception:
    拒绝访问`），或用户显式设了 `TMC_FORCE_TK=1`。
    """
    trigger = "TMC_FORCE_TK" if os.environ.get("TMC_FORCE_TK") else None
    _l = tc.get_lang()
    msg = (
        tc.tr("无法启动 pywebview 界面（需要 .NET / pythonnet）：",
              "Unable to start the pywebview interface (requires .NET / pythonnet):", _l)
        + "\n  " + f"{reason}\n\n"
        + tc.tr("已自动改用 tkinter 界面，功能完全一致。",
                "Automatically switched to the tkinter interface — identical features.", _l)
    )
    hint = _chinese_path_hint()
    if hint:
        msg += "\n\n" + hint
    try:
        print(msg, flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        import tempo_converter_gui
    except Exception as e:  # noqa: BLE001
        _report_fatal(tc.tr(f"tkinter 回退界面也无法加载：{type(e).__name__}: {e}",
                            f"The tkinter fallback interface also failed to load: {type(e).__name__}: {e}", _l)
                      + "\n\n" + msg)
        return 1
    try:
        tempo_converter_gui.main()
        return 0
    except Exception:  # noqa: BLE001
        _report_fatal(tc.tr("tkinter 回退界面启动失败：\n", "The tkinter fallback interface failed to start:\n", _l)
                      + traceback.format_exc())
        return 1


def _report_fatal(message: str) -> None:
    """把致命错误同时写日志 + 弹窗提示（打包后没有 stdout，弹窗是唯一可见渠道）。

    这里**绝不调用 input()**：--windowed 打包后没有 stdin，`input()` 会抛
    `RuntimeError: lost sys.stdin`，把真正的错误信息盖掉（实测踩过）。
    """
    try:
        (APP_DIR / "app_error.log").write_text(message, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _root = _tk.Tk()
        _root.withdraw()
        _mb.showerror(tc.tr("BeatSync 启动失败", "BeatSync failed to start", tc.get_lang()), message[:2000])
        _root.destroy()
    except Exception:  # noqa: BLE001
        pass



class Api:
    """暴露给前端 JS 的接口（pywebview js_api）。"""

    def __init__(self):
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

    # ---------- 内部 ----------
    def _emit(self, payload: dict) -> None:
        try:
            webview.windows[0].evaluate_js(
                "window.__appEvent(" + json.dumps(payload, ensure_ascii=False) + ")")
        except Exception:
            pass

    # ---------- 文件/目录（原生资源管理器对话框）----------
    def select_files(self, lang=None):
        _l = lang or tc.get_lang()
        types = (tc.tr("音频文件 (*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac;*.wma;*.aif;*.aiff)",
                       "Audio files (*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac;*.wma;*.aif;*.aiff)", _l),
                 tc.tr("所有文件 (*.*)", "All files (*.*)", _l))
        res = webview.windows[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True, file_types=types)
        return [str(p) for p in (res or [])]

    def select_folder(self):
        res = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
        return str(res[0]) if res else ""

    def select_out_dir(self):
        return self.select_folder()

    def expand(self, paths):
        """把文件/文件夹路径展开为具体音频文件列表（含子目录）。"""
        try:
            return [str(f) for f in tc.collect_inputs(paths or [], recursive=True)]
        except Exception:
            return []

    def reveal_file(self, path: str):
        """在资源管理器中定位文件。"""
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(path)])
            return True
        except Exception:
            return self.open_folder(str(Path(path).parent))

    def open_folder(self, path: str):
        try:
            os_startfile = __import__("os").startfile
            os_startfile(str(Path(path)))
            return True
        except Exception:
            return False

    # ---------- 兼容界面（tkinter） ----------
    def launch_tk(self):
        """另起一个进程打开 tkinter 兼容界面。

        与 `--tk` 走的是同一套界面（`tempo_converter_gui`），只是从已运行的
        桌面版里再开一个窗口，免得用户只能去开始菜单翻那条快捷方式。
        用独立进程而不是在当前进程里 import：主循环已经在 pywebview 手里，
        同进程再起一个 tkinter 主循环会互相打架；独立进程互不影响，
        关掉哪个都不影响另一个。
        """
        try:
            if getattr(sys, "frozen", False):
                # 打包版：重启自己并带上 --tk
                cmd = [sys.executable, "--tk"]
            else:
                # 源码运行：用同一个解释器跑 gui 模块
                gui = Path(__file__).with_name("tempo_converter_gui.py")
                cmd = [sys.executable, str(gui)]
            kwargs = {"close_fds": True}
            if os.name == "nt":
                # DETACHED_PROCESS：子进程不继承本进程的控制台/句柄，
                # 本程序退出时它不会被一起带走。
                kwargs["creationflags"] = 0x00000008
            subprocess.Popen(cmd, cwd=str(_work_dir()), **kwargs)
            return True
        except Exception as e:
            _report_fatal(tc.tr(f"无法启动兼容界面：{type(e).__name__}: {e}",
                                f"Could not start the compatibility interface: {type(e).__name__}: {e}"))
            return False

    # ---------- 音色试听 ----------
    def preview_timbre(self, timbre: str):
        """合成一小段节拍器样本，返回 WAV data URL 供前端播放。"""
        if timbre not in tc.TIMBRES:
            return ""
        sr = 44100
        bpm = 120.0
        n = int((4 * 60.0 / bpm + 0.5) * sr)
        rng = np.random.default_rng(7)
        times = [k * 60.0 / bpm for k in range(4)]
        track = tc.build_click_track(n, sr, times, timbre, rng,
                                     music_rms_db=-18.0, rel_db=-6.0)
        buf = io.BytesIO()
        sf.write(buf, track, sr, format="WAV", subtype="PCM_16")
        return "data:audio/wav;base64," + base64.b64encode(buf.getvalue()).decode()

    # ---------- 批量处理 ----------
    def start(self, paths, opts, roots=None):
        opts = opts or {}
        _l = str(opts.get("lang") or "").strip().lower()
        lang = _l if _l in ("zh", "en") else tc.get_lang()
        if self.worker is not None and self.worker.is_alive():
            return {"ok": False, "error": tc.tr("已有任务正在进行，请先等待完成或停止",
                                               "A task is already running; wait for it or stop it first", lang)}
        paths = [str(p) for p in (paths or [])]
        files = list(tc.collect_inputs(paths, recursive=True))
        if not files:
            return {"ok": False, "error": tc.tr("没有可处理的音频文件（支持 wav/mp3/flac/ogg/m4a 等）",
                                               "No audio files to process (supports wav/mp3/flac/ogg/m4a, etc.)", lang)}

        # 每个文件对应的「添加时的根」。前端按输入项逐个回传（roots[i] ↔ paths[i]）。
        # 注意 paths 有两种形态，roots 的语义随之不同，两者都要支持：
        #   ① paths 是**输入项**（可能含文件夹，如用户「添加文件夹」）→ 展开后每个
        #      文件都以该输入项为根；
        #   ② paths 已经是**具体文件**（前端把文件夹展开后再逐个回传，roots[i] 就是
        #      该文件应恢复到的层级根）→ 必须直接采用 roots[i]，不能被父目录覆盖。
        # 判据用「paths 里有没有目录」：有目录就得展开，全是文件就按 roots 直接对齐。
        base_of = {}
        roots = list(roots or [])
        has_dir = any(Path(p).is_dir() for p in paths)
        if len(roots) == len(paths) and not has_dir:
            # 形态 ②：逐文件对齐，roots[i] 即该文件的根
            for p, r in zip(paths, roots):
                if r:
                    base_of[str(p)] = str(r)
        # 形态 ①（或前端没给 roots）：按输入项展开，逐个文件登记所属根
        for f, b in tc.collect_inputs_with_base(paths, recursive=True):
            key = str(f)
            if key in base_of:
                continue
            if len(roots) == len(paths):
                # 前端给了根：输入项 b 若是其中的键，用前端指定的根替换
                r = {str(pp): str(rr) for pp, rr in zip(paths, roots) if rr}.get(str(b))
                base_of[key] = r or str(b)
            else:
                base_of[key] = str(b)

        detect_only = bool(opts.get("detectOnly"))
        try:
            target = float(opts.get("target") or 0)
        except (TypeError, ValueError):
            target = 0.0
        if not detect_only and target <= 0:
            return {"ok": False, "error": tc.tr("请填写目标速度 BPM", "Please enter a target BPM", lang)}

        manual = None
        if str(opts.get("manualBpm") or "").strip():
            try:
                manual = float(str(opts["manualBpm"]).strip())
                if manual <= 0:
                    raise ValueError
            except ValueError:
                return {"ok": False, "error": tc.tr("原速 BPM 必须是正数，或留空自动检测",
                                                   "Original BPM must be a positive number, or leave it blank to auto-detect", lang)}

        opt = tc.ConvertOptions(
            target_bpm=target,
            bpm=manual,
            detect_only=detect_only,
            strict=bool(opts.get("strict")),
            engine=opts.get("engine") or "phasevocoder",
            metronome=opts.get("metronome") or None,
            click_rel_db=_num(opts.get("clickRelDb"), -6.0),
            output_format=opts.get("outputFormat") or "source",
            out_dir=Path(opts["outDir"]) if opts.get("outDir") else None,
            mirror_dirs=bool(opts.get("mirrorDirs", True)),
            max_slowdown=_pct(opts.get("maxSlowdown"), tc.DEFAULT_MAX_SLOWDOWN),
            max_speedup=_pct(opts.get("maxSpeedup"), tc.DEFAULT_MAX_SPEEDUP),
            segments=bool(opts.get("segments")),
            lang=lang,
        )
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._run, args=(files, opt, base_of), daemon=True)
        self.worker.start()
        return {"ok": True, "count": len(files)}

    def stop(self):
        self.stop_event.set()
        return True

    def _run(self, files, opt, base_of=None):
        rows, ok, fail, skipped = [], 0, 0, 0
        total = len(files)
        L = getattr(opt, "lang", None) or tc.get_lang()
        # 每个文件所属的「用户添加的根」，用于在指定输出目录时镜像原目录层级
        base_of = base_of or {}
        self._emit({"type": "started", "total": total})
        for i, p in enumerate(files):
            if self.stop_event.is_set():
                self._emit({"type": "log", "text": tc.tr("已停止。", "Stopped.", L)})
                break
            self._emit({"type": "file-status", "path": str(p), "status": "running", "index": i + 1})
            row = tc.process_file(p, opt, rel_base=base_of.get(str(p)),
                                  log_fn=lambda m: self._emit({"type": "log", "text": str(m)}))
            rows.append(row)
            status = tc.row_status(str(row.get("备注") or ""))
            if status == "skipped":
                skipped += 1
            elif status == "failed":
                fail += 1
            else:
                ok += 1
            self._emit({
                "type": "file-status", "path": str(p), "status": status,
                "index": i + 1,
                "detected": row.get("检测BPM"), "a2": row.get("拉伸后BPM"),
                "rate": row.get("拉伸比"), "relation": row.get("节拍关系"),
                "clickBpm": row.get("节拍器BPM"), "note": row.get("备注"),
                "out": row.get("输出文件"),
                "segments": row.get("_分段"),
            })
            self._emit({"type": "progress", "done": i + 1, "total": total})

        report = None
        if rows:
            report = (APP_DIR / "conversion_report.csv").resolve()
            try:
                tc.write_report(rows, report, lang=L)
            except Exception:
                report = None
        self._emit({"type": "done", "ok": ok, "fail": fail, "skipped": skipped,
                    "stopped": self.stop_event.is_set(),
                    "report": str(report) if report else None})

    # ---------- 拖拽导入 ----------
    def bind_dnd(self, window) -> None:
        def on_drop(event):
            try:
                files = ((event or {}).get("dataTransfer") or {}).get("files") or []
                paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
            except Exception:
                paths = []
            if paths:
                pairs = list(tc.collect_inputs_with_base(paths, recursive=True))
                if pairs:
                    # 一并回传「每个文件属于哪个根」，前端才能在指定输出目录时重建层级
                    self._emit({"type": "files-added",
                                "files": [str(f) for f, _b in pairs],
                                "roots": [str(b) for _f, b in pairs]})

        try:
            window.dom.document.events.drop += on_drop
        except Exception as e:
            print("拖拽绑定失败（可改用按钮添加文件）:", e, file=sys.stderr)


def _icon_path() -> str | None:
    """窗口/任务栏图标（.ico）路径；找不到返回 None（pywebview 会退回默认图标）。

    注意：pywebview 的窗口图标**不在 create_window() 上设**，而是
    `webview.start(icon=...)` —— winforms 后端读 `_state['icon']`；
    不给的话它会从 `sys.executable` 抽图标（源码运行 = pythonw 的默认图标）。
    """
    candidates = [_resource_dir() / "assets" / "icon.ico"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "assets" / "icon.ico")
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def _tk_requested() -> bool:
    """是否要改走 tkinter 界面。

    两种触发方式，效果相同：
      * 命令行参数 `--tk`（安装版会建一个「BeatSync（兼容界面）」快捷方式用它）；
      * 环境变量 `TMC_FORCE_TK=1`（老用法，源码运行时方便）。
    """
    if os.environ.get("TMC_FORCE_TK"):
        return True
    return "--tk" in sys.argv[1:]


def main() -> int:
    # 桌面版：直接用 pywebview 原生窗口（依赖 WebView2 / pythonnet，本机已验证可用）。
    # 唯一的分支是 --tk / TMC_FORCE_TK=1 —— 让不想/不能用 .NET 的人显式改走 tkinter
    # 界面（打包时已把 tempo_converter_gui 与 tkinter 一并收进包）。
    # 注意：**不做自动降级**。pythonnet 起不来时会由 webview.start() 抛错、
    # 交给 __main__ 里的 except 走 _report_fatal（写 app_error.log + 弹窗），
    # 而不是静默换一套界面——用户要求"只要桌面版"，静默降级会让人以为界面变了。
    if _tk_requested():
        return _run_tk_fallback(tc.tr("已显式选择 tkinter 界面（--tk）",
                                      "tkinter interface requested (--tk)", tc.get_lang()))
    api = Api()
    window = webview.create_window(
        "BeatSync",
        str(FRONTEND),
        js_api=api,
        width=1150, height=800, min_size=(980, 660),
        background_color="#0a0a0b",
    )
    window.events.loaded += lambda: api.bind_dnd(window)
    webview.start(icon=_icon_path())
    return 0


def smoke() -> int:
    """无交互冒烟测试：开窗 → 验证桥接 → 跑一次真实转换 → 汇报退出。"""
    ok, reason = _webview_available()
    if not ok:
        results = {"bridge": False, "done": False, "reason": reason}
        try:
            (APP_DIR / "_smoke_result.json").write_text(
                json.dumps(results, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        print("SMOKE:" + json.dumps(results, ensure_ascii=False), flush=True)
        return 1
    import webview

    api = Api()
    window = webview.create_window(
        "BeatSync（冒烟测试）", str(FRONTEND), js_api=api,
        width=1150, height=800, background_color="#0a0a0b")

    results = {}
    ev = []
    shown = threading.Event()
    window.events.shown += lambda: shown.set()

    def wait_bridge(timeout: float = 120.0) -> bool:
        """等前端桥接就绪。

        pywebview 的 evaluate_js 内部只等 20 秒就抛 WebViewException；打包版首次启动要
        初始化 .NET / WebView2，可能超过 20 秒，所以这里自己轮询并容错重试。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if window.evaluate_js("window.__bridgeReady === true"):
                    return True
                results["bridgeState"] = {
                    "ready": window.evaluate_js("window.__bridgeReady"),
                    "readyState": window.evaluate_js("document.readyState"),
                    "hasApi": window.evaluate_js("!!(window.pywebview && window.pywebview.api)"),
                }
            except Exception as e:  # noqa: BLE001 - 冒烟测试里记录即可，不中断
                results["bridgeError"] = repr(e)
            time.sleep(0.3)
        return False

    def runner():
        try:
            # 先等窗口真正显示（冻结版首启较慢），再等 JS 桥接注入
            results["shown"] = shown.wait(180)
            results["bridge"] = wait_bridge() if results["shown"] else False

            tmp = APP_DIR / "_smoke_test.wav"
            sr = 22050
            rng = np.random.default_rng(3)
            n = int(sr * 10)
            times = tc.click_schedule(None, 1.0, 120.0, 10.0)
            track = tc.build_click_track(n, sr, times, "click", rng,
                                         music_rms_db=-18.0, rel_db=-6.0)
            sf.write(str(tmp), track, sr, subtype="PCM_16")

            orig_emit = api._emit
            api._emit = lambda payload: (ev.append(payload), orig_emit(payload))

            r = api.start([str(tmp)], {"target": "180"})
            for _ in range(360):  # 最多等 180 秒（冻结版首次转换含 numba 即时编译）
                if any(e.get("type") == "done" for e in ev):
                    break
                time.sleep(0.5)
            results["start"] = r
            results["done"] = any(e.get("type") == "done" for e in ev)
            results["files_done"] = [e for e in ev
                                     if e.get("type") == "file-status" and e.get("status") == "done"]
            tmp.unlink(missing_ok=True)
        except Exception as e:
            results["error"] = traceback.format_exc()
        finally:
            # 结果写盘：--windowed 打包后无控制台输出，靠文件汇报
            try:
                (APP_DIR / "_smoke_result.json").write_text(
                    json.dumps(results, ensure_ascii=False)[:4000], encoding="utf-8")
            except Exception:
                pass
            window.destroy()
        print("SMOKE:" + json.dumps(results, ensure_ascii=False)[:1200], flush=True)

    threading.Thread(target=runner, daemon=True).start()
    webview.start(icon=_icon_path())
    ok = results.get("bridge") and results.get("done") and results.get("files_done")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(smoke() if "--smoke" in sys.argv else main())
    except Exception:
        # 打包后没有 stdout/stdin，绝不能用 input() 收尾（会抛 lost sys.stdin 盖掉真错误）
        msg = tc.tr("启动出错：\n\n", "Startup error:\n\n", tc.get_lang()) + traceback.format_exc()
        # main() 刻意不做自动降级（用户要求"只要桌面版"，静默换界面会让人以为
        # 程序变了），所以这里是唯一的兜底现场：把"路径含中文"这个最常见、且用户
        # 自己就能修的原因放到错误信息最前面，而不是让用户对着 traceback 干瞪眼。
        hint = _chinese_path_hint()
        if hint:
            msg += "\n\n" + "=" * 60 + "\n\n" + hint
        _report_fatal(msg)
        sys.exit(1)
