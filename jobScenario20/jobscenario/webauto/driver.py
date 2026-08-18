"""
브라우저(Edge / Chrome) 실행 및 연결 담당 모듈.

사내 환경을 고려한 설계
  - 전용 프로필 폴더를 사용한다 -> 한 번 로그인해 두면 다음 실행 때도 유지된다.
  - 드라이버 파일(msedgedriver.exe)이 exe 옆에 있으면 그것을 먼저 쓴다.
    (사내망에서 자동 다운로드가 막혀 있어도 동작하게 하기 위함)
  - 이미 띄워 둔 브라우저에 붙는 방식(디버깅 포트)도 지원한다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService

BROWSERS = ("edge", "chrome")

DRIVER_FILES = {
    "edge": "msedgedriver.exe",
    "chrome": "chromedriver.exe",
}

# 브라우저 버전이 적혀 있는 레지스트리 위치 (윈도우)
VERSION_KEYS = {
    "edge": r"Software\Microsoft\Edge\BLBeacon",
    "chrome": r"Software\Google\Chrome\BLBeacon",
}

DRIVER_DOWNLOAD = {
    "edge": "https://developer.microsoft.com/microsoft-edge/tools/webdriver/",
    "chrome": "https://googlechromelabs.github.io/chrome-for-testing/",
}

BROWSER_NAME = {"edge": "Microsoft Edge", "chrome": "Google Chrome"}


def app_dir() -> Path:
    """exe 로 배포됐을 때는 exe 가 있는 폴더, 개발 중에는 프로젝트 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """시나리오/설정/프로필을 저장할 사용자 폴더."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "jobScenario"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _local_driver(browser: str) -> str | None:
    """exe 옆에 드라이버 파일이 있으면 그 경로를 돌려준다."""
    name = DRIVER_FILES.get(browser)
    if not name:
        return None
    for folder in (app_dir(), app_dir() / "driver", data_dir()):
        p = folder / name
        if p.exists():
            return str(p)
    return None


def _major(version: str) -> str:
    """'141.0.7390.54' -> '141'"""
    m = re.match(r"\s*(\d+)", version or "")
    return m.group(1) if m else ""


def browser_version(browser: str) -> str:
    """설치된 브라우저 버전을 읽는다. 알 수 없으면 빈 문자열."""
    key = VERSION_KEYS.get(browser)
    if not key or os.name != "nt":
        return ""
    try:
        import winreg
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, key) as k:
                    return str(winreg.QueryValueEx(k, "version")[0])
            except OSError:
                continue
    except Exception:
        pass
    return ""


def driver_version(path: str) -> str:
    """드라이버 실행 파일의 버전을 읽는다. 알 수 없으면 빈 문자열."""
    if not path or not Path(path).exists():
        return ""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True,
                             timeout=15).stdout
    except Exception:
        return ""
    m = re.search(r"(\d+\.\d+\.\d+\.\d+)", out or "")
    return m.group(1) if m else ""


def version_report(browser: str) -> dict:
    """브라우저와 로컬 드라이버의 버전을 모아서 돌려준다."""
    drv = _local_driver(browser)
    bver = browser_version(browser)
    dver = driver_version(drv) if drv else ""
    return {
        "browser": browser,
        "browser_version": bver,
        "driver_path": drv or "",
        "driver_version": dver,
        # 둘 다 알아냈을 때만 판정한다(모르면 굳이 경고하지 않는다)
        "mismatch": bool(bver and dver and _major(bver) != _major(dver)),
    }


def version_hint(browser: str) -> str:
    """버전이 어긋나 있으면 무엇을 어떻게 하면 되는지 한 줄로 알려준다."""
    info = version_report(browser)
    if not info["mismatch"]:
        return ""
    return ("%s %s 인데 드라이버는 %s 입니다. 주 버전(%s vs %s)이 달라 실행이 실패할 수 있습니다."
            % (BROWSER_NAME.get(browser, browser), info["browser_version"],
               info["driver_version"], _major(info["browser_version"]),
               _major(info["driver_version"])))


def diagnose(browser: str, error: Exception = None) -> str:
    """브라우저 실행이 실패했을 때 사용자가 바로 조치할 수 있는 안내문을 만든다."""
    info = version_report(browser)
    name = BROWSER_NAME.get(browser, browser)
    lines = ["브라우저를 실행하지 못했습니다."]
    if error is not None:
        lines += ["", str(error)[:300]]

    lines += ["", "■ 현재 상태",
              "  · %s : %s" % (name, info["browser_version"] or "버전을 확인하지 못했습니다"),
              "  · 드라이버 : %s" % (
                  "%s (%s)" % (info["driver_version"] or "버전 확인 불가", info["driver_path"])
                  if info["driver_path"] else "이 폴더에 없음 (자동 내려받기를 시도합니다)")]

    lines.append("")
    lines.append("■ 조치 방법")
    if info["mismatch"]:
        need = _major(info["browser_version"])
        lines += ["  버전이 어긋났습니다. %s 버전대의 드라이버를 새로 받아" % need,
                  "  아래 파일을 덮어써 주세요.",
                  "    %s" % info["driver_path"],
                  "    받는 곳: %s" % DRIVER_DOWNLOAD.get(browser, "")]
    elif not info["driver_path"]:
        lines += ["  · %s 가 설치되어 있는지 확인해 주세요." % name,
                  "  · 사내망에서 자동 내려받기가 막혀 있다면, 설치된 %s 와" % name,
                  "    같은 버전(%s)의 %s 를"
                  % (_major(info["browser_version"]) or "동일", DRIVER_FILES.get(browser, "드라이버")),
                  "    이 프로그램과 같은 폴더에 두세요.",
                  "    받는 곳: %s" % DRIVER_DOWNLOAD.get(browser, "")]
    else:
        lines += ["  · %s 가 실행 중이면 모두 닫고 다시 시도해 주세요." % name,
                  "  · 그래도 안 되면 드라이버를 최신으로 다시 받아 주세요.",
                  "    받는 곳: %s" % DRIVER_DOWNLOAD.get(browser, "")]
    return "\n".join(lines)


def create_driver(browser: str = "edge", profile: bool = True,
                  download_dir: str = "", attach_port: int = 0,
                  headless: bool = False, log=None):
    """
    브라우저를 띄우고 WebDriver 를 돌려준다.

      browser      : "edge" 또는 "chrome"
      profile      : True 면 전용 프로필 사용(로그인 상태 유지)
      download_dir : 파일 내려받기 폴더
      attach_port  : 0 이 아니면 이미 열려 있는 브라우저에 연결
      headless     : 화면 없이 실행(사내 사이트는 보통 False 권장)
      log          : 안내 메시지를 받을 함수
    """
    browser = (browser or "edge").lower()
    if browser not in BROWSERS:
        browser = "edge"

    # 버전이 어긋나 있으면 실패하기 전에 미리 알려준다
    if log:
        hint = version_hint(browser)
        if hint:
            log("주의: " + hint)

    is_edge = browser == "edge"
    options = EdgeOptions() if is_edge else ChromeOptions()

    if attach_port:
        # 이미 실행 중인 브라우저에 붙는다(로그인/보안 프로그램이 걸린 사이트에 유용)
        options.add_experimental_option("debuggerAddress", "127.0.0.1:%d" % int(attach_port))
    else:
        if profile:
            prof = data_dir() / ("edge_profile" if is_edge else "chrome_profile")
            prof.mkdir(parents=True, exist_ok=True)
            options.add_argument("--user-data-dir=%s" % prof)
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        prefs = {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
        }
        if download_dir:
            Path(download_dir).mkdir(parents=True, exist_ok=True)
            prefs["download.default_directory"] = str(download_dir)
            prefs["download.prompt_for_download"] = False
        options.add_experimental_option("prefs", prefs)

    driver_path = _local_driver(browser)
    if is_edge:
        service = EdgeService(executable_path=driver_path) if driver_path else EdgeService()
        driver = webdriver.Edge(service=service, options=options)
    else:
        service = ChromeService(executable_path=driver_path) if driver_path else ChromeService()
        driver = webdriver.Chrome(service=service, options=options)

    driver.set_page_load_timeout(120)
    return driver


def quit_driver(driver) -> None:
    """예외를 삼키며 안전하게 브라우저를 닫는다."""
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass
