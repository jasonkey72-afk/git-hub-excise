"""
CDPActions - 드라이버 없이 동작하는 자동화 엔진
================================================
selenium 판 WebActions 와 **같은 메서드 이름/인자**를 제공하므로
실행 엔진(runner)은 어느 쪽을 쓰는지 몰라도 된다.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..locator import parse_target, Target, ElementNotFound
from .. import picker as picker_mod
from .client import CDPError, CDPClosed
from .launcher import launch, attach_existing
from .page import CDPBrowser, CDPElement

HIGHLIGHT_JS = """
function () {
  this.scrollIntoView({block: 'center', inline: 'center'});
  var old = this.style.outline, oldBg = this.style.backgroundColor;
  var el = this;
  el.style.outline = '3px solid #f97316';
  el.style.backgroundColor = 'rgba(249,115,22,.20)';
  setTimeout(function () {
    el.style.outline = old; el.style.backgroundColor = oldBg;
  }, 1600);
  return true;
}
"""


class CDPActions:
    """브라우저 하나에 대한 동작 묶음(selenium 판과 동일한 사용법)."""

    engine_name = "cdp"

    def __init__(self, browser: CDPBrowser, timeout: float = 15.0, log=None):
        self.browser = browser
        self.timeout = timeout
        self.log = log or (lambda msg: None)

    # -- 만들기 / 끝내기 ---------------------------------------------------

    @classmethod
    def start(cls, browser: str = "edge", user_data_dir: str = "",
              download_dir: str = "", headless: bool = False, port: int = 0,
              timeout: float = 15.0, log=None) -> "CDPActions":
        client, proc, used_port = launch(browser, user_data_dir=user_data_dir,
                                        headless=headless, port=port, log=log)
        return cls._from_connection(client, proc, used_port, download_dir,
                                    timeout=timeout, log=log)

    @classmethod
    def attach(cls, window: dict, download_dir: str = "", timeout: float = 15.0,
              log=None) -> "CDPActions":
        """사용자가 화면에서 고른, 이미 열려 있는 브라우저 창에 연결한다."""
        client, proc, used_port = attach_existing(window, log=log)
        return cls._from_connection(client, proc, used_port, download_dir,
                                    timeout=timeout, log=log)

    @classmethod
    def _from_connection(cls, client, proc, port, download_dir, timeout, log):
        try:
            browser_obj = CDPBrowser(client, proc, port, download_dir, log=log)
        except Exception:
            # 탭 붙이기 등 초기화 단계에서 실패하면 방금 띄운 프로세스가 그대로
            # 남아 프로필을 계속 잠근 채 좀비로 남는다 -> 다음 실행도 실패시킨다.
            client.close()
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            raise
        return cls(browser_obj, timeout=timeout, log=log)

    @property
    def page(self):
        if self.browser.page is None:
            self.browser.switch_tab(-1)
        return self.browser.page

    @property
    def alive(self) -> bool:
        return self.browser.alive

    def quit(self):
        self.browser.quit()

    # -- 상태 읽기(엔진 공통 인터페이스) -----------------------------------

    def title(self) -> str:
        return self.page.title

    def current_url(self) -> str:
        return self.page.url

    def page_source(self) -> str:
        return self.page.page_source()

    def save_screenshot(self, path: str) -> str:
        return self.page.screenshot(path)

    # -- 탐색 --------------------------------------------------------------

    def find(self, target, timeout=None, visible_only: bool = True) -> CDPElement:
        spec = target if isinstance(target, dict) else None
        if spec is None:
            spec = (target if isinstance(target, Target) else parse_target(target)).to_dict()
        return self.page.find(spec, self.timeout if timeout is None else timeout,
                              visible_only, log=self.log)

    def highlight(self, el: CDPElement):
        try:
            self.page.call_on(el, HIGHLIGHT_JS)
        except CDPError:
            pass

    def element_label(self, el: CDPElement) -> str:
        info = el.info() or {}
        return "<%s> %s" % (info.get("tag", "?"),
                            (info.get("text") or info.get("value") or "")[:30])

    # -- 웹 동작 -----------------------------------------------------------

    def open_url(self, url: str, timeout: float = 60.0):
        if not url:
            raise ValueError("주소(URL)가 비어 있습니다.")
        if "://" not in url:
            url = "https://" + url
        self.page.navigate(url, timeout)

    def wait_ready(self, timeout: float = 30.0):
        return self.page.wait_ready(timeout)

    def click(self, target, timeout=None, new_tab_wait: bool = True):
        before = len(self.browser.page_targets())
        el = self.find(target, timeout)
        how = self.page.click(el)
        if how.startswith("js("):
            self.log("  · 다른 요소에 가려져 있어 스크립트로 클릭했습니다.")
        if new_tab_wait:
            self.browser.follow_new_tab(before)
        return True

    def double_click(self, target, timeout=None):
        el = self.find(target, timeout)
        self.page.click(el)
        self.page.click(el)
        return True

    def hover(self, target, timeout=None):
        self.page.hover(self.find(target, timeout))
        time.sleep(0.3)
        return True

    def input_text(self, target, text: str, timeout=None, clear: bool = True,
                   enter: bool = False):
        el = self.find(target, timeout)
        self.page.type_text(el, text, clear=clear)
        if enter:
            self.page.press_key("enter")
        return True

    def select_option(self, target, value: str, timeout=None):
        el = self.find(target, timeout)
        result = self.page.select_option(el, value)
        if result == "not-select":
            # 흔히 쓰는 '가짜 콤보박스': 눌러서 펼친 뒤 항목을 글자로 찾는다
            self.page.click(el)
            time.sleep(0.3)
            return self.click("text=%s" % value, timeout, new_tab_wait=False)
        if result == "no-option":
            raise ElementNotFound("목록에서 '%s' 항목을 찾지 못했습니다." % value)
        return True

    def set_checkbox(self, target, checked: bool = True, timeout=None):
        return self.page.set_checkbox(self.find(target, timeout), checked)

    def press_key(self, key: str, target=None, timeout=None):
        if target is not None and not self._is_empty(target):
            el = self.find(target, timeout)
            self.page.call_on(el, "function(){ this.focus(); return true; }")
        return self.page.press_key(key)

    def upload_file(self, target, path: str, timeout=None):
        el = self.find(target, timeout, visible_only=False)
        return self.page.upload(el, [str(Path(path))])

    def wait_element(self, target, timeout=None):
        self.find(target, timeout)
        return True

    def wait_text(self, text: str, timeout: float = 30.0):
        end = time.time() + timeout
        needle = json.dumps(text, ensure_ascii=False)
        expr = ("(function(){var b=document.body; return !!b && "
                "(b.innerText||'').indexOf(%s) >= 0;})()" % needle)
        while time.time() < end:
            try:
                for _, ctx in self.page.frame_contexts():
                    if self.page.evaluate(expr, context_id=ctx):
                        return True
            except CDPClosed:
                raise
            except CDPError:
                pass
            time.sleep(0.4)
        raise ElementNotFound("화면에서 '%s' 글자를 찾지 못했습니다." % text)

    def get_text(self, target, timeout=None) -> str:
        info = self.find(target, timeout).info() or {}
        return (info.get("text") or info.get("value") or "").strip()

    def switch_tab(self, index: int = -1):
        self.browser.switch_tab(index)
        return True

    def close_tab(self):
        return self.browser.close_tab()

    def handle_alert(self, accept: bool = True, text: str = ""):
        return self.page.handle_dialog(accept, text)

    def run_script(self, script: str):
        """
        selenium 은 'return document.title' 처럼 함수 본문으로 쓰고,
        CDP 는 식(expression)을 받는다. 두 방식 모두 되도록 필요할 때만 감싼다.
        (시나리오 파일을 두 엔진이 함께 쓸 수 있어야 한다)
        """
        text = (script or "").strip()
        if re.search(r"(^|[\n;{])\s*return\b", text):
            text = "(function () { %s })()" % text
        return self.page.evaluate(text)

    def screenshot(self, path: str):
        return self.page.screenshot(path)

    def scroll(self, amount: str = "bottom"):
        return self.page.scroll(amount)

    # -- 요소 선택기 -------------------------------------------------------

    def pick_element(self, timeout: float = 180.0, on_status=None) -> Target:
        """
        사용자가 브라우저에서 요소를 클릭할 때까지 기다린다.
        selenium 판과 달리 프레임을 옮겨 다니지 않고 각 컨텍스트에 바로 심는다.
        """
        deadline = time.time() + timeout
        injected_at = 0.0
        while time.time() < deadline:
            pairs = self.page.frame_contexts()

            if time.time() - injected_at > 1.5:
                for _, ctx in pairs:
                    try:
                        self.page.evaluate(picker_mod.PICKER_JS, context_id=ctx,
                                           by_value=True)
                    except CDPError:
                        pass
                injected_at = time.time()
                if on_status:
                    on_status("브라우저에서 원하는 요소를 클릭하세요. (ESC = 취소)")

            for path_index, (_, ctx) in enumerate(pairs):
                try:
                    raw = self.page.evaluate(picker_mod.READ_JS_EXPR, context_id=ctx)
                except CDPError:
                    continue
                if not raw:
                    continue
                info = json.loads(raw)
                if info.get("cancel"):
                    self.stop_picker()
                    raise picker_mod.PickCancelled("사용자가 선택을 취소했습니다.")
                if info.get("error"):
                    continue
                frames = self._frame_path(path_index)
                self.stop_picker()
                return picker_mod.build_target(info, frames)

            time.sleep(0.25)

        self.stop_picker()
        raise TimeoutError("요소 선택 시간이 초과되었습니다.")

    def stop_picker(self):
        for _, ctx in self.page.frame_contexts():
            try:
                self.page.evaluate(picker_mod.STOP_JS, context_id=ctx)
            except CDPError:
                pass

    def _frame_path(self, flat_index: int) -> list:
        """
        평평한 프레임 순서(깊이 우선)에서의 위치를 저장용 경로([0, 1])로 바꾼다.
        selenium 판과 같은 형식이므로 시나리오 파일을 두 엔진이 함께 쓸 수 있다.
        """
        if flat_index <= 0:
            return []
        try:
            tree = self.page.send("Page.getFrameTree").get("frameTree", {})
        except CDPError:
            return []
        order = []

        def walk(node, path):
            order.append(list(path))
            for i, child in enumerate(node.get("childFrames") or []):
                walk(child, path + [i])

        walk(tree, [])
        return order[flat_index] if flat_index < len(order) else []

    # -- 내부 -------------------------------------------------------------

    @staticmethod
    def _is_empty(target) -> bool:
        if isinstance(target, dict):
            return not target.get("strategies")
        if isinstance(target, Target):
            return target.is_empty()
        return not str(target or "").strip()


__all__ = ["CDPActions", "ElementNotFound"]
