"""
윈도우 탐색 / 종료 (윈도우 전용, 추가 의존성 없음)
====================================================
"이미 열려 있는 브라우저에 연결" 기능을 위해 작업 표시줄에 보이는
Edge/Chrome 창을 찾고, 선택된 창(의 프로세스)을 안전하게 종료한다.
ctypes 로 user32/kernel32 를 직접 불러 쓰므로 pywin32 가 없어도 동작한다.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

BROWSER_EXE = {"msedge.exe": "edge", "chrome.exe": "chrome"}

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4
STILL_ACTIVE = 259
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _process_image_path(pid: int) -> str:
    """관리자 권한 없이 PID로 실행 파일 전체 경로를 얻는다."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(handle)


def list_open_windows() -> list:
    """
    작업 표시줄에 보이는(=최상위, 소유 창 없는) Edge/Chrome 창을 찾아
    [{hwnd, pid, title, browser, exe}, ...] 로 돌려준다. 윈도우가 아니면 빈 목록.
    """
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _process_image_path(pid.value)
        browser = BROWSER_EXE.get(Path(exe).name.lower()) if exe else None
        if browser:
            results.append({"hwnd": hwnd, "pid": pid.value, "title": title,
                            "browser": browser, "exe": exe})
        return True

    user32.EnumWindows(_callback, 0)
    return results


def _pid_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def close_process(pid: int, timeout: float = 12.0, log=None) -> bool:
    """
    프로세스를 먼저 정상 종료(창 닫기) 요청하고, 시간 안에 안 끝나면 강제로 끝낸다.
    브라우저가 '변경 사항이 있습니다' 같은 확인 창을 띄우면 정상 종료가 막힐 수
    있으므로 끝까지 기다리지 않고 강제 종료로 넘어간다.
    """
    if not _pid_alive(pid):
        return True
    try:
        subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True,
                       timeout=5, creationflags=_NO_WINDOW)
    except Exception:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.3)

    if log:
        log("브라우저가 정상적으로 닫히지 않아 강제로 종료합니다.")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                       timeout=5, creationflags=_NO_WINDOW)
    except Exception:
        pass
    time.sleep(0.5)
    return not _pid_alive(pid)


__all__ = ["list_open_windows", "close_process"]
