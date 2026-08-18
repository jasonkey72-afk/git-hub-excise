"""
테스트용 브라우저 준비
=======================
기본은 사내 환경과 똑같이 Edge 를 띄운다.
CI/컨테이너에서는 아래 환경변수로 이미 떠 있는 브라우저에 붙일 수 있다.

    JOBSCN_TEST_ENGINE    cdp(기본, 드라이버 없음) / selenium
    JOBSCN_TEST_BROWSER   edge(기본) / chrome
    JOBSCN_TEST_ATTACH    127.0.0.1:9222  (이미 실행 중인 브라우저에 연결)
    JOBSCN_TEST_DRIVER    드라이버 실행 파일 경로(자동 탐지가 안 될 때만)
    JOBSCN_TEST_HEADLESS  1 이면 화면 없이 실행
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PAGES = Path(__file__).resolve().parent / "pages"
MAIN_PAGE = "file://" + str(PAGES / "portal.html")


def test_engine() -> str:
    """검사할 엔진. 기본은 드라이버가 필요 없는 cdp."""
    return os.environ.get("JOBSCN_TEST_ENGINE", "cdp").lower()


def make_engine(log=None):
    """
    엔진(CDPActions 또는 WebActions)을 만든다.
    두 엔진은 같은 메서드를 제공하므로 시나리오 실행 검사는 그대로 재사용된다.
    """
    if test_engine() == "cdp":
        from jobscenario.webauto.cdp import CDPActions
        return CDPActions.start(
            browser=os.environ.get("JOBSCN_TEST_BROWSER", "edge"),
            headless=os.environ.get("JOBSCN_TEST_HEADLESS") == "1",
            port=int(os.environ.get("JOBSCN_TEST_PORT", "0") or 0),
            log=log or (lambda m: None))
    from jobscenario.webauto.actions import WebActions
    return WebActions(make_driver(), log=log or (lambda m: None))


def make_driver():
    """테스트용 WebDriver 를 만든다."""
    browser = os.environ.get("JOBSCN_TEST_BROWSER", "edge").lower()
    attach = os.environ.get("JOBSCN_TEST_ATTACH", "")
    driver_path = os.environ.get("JOBSCN_TEST_DRIVER", "")

    if attach:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        opts = Options()
        opts.add_experimental_option("debuggerAddress", attach)
        service = Service(driver_path) if driver_path else Service()
        return webdriver.Chrome(service=service, options=opts)

    from jobscenario.webauto.driver import create_driver
    return create_driver(browser=browser, profile=False,
                         headless=os.environ.get("JOBSCN_TEST_HEADLESS") == "1")


class Report:
    """테스트 결과를 모아 마지막에 요약을 찍는다."""

    def __init__(self, name: str):
        self.name = name
        self.passed = []
        self.failed = []

    def check(self, title: str, fn):
        """fn 이 예외 없이 끝나면 성공."""
        try:
            fn()
            self.passed.append(title)
            print("  PASS  %s" % title)
        except Exception as e:
            self.failed.append(title)
            print("  FAIL  %s -> %s: %s" % (title, type(e).__name__, str(e)[:160]))

    def expect(self, title: str, condition: bool, detail=""):
        """조건이 참이면 성공."""
        (self.passed if condition else self.failed).append(title)
        print("  %s  %s%s" % ("PASS" if condition else "FAIL", title,
                              "" if condition else "  -> %s" % (detail,)))

    def finish(self) -> int:
        print("\n[%s] 성공 %d / 실패 %d" % (self.name, len(self.passed), len(self.failed)))
        return 1 if self.failed else 0


def assert_eq(actual, expected):
    if actual != expected:
        raise AssertionError("기대값 %r != 실제값 %r" % (expected, actual))
