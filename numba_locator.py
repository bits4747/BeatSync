"""numba 的缓存定位器：让打包版（PyInstaller）也能用 JIT 缓存。

为什么需要这个模块
------------------
librosa 1.0 里 `librosa/core/notation.py` 的 `__o_fold` / `__bo_fold` 是
`@jit(cache=True)`。numba 在装饰时就要给这个函数挑一个「缓存目录定位器」，
而内置的四个定位器（`UserProvidedCacheLocator` / `InTreeCacheLocator` /
`UserWideCacheLocator` / `ZipCacheLocator`）都从 `inspect.getfile(py_func)`
拿源码路径，再用 `os.path.exists(py_file)` 判断能不能用。

PyInstaller 冻结进去的 py 模块，`co_filename` 是**相对路径**
（如 `librosa\\core\\notation.py`），`os.path.exists()` 于是按**当前工作目录**
去解析——只有恰好在 `_internal\\` 里启动才碰得到。用户在任意目录双击 exe 时四个
定位器全部落空，numba 抛：

    RuntimeError: cannot cache function '__o_fold':
    no locator available for file 'librosa\\core\\notation.py'

该异常在 `import librosa` 阶段抛出，表现为「点开始处理就失败」「报告里写
失败: cannot cache function ...」，整个程序失去转换能力。**源码运行永远复现不了**
（那里 `co_filename` 是绝对路径），只有打包版会中招。

这个定位器刻意**不看** `co_filename` 是否存在：
  · 缓存目录直接用 `NUMBA_CACHE_DIR`（由 `tempo_converter._setup_numba_cache`
    保证已设为可写位置），再按 `py_file` 的哈希分子目录，避免不同模块的重名文件
    互相覆盖；
  · 子目录名用 `py_file` 字符串本身的 sha1，不用 `os.path.abspath`——后者依赖
    工作目录，会让同一次安装在每个启动目录下建一份缓存；
  · 源码时间戳在冻结版用 `sys.executable` 的 mtime/size（可执行文件变了缓存就该
    失效），源码运行用真实文件，与 numba 内置行为一致。

注册方式：`NUMBA_CACHE_LOCATOR_CLASSES=BeatSyncCacheLocator` 不行（没带点会被
当成 numba 模块内的全局名），所以 `_setup_numba_cache` 写的是
`NUMBA_CACHE_LOCATOR_CLASSES=numba_locator.BeatSyncCacheLocator`
（numba 认「带点的视为完整模块路径」）。**必须在 import numba 之前设置**，
numba 在 `numba.core.config` 里一次性读进 `config.CACHE_LOCATOR_CLASSES`。
"""

from __future__ import annotations

import hashlib
import os
import sys


class BeatSyncCacheLocator:
    """不依赖 ``co_filename`` 是否可解析的 numba 缓存定位器。"""

    def __init__(self, py_func, py_file):
        self._py_file = str(py_file)
        self._lineno = py_func.__code__.co_firstlineno
        base = os.environ.get("NUMBA_CACHE_DIR") or ""
        if not base:
            # 没有可写的缓存根目录时宁可让它抛错由调用方兜底，也不要往
            # 只读的程序目录里写东西。
            raise OSError("NUMBA_CACHE_DIR is not set")
        # 用 py_file 字符串本身做哈希（不用 abspath：那会随工作目录变化）
        digest = hashlib.sha1(self._py_file.encode("utf-8", "replace")).hexdigest()[:16]
        parent = os.path.basename(os.path.dirname(self._py_file)) or "mod"
        self._cache_path = os.path.join(base, parent + "_" + digest)

    # --- numba 需要的四个方法 -------------------------------------------------
    def get_cache_path(self):
        return self._cache_path

    def get_source_stamp(self):
        try:
            if getattr(sys, "frozen", False):
                st = os.stat(sys.executable)
            else:
                st = os.stat(self._py_file)
            return st.st_mtime, st.st_size
        except OSError:
            # 源码路径不存在时给个稳定值，别让缓存键随环境漂移
            return 0.0, 0

    def get_disambiguator(self):
        return str(self._lineno)

    def ensure_cache_path(self):
        os.makedirs(self._cache_path, exist_ok=True)

    @classmethod
    def from_function(cls, py_func, py_file):
        """永远返回实例：不检查文件是否存在，这正是本模块存在的理由。"""
        try:
            return cls(py_func, py_file)
        except OSError:
            # 没有缓存根目录：返回 None 让 numba 继续试别的定位器
            return None
