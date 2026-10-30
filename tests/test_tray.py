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
    prefs = tray.load_prefs(tmp_path)
    assert prefs["close_to_tray"] is True
    assert prefs["custom_timbres"] == []


def test_save_then_load_roundtrip(tmp_path: Path):
    tray.save_prefs({"close_to_tray": False}, tmp_path)
    assert tray.load_prefs(tmp_path)["close_to_tray"] is False
    assert json.loads((tmp_path / tray.PREFS_FILE).read_text(encoding="utf-8")) == {
        "close_to_tray": False, "custom_timbres": []
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
    prefs = tray.load_prefs(tmp_path)
    assert prefs["close_to_tray"] is True
    assert prefs["custom_timbres"] == []


def test_custom_timbres_roundtrip(tmp_path: Path):
    """自定义音色路径必须真的落盘。

    回归用：`save_prefs` / `load_prefs` 都只处理 `DEFAULT_PREFS` 里声明过的键，
    `custom_timbres` 一度没在里面，于是「导入音色」写盘时被静默丢弃，
    下次启动列表就空了——界面上看不出任何错误。
    """
    tray.save_prefs({"custom_timbres": ["C:/a.wav", "C:/b.wav"]}, tmp_path)
    assert tray.load_prefs(tmp_path)["custom_timbres"] == ["C:/a.wav", "C:/b.wav"]
    raw = json.loads((tmp_path / tray.PREFS_FILE).read_text(encoding="utf-8"))
    assert raw["custom_timbres"] == ["C:/a.wav", "C:/b.wav"]


def test_custom_timbres_dedup_case_insensitive(tmp_path: Path):
    """Windows 路径不分大小写，同一文件两个写法只留一条。"""
    tray.save_prefs({"custom_timbres": ["C:/A.wav", "C:/a.wav", "C:/b.wav"]}, tmp_path)
    assert tray.load_prefs(tmp_path)["custom_timbres"] == ["C:/A.wav", "C:/b.wav"]


def test_custom_timbres_drops_non_strings(tmp_path: Path):
    """偏好文件是用户可手改的，塞数字/对象进去不能让下游炸。"""
    (tmp_path / tray.PREFS_FILE).write_text(
        json.dumps({"custom_timbres": ["C:/a.wav", 123, None, {"x": 1}, ""]}),
        encoding="utf-8")
    assert tray.load_prefs(tmp_path)["custom_timbres"] == ["C:/a.wav"]


def test_custom_timbres_wrong_type_falls_back_to_empty(tmp_path: Path):
    (tmp_path / tray.PREFS_FILE).write_text(
        json.dumps({"custom_timbres": "C:/a.wav"}), encoding="utf-8")
    assert tray.load_prefs(tmp_path)["custom_timbres"] == []


def test_save_prefs_is_atomic(tmp_path: Path):
    """写盘用临时文件 + replace，不能留下 .tmp 残骸。"""
    tray.save_prefs({"close_to_tray": False}, tmp_path)
    assert (tmp_path / tray.PREFS_FILE).exists()
    assert not (tmp_path / (tray.PREFS_FILE + ".tmp")).exists()


def test_prefs_path_prefers_app_dir_when_writable(tmp_path: Path):
    assert tray.prefs_path(tmp_path) == tmp_path / tray.PREFS_FILE


# ------------------------------------------- 读不出来时不许覆盖用户的设置
def test_prefs_health_ok_when_missing(tmp_path: Path):
    """没写过 ≠ 读不出来：缺文件是正常状态，不该阻止保存。"""
    tray.load_prefs(tmp_path)
    h = tray.prefs_health(tmp_path)
    assert h["exists"] is False, "文件不存在，exists 必须是 False"
    assert h["reason"] == "missing"
    # 调用方的判定是「ok 为假 且 文件存在」才拦保存，missing 不会挡住首次保存
    assert not (h["ok"] is False and h["exists"] is True)


def test_prefs_health_reports_corrupt_file(tmp_path: Path):
    """损坏的偏好文件必须被标记出来（桌面版靠它决定「这次不保存」）。"""
    (tmp_path / tray.PREFS_FILE).write_text("{ broken", encoding="utf-8")
    tray.load_prefs(tmp_path)
    h = tray.prefs_health(tmp_path)
    assert h["ok"] is False
    assert h["exists"] is True
    assert "corrupt" in h["reason"]


def test_prefs_health_recovers_after_good_load(tmp_path: Path):
    tray.save_prefs({"close_to_tray": False}, tmp_path)
    tray.load_prefs(tmp_path)
    assert tray.prefs_health(tmp_path)["ok"] is True


def test_save_prefs_keeps_broken_file_as_backup(tmp_path: Path):
    """回归：损坏的偏好文件不能被无声覆盖，必须先改名留档。

    旧行为是 load 失败静默返回默认值，save 再拿这份默认值写盘——用户手改
    坏一个字符，界面上点一下「关闭时最小化」，他原来的全部设置就没了。
    """
    bad = tmp_path / tray.PREFS_FILE
    bad.write_text("{ broken", encoding="utf-8")
    tray.save_prefs({"close_to_tray": False}, tmp_path)

    backups = list(tmp_path.glob(tray.PREFS_FILE + ".bad-*"))
    assert len(backups) == 1, f"坏文件应被改名留档，实际 {[p.name for p in backups]}"
    assert backups[0].read_text(encoding="utf-8") == "{ broken", "留档内容必须是原样"
    # 新文件正常写出来了
    assert json.loads(bad.read_text(encoding="utf-8"))["close_to_tray"] is False
    # 留档之后健康状态恢复
    assert tray.prefs_health(tmp_path)["ok"] is True


def test_save_prefs_no_backup_when_file_missing(tmp_path: Path):
    """首次保存不该凭空造出 .bad-* 文件。"""
    tray.save_prefs({"close_to_tray": True}, tmp_path)
    assert not list(tmp_path.glob(tray.PREFS_FILE + ".bad-*"))


def test_save_prefs_no_backup_for_healthy_file(tmp_path: Path):
    """正常文件覆盖保存时不留档，否则每次保存都堆一个备份。"""
    tray.save_prefs({"close_to_tray": True}, tmp_path)
    tray.save_prefs({"close_to_tray": False}, tmp_path)
    assert not list(tmp_path.glob(tray.PREFS_FILE + ".bad-*"))


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


# --------------------------------------------------------------------- 托盘图标来源
def test_find_icon_finds_bundled_icon(monkeypatch, tmp_path: Path):
    """软件图标必须能被自动找到（打包版靠它拿到真图标，而不是兜底蓝点）。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    ico = assets / "icon.ico"
    ico.write_bytes(b"fake")
    monkeypatch.setattr(tray, "_candidate_dirs", lambda: [tmp_path])
    assert tray.find_icon() == str(ico)


def test_find_icon_falls_back_to_png(monkeypatch, tmp_path: Path):
    assets = tmp_path / "assets"
    assets.mkdir()
    png = assets / "icon.png"
    png.write_bytes(b"fake")
    monkeypatch.setattr(tray, "_candidate_dirs", lambda: [tmp_path])
    assert tray.find_icon() == str(png)


def test_find_icon_returns_none_when_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(tray, "_candidate_dirs", lambda: [tmp_path])
    assert tray.find_icon() is None


def test_cradle_uses_software_icon_when_no_explicit_path(monkeypatch, tmp_path: Path):
    """没显式给 icon_path 时，托盘必须自己去拿软件图标（回归：桌面版曾传 None）。"""
    assets = tmp_path / "assets"
    assets.mkdir()
    ico = assets / "icon.ico"
    ico.write_bytes(b"fake")
    monkeypatch.setattr(tray, "_candidate_dirs", lambda: [tmp_path])

    opened: list[str] = []

    def importer(name):
        if name == "pystray":
            class _Mod:
                Icon = _FakeIcon
                Menu = lambda *items: type("M", (), {"items": list(items)})()
                MenuItem = _FakeMenuItem
            return _Mod
        mod = type("M", (), {})()
        mod.new = staticmethod(lambda *a, **k: _FakeImage())

        def _open(path):
            opened.append(str(path))
            return type("I", (), {"convert": lambda self, m: _FakeImage()})()

        mod.open = staticmethod(_open)
        return mod

    _FakeIcon.instances = []
    monkeypatch.setattr(tray, "_default_importer", importer)
    cradle = tray.TrayCradle()
    assert cradle.ensure(lambda: None, lambda: None) is True
    assert opened == [str(ico)]

