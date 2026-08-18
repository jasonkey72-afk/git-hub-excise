"""
웹 요소 선택기(Picker)
=======================
브라우저 화면 위에서 사용자가 마우스로 직접 요소를 클릭하면,
그 요소를 다시 찾아갈 수 있는 여러 개의 선택자를 한꺼번에 수집한다.

"어느 버튼인지 어떻게 지정하지?" 라는 가장 큰 어려움을
'그냥 눈으로 보고 클릭하세요' 로 바꿔주는 부분이다.
사용 방법:
    target = pick_element(driver)      # 사용자가 클릭할 때까지 기다린다
"""

from __future__ import annotations

import json
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

from .locator import (
    Strategy, Target, STRATEGY_SCORE, BY_DEEP, _is_stable_key,
    strategies_for_text, strategies_for_label,
)

# ---------------------------------------------------------------------------
# 브라우저에 심는 자바스크립트 (모든 프레임에 각각 주입된다)
# ---------------------------------------------------------------------------

PICKER_JS = r"""
(function () {
  if (window.__JSP_ON__) { return 'already'; }
  window.__JSP_ON__ = true;
  window.__JS_PICK__ = null;

  var box = document.createElement('div');
  box.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;' +
    'border:2px solid #2563eb;background:rgba(37,99,235,.15);border-radius:3px;' +
    'transition:all .04s linear;display:none;';
  var tip = document.createElement('div');
  tip.style.cssText = 'position:fixed;z-index:2147483647;pointer-events:none;' +
    'background:#111827;color:#fff;font:12px/1.5 "Malgun Gothic",sans-serif;' +
    'padding:3px 8px;border-radius:4px;display:none;max-width:60vw;' +
    'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
  var bar = document.createElement('div');
  bar.style.cssText = 'position:fixed;left:50%;top:12px;transform:translateX(-50%);' +
    'z-index:2147483647;pointer-events:none;background:#2563eb;color:#fff;' +
    'font:13px/1.6 "Malgun Gothic",sans-serif;padding:6px 16px;border-radius:20px;' +
    'box-shadow:0 4px 12px rgba(0,0,0,.25);';
  bar.textContent = '선택할 요소를 클릭하세요 (ESC = 취소)';

  function mount() {
    var root = document.body || document.documentElement;
    if (!root) { return; }
    root.appendChild(box); root.appendChild(tip); root.appendChild(bar);
  }
  mount();

  function txt(el) {
    var t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    return t.length > 80 ? t.slice(0, 80) : t;
  }

  function cssPath(el) {
    var parts = [], cur = el, guard = 0;
    while (cur && cur.nodeType === 1 && guard++ < 12) {
      if (cur.id && /^[A-Za-z][\w\-]*$/.test(cur.id)) {
        parts.unshift('#' + cur.id);
        break;
      }
      var name = cur.nodeName.toLowerCase(), parent = cur.parentNode, idx = 1;
      if (parent) {
        var sibs = parent.children, n = 0;
        for (var i = 0; i < sibs.length; i++) {
          if (sibs[i].nodeName === cur.nodeName) {
            n++;
            if (sibs[i] === cur) { idx = n; }
          }
        }
        if (n > 1) { name += ':nth-of-type(' + idx + ')'; }
      }
      parts.unshift(name);
      cur = parent;
      if (cur === document.documentElement) { break; }
    }
    return parts.join(' > ');
  }

  function absXPath(el) {
    var parts = [], cur = el, guard = 0;
    while (cur && cur.nodeType === 1 && guard++ < 30) {
      var idx = 1, sib = cur.previousSibling;
      while (sib) {
        if (sib.nodeType === 1 && sib.nodeName === cur.nodeName) { idx++; }
        sib = sib.previousSibling;
      }
      parts.unshift(cur.nodeName.toLowerCase() + '[' + idx + ']');
      cur = cur.parentNode;
      if (!cur || cur.nodeType !== 1) { break; }
    }
    return '/' + parts.join('/');
  }

  function labelOf(el) {
    if (el.id) {
      var lb = document.querySelector('label[for="' + el.id + '"]');
      if (lb) { return txt(lb); }
    }
    var p = el.closest ? el.closest('label') : null;
    if (p) { return txt(p); }
    var cell = el.closest ? el.closest('td,div,li,dd') : null;
    if (cell && cell.previousElementSibling) { return txt(cell.previousElementSibling); }
    return '';
  }

  function datasets(el) {
    var out = {};
    if (!el.attributes) { return out; }
    for (var i = 0; i < el.attributes.length; i++) {
      var a = el.attributes[i];
      if (a.name.indexOf('data-') === 0 && a.value && a.value.length < 60) {
        out[a.name] = a.value;
      }
    }
    return out;
  }

  // Shadow DOM 안을 클릭하면 e.target 이 바깥 껍데기(host)로 바뀌어 온다.
  // composedPath()[0] 이 사용자가 실제로 클릭한 요소다.
  function real(e) {
    var path = e.composedPath ? e.composedPath() : null;
    return (path && path.length) ? path[0] : e.target;
  }

  // 요소가 Shadow DOM 안에 있으면, 그 안에서의 CSS 경로를 따로 만들어 둔다
  function shadowInfo(el) {
    var root = el.getRootNode ? el.getRootNode() : null;
    if (!root || root.nodeType !== 11) { return null; }
    var parts = [], cur = el, guard = 0;
    while (cur && cur.nodeType === 1 && guard++ < 12) {
      if (cur.id && /^[A-Za-z][\w\-]*$/.test(cur.id)) {
        parts.unshift('#' + cur.id);
        break;
      }
      var name = cur.nodeName.toLowerCase(), parent = cur.parentNode, idx = 1, n = 0;
      if (parent && parent.children) {
        for (var i = 0; i < parent.children.length; i++) {
          if (parent.children[i].nodeName === cur.nodeName) {
            n++;
            if (parent.children[i] === cur) { idx = n; }
          }
        }
        if (n > 1) { name += ':nth-of-type(' + idx + ')'; }
      }
      parts.unshift(name);
      cur = parent;
      if (!cur || cur.nodeType === 11) { break; }
    }
    return parts.join(' > ');
  }

  function describe(el) {
    return {
      shadow: shadowInfo(el),
      tag: el.nodeName.toLowerCase(),
      id: el.id || '',
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      cls: (el.getAttribute('class') || '').trim(),
      text: txt(el),
      value: el.value === undefined ? '' : String(el.value).slice(0, 60),
      placeholder: el.getAttribute('placeholder') || '',
      aria: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      role: el.getAttribute('role') || '',
      href: el.getAttribute('href') || '',
      label: labelOf(el),
      data: datasets(el),
      css: cssPath(el),
      xpath: absXPath(el),
      url: location.href
    };
  }

  function move(e) {
    var el = real(e);
    if (!el || el === box || el === tip || el === bar) { return; }
    var r = el.getBoundingClientRect();
    box.style.display = 'block';
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
    tip.style.display = 'block';
    tip.style.left = r.left + 'px';
    tip.style.top = (r.top > 26 ? r.top - 24 : r.bottom + 4) + 'px';
    tip.textContent = el.nodeName.toLowerCase() +
      (el.id ? '#' + el.id : '') + ' ' + txt(el).slice(0, 40);
  }

  function stop() {
    window.__JSP_ON__ = false;
    document.removeEventListener('mouseover', move, true);
    document.removeEventListener('click', hit, true);
    document.removeEventListener('keydown', esc, true);
    [box, tip, bar].forEach(function (n) {
      if (n && n.parentNode) { n.parentNode.removeChild(n); }
    });
  }

  function hit(e) {
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    try { window.__JS_PICK__ = JSON.stringify(describe(real(e))); }
    catch (err) { window.__JS_PICK__ = JSON.stringify({ error: String(err) }); }
    stop();
    return false;
  }

  function esc(e) {
    if (e.key === 'Escape') {
      window.__JS_PICK__ = JSON.stringify({ cancel: true });
      stop();
    }
  }

  document.addEventListener('mouseover', move, true);
  document.addEventListener('click', hit, true);
  document.addEventListener('keydown', esc, true);
  return 'on';
})();
"""

READ_JS = ("var v = window.__JS_PICK__; window.__JS_PICK__ = null; "
           "return v ? v : null;")

# CDP 의 Runtime.evaluate 는 식(expression)을 받으므로 같은 내용을 식으로도 준비한다
READ_JS_EXPR = ("(function () { var v = window.__JS_PICK__; "
                "window.__JS_PICK__ = null; return v ? v : null; })()")

STOP_JS = ("if (window.__JSP_STOP__) { window.__JSP_STOP__(); } "
           "window.__JSP_ON__ = false; window.__JS_PICK__ = null;")

HIGHLIGHT_JS = r"""
var el = arguments[0];
el.scrollIntoView({block:'center', inline:'center'});
var old = el.style.outline, oldBg = el.style.backgroundColor;
el.style.outline = '3px solid #f97316';
el.style.backgroundColor = 'rgba(249,115,22,.20)';
setTimeout(function(){ el.style.outline = old; el.style.backgroundColor = oldBg; }, 1600);
"""


class PickCancelled(Exception):
    """사용자가 ESC 로 선택을 취소했다."""


# ---------------------------------------------------------------------------
# 프레임 순회
# ---------------------------------------------------------------------------

def _frame_paths(driver, path=None, depth=0, acc=None) -> list:
    """[], [0], [0,1] ... 형태로 모든 프레임 경로를 모아 돌려준다."""
    if acc is None:
        acc = [[]]
        path = []
    if depth > 3:
        return acc
    try:
        count = len(driver.find_elements(By.CSS_SELECTOR, "iframe, frame"))
    except WebDriverException:
        return acc
    for i in range(count):
        cur = path + [i]
        if not _goto_frame(driver, cur):
            continue
        acc.append(cur)
        _frame_paths(driver, cur, depth + 1, acc)
    _goto_frame(driver, path)
    return acc


def _goto_frame(driver, path: list) -> bool:
    """default_content 부터 시작해 지정한 순번 경로의 프레임으로 이동한다."""
    try:
        driver.switch_to.default_content()
        for idx in path:
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            if idx >= len(frames):
                return False
            driver.switch_to.frame(frames[idx])
        return True
    except WebDriverException:
        return False


# ---------------------------------------------------------------------------
# 수집한 정보 -> Target 변환
# ---------------------------------------------------------------------------

def _css_escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("'", "\\'")


def build_target(info: dict, frames: list) -> Target:
    """picker 가 수집한 정보로 '실패에 강한' Target 을 만든다."""
    tag = info.get("tag", "")
    strategies = []

    def add(kind, by, value, bonus=0):
        if value:
            strategies.append(Strategy(kind, by, value, STRATEGY_SCORE[kind] + bonus))

    if _is_stable_key(info.get("id", "")):
        add("id", By.ID, info["id"])
    for k, v in (info.get("data") or {}).items():
        add("data", By.CSS_SELECTOR, "%s[%s='%s']" % (tag, k, _css_escape(v)))
    if _is_stable_key(info.get("name", "")):
        add("name", By.NAME, info["name"])
    if info.get("aria"):
        add("aria", By.CSS_SELECTOR, "[aria-label='%s']" % _css_escape(info["aria"]))
    if info.get("placeholder"):
        add("placeholder", By.CSS_SELECTOR,
            "[placeholder='%s']" % _css_escape(info["placeholder"]))

    text = (info.get("text") or "").strip()
    value = (info.get("value") or "").strip()
    if tag == "a" and text:
        strategies.extend(strategies_for_text(text))
    elif text and len(text) <= 40:
        strategies.extend(strategies_for_text(text))
    elif value and len(value) <= 40 and tag == "input":
        strategies.extend(strategies_for_text(value))

    # 입력창은 라벨 글자로도 찾을 수 있게 해 둔다
    if tag in ("input", "select", "textarea") and info.get("label"):
        strategies.extend(strategies_for_label(info["label"].strip()))

    # Shadow DOM 안의 요소는 평범한 CSS/XPath 로는 아예 보이지 않으므로
    # 경계를 넘어가며 찾는 전용 전략을 함께 넣어 둔다
    shadow = info.get("shadow")
    if shadow:
        add("deep", BY_DEEP, shadow)
        if _is_stable_key(info.get("id", "")):
            add("deep", BY_DEEP, "#%s" % info["id"], 2)

    # id 가 섞이지 않은 경로형 선택자는 다른 화면에도 우연히 맞을 수 있어 뒤로 미룬다
    css = info.get("css", "")
    add("css", By.CSS_SELECTOR, css, 0 if "#" in css else -25)
    add("abs_xpath", By.XPATH, info.get("xpath", ""))

    label = (info.get("label") or "").strip()
    desc = "%s %s" % (tag, text or value or info.get("placeholder") or
                      info.get("aria") or label or info.get("id") or "")
    return Target(strategies=strategies, frames=list(frames),
                  desc=desc.strip(), tag=tag, text=text or value)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def pick_element(driver, timeout: float = 180.0, on_status=None) -> Target:
    """
    사용자가 브라우저에서 요소를 클릭할 때까지 기다렸다가 Target 을 돌려준다.
    ESC 를 누르면 PickCancelled 예외가 발생한다.
    """
    deadline = time.time() + timeout
    injected_at = 0.0

    try:
        driver.switch_to.default_content()
    except WebDriverException:
        pass

    while time.time() < deadline:
        paths = _frame_paths(driver)

        # 페이지가 바뀌었을 수 있으므로 주기적으로 다시 심는다
        if time.time() - injected_at > 1.5:
            for p in paths:
                if _goto_frame(driver, p):
                    try:
                        driver.execute_script(PICKER_JS)
                    except WebDriverException:
                        pass
            injected_at = time.time()
            if on_status:
                on_status("브라우저에서 원하는 요소를 클릭하세요. (ESC = 취소)")

        for p in paths:
            if not _goto_frame(driver, p):
                continue
            try:
                raw = driver.execute_script(READ_JS)
            except WebDriverException:
                continue
            if not raw:
                continue
            info = json.loads(raw)
            if info.get("cancel"):
                stop_picker(driver)
                raise PickCancelled("사용자가 선택을 취소했습니다.")
            if info.get("error"):
                continue
            stop_picker(driver)
            return build_target(info, p)

        time.sleep(0.25)

    stop_picker(driver)
    raise TimeoutError("요소 선택 시간이 초과되었습니다.")


def stop_picker(driver) -> None:
    """모든 프레임에서 선택기 표시를 걷어낸다."""
    try:
        for p in _frame_paths(driver):
            if _goto_frame(driver, p):
                try:
                    driver.execute_script(STOP_JS)
                except WebDriverException:
                    pass
        driver.switch_to.default_content()
    except WebDriverException:
        pass


def flash(driver, element) -> None:
    """찾은 요소를 잠깐 주황색으로 표시해 사용자가 확인할 수 있게 한다."""
    try:
        driver.execute_script(HIGHLIGHT_JS, element)
    except WebDriverException:
        pass
