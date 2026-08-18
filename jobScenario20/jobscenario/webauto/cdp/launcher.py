"""
브라우저 실행 / 접속 (드라이버 없음)
=====================================
설치된 Edge(또는 Chrome)를 디버깅 포트와 함께 직접 실행하고,
그 포트로 CDP 연결 주소를 알아낸다.

  · msedgedriver.exe / chromedriver.exe 를 쓰지 않는다 -> 버전 문제가 없다
  · launch() : 지정한 프로필로 새 브라우저를 띄운다(주로 테스트에서 사용)
  · attach_existing() : 사용자가 이미 열어 둔 창을 골라 그 창에 연결한다
    ('브라우저 선택' 기능의 실제 동작 -- 디버깅 포트가 없는 보통 창에는 CDP 로
    바로 붙을 수 없어서, 그 창을 잠깐 닫았다가 디버깅 포트를 켜서 다시 연다)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from .client import CDPClient, CDPError
from .windows import close_process

# 설치 경로를 못 찾을 때 확인할 표준 위치
EXE_CANDIDATES = {
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ],
}

APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s"
EXE_NAME = {"edge": "msedge.exe", "chrome": "chrome.exe"}

# 리눅스/맥에서 개발·시험할 때 쓰는 경로 (사내 배포는 윈도우)
POSIX_CANDIDATES = {
    "edge": ["microsoft-edge", "microsoft-edge-stable"],
    "chrome": ["google-chrome", "chromium", "chromium-browser"],
}


def find_browser(browser: str) -> str:
    """브라우저 실행 파일 경로를 찾는다. 못 찾으면 빈 문자열."""
    override = os.environ.get("JOBSCN_BROWSER_PATH", "")
    if override and Path(override).exists():
        return override

    if os.name == "nt":
        try:
            import winreg
            name = EXE_NAME.get(browser, "msedge.exe")
            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(root, APP_PATHS_KEY % name) as k:
                        path = str(winreg.QueryValueEx(k, "")[0]).strip('"')
                        if Path(path).exists():
                            return path
                except OSError:
                    continue
        except Exception:
            pass
        for path in EXE_CANDIDATES.get(browser, []):
            if Path(path).exists():
                return path
        return ""

    import shutil
    for name in POSIX_CANDIDATES.get(browser, []):
        found = shutil.which(name)
        if found:
            return found
    return ""


def free_port() -> int:
    """비어 있는 포트 하나를 고른다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_json(url: str, timeout: float = 2.0):
    """localhost 요청이 사내 프록시로 새어 나가지 않도록 프록시를 끄고 호출한다."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_endpoint(port: int, timeout: float = 40.0) -> dict:
    """브라우저가 디버깅 포트를 열 때까지 기다린 뒤 접속 정보를 돌려준다."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            return _http_json("http://127.0.0.1:%d/json/version" % port)
        except Exception as e:
            last = e
            time.sleep(0.3)
    raise CDPError("브라우저가 디버깅 포트(%d)를 열지 않았습니다: %s" % (port, last))


# 우리가 이 프로필로 마지막에 띄운 브라우저의 포트를 적어 두는 표시 파일 이름.
# (Edge 는 --remote-debugging-port 를 직접 지정해서 띄우면 셀레니엄 등이 쓰는
#  DevToolsActivePort 파일을 안 만들어 준다 -- 확인해 보니 실제로 안 생긴다.
#  그래서 우리가 직접 남겨 둔다.)
_PORT_MARKER_NAME = ".jobscenario_debug_port"


def _port_marker(user_data_dir: str) -> Path:
    return Path(user_data_dir) / _PORT_MARKER_NAME


def _running_debug_port(user_data_dir: str) -> int:
    """
    이 프로필로 이미 떠 있는 브라우저가 있으면 그 디버깅 포트를 돌려준다(없으면 0).

    같은 프로필로 이미 브라우저가 열려 있는데 모르고 또 새로 띄우려 하면,
    새 프로세스는 그 기존 브라우저에 명령만 넘기고 조용히 끝나 버려서
    '디버깅 포트를 열지 않았다' 는 오류로 이어진다(실제로 겪은 문제).
    새로 띄우기 전에 여기서 먼저 살아있는 기존 브라우저를 찾아 그냥 거기에 붙는다.
    (표시 파일이 남아 있어도 가리키는 포트가 죽어 있으면 그냥 0 을 돌려주고
    평소처럼 새로 띄운다 -- 비정상 종료로 파일이 안 지워졌어도 문제없다.)
    """
    if not user_data_dir:
        return 0
    try:
        port = int(_port_marker(user_data_dir).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    try:
        wait_endpoint(port, timeout=1.5)
    except CDPError:
        return 0
    return port


def _remember_port(user_data_dir: str, port: int) -> None:
    if not user_data_dir:
        return
    try:
        _port_marker(user_data_dir).write_text(str(port), encoding="utf-8")
    except OSError:
        pass


def launch(browser: str = "edge", user_data_dir: str = "", headless: bool = False,
           port: int = 0, extra_args=None, log=None, exe_path: str = None,
           open_blank: bool = True):
    """
    브라우저를 실행하고 (CDPClient, Popen, port) 를 돌려준다.
    port 를 지정하면 이미 그 포트로 열려 있는 브라우저에 그냥 연결한다.
    exe_path 를 주면 find_browser() 로 다시 찾지 않고 그 실행 파일을 그대로 쓴다
    (attach_existing() 처럼 이미 어떤 창인지 알고 있는 경우에 쓴다).
    """
    browser = (browser or "edge").lower()

    # 이미 열려 있는 브라우저에 붙는 경우
    if port:
        info = wait_endpoint(port, timeout=5)
        if log:
            log("이미 열려 있는 브라우저에 연결했습니다. (%s)" % info.get("Browser", ""))
        return CDPClient(info["webSocketDebuggerUrl"]), None, port

    # 같은 프로필로 이미 떠 있는 브라우저가 있으면 새로 띄우지 않고 그걸 쓴다
    # (활성화되어 있는 브라우저로 자동 전환).
    existing = _running_debug_port(user_data_dir)
    if existing:
        info = wait_endpoint(existing, timeout=5)
        if log:
            log("이미 열려 있는 브라우저(같은 프로필)에 연결했습니다. (%s)"
                % info.get("Browser", ""))
        return CDPClient(info["webSocketDebuggerUrl"]), None, existing

    exe = exe_path or find_browser(browser)
    if not exe:
        raise CDPError(
            "%s 를 찾을 수 없습니다.\n"
            "설치되어 있는지 확인해 주세요. 설치 경로가 특이한 경우에는\n"
            "환경변수 JOBSCN_BROWSER_PATH 에 실행 파일 경로를 지정할 수 있습니다."
            % ("Microsoft Edge" if browser == "edge" else "Google Chrome"))

    port = free_port()
    args = [
        exe,
        "--remote-debugging-port=%d" % port,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-notifications",
        "--disable-features=Translate",
        # 사용자가 쓰고 있는 창과 섞이지 않게 별도 인스턴스로 띄운다
        "--remote-allow-origins=*",
    ]
    if user_data_dir:
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        args.append("--user-data-dir=%s" % user_data_dir)
    if headless:
        args.append("--headless=new")
    else:
        args.append("--start-maximized")
    if os.name != "nt":
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    if extra_args:
        args += list(extra_args)
    if open_blank:
        args.append("about:blank")

    if log:
        log("브라우저를 실행합니다 (드라이버 없이 직접 연결): %s" % Path(exe).name)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=creationflags)

    try:
        info = wait_endpoint(port)
    except CDPError:
        # 디버깅 포트가 끝내 열리지 않았다(대개 같은 프로필을 쓰는 좀비 프로세스가
        # 이미 있어서 새 프로세스가 그쪽으로 넘기고 조용히 종료된 경우다).
        # 여기서 정리하지 않으면 방금 띄운 프로세스가 그대로 남아 다음 실행도
        # 계속 실패하게 만든다(프로필이 계속 잠긴 채로 쌓인다).
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        raise

    if log:
        log("연결됨: %s" % info.get("Browser", ""))
    _remember_port(user_data_dir, port)
    return CDPClient(info["webSocketDebuggerUrl"]), proc, port


def attach_existing(window: dict, log=None):
    """
    사용자가 고른, 이미 열려 있는 브라우저 창에 연결한다.
    일반적으로 연 브라우저는 디버깅 포트가 없어 CDP 로 바로 붙을 수 없다.
    그래서 그 창(같은 프로필의 다른 창 포함, 보통 같은 프로세스가 다 들고 있다)을
    닫았다가 디버깅 포트를 켜서 즉시 다시 띄운다. '마지막 세션 복원' 옵션을
    함께 주므로 열려 있던 탭은 대개 그대로 돌아온다(사용자에게 미리 알려야 한다 --
    창이 실제로 한 번 닫혔다 열리므로 다른 탭의 저장하지 않은 내용은 사라질 수 있다).
    """
    pid = window.get("pid")
    if not pid or not close_process(pid, log=log):
        raise CDPError("선택한 브라우저를 닫지 못했습니다. 직접 닫은 뒤 다시 시도해 주세요.")
    time.sleep(0.5)
    return launch(window.get("browser", "edge"), user_data_dir="", port=0, log=log,
                  extra_args=["--restore-last-session"], exe_path=window.get("exe"),
                  open_blank=False)
