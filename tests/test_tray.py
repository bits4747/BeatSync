"""托盘功能（tray_icon.py）的单元测试。

覆盖重点，都是「改坏了会静默出错」的地方：
  * 偏好文件的读写、损坏兜底、原子写；
  * 缺 pystray / 缺 Pillow / 非 Windows 时必须优雅降级，而不是抛异常；
  * 「要不要收托盘」的决策：用户想收 **且** 这台机器能收，缺一就正常退出；
  * TrayCradle 反复 stop / notify 不炸（真退出路径上会调）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tray_icon as tray


# --------------------------------------------------------------------- 偏好文件
def test_prefs_default_is_close_to_tray(tmp_path: Path):
    """没写过偏好时默认收托盘——这是用户提需求时的期望行为。"""
    assert tray.load_prefs(tmp_path) == {"close_to_tray": True}


def test_save_then_load_roundtrip(tmp_path: Path):
    tray.save_prefs({"close_to_tray": False}, tmp_path)
    assert tray.load_prefs(tmp_path)["close_to_tray"] is False
    assert json.loads((tmp_path / tray.PREFS_FILE).read_text(encoding="utf-8")) == {
        "close_to_tray": False
    }


def test_save_prefs_ignores_unknown_keys(tmp_path: Path):
    """未知键不落盘：文件是给人看的，塞垃圾进去会让手改时更难判断。"""
    tray.save_prefs({"close_to_tray": False, "whatever": 1}, tmp_path)
    raw = json.loads((tmp_path / tray.PREFS_FILE).read_text(encoding="utf-8"))
    assert "whatever" not in raw
    assert raw["close_to_tray"] is False


def test_save_prefs_accepts_int_like_bool(tmp_path: Path):
    """前端/JSON 有时给 0/1，按布尔处理（js 的 true/false 就是这种语义）。"""
    tray.save_prefs({"close_to_tray": 0}, tmp_path)
    assert tray.load_prefs(tmp_path)["close_to_tray"] is False


@pytest.mark.parametrize("payload", ["", "{", "[]", "null", '"x"', "{\"close_to_tray\": \"no\"}"])
def test_load_prefs_survives_broken_file(tmp_path: Path, payload: str):
    """损坏/类型不对的偏好一律回默认值，绝不让程序启动失败。"""
    (tmp_path / tray.PREFS_FILE).write_text(payload, encoding="utf-8")
    assert tray.load_prefs(tmp_path) == {"close_to_tray": True}


def test_save_prefs_is_atomic(tmp_path: Path):
    """写盘用临时文件 + replace，不能留下 .tmp 残骸。"""
    tray.save_prefs({"close_to_tray": False}, tmp_path)
    assert (tmp_path / tray.PREFS_FILE).exists()
    assert not (tmp_path / (tray.PREFS_FILE + ".tmp")).exists()


def test_prefs_path_prefers_app_dir_when_writable(tmp_path: Path):
    assert tray.prefs_path(tmp_path) == tmp_path / tray.PREFS_FILE


# --------------------------------------------------------------------- 可用性探测
def test_probe_unavailable_when_pystray_missing():
    def importer(name):
        if name == "pystray":
            raise ImportError("no pystray")
        return object()

    ok, why = tray.probe_available(importer=importer)
    assert ok is False
    assert "pystray" in why


def test_probe_unavailable_when_pillow_missing():
    def importer(name):
        if name.startswith("PIL"):
            raise ImportError("no PIL")
        return object()

    ok, why = tray.probe_available(importer=importer)
    assert ok is False
    assert "Pillow" in why


def test_probe_available_when_both_present(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "win32")
    ok, why = tray.probe_available(importer=lambda name: object())
    assert ok is True
    assert why == ""


def test_probe_unavailable_off_windows(monkeypatch):
    """非 Windows 直接判不可用：pystray 在 mac/linux 上是 GTK/AppKit 依赖，不保证有。"""
    monkeypatch.setattr(tray.sys, "platform", "linux")
    ok, why = tray.probe_available(importer=lambda name: object())
    assert ok is False
    assert "Windows" in why


# --------------------------------------------------------------------- 决策
@pytest.mark.parametrize(
    "want,avail,expect",
    [
        (True, True, True),
        (True, False, False),   # 用户想收，但机器收不了 → 必须真退出
        (False, True, False),   # 用户不想收 → 真退出
        (False, False, False),
        (None, True, False),
        (0, True, False),
        (1, True, True),
    ],
)
def test_should_hide_to_tray(want, avail, expect):
    assert tray.should_hide_to_tray(want, avail) is expect


# --------------------------------------------------------------------- TrayCradle
class _FakeImage:
    pass


class _FakeIcon:
    instances: list["_FakeIcon"] = []

    def __init__(self, name, icon, title, menu):
        self.name = name
        self.icon = icon
        self.title = title
        self.menu = menu
        self.notified = []
        self.stopped = 0
        self.removed = 0
        _FakeIcon.instances.append(self)

    def run_detached(self, setup=None):
        self.detached = True

    def notify(self, message, title=None):
        self.notified.append((message, title))

    def remove_notification(self):
        self.removed += 1

    def stop(self):
        self.stopped += 1


class _FakeMenuItem:
    def __init__(self, text, action, default=False):
        self.text = text
        self.action = action
        self.default = default


def _fake_modules(monkeypatch):
    class _Menu:
        def __init__(self, *items):
            self.items = list(items)

    class _Mod:
        Icon = _FakeIcon
        Menu = _Menu
        MenuItem = _FakeMenuItem

    def importer(name):
        if name == "pystray":
            return _Mod
        if name == "PIL.Image":
            mod = type("M", (), {})()
            mod.new = staticmethod(lambda *a, **k: _FakeImage())
            return mod
        if name == "PIL.ImageDraw":
            mod = type("M", (), {})()
            mod.Draw = staticmethod(lambda img: type("D", (), {
                "ellipse": staticmethod(lambda *a, **k: None)})())
            return mod
        raise ImportError(name)

    _FakeIcon.instances = []
    monkeypatch.setattr(tray, "_default_importer", importer)
    return importer


def test_cradle_creates_icon_once(monkeypatch, tmp_path: Path):
    _fake_modules(monkeypatch)
    cradle = tray.TrayCradle(icon_path=None, lang="zh")
    assert cradle.running is False
    assert cradle.ensure(lambda: None, lambda: None) is True
    assert cradle.running is True
    assert len(_FakeIcon.instances) == 1
    # 第二次 ensure 复用，不再新建图标（否则托盘里会冒出两个）
    assert cradle.ensure(lambda: None, lambda: None) is True
    assert len(_FakeIcon.instances) == 1


def test_cradle_menu_has_show_and_quit(monkeypatch, tmp_path: Path):
    _fake_modules(monkeypatch)
    cradle = tray.TrayCradle(lang="en")
    show_calls, quit_calls = [], []
    cradle.ensure(lambda: show_calls.append(1), lambda: quit_calls.append(1))
    menu = _FakeIcon.instances[0].menu
    labels = [it.text for it in menu.items]
    assert labels == ["Show window", "Quit"]
    assert menu.items[0].default is True  # 双击托盘图标 = 显示窗口


def test_cradle_menu_callbacks_swallow_errors(monkeypatch):
    """回调里抛异常不能把 pystray 的消息循环带崩（否则托盘整个失效）。"""
    _fake_modules(monkeypatch)

    def boom():
        raise RuntimeError("x")

    cradle = tray.TrayCradle()
    cradle.ensure(boom, boom)
    item = _FakeIcon.instances[0].menu.items[0]
    item.action(None, None)  # 内部丢线程 + _safe_call，不应抛


def test_cradle_stop_is_idempotent(monkeypatch):
    _fake_modules(monkeypatch)
    cradle = tray.TrayCradle()
    cradle.ensure(lambda: None, lambda: None)
    cradle.stop()
    cradle.stop()  # 重复调用安全（退出路径上 app/gui 都可能调）
    assert _FakeIcon.instances[0].stopped == 1
    assert cradle.running is False


def test_cradle_notify_without_icon_returns_false():
    cradle = tray.TrayCradle()
    assert cradle.notify("hi") is False


def test_cradle_degrades_when_pystray_missing(monkeypatch):
    def importer(name):
        raise ImportError("nope")

    monkeypatch.setattr(tray, "_default_importer", importer)
    cradle = tray.TrayCradle()
    assert cradle.ensure(lambda: None, lambda: None) is False
    assert cradle.running is False
    assert "ImportError" in cradle.last_error


def test_cradle_survives_icon_init_failure(monkeypatch):
    def importer(name):
        if name == "pystray":
            class _Bad:
                Icon = None
                Menu = None
                MenuItem = None
            return _Bad
        mod = type("M", (), {})()
        mod.new = staticmethod(lambda *a, **k: _FakeImage())
        return mod

    monkeypatch.setattr(tray, "_default_importer", importer)
    cradle = tray.TrayCradle()
    assert cradle.ensure(lambda: None, lambda: None) is False
    assert cradle.running is False


def test_cradle_lang_switch():
    assert tray.TrayCradle(lang="en")._t("中", "EN") == "EN"
    assert tray.TrayCradle(lang="zh")._t("中", "EN") == "中"
    assert tray.TrayCradle(lang="en-US")._t("中", "EN") == "EN"
    assert tray.TrayCradle(lang="")._t("中", "EN") == "中"
