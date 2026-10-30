"""`tempo_converter_app.Api` 里与偏好相关的接口测试。

只测「改坏了会静默出错」的那几处：自定义节拍器音色的记录与移除。
界面是 pywebview + 前端 JS，真正的渲染没法在这里测；这里守的是**数据**：
偏好里该留什么、该丢什么，以及删掉一个音色时用户的音频文件绝不能被动到。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import tempo_converter as tc  # noqa: E402
import tempo_converter_app as app  # noqa: E402
import tray_icon as tray  # noqa: E402


@pytest.fixture()
def api(tmp_path: Path):
    """一个把偏好指向 tmp_path 的 Api，测试结束自动还原全局状态。

    `PREFS` / `PREFS_HEALTH` / `APP_DIR` 都是模块级全局，直接改会串到别的用例，
    所以这里存一份原值、finally 还原。
    """
    saved = (app.PREFS, app.PREFS_HEALTH, app.APP_DIR)
    app.APP_DIR = tmp_path
    app.PREFS = tray.load_prefs(tmp_path)
    app.PREFS_HEALTH = tray.prefs_health(tmp_path)
    try:
        yield app.Api()
    finally:
        app.PREFS, app.PREFS_HEALTH, app.APP_DIR = saved


def _wav(tmp_path: Path, name: str = "click.wav") -> Path:
    """写一个最小可用的 wav（内容不重要，只要 is_file() 为真）。"""
    p = tmp_path / name
    p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
                  b"\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00"
                  b"\x02\x00\x10\x00data\x00\x00\x00\x00")
    return p


# ------------------------------------------------- 自定义音色的记录与移除
def test_remember_then_forget_roundtrip(api, tmp_path: Path):
    """记进去、读出来、再删掉，磁盘上也要跟着变。"""
    p = _wav(tmp_path)
    key = tc.custom_timbre(p)
    assert api.remember_timbre(key)["ok"] is True
    assert tray.load_prefs(tmp_path)["custom_timbres"] == [str(p)]

    res = api.forget_timbre(key)
    assert res["ok"] is True
    assert res["custom_timbres"] == []
    assert tray.load_prefs(tmp_path)["custom_timbres"] == []


def test_forget_timbre_keeps_the_audio_file(api, tmp_path: Path):
    """「移除」只摘记录，绝不删用户的音频文件——这是产品承诺，也写进了界面提示。"""
    p = _wav(tmp_path)
    key = tc.custom_timbre(p)
    api.remember_timbre(key)
    api.forget_timbre(key)
    assert p.is_file(), "移除音色不能动用户的文件"


def test_forget_timbre_ignores_builtin(api):
    """内置 6 种不在偏好里，传进来返回 ok=False 且不改任何东西。"""
    before = dict(app.PREFS)
    res = api.forget_timbre("woodblock")
    assert res["ok"] is False
    assert app.PREFS["custom_timbres"] == before["custom_timbres"]


def test_forget_timbre_is_case_insensitive(api, tmp_path: Path):
    """Windows 路径不分大小写，换个写法也得能删掉。"""
    p = _wav(tmp_path, "Drum.WAV")
    api.remember_timbre(tc.custom_timbre(p))
    api.forget_timbre(tc.custom_timbre(str(p).upper()))
    assert app.PREFS["custom_timbres"] == []


def test_forget_missing_entry_is_ok(api):
    """删一个本来就不在列表里的项：不算错，前端不该弹红字。"""
    res = api.forget_timbre(tc.custom_timbre("C:/definitely/not/here.wav"))
    assert res["ok"] is True
    assert res["custom_timbres"] == []


def test_custom_timbre_list_skips_deleted_files(api, tmp_path: Path):
    """列表只回传仍然存在的文件：文件被删了那条就该消失，而不是点了没声音。"""
    a = _wav(tmp_path, "a.wav")
    b = _wav(tmp_path, "b.wav")
    app.PREFS["custom_timbres"] = [str(a), str(b)]
    b.unlink()
    got = app._custom_timbre_list(app.PREFS)
    assert [x["key"] for x in got] == [tc.custom_timbre(a)]
    assert got[0]["name"] == "a.wav"


def test_remember_timbre_caps_at_eight(api, tmp_path: Path):
    """最多留 8 条：下拉框再堆就没法用了。最新的排在最前。"""
    for i in range(10):
        api.remember_timbre(tc.custom_timbre(_wav(tmp_path, f"c{i}.wav")))
    kept = app.PREFS["custom_timbres"]
    assert len(kept) == 8
    assert kept[0].endswith("c9.wav")


# ------------------------------------------------- 试听（本机回环 HTTP 服务）
# tap 测速要跟着音乐点，所以界面必须有播放按钮；播放走的是本机服务而不是 file://。
def test_audio_url_serves_the_file(api, tmp_path: Path):
    """登记的音频能被取回，字节数与磁盘一致。"""
    import urllib.request

    p = _wav(tmp_path)
    res = api.audio_url(str(p))
    assert res["ok"] is True
    body = urllib.request.urlopen(res["url"], timeout=10).read()
    assert body == p.read_bytes()


def test_audio_url_supports_range(api, tmp_path: Path):
    """拖动进度条要 Range：回 206 且 Content-Range 正确。"""
    import urllib.request

    p = _wav(tmp_path)
    url = api.audio_url(str(p))["url"]
    req = urllib.request.Request(url, headers={"Range": "bytes=4-11"})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 206
        assert r.headers["Content-Range"].startswith("bytes 4-11/")
        assert r.read() == p.read_bytes()[4:12]


def test_audio_url_refuses_non_audio(api, tmp_path: Path):
    """不是音频后缀就不给播（别让这个服务变成任意文件读取口子）。"""
    q = tmp_path / "notes.txt"
    q.write_text("hi", encoding="utf-8")
    assert api.audio_url(str(q))["ok"] is False


def test_audio_url_refuses_missing(api, tmp_path: Path):
    """文件已被移动/删除：明确报错，而不是给一个点了没反应的地址。"""
    res = api.audio_url(str(tmp_path / "gone.mp3"))
    assert res["ok"] is False
    assert res["error"]


def test_audio_url_handles_unicode_path(api, tmp_path: Path):
    """中文/空格路径必须能播——这是最常见的真实路径。"""
    import urllib.request

    p = _wav(tmp_path, "中文 名 字.wav")
    res = api.audio_url(str(p))
    assert res["ok"] is True
    assert urllib.request.urlopen(res["url"], timeout=10).read() == p.read_bytes()


def test_preview_server_404_for_unregistered():
    """只服务显式登记过的路径，别的路径一律 404。"""
    import audio_server
    import urllib.error
    import urllib.request

    srv = audio_server.PreviewServer()
    try:
        url = srv.register(_wav_stub())
        assert url
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/etc/passwd", timeout=10)
        assert ei.value.code == 404
    finally:
        srv.stop()


def _wav_stub() -> str:
    """给上面那个用例造一个临时 wav（用 tmp_path 会跟 fixture 搅在一起）。"""
    import tempfile

    d = tempfile.mkdtemp()
    p = Path(d) / "s.wav"
    p.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
                  b"\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00"
                  b"\x02\x00\x10\x00data\x00\x00\x00\x00")
    return str(p)
