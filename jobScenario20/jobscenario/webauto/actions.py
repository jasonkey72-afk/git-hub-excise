"""
고수준 웹 동작 모음
====================
'클릭이 가끔 안 먹는다', '입력이 지워지지 않는다', '새 창이 떠서 멈춘다' 같은
실무에서 반복되는 실패를 모두 이 안에서 흡수한다.
SmartLocator 와 함께 다른 프로젝트에 그대로 옮겨 쓸 수 있다.
"""

from __future__ import annotations

import time
from pathlib import Path

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    StaleElementReferenceException,
    UnexpectedAlertPresentException,
    NoAlertPresentException,
    WebDriverException,
)

from .locator import SmartLocator, ElementNotFound, parse_target

KEY_MAP = {
    "enter": Keys.ENTER, "tab": Keys.TAB, "esc": Keys.ESCAPE,
    "escape": Keys.ESCAPE, "space": Keys.SPACE, "backspace": Keys.BACK_SPACE,
    "delete": Keys.DELETE, "up": Keys.ARROW_UP, "down": Keys.ARROW_DOWN,
    "left": Keys.ARROW_LEFT, "right": Keys.ARROW_RIGHT,
    "pageup": Keys.PAGE_UP, "pagedown": Keys.PAGE_DOWN,
    "home": Keys.HOME, "end": Keys.END, "f5": Keys.F5,
}

SET_VALUE_JS = """
var el = arguments[0], v = arguments[1];
var proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
var setter = Object.getOwnPropertyDescriptor(proto, 'value');
if (setter && setter.set) { setter.set.call(el, v); } else { el.value = v; }
el.dispatchEvent(new Event('input', {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
"""


class WebActions:
    """드라이버 하나에 대한 동작 묶음."""

    engine_name = "selenium"

    def __init__(self, driver, timeout: float = 15.0, log=None, process=None):
        self.driver = driver
        self.locator = SmartLocator(driver, default_timeout=timeout)
        self.log = log or (lambda msg: None)
        # 이미 열려 있던 창에 연결(attach_port)한 경우, 우리가 직접 다시 띄운
        # 프로세스를 여기에 들고 있다가 quit() 에서 함께 정리한다(드라이버의
        # quit() 만으로는 attach 로 연결한 브라우저가 안 닫힐 때가 있다).
        self.process = process

    # -- 기본 --------------------------------------------------------------

    def find(self, target, timeout=None, visible_only=True):
        return self.locator.find(target, timeout=timeout, visible_only=visible_only,
                                 log=self.log)

    # -- 엔진 공통 인터페이스 (cdp 판과 같은 이름으로 제공한다) ------------

    @property
    def alive(self) -> bool:
        try:
            _ = self.driver.current_url
            return True
        except Exception:
            return False

    def quit(self):
        try:
            self.driver.quit()
        except Exception:
            pass
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                pass

    def title(self) -> str:
        try:
            return self.driver.title or ""
        except WebDriverException:
            return ""

    def current_url(self) -> str:
        try:
            return self.driver.current_url or ""
        except WebDriverException:
            return ""

    def page_source(self) -> str:
        try:
            return self.driver.page_source or ""
        except WebDriverException:
            return ""

    def save_screenshot(self, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.driver.save_screenshot(str(path))
        return str(path)

    def pick_element(self, timeout: float = 180.0, on_status=None):
        from .picker import pick_element
        return pick_element(self.driver, timeout=timeout, on_status=on_status)

    def highlight(self, element):
        from .picker import flash
        flash(self.driver, element)

    def element_label(self, element) -> str:
        try:
            return "<%s> %s" % (element.tag_name, (element.text or "")[:30])
        except WebDriverException:
            return "요소"

    def open_url(self, url: str, timeout: float = 60.0):
        if not url:
            raise ValueError("주소(URL)가 비어 있습니다.")
        if "://" not in url:
            url = "https://" + url
        self.driver.get(url)
        self.wait_ready(timeout)

    def wait_ready(self, timeout: float = 30.0):
        """document.readyState 가 complete 가 될 때까지 기다린다."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    return True
            except UnexpectedAlertPresentException:
                return True
            except WebDriverException:
                pass
            time.sleep(0.2)
        return False

    # -- 클릭 --------------------------------------------------------------

    def click(self, target, timeout=None, new_tab_wait: bool = True):
        """일반 클릭 -> 스크롤 후 클릭 -> JS 클릭 순으로 시도한다."""
        before = len(self.driver.window_handles)
        el = self.find(target, timeout)
        last = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    self._scroll_into_view(el)
                el.click()
                last = None
                break
            except (ElementClickInterceptedException, ElementNotInteractableException) as e:
                last = e
                time.sleep(0.4)
            except StaleElementReferenceException:
                el = self.find(target, timeout)
                last = None
            except WebDriverException as e:
                last = e
                break
        if last is not None:
            # 팝업/레이어에 가려진 경우까지 처리하는 마지막 수단
            self.driver.execute_script("arguments[0].click();", el)

        if new_tab_wait:
            self._follow_new_tab(before)
        return True

    def double_click(self, target, timeout=None):
        from selenium.webdriver import ActionChains
        el = self.find(target, timeout)
        ActionChains(self.driver).double_click(el).perform()
        return True

    def hover(self, target, timeout=None):
        """메뉴가 마우스를 올려야 펼쳐지는 사이트에 사용한다."""
        from selenium.webdriver import ActionChains
        el = self.find(target, timeout)
        self._scroll_into_view(el)
        ActionChains(self.driver).move_to_element(el).perform()
        time.sleep(0.3)
        return True

    # -- 입력 --------------------------------------------------------------

    def input_text(self, target, text: str, timeout=None, clear: bool = True,
                   enter: bool = False):
        el = self.find(target, timeout)
        self._scroll_into_view(el)
        try:
            el.click()
        except WebDriverException:
            pass
        if clear:
            try:
                el.clear()
            except WebDriverException:
                pass
            try:
                el.send_keys(Keys.CONTROL, "a")
                el.send_keys(Keys.DELETE)
            except WebDriverException:
                pass
        try:
            el.send_keys(text)
        except WebDriverException:
            # 달력/금액 입력창처럼 직접 입력이 막힌 경우 값을 직접 넣어준다
            self.driver.execute_script(SET_VALUE_JS, el, text)
        if enter:
            el.send_keys(Keys.ENTER)
        return True

    def select_option(self, target, value: str, timeout=None):
        """콤보박스 선택. 화면 글자 -> value -> 순번 순으로 시도한다."""
        el = self.find(target, timeout)
        if el.tag_name.lower() != "select":
            # 흔히 쓰는 '가짜 콤보박스'는 클릭 후 항목을 글자로 찾는다
            self.click(target, timeout, new_tab_wait=False)
            time.sleep(0.3)
            return self.click("text=%s" % value, timeout, new_tab_wait=False)
        sel = Select(el)
        try:
            sel.select_by_visible_text(value)
        except WebDriverException:
            try:
                sel.select_by_value(value)
            except WebDriverException:
                sel.select_by_index(int(value))
        return True

    def set_checkbox(self, target, checked: bool = True, timeout=None):
        el = self.find(target, timeout)
        if bool(el.is_selected()) != bool(checked):
            try:
                el.click()
            except WebDriverException:
                self.driver.execute_script("arguments[0].click();", el)
        return True

    def press_key(self, key: str, target=None, timeout=None):
        k = KEY_MAP.get((key or "").strip().lower(), key)
        if target:
            self.find(target, timeout).send_keys(k)
        else:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(k)
        return True

    def upload_file(self, target, path: str, timeout=None):
        el = self.find(target, timeout, visible_only=False)
        el.send_keys(str(Path(path)))
        return True

    # -- 대기 / 확인 -------------------------------------------------------

    def wait_element(self, target, timeout=None):
        self.find(target, timeout)
        return True

    def wait_text(self, text: str, timeout: float = 30.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if text in self.driver.find_element(By.TAG_NAME, "body").text:
                    return True
            except WebDriverException:
                pass
            time.sleep(0.4)
        raise ElementNotFound("화면에서 '%s' 글자를 찾지 못했습니다." % text)

    def get_text(self, target, timeout=None) -> str:
        el = self.find(target, timeout)
        return (el.text or el.get_attribute("value") or "").strip()

    # -- 창 / 프레임 -------------------------------------------------------

    def switch_tab(self, index: int = -1):
        handles = self.driver.window_handles
        if not handles:
            return False
        self.driver.switch_to.window(handles[int(index)])
        self.driver.switch_to.default_content()
        return True

    def close_tab(self):
        if len(self.driver.window_handles) > 1:
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[-1])
        return True

    def handle_alert(self, accept: bool = True, text: str = ""):
        try:
            alert = self.driver.switch_to.alert
        except NoAlertPresentException:
            return False
        if text:
            alert.send_keys(text)
        alert.accept() if accept else alert.dismiss()
        return True

    def run_script(self, script: str):
        return self.driver.execute_script(script)

    def screenshot(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.driver.save_screenshot(str(path))
        return str(path)

    def scroll(self, amount: str = "bottom"):
        js = {
            "bottom": "window.scrollTo(0, document.body.scrollHeight);",
            "top": "window.scrollTo(0, 0);",
        }.get(str(amount).lower())
        if js is None:
            js = "window.scrollBy(0, %d);" % int(amount)
        self.driver.execute_script(js)
        return True

    # -- 내부 --------------------------------------------------------------

    def _scroll_into_view(self, el):
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'center'});", el)
            time.sleep(0.15)
        except WebDriverException:
            pass

    def _follow_new_tab(self, before_count: int, wait: float = 1.5):
        """클릭 때문에 새 창이 떴으면 그 창으로 자동 이동한다."""
        end = time.time() + wait
        while time.time() < end:
            handles = self.driver.window_handles
            if len(handles) > before_count:
                self.driver.switch_to.window(handles[-1])
                self.wait_ready(20)
                self.log("  · 새 창으로 이동했습니다.")
                return True
            time.sleep(0.2)
        return False


__all__ = ["WebActions", "ElementNotFound", "parse_target"]
