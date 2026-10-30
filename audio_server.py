"""给界面的试听播放器用的本机 HTTP 服务。

为什么不直接给前端 `file:///...` 让 `<audio>` 自己读：
  * 界面本身是 `file://` 加载的，再喂一个 `file://` 音频，WebView2 的跨源策略
    在部分版本上会拒绝（表现为点播放毫无反应、控制台只有一句 Not allowed）；
  * 中文/空格路径在 `file://` 里要做百分号编码，前后端任一处漏了就是 404；
  * 没有 Range 支持就不能拖进度条，而试听恰恰最需要「拖到变拍那一段」。
所以这里起一个只监听 127.0.0.1 的小服务，并且**只服务显式登记过的绝对路径**
（前端要播哪首，得先调 `Api.audio_url()` 登记），避免变成任意文件读取口子。
"""

from __future__ import annotations

import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# 试听只认这几种，别的一律不登记（也顺手挡住 .exe/.json 之类被当成音频播）
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac",
             ".wma", ".aif", ".aiff", ".opus", ".webm"}

# 单个请求最多回多少字节。拖动进度条时前端会要 Range，这里只是给整段下载设个上限，
# 避免一次把 200MB 的 wav 全读进内存。
_MAX_CHUNK = 1 << 20          # 1 MiB


class _Handler(BaseHTTPRequestHandler):
    server_version = "BeatSyncPreview/1"
    protocol_version = "HTTP/1.1"

    # 关掉默认的 stderr 日志：这个服务只为试听存在，每次拖动都打一行会把
    # 控制台刷满（用户明确要求不要输出日志）。
    def log_message(self, fmt, *args):      # noqa: A003 - 覆盖父类
        pass

    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve(head_only=False)

    def _serve(self, head_only: bool):
        registry = self.server.registry            # type: ignore[attr-defined]
        key = unquote(urlparse(self.path).path.lstrip("/"))
        target = registry.get(key)
        if target is None:
            self._plain(404, "not registered")
            return
        try:
            size = os.path.getsize(target)
        except OSError:
            self._plain(404, "gone")
            return

        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        partial = False
        if rng:
            # 只认 `bytes=a-b` / `bytes=a-`，别的一律忽略并回整段。
            spec = rng.split("=", 1)[-1].split(",")[0].strip()
            try:
                a, _, b = spec.partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                if start < 0 or end < start:
                    raise ValueError
                end = min(end, size - 1)
                partial = True
            except ValueError:
                start, end, partial = 0, size - 1, False

        length = max(0, end - start + 1)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head_only:
            return

        try:
            with open(target, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    buf = f.read(min(_MAX_CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            # 用户拖了进度条/切了歌，旧连接被前端主动断开，属正常
            pass
        except OSError:
            pass

    def _plain(self, code: int, msg: str):
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class PreviewServer:
    """懒启动的本机试听服务。没有试听需求时一个线程都不开。"""

    def __init__(self):
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()
        self.port = 0

    def start(self) -> bool:
        with self._lock:
            if self._httpd is not None:
                return True
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            except OSError:
                return False
            httpd.daemon_threads = True
            httpd.registry = {}                # type: ignore[attr-defined]
            self._httpd = httpd
            self.port = httpd.server_address[1]
            self._thread = threading.Thread(
                target=httpd.serve_forever, name="preview-http", daemon=True)
            self._thread.start()
            return True

    def register(self, path) -> str:
        """登记一个文件并返回它在本服务里的 URL；不可用时返回空串。

        同一个文件反复点播放只占一个键（用绝对路径当键），不会无限增长。
        """
        try:
            p = Path(path)
            if p.suffix.lower() not in AUDIO_EXT or not p.is_file():
                return ""
        except OSError:
            return ""
        if not self.start():
            return ""
        key = str(p.resolve())
        self._httpd.registry[key] = key      # type: ignore[attr-defined]
        # 前端按这个相对路径请求；服务端用 unquote 还原成同一个绝对路径当键。
        from urllib.parse import quote
        return f"http://127.0.0.1:{self.port}/{quote(key, safe='')}"

    def stop(self):
        with self._lock:
            httpd, self._httpd = self._httpd, None
            self._thread = None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except OSError:
                pass
