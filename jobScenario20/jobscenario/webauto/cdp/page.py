"""
CDP 브라우저 / 탭 / 요소 조작
==============================
selenium 의 driver 역할을 대신한다. 드라이버 실행 파일이 전혀 필요 없다.

프레임 처리가 selenium 보다 단순하다.
  selenium : switch_to.frame() 로 옮겨 다니며 찾고, 형제 프레임을 보려면 되돌아와야 한다.
  CDP      : 프레임마다 실행 컨텍스트가 있어 **옮겨 다니지 않고** 각 컨텍스트에서 바로 찾는다.
             교차 출처(cross-origin) iframe 도 똑같이 처리된다.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from ..locator import ElementNotFound
from .client import CDPClient, CDPError, CDPClosed
from .finder import (FIND_JS, INFO_JS, SCROLL_JS, CLEAR_JS, SET_VALUE_JS,
                     SELECT_JS, CLICK_JS)

# locator.py 와 같은 기준: 이 점수 이상은 어느 프레임에서 찾아도 믿을 수 있다
STRONG_MIN = 50

COVER_CHECK_JS = """
function () {
  var r = this.getBoundingClientRect();
  var x = r.left + r.width / 2, y = r.top + r.height / 2;
  var top = document.elementFromPoint(x, y);
  if (!top) { return 'none'; }
  if (top === this || this.contains(top) || top.contains(this)) { return 'ok'; }
  return 'covered:' + top.nodeName.toLowerCase();
}
"""


class CDPElement:
    """브라우저 안의 요소 하나를 가리키는 손잡이."""

    def __init__(self, page: "CDPPage", object_id: str, context_id: int):
        self.page = page
        self.object_id = object_id
        self.context_id = context_id

    # 자주 쓰는 정보는 한 번에 읽어 온다
    def info(self) -> dict:
        return self.page.call_on(self, INFO_JS) or {}

    @property
    def tag_name(self) -> str:
        return self.info().get("tag", "")

    @property
    def text(self) -> str:
        i = self.info()
        return i.get("text") or i.get("value") or ""


class CDPPage:
    """탭 하나. 프레임 목록과 요소 탐색을 담당한다."""

    def __init__(self, browser: "CDPBrowser", target_id: str, session_id: str):
        self.browser = browser
        self.target_id = target_id
        self.session_id = session_id
        self._frame_ctx = {}        # frameId -> executionContextId (기본 세계)
        self.dialog = None          # 떠 있는 알림창 정보
        self._enable()

    # -- 준비 -------------------------------------------------------------

    def send(self, method: str, params: dict = None, timeout: float = None) -> dict:
        return self.browser.client.send(method, params, self.session_id, timeout)

    def _enable(self):
        for domain in ("Page", "Runtime", "DOM"):
            try:
                self.send("%s.enable" % domain)
            except CDPError:
                pass
        try:
            self.send("Page.setLifecycleEventsEnabled", {"enabled": True})
        except CDPError:
            pass
        # Runtime.enable 은 그 순간 존재하는 실행 컨텍스트만 알려준다. 새로 열린
        # 탭(예: 링크를 새 창으로 열 때)에 붙는 시점이 그 탭이 about:blank 에서
        # 실제 주소로 막 넘어가는 순간과 겹치면, 넘어가기 전의 임시 컨텍스트나
        # 늦게 뜨는 광고 iframe 만 잡히고 정작 메인 프레임 컨텍스트는 놓칠 수
        # 있다(실제로 겪은 문제: 요소가 화면에 분명히 있는데도 못 찾음).
        # 잠깐 기다렸다가 다시 구독해 최신 상태로 맞춘다.
        try:
            self.browser.client.pump(0.3)
            self.send("Runtime.disable")
            self.send("Runtime.enable")
            self.browser.client.pump(0.3)
        except CDPError:
            pass

    def on_event(self, msg: dict):
        """클라이언트가 넘겨준 이벤트로 프레임/알림창 상태를 갱신한다."""
        method = msg.get("method", "")
        params = msg.get("params", {})
        if method == "Runtime.executionContextCreated":
            ctx = params.get("context", {})
            aux = ctx.get("auxData") or {}
            if aux.get("isDefault") and aux.get("frameId"):
                self._frame_ctx[aux["frameId"]] = ctx.get("id")
        elif method == "Runtime.executionContextDestroyed":
            cid = params.get("executionContextId")
            for fid, c in list(self._frame_ctx.items()):
                if c == cid:
                    self._frame_ctx.pop(fid, None)
        elif method == "Runtime.executionContextsCleared":
            self._frame_ctx.clear()
        elif method == "Page.javascriptDialogOpening":
            self.dialog = params
        elif method == "Page.javascriptDialogClosed":
            self.dialog = None

    # -- 프레임 -----------------------------------------------------------

    def frame_contexts(self) -> list:
        """[(frameId, contextId), ...] 를 화면 구조 순서(최상위 먼저)로 돌려준다."""
        self.browser.client.pump()
        try:
            tree = self.send("Page.getFrameTree").get("frameTree", {})
        except CDPError:
            return list(self._frame_ctx.items())

        order = []

        def walk(node):
            frame = node.get("frame") or {}
            if frame.get("id"):
                order.append(frame["id"])
            for child in node.get("childFrames", []) or []:
                walk(child)

        walk(tree)
        pairs = [(fid, self._frame_ctx[fid]) for fid in order if fid in self._frame_ctx]
        if not pairs:
            # 컨텍스트 통지를 놓친 경우 다시 받아 본다
            try:
                self.send("Runtime.disable")
                self.send("Runtime.enable")
                self.browser.client.pump(0.3)
            except CDPError:
                pass
            pairs = [(fid, self._frame_ctx[fid]) for fid in order if fid in self._frame_ctx]
        return pairs

    @property
    def main_context(self):
        pairs = self.frame_contexts()
        return pairs[0][1] if pairs else None

    # -- 자바스크립트 실행 -------------------------------------------------

    def evaluate(self, expression: str, context_id=None, by_value: bool = True,
                 timeout: float = None):
        """식을 실행한다. by_value=False 면 요소 손잡이(objectId)를 받는다."""
        self._block_if_dialog()
        params = {"expression": expression, "returnByValue": by_value,
                  "awaitPromise": True}
        if context_id is not None:
            params["contextId"] = context_id
        try:
            result = self.send("Runtime.evaluate", params, timeout).get("result", {})
        except CDPError as e:
            # window.open 처럼 값으로 옮길 수 없는 것(창·DOM 요소)을 돌려주는 경우.
            # 스크립트 자체는 이미 실행됐으므로 설명만 돌려주고 넘어간다.
            if by_value and "reference chain" in str(e):
                params["returnByValue"] = False
                result = self.send("Runtime.evaluate", params, timeout).get("result", {})
                return result.get("description") or result.get("className") or ""
            raise
        return result if not by_value else result.get("value")

    def call_on(self, el: CDPElement, func: str, args: list = None,
                by_value: bool = True, timeout: float = None):
        """요소를 this 로 하여 함수를 실행한다."""
        self._block_if_dialog()
        params = {
            "functionDeclaration": func,
            "objectId": el.object_id,
            "arguments": [{"value": a} for a in (args or [])],
            "returnByValue": by_value,
            "awaitPromise": True,
        }
        result = self.send("Runtime.callFunctionOn", params, timeout).get("result", {})
        return result if not by_value else result.get("value")

    def _block_if_dialog(self):
        """
        알림창이 떠 있으면 브라우저가 자바스크립트를 처리하지 못한다.
        멋대로 '확인' 을 누르면 위험하므로(삭제 확인 등) 사용자에게 알린다.
        """
        if self.dialog:
            kind = self.dialog.get("type", "alert")
            text = (self.dialog.get("message") or "")[:80]
            raise CDPError(
                "알림창(%s)이 떠 있어 진행할 수 없습니다: %s\n"
                "  시나리오에 '알림창 확인/취소' 단계를 추가해 주세요." % (kind, text))

    # -- 요소 탐색 --------------------------------------------------------

    def find(self, target: dict, timeout: float = 15.0, visible_only: bool = True,
             log=None) -> CDPElement:
        """
        Target(JSON) 을 실제 요소로 바꾼다.
        locator.py 와 같은 2단계 원칙: 신뢰도 높은 전략을 모든 프레임에서 먼저 시도하고,
        절대 경로 같은 약한 전략은 등록될 때의 프레임에서만 쓴다.
        """
        strategies = sorted(target.get("strategies", []),
                            key=lambda s: -int(s.get("score", 0)))
        if not strategies:
            raise ElementNotFound("찾을 대상이 비어 있습니다.")

        strong = [s for s in strategies if int(s.get("score", 0)) >= STRONG_MIN]
        weak = [s for s in strategies if int(s.get("score", 0)) < STRONG_MIN]
        desc = target.get("desc") or target.get("text") or "(설명 없음)"
        frames = target.get("frames") or []

        deadline = time.time() + max(timeout, 0.1)
        relaxed = False
        while True:
            want_visible = visible_only and not relaxed
            try:
                if strong:
                    el = self._search_all_frames(target, strong, want_visible)
                    if el is not None:
                        return el
                if weak:
                    el = self._search_recorded(target, weak, want_visible, frames)
                    if el is not None:
                        return el
            except CDPClosed:
                raise
            except CDPError:
                pass                      # 화면 전환 중이면 잠시 후 다시

            if time.time() >= deadline:
                if not relaxed:
                    relaxed = True        # 마지막 기회: '보이는 요소' 조건을 푼다
                    deadline = time.time() + 2.5
                    continue
                break
            if log:
                log("  · 대상을 찾는 중... (%s)" % desc)
            time.sleep(0.35)

        raise ElementNotFound("요소를 찾지 못했습니다: %s" % desc)

    def _search_all_frames(self, target: dict, strategies: list, visible_only: bool):
        for _, ctx in self.frame_contexts():
            el = self._search_in_context(target, strategies, visible_only, ctx)
            if el is not None:
                return el
        return None

    def _search_recorded(self, target: dict, strategies: list, visible_only: bool,
                         frames: list):
        """등록될 때의 프레임(없으면 최상위)에서만 찾는다."""
        ctx = self.context_for_path(frames)
        if ctx is None:
            return None
        return self._search_in_context(target, strategies, visible_only, ctx)

    def context_for_path(self, path: list):
        """
        저장된 프레임 경로([0, 1] = 첫 iframe 의 두 번째 iframe)를
        지금 화면의 실행 컨텍스트로 바꾼다.
        프레임 id 는 새로 열 때마다 바뀌므로 '몇 번째 프레임인가'로 저장해 둔다.
        """
        self.browser.client.pump()
        try:
            node = self.send("Page.getFrameTree").get("frameTree", {})
        except CDPError:
            return None
        for raw in (path or []):
            try:
                index = int(raw if not isinstance(raw, dict) else raw.get("index", 0))
            except (TypeError, ValueError):
                return None
            children = node.get("childFrames") or []
            if index >= len(children):
                return None
            node = children[index]
        frame_id = (node.get("frame") or {}).get("id")
        return self._frame_ctx.get(frame_id)

    def _search_in_context(self, target: dict, strategies: list, visible_only: bool,
                           context_id):
        spec = {
            "strategies": [{"by": s.get("by"), "value": s.get("value")}
                           for s in strategies],
            "tag": target.get("tag", ""),
            "text": target.get("text", ""),
            "visibleOnly": bool(visible_only),
        }
        import json as _json
        expression = "(%s)(%s)" % (FIND_JS, _json.dumps(spec, ensure_ascii=False))
        try:
            result = self.evaluate(expression, context_id=context_id, by_value=False)
        except CDPError:
            return None
        object_id = result.get("objectId")
        if not object_id or result.get("subtype") == "null":
            return None
        return CDPElement(self, object_id, context_id)

    # -- 요소 동작 --------------------------------------------------------

    def scroll_into_view(self, el: CDPElement) -> dict:
        try:
            self.send("DOM.scrollIntoViewIfNeeded", {"objectId": el.object_id})
        except CDPError:
            pass
        return self.call_on(el, SCROLL_JS) or {}

    def click(self, el: CDPElement) -> str:
        """
        실제 마우스 클릭(Input 이벤트)을 우선 시도하고,
        가려져 있거나 좌표를 못 구하면 자바스크립트 클릭으로 넘어간다.
        """
        self.scroll_into_view(el)
        cover = self.call_on(el, COVER_CHECK_JS)
        point = self._click_point(el)

        if point and cover == "ok":
            x, y = point
            common = {"x": x, "y": y, "button": "left", "clickCount": 1,
                      "buttons": 1}
            self.send("Input.dispatchMouseEvent", dict(common, type="mouseMoved",
                                                      buttons=0))
            self.send("Input.dispatchMouseEvent", dict(common, type="mousePressed"))
            self.send("Input.dispatchMouseEvent", dict(common, type="mouseReleased"))
            return "native"

        self.call_on(el, CLICK_JS)
        return "js" if cover == "ok" else "js(%s)" % cover

    def _click_point(self, el: CDPElement):
        """클릭할 좌표(최상위 화면 기준). iframe 안이어도 CDP 가 위치를 맞춰 준다."""
        try:
            quads = self.send("DOM.getContentQuads",
                              {"objectId": el.object_id}).get("quads", [])
        except CDPError:
            return None
        for q in quads:
            if len(q) != 8:
                continue
            xs, ys = q[0::2], q[1::2]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            if w > 1 and h > 1:
                return sum(xs) / 4.0, sum(ys) / 4.0
        return None

    def type_text(self, el: CDPElement, text: str, clear: bool = True) -> str:
        """실제 키 입력처럼 글자를 넣는다. 막혀 있으면 값을 직접 설정한다."""
        self.scroll_into_view(el)
        try:
            self.click(el)
        except CDPError:
            pass
        if clear:
            self.call_on(el, CLEAR_JS)
        if text:
            self.send("Input.insertText", {"text": text})

        actual = (self.info_value(el) or "")
        if text and actual != text:
            # 달력·금액 입력창처럼 직접 입력이 막힌 경우
            self.call_on(el, SET_VALUE_JS, [text])
            actual = self.info_value(el) or ""
        return actual

    def info_value(self, el: CDPElement) -> str:
        return (el.info() or {}).get("value", "")

    def select_option(self, el: CDPElement, value: str) -> str:
        return self.call_on(el, SELECT_JS, [value])

    def set_checkbox(self, el: CDPElement, checked: bool) -> bool:
        if bool((el.info() or {}).get("checked")) != bool(checked):
            self.click(el)
        return True

    def hover(self, el: CDPElement) -> bool:
        point = self.scroll_into_view(el) and self._click_point(el)
        if point:
            self.send("Input.dispatchMouseEvent",
                      {"type": "mouseMoved", "x": point[0], "y": point[1],
                       "buttons": 0})
        return True

    def upload(self, el: CDPElement, paths: list) -> bool:
        self.send("DOM.setFileInputFiles",
                  {"files": [str(p) for p in paths], "objectId": el.object_id})
        return True

    # -- 키 입력 ----------------------------------------------------------

    KEYS = {
        "enter": ("Enter", "Enter", 13, "\r"),
        "tab": ("Tab", "Tab", 9, "\t"),
        "esc": ("Escape", "Escape", 27, ""),
        "escape": ("Escape", "Escape", 27, ""),
        "space": (" ", "Space", 32, " "),
        "backspace": ("Backspace", "Backspace", 8, ""),
        "delete": ("Delete", "Delete", 46, ""),
        "up": ("ArrowUp", "ArrowUp", 38, ""),
        "down": ("ArrowDown", "ArrowDown", 40, ""),
        "left": ("ArrowLeft", "ArrowLeft", 37, ""),
        "right": ("ArrowRight", "ArrowRight", 39, ""),
        "pageup": ("PageUp", "PageUp", 33, ""),
        "pagedown": ("PageDown", "PageDown", 34, ""),
        "home": ("Home", "Home", 36, ""),
        "end": ("End", "End", 35, ""),
        "f5": ("F5", "F5", 116, ""),
    }

    def press_key(self, key: str) -> bool:
        name = (key or "").strip().lower()
        spec = self.KEYS.get(name)
        if spec is None:
            if len(key or "") == 1:
                self.send("Input.insertText", {"text": key})
                return True
            raise CDPError("알 수 없는 키입니다: %s" % key)
        k, code, vk, text = spec
        base = {"key": k, "code": code, "windowsVirtualKeyCode": vk,
                "nativeVirtualKeyCode": vk}
        if text:
            base["text"] = text
        self.send("Input.dispatchKeyEvent", dict(base, type="keyDown"))
        self.send("Input.dispatchKeyEvent", dict(base, type="keyUp"))
        return True

    # -- 화면 이동 / 상태 --------------------------------------------------

    def navigate(self, url: str, timeout: float = 60.0):
        self.send("Page.navigate", {"url": url}, timeout)
        self.wait_ready(timeout)

    def wait_ready(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.evaluate("document.readyState") == "complete":
                    return True
            except CDPClosed:
                raise
            except CDPError:
                pass
            time.sleep(0.2)
        return False

    @property
    def url(self) -> str:
        try:
            return self.evaluate("location.href") or ""
        except CDPError:
            return ""

    @property
    def title(self) -> str:
        try:
            return self.evaluate("document.title") or ""
        except CDPError:
            return ""

    def page_source(self) -> str:
        try:
            return self.evaluate("document.documentElement.outerHTML") or ""
        except CDPError:
            return ""

    def screenshot(self, path: str) -> str:
        data = self.send("Page.captureScreenshot", {"format": "png"},
                         timeout=60).get("data", "")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(data))
        return str(target)

    def scroll(self, amount: str = "bottom") -> bool:
        js = {"bottom": "window.scrollTo(0, document.body.scrollHeight)",
              "top": "window.scrollTo(0, 0)"}.get(str(amount).lower())
        if js is None:
            js = "window.scrollBy(0, %d)" % int(amount)
        self.evaluate(js)
        return True

    def handle_dialog(self, accept: bool = True, text: str = "") -> bool:
        if not self.dialog:
            self.browser.client.pump(0.2)
        if not self.dialog:
            return False
        params = {"accept": bool(accept)}
        if text:
            params["promptText"] = text
        self.send("Page.handleJavaScriptDialog", params)
        self.dialog = None
        return True


class CDPBrowser:
    """브라우저 하나. 탭(Page) 목록을 관리한다."""

    def __init__(self, client: CDPClient, process=None, port: int = 0,
                 download_dir: str = "", log=None):
        self.client = client
        self.process = process
        self.port = port
        self.log = log or (lambda m: None)
        self.pages = {}                 # sessionId -> CDPPage
        self.page = None                # 지금 보고 있는 탭
        self._order = []                # 탭이 열린 순서(targetId)
        self.client.on_event = self._on_event

        # 탭 생성/삭제 통지를 받아 '열린 순서' 를 관리한다.
        # Target.getTargets 의 목록 순서는 생성 순서가 아니므로 이 정보가 필요하다.
        try:
            self.client.send("Target.setDiscoverTargets", {"discover": True})
            self.client.pump(0.3)
        except CDPError:
            pass

        self._attach_first_page()
        if download_dir:
            self.set_download_dir(download_dir)

    # -- 이벤트 -----------------------------------------------------------

    def _on_event(self, msg: dict):
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "Target.targetCreated":
            info = params.get("targetInfo", {})
            if info.get("type") == "page" and info.get("targetId") not in self._order:
                self._order.append(info["targetId"])
        elif method == "Target.targetDestroyed":
            tid = params.get("targetId")
            if tid in self._order:
                self._order.remove(tid)
        elif method == "Target.detachedFromTarget":
            self.pages.pop(params.get("sessionId", ""), None)

        sid = msg.get("sessionId")
        page = self.pages.get(sid)
        if page is not None:
            page.on_event(msg)

    # -- 탭 ---------------------------------------------------------------

    def page_targets(self) -> list:
        """열린 탭 목록을 **열린 순서대로** 돌려준다(마지막 항목이 가장 새 탭)."""
        self.client.pump()
        targets = self.client.send("Target.getTargets").get("targetInfos", [])
        pages = [t for t in targets
                 if t.get("type") == "page"
                 and not t.get("url", "").startswith("devtools://")]
        rank = {tid: i for i, tid in enumerate(self._order)}
        # 통지를 놓친 탭은 뒤로 보낸다
        return sorted(pages, key=lambda t: rank.get(t["targetId"], len(rank)))

    def attach(self, target_id: str) -> CDPPage:
        for page in self.pages.values():
            if page.target_id == target_id:
                return page
        sid = self.client.send("Target.attachToTarget",
                              {"targetId": target_id, "flatten": True})["sessionId"]
        page = CDPPage(self, target_id, sid)
        self.pages[sid] = page
        return page

    def _attach_first_page(self):
        """
        디버깅 포트가 열렸다고 해서 첫 탭(about:blank)이 곧바로 목록에
        잡히는 것은 아니다. 특히 프로필을 처음 쓰는 경우(최초 실행) 첫 실행
        준비 때문에 탭 등록이 살짝 늦을 수 있어, 바로 실패시키지 않고
        잠깐 기다렸다가 다시 확인한다.
        """
        deadline = time.time() + 10.0
        targets = self.page_targets()
        while not targets and time.time() < deadline:
            time.sleep(0.2)
            targets = self.page_targets()
        if not targets:
            raise CDPError("브라우저에 열린 탭이 없습니다.")
        self.page = self.attach(targets[0]["targetId"])

    def switch_tab(self, index: int = -1) -> CDPPage:
        targets = self.page_targets()
        if not targets:
            raise CDPError("열린 탭이 없습니다.")
        self.page = self.attach(targets[int(index)]["targetId"])
        try:
            self.client.send("Target.activateTarget",
                             {"targetId": self.page.target_id})
        except CDPError:
            pass
        return self.page

    def follow_new_tab(self, before: int, wait: float = 2.0) -> bool:
        """클릭 때문에 새 탭이 열렸으면 그 탭으로 옮긴다."""
        end = time.time() + wait
        while time.time() < end:
            targets = self.page_targets()
            if len(targets) > before:
                self.page = self.attach(targets[-1]["targetId"])
                self.page.wait_ready(20)
                self.log("  · 새 창으로 이동했습니다.")
                return True
            time.sleep(0.2)
        return False

    def close_tab(self) -> bool:
        targets = self.page_targets()
        if len(targets) <= 1 or self.page is None:
            return False
        closing = self.page.target_id
        self.client.send("Target.closeTarget", {"targetId": closing})
        self.pages = {s: p for s, p in self.pages.items() if p.target_id != closing}
        remaining = self.page_targets()
        self.page = self.attach(remaining[-1]["targetId"]) if remaining else None
        return True

    def set_download_dir(self, folder: str):
        Path(folder).mkdir(parents=True, exist_ok=True)
        try:
            self.client.send("Browser.setDownloadBehavior",
                             {"behavior": "allow", "downloadPath": str(folder)})
        except CDPError:
            pass

    # -- 생명주기 ---------------------------------------------------------

    @property
    def alive(self) -> bool:
        if self.client.closed:
            return False
        try:
            self.client.send("Browser.getVersion", timeout=5)
            return True
        except CDPError:
            return False

    def quit(self):
        try:
            self.client.send("Browser.close", timeout=5)
        except CDPError:
            pass
        self.client.close()
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except Exception:
                pass
