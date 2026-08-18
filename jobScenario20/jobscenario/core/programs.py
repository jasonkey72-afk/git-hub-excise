"""
사내 프로그램 실행 담당
========================
exe 실행, 문서/폴더 열기, 명령 실행을 한 곳에서 처리한다.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def _split(command: str) -> list:
    """윈도우 경로(역슬래시)를 깨뜨리지 않고 명령을 나눈다."""
    return shlex.split(command, posix=not IS_WINDOWS)


def run_program(path_and_args: str, wait: bool = False, timeout: float = 0) -> str:
    """
    실행 파일을 띄운다. 인자를 함께 적을 수 있다.
        C:\\Program Files\\사내ERP\\erp.exe /user:hong
    """
    text = (path_and_args or "").strip()
    if not text:
        raise ValueError("실행할 프로그램 경로가 비어 있습니다.")

    parts = _split(text)
    exe = Path(parts[0])
    if not exe.exists() and len(parts) == 1:
        raise FileNotFoundError("실행 파일을 찾을 수 없습니다: %s" % parts[0])

    if wait:
        proc = subprocess.run(parts, capture_output=True, text=True,
                              timeout=timeout or None)
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            raise RuntimeError("프로그램이 오류로 끝났습니다(코드 %d). %s"
                               % (proc.returncode, (proc.stderr or "").strip()[:200]))
        return out
    subprocess.Popen(parts, close_fds=True)
    return "실행함"


def open_path(path: str) -> str:
    """문서/폴더를 연결된 프로그램으로 연다(더블클릭과 같은 동작)."""
    target = (path or "").strip()
    if not target:
        raise ValueError("열 파일 또는 폴더 경로가 비어 있습니다.")
    if not Path(target).exists():
        raise FileNotFoundError("경로를 찾을 수 없습니다: %s" % target)

    if IS_WINDOWS:
        os.startfile(target)                       # noqa: S606 (윈도우 표준 방식)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
    return "열기 완료"


def run_command(command: str, timeout: float = 300) -> str:
    """명령을 실행하고 출력 결과를 돌려준다."""
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("실행할 명령이 비어 있습니다.")
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout or None)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise RuntimeError("명령이 오류로 끝났습니다(코드 %d). %s"
                           % (proc.returncode, out[:300]))
    return out[:1000]
