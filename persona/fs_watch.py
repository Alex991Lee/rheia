# -*- coding: utf-8 -*-
# L1-220：persona/fs_watch.py —— 等文件系统事件，不靠 sleep / timeout。
r"""persona/fs_watch.py —— 等文件系统事件，不靠 sleep / timeout。"""









from __future__ import annotations

import os
import select
import sys
from typing import Iterable


# L22-36：_watchDirs — 把文件/目录路径归一成“要盯的目录”列表（去重、保序）。
def _watchDirs(paths: Iterable[str]) -> list[str]:
    r"""把文件/目录路径归一成“要盯的目录”列表（去重、保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = os.path.abspath(str(raw))
        d = p if os.path.isdir(p) else os.path.dirname(p)
        if not d or not os.path.isdir(d):
            continue
        key = os.path.normcase(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _OPEN_EXISTING = 3
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OVERLAPPED = 0x40000000
    _FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
    _FILE_NOTIFY_CHANGE_DIR_NAME = 0x00000002
    _FILE_NOTIFY_CHANGE_ATTRIBUTES = 0x00000004
    _FILE_NOTIFY_CHANGE_SIZE = 0x00000008
    _FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
    _FILE_NOTIFY_CHANGE_CREATION = 0x00000040
    _INFINITE = 0xFFFFFFFF
    _WAIT_OBJECT_0 = 0x00000000
    _WAIT_TIMEOUT = 0x00000102
    _ERROR_IO_PENDING = 997

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p),
            ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.ReadDirectoryChangesW.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.BOOL,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED), wintypes.LPVOID,
    ]
    _k32.ReadDirectoryChangesW.restype = wintypes.BOOL
    _k32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD,
    ]
    _k32.WaitForMultipleObjects.restype = wintypes.DWORD
    _k32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED), ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
    ]
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    def waitForAnyChange(paths: Iterable[str]) -> str:
        dirs = _watchDirs(paths)
        if not dirs:
            raise FileNotFoundError("fs_watch: 没有可盯的目录")
        handles: list[int] = []
        events: list[int] = []
        overlapped: list[_OVERLAPPED] = []
        buffers: list[ctypes.Array] = []
        try:
            for d in dirs:
                h = _k32.CreateFileW(
                    d, _FILE_LIST_DIRECTORY,
                    _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
                    None, _OPEN_EXISTING,
                    _FILE_FLAG_BACKUP_SEMANTICS | _FILE_FLAG_OVERLAPPED, None,
                )
                if not h or h == wintypes.HANDLE(-1).value:
                    raise OSError(ctypes.get_last_error(), "CreateFileW 失败: %s" % d)
                ev = _k32.CreateEventW(None, True, False, None)
                if not ev:
                    _k32.CloseHandle(h)
                    raise OSError(ctypes.get_last_error(), "CreateEventW 失败")
                ov = _OVERLAPPED()
                ov.hEvent = ev
                buf = ctypes.create_string_buffer(64 * 1024)
                got = wintypes.DWORD(0)
                ok = _k32.ReadDirectoryChangesW(
                    h, buf, len(buf), True,
                    _FILE_NOTIFY_CHANGE_FILE_NAME | _FILE_NOTIFY_CHANGE_DIR_NAME |
                    _FILE_NOTIFY_CHANGE_ATTRIBUTES | _FILE_NOTIFY_CHANGE_SIZE |
                    _FILE_NOTIFY_CHANGE_LAST_WRITE | _FILE_NOTIFY_CHANGE_CREATION,
                    ctypes.byref(got), ctypes.byref(ov), None,
                )
                if not ok and ctypes.get_last_error() != _ERROR_IO_PENDING:
                    _k32.CloseHandle(ev)
                    _k32.CloseHandle(h)
                    raise OSError(ctypes.get_last_error(), "ReadDirectoryChangesW 失败: %s" % d)
                handles.append(h)
                events.append(ev)
                overlapped.append(ov)
                buffers.append(buf)
            arr = (wintypes.HANDLE * len(events))(*events)
            rc = _k32.WaitForMultipleObjects(len(events), arr, False, _INFINITE)
            if rc == _WAIT_TIMEOUT:
                raise TimeoutError("fs_watch: WaitForMultipleObjects 返回 WAIT_TIMEOUT")
            if rc < _WAIT_OBJECT_0 or rc >= _WAIT_OBJECT_0 + len(events):
                raise OSError(ctypes.get_last_error(), "WaitForMultipleObjects 失败")
            idx = int(rc - _WAIT_OBJECT_0)
            got = wintypes.DWORD(0)
            _k32.GetOverlappedResult(handles[idx], ctypes.byref(overlapped[idx]), ctypes.byref(got), False)
            return dirs[idx]
        finally:
            for h, ev in zip(handles, events):
                try:
                    _k32.CancelIoEx(h, None)
                except Exception:
                    pass
                try:
                    _k32.CloseHandle(ev)
                except Exception:
                    pass
                try:
                    _k32.CloseHandle(h)
                except Exception:
                    pass

else:
    import ctypes
    import ctypes.util

    _IN_MODIFY = 0x00000002
    _IN_ATTRIB = 0x00000004
    _IN_CLOSE_WRITE = 0x00000008
    _IN_MOVED_FROM = 0x00000040
    _IN_MOVED_TO = 0x00000080
    _IN_CREATE = 0x00000100
    _IN_DELETE = 0x00000200
    _IN_DELETE_SELF = 0x00000400

    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    _libc.inotify_init1.argtypes = [ctypes.c_int]
    _libc.inotify_init1.restype = ctypes.c_int
    _libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    _libc.inotify_add_watch.restype = ctypes.c_int
    _libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
    _libc.inotify_rm_watch.restype = ctypes.c_int

    def waitForAnyChange(paths: Iterable[str]) -> str:
        dirs = _watchDirs(paths)
        if not dirs:
            raise FileNotFoundError("fs_watch: 没有可盯的目录")
        fd = _libc.inotify_init1(getattr(os, "O_CLOEXEC", 0))
        if fd < 0:
            raise OSError(ctypes.get_errno(), "inotify_init1 失败")
        wds: list[int] = []
        try:
            mask = (_IN_MODIFY | _IN_ATTRIB | _IN_CLOSE_WRITE | _IN_MOVED_FROM |
                    _IN_MOVED_TO | _IN_CREATE | _IN_DELETE | _IN_DELETE_SELF)
            for d in dirs:
                wd = _libc.inotify_add_watch(fd, d.encode("utf-8", "replace"), mask)
                if wd < 0:
                    raise OSError(ctypes.get_errno(), "inotify_add_watch 失败: %s" % d)
                wds.append(wd)
            _poll = getattr(select, "poll", None)
            if _poll is None:
                raise NotImplementedError("fs_watch: 当前平台没有 select.poll")
            p = _poll()
            p.register(fd, getattr(select, "POLLIN", 1))
            p.poll()
            try:
                os.read(fd, 64 * 1024)
            except OSError:
                pass
            return dirs[0]
        finally:
            for wd in wds:
                try:
                    _libc.inotify_rm_watch(fd, wd)
                except Exception:
                    pass
            try:
                os.close(fd)
            except Exception:
                pass
