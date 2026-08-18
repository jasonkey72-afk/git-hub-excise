"""
SmartLocator - 웹 요소 정밀 탐색 엔진
=====================================
이 모듈이 jobScenario 프로젝트의 핵심(Key Factor)이며, 다른 웹 자동화
프로젝트에 그대로 복사해서 재사용할 수 있도록 selenium 외의 의존성이 없다.

해결하는 난제
  1) 원하는 위치(요소)를 정확히 찾아간다   -> 다중 전략(Strategy) + 점수화
  2) 정확한 버튼을 클릭한다                -> 보이는/활성 요소 우선 선택
  3) 입력창의 정확한 위치를 찾아간다       -> label/placeholder 기반 탐색
  4) iframe 안에 숨어있는 요소를 찾는다    -> 전체 프레임 자동 순회
  5) 화면이 늦게 그려진다                  -> 지정 시간까지 재시도(polling)

사용법
    from jobscenario.webauto.locator import SmartLocator, parse_target
    loc = SmartLocator(driver)
    el = loc.find(parse_target("text=조회"), timeout=10)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

# ---------------------------------------------------------------------------
# 탐색 전략(Strategy) 정의
# ---------------------------------------------------------------------------

# 전략별 신뢰도 점수. 값이 클수록 먼저 시도하고, 더 신뢰한다.
STRATEGY_SCORE = {
    "id": 100,
    "data": 95,
    "name": 90,
    "aria": 80,
    "placeholder": 78,
    "label": 76,
    "link": 74,
    "text": 72,
    "css": 60,
    "xpath": 55,
    "deep": 58,        # Shadow DOM 안쪽까지 파고드는 CSS 탐색
    "abs_xpath": 30,
}

# Shadow DOM(최신 웹 컴포넌트) 안에 숨은 요소를 찾는 표시.
# selenium 의 표준 By 가 아니므로 _find_by_strategy 에서 따로 처리한다.
BY_DEEP = "deep css"

# shadowRoot 를 뚫고 들어가며 CSS 선택자로 요소를 모으는 스크립트
DEEP_QUERY_JS = """
var sel = arguments[0], out = [], seen = new Set();
function walk(root) {
  if (!root || seen.has(root)) { return; }
  seen.add(root);
  try { root.querySelectorAll(sel).forEach(function (e) { out.push(e); }); }
  catch (e) { return; }
  var all = root.querySelectorAll('*');
  for (var i = 0; i < all.length; i++) {
    if (all[i].shadowRoot) { walk(all[i].shadowRoot); }
  }
}
walk(document);
return out;
"""

# 이 화면에 Shadow DOM 이 하나라도 있는지 (있을 때만 깊은 탐색을 켠다)
HAS_SHADOW_JS = """
var all = document.querySelectorAll('*');
for (var i = 0; i < all.length; i++) { if (all[i].shadowRoot) { return true; } }
return false;
"""

# 이 점수 이상이면 '어느 프레임에서 찾아도 믿을 수 있는' 전략으로 본다.
STRONG_MIN = 50

# 자동 생성된 것으로 의심되는 id/name (매번 바뀌므로 신뢰하면 안 된다)
_AUTO_ID_PAT = re.compile(
    r"(\d{5,}|[0-9a-f]{8}-[0-9a-f]{4}|^ctl\d|__\d+$|:\w+:)", re.IGNORECASE
)


def _is_stable_key(value: str) -> bool:
    """id/name 값이 매번 바뀌지 않고 재사용 가능한지 판단한다."""
    if not value or len(value) > 60:
        return False
    return not _AUTO_ID_PAT.search(value)


@dataclass
class Strategy:
    """한 가지 탐색 방법. by 는 selenium By 값, value 는 선택자."""

    kind: str
    by: str
    value: str
    score: int = 0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "by": self.by, "value": self.value, "score": self.score}

    @staticmethod
    def from_dict(d: dict) -> "Strategy":
        return Strategy(d.get("kind", "css"), d.get("by", By.CSS_SELECTOR),
                        d.get("value", ""), int(d.get("score", 0)))


@dataclass
class Target:
    """찾아갈 대상 하나. 여러 전략을 함께 보관해 한 전략이 실패해도 복구한다."""

    strategies: list = field(default_factory=list)
    frames: list = field(default_factory=list)   # iframe 경로(선택자 목록)
    desc: str = ""                               # 사람이 읽는 설명
    tag: str = ""
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "strategies": [s.to_dict() for s in self.strategies],
            "frames": self.frames,
            "desc": self.desc,
            "tag": self.tag,
            "text": self.text,
        }

    @staticmethod
    def from_dict(d: dict) -> "Target":
        if not d:
            return Target()
        return Target(
            strategies=[Strategy.from_dict(s) for s in d.get("strategies", [])],
            frames=list(d.get("frames", [])),
            desc=d.get("desc", ""),
            tag=d.get("tag", ""),
            text=d.get("text", ""),
        )

    def is_empty(self) -> bool:
        return not self.strategies

    def sorted_strategies(self) -> list:
        return sorted(self.strategies, key=lambda s: -s.score)


# ---------------------------------------------------------------------------
# 사용자가 손으로 입력한 문자열 -> Target 변환
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    """XPath 문자열 리터럴로 안전하게 감싼다(따옴표 혼용 대응)."""
    if '"' not in text:
        return '"%s"' % text
    if "'" not in text:
        return "'%s'" % text
    parts = text.split('"')
    return "concat(" + ', \'"\', '.join('"%s"' % p for p in parts) + ")"


def strategies_for_text(text: str) -> list:
    """보이는 글자로 버튼/링크/메뉴를 찾는 전략 묶음을 만든다."""
    q = _esc(text)
    lower = _esc(text.lower())
    return [
        # 1순위: 글자가 정확히 일치하는 클릭 가능 요소
        Strategy("text", By.XPATH,
                 "(//button|//a|//input[@type='button' or @type='submit']|//*[@role='button']"
                 "|//label|//span|//div|//li|//td|//th)"
                 "[normalize-space(.)=%s]" % q,
                 STRATEGY_SCORE["text"]),
        # 2순위: value 속성이 일치하는 입력 버튼
        Strategy("text", By.XPATH,
                 "//input[@value=%s] | //*[@title=%s] | //*[@aria-label=%s]" % (q, q, q),
                 STRATEGY_SCORE["text"] - 2),
        # 3순위: 글자를 포함(대소문자 무시)하는 가장 안쪽 요소
        Strategy("text", By.XPATH,
                 "//*[contains(translate(normalize-space(.),"
                 "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), %s)]"
                 "[not(.//*[contains(translate(normalize-space(.),"
                 "'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), %s)])]"
                 % (lower, lower),
                 STRATEGY_SCORE["text"] - 4),
    ]


def strategies_for_label(text: str) -> list:
    """'라벨 글자' 옆/아래의 입력창을 찾는 전략 묶음을 만든다."""
    q = _esc(text)
    return [
        # label for="..." 로 연결된 입력창
        Strategy("label", By.XPATH,
                 "//input[@id=//label[normalize-space(.)=%s]/@for] "
                 "| //select[@id=//label[normalize-space(.)=%s]/@for] "
                 "| //textarea[@id=//label[normalize-space(.)=%s]/@for]" % (q, q, q),
                 STRATEGY_SCORE["label"]),
        # label 안에 들어있는 입력창
        Strategy("label", By.XPATH,
                 "//label[contains(normalize-space(.), %s)]//input"
                 "|//label[contains(normalize-space(.), %s)]//select" % (q, q),
                 STRATEGY_SCORE["label"] - 1),
        # 표(table) 형태: 라벨 칸 오른쪽 칸의 입력창
        Strategy("label", By.XPATH,
                 "//*[self::th or self::td or self::dt or self::span or self::div]"
                 "[normalize-space(.)=%s]/following::input[1]" % q,
                 STRATEGY_SCORE["label"] - 2),
        # placeholder 로 표시된 경우
        Strategy("placeholder", By.CSS_SELECTOR,
                 "input[placeholder='%s'], textarea[placeholder='%s']"
                 % (text.replace("'", "\\'"), text.replace("'", "\\'")),
                 STRATEGY_SCORE["placeholder"] - 4),
    ]


def _with(target: Target, strategies: list) -> Target:
    """전략 목록만 바꾼 사본을 만든다(프레임/설명은 그대로)."""
    return Target(strategies=strategies, frames=target.frames, desc=target.desc,
                  tag=target.tag, text=target.text)


def parse_target(spec, desc: str = "") -> Target:
    """
    사용자가 입력한 한 줄을 Target 으로 바꾼다.

      #id  .class  div>button   -> CSS 선택자
      //button[...]             -> XPath
      id=userId                 -> id 속성
      name=searchKey            -> name 속성
      text=조회                 -> 화면에 보이는 글자
      label=사번                -> 라벨 옆 입력창
      placeholder=검색어 입력   -> placeholder
      css=...  xpath=...        -> 명시적 지정
      deep=#code                -> Shadow DOM(웹 컴포넌트) 안쪽까지 탐색
      그 외 일반 글자           -> 글자 + 라벨을 모두 시도
    """
    if isinstance(spec, Target):
        return spec
    if isinstance(spec, dict):
        return Target.from_dict(spec)

    raw = (spec or "").strip()
    if not raw:
        return Target()

    t = Target(desc=desc or raw, text=raw)
    prefix, _, rest = raw.partition("=")
    key, val = prefix.strip().lower(), rest.strip()

    if key == "id" and val:
        t.strategies = [Strategy("id", By.ID, val, STRATEGY_SCORE["id"])]
    elif key == "name" and val:
        t.strategies = [Strategy("name", By.NAME, val, STRATEGY_SCORE["name"])]
    elif key == "css" and val:
        t.strategies = [Strategy("css", By.CSS_SELECTOR, val, STRATEGY_SCORE["css"])]
    elif key == "deep" and val:
        # Shadow DOM 안쪽까지 파고들어 찾는다
        t.strategies = [Strategy("deep", BY_DEEP, val, STRATEGY_SCORE["deep"])]
    elif key == "xpath" and val:
        t.strategies = [Strategy("xpath", By.XPATH, val, STRATEGY_SCORE["xpath"])]
    elif key == "text" and val:
        t.strategies = strategies_for_text(val)
        t.text = val
    elif key == "label" and val:
        t.strategies = strategies_for_label(val)
        t.text = val
    elif key == "placeholder" and val:
        t.strategies = [Strategy("placeholder", By.CSS_SELECTOR,
                                 "[placeholder='%s']" % val.replace("'", "\\'"),
                                 STRATEGY_SCORE["placeholder"])]
    elif raw.startswith("//") or raw.startswith("(//") or raw.startswith("./"):
        t.strategies = [Strategy("xpath", By.XPATH, raw, STRATEGY_SCORE["xpath"])]
    elif raw.startswith("#") or raw.startswith(".") or re.search(r"[>\[\]]", raw):
        t.strategies = [Strategy("css", By.CSS_SELECTOR, raw, STRATEGY_SCORE["css"])]
    else:
        # 아무 표시 없이 글자만 넣은 경우 -> 글자/라벨/id/name 을 모두 시도한다
        t.strategies = (strategies_for_text(raw)
                        + strategies_for_label(raw)
                        + [Strategy("id", By.ID, raw, STRATEGY_SCORE["id"] - 30),
                           Strategy("name", By.NAME, raw, STRATEGY_SCORE["name"] - 30)])
    return t


# ---------------------------------------------------------------------------
# 탐색 엔진
# ---------------------------------------------------------------------------

class ElementNotFound(Exception):
    """지정한 시간 안에 요소를 찾지 못했을 때 발생한다."""


class SmartLocator:
    """드라이버 하나에 붙어서 Target 을 실제 WebElement 로 바꿔주는 객체."""

    def __init__(self, driver, default_timeout: float = 15.0, poll: float = 0.4):
        self.driver = driver
        self.default_timeout = default_timeout
        self.poll = poll
        self.last_frame_path = []      # 마지막으로 요소를 찾은 iframe 경로
        # Shadow DOM 깊은 탐색은 느리므로, 평소에는 끄고 마지막 시도에서만 켠다
        self._allow_deep = False

    # -- 공개 API ----------------------------------------------------------

    def find(self, target, timeout: float = None, visible_only: bool = True,
             log=None):
        """
        Target 을 찾아 WebElement 로 돌려준다.
        찾은 요소가 iframe 안에 있으면 드라이버 컨텍스트를 그 프레임으로 옮겨둔다.
        """
        target = parse_target(target) if not isinstance(target, Target) else target
        if target.is_empty():
            raise ElementNotFound("찾을 대상이 비어 있습니다.")

        timeout = self.default_timeout if timeout is None else timeout
        deadline = time.time() + max(timeout, 0.1)
        last_err = None
        relaxed = False
        # 등록할 때 Shadow DOM 안에 있던 요소라면 처음부터 깊은 탐색을 허용한다
        self._allow_deep = any(s.by == BY_DEEP for s in target.strategies)

        while True:
            try:
                el = self._search_all_frames(target, visible_only and not relaxed)
                if el is not None:
                    return el
            except WebDriverException as e:      # 페이지 전환 중이면 잠시 후 재시도
                last_err = e

            if time.time() >= deadline:
                if not relaxed:
                    # 마지막 기회: '보이는 요소' 조건을 풀고,
                    # Shadow DOM 안쪽까지 뒤지며 한 번 더 찾는다
                    relaxed = True
                    self._allow_deep = True
                    deadline = time.time() + 2.5
                    continue
                break
            if log:
                log("  · 대상을 찾는 중... (%s)" % (target.desc or target.text))
            time.sleep(self.poll)

        raise ElementNotFound(
            "요소를 찾지 못했습니다: %s%s"
            % (target.desc or target.text or "(설명 없음)",
               "" if last_err is None else " / %s" % type(last_err).__name__)
        )

    def find_all_matches(self, target, timeout: float = 3.0) -> list:
        """같은 조건에 맞는 요소를 모두 돌려준다(검증/디버깅용)."""
        target = parse_target(target) if not isinstance(target, Target) else target
        deadline = time.time() + timeout
        while True:
            found = []
            for st in target.sorted_strategies():
                found.extend(self._find_by_strategy(st))
            if found or time.time() >= deadline:
                return found
            time.sleep(self.poll)

    # -- 내부 구현 ---------------------------------------------------------

    def _search_all_frames(self, target: Target, visible_only: bool):
        """
        신뢰도가 높은 전략을 '모든 프레임에서' 먼저 시도하고,
        그래도 못 찾을 때만 신뢰도가 낮은 전략(절대 경로 등)을 쓴다.

        순서를 이렇게 나누지 않으면, 절대 경로처럼 어느 문서에나 우연히
        맞는 선택자가 엉뚱한 화면의 다른 버튼을 먼저 잡아버린다.
        """
        strong = [s for s in target.sorted_strategies() if s.score >= STRONG_MIN]
        weak = [s for s in target.sorted_strategies() if s.score < STRONG_MIN]

        if strong:
            el = self._search_everywhere(_with(target, strong), visible_only)
            if el is not None:
                return el
        if weak:
            # 절대 경로는 원래 문서 안에서만 의미가 있으므로 등록된 프레임에서만 찾는다
            el = self._search_recorded(_with(target, weak), visible_only)
            if el is not None:
                return el
        return None

    def _search_everywhere(self, target: Target, visible_only: bool):
        """현재 프레임 -> 저장된 프레임 경로 -> 최상위 -> 모든 iframe 순으로 찾는다."""
        el = self._pick_best(target, visible_only)
        if el is not None:
            return el

        self.driver.switch_to.default_content()
        if target.frames:
            if self._enter_frame_path(target.frames):
                el = self._pick_best(target, visible_only)
                if el is not None:
                    self.last_frame_path = list(target.frames)
                    return el
            self.driver.switch_to.default_content()

        el = self._pick_best(target, visible_only)
        if el is not None:
            self.last_frame_path = []
            return el
        return self._walk_frames(target, visible_only, path=[])

    def _search_recorded(self, target: Target, visible_only: bool):
        """요소를 등록할 때의 프레임(없으면 최상위)에서만 찾는다."""
        self.driver.switch_to.default_content()
        if target.frames and not self._enter_frame_path(target.frames):
            self.driver.switch_to.default_content()
            return None
        el = self._pick_best(target, visible_only)
        if el is not None:
            self.last_frame_path = list(target.frames)
        return el

    def _walk_frames(self, target: Target, visible_only: bool, path: list, depth: int = 0):
        """iframe 트리를 재귀적으로 순회하며 탐색한다."""
        if depth > 4:                                  # 과도한 중첩은 제외
            return None
        try:
            frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        except WebDriverException:
            return None

        for idx in range(len(frames)):
            try:
                # 프레임 목록은 이동할 때마다 무효화되므로 매번 다시 읽는다
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                if idx >= len(frames):
                    break
                self.driver.switch_to.frame(frames[idx])
            except (WebDriverException, StaleElementReferenceException):
                continue

            cur = path + [idx]
            el = self._pick_best(target, visible_only)
            if el is not None:
                self.last_frame_path = cur
                return el

            el = self._walk_frames(target, visible_only, cur, depth + 1)
            if el is not None:
                return el

            # 형제 프레임을 보려면 부모로 되돌아가야 한다
            self.driver.switch_to.default_content()
            self._enter_frame_index_path(path)
        return None

    def _enter_frame_index_path(self, path: list) -> bool:
        for idx in path:
            try:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
                self.driver.switch_to.frame(frames[idx])
            except (WebDriverException, IndexError):
                return False
        return True

    def _enter_frame_path(self, frames: list) -> bool:
        """저장된 프레임 경로(선택자 또는 순번)를 따라 이동한다."""
        for f in frames:
            try:
                if isinstance(f, int):
                    self.driver.switch_to.frame(f)
                elif isinstance(f, str):
                    self.driver.switch_to.frame(
                        self.driver.find_element(By.CSS_SELECTOR, f))
                elif isinstance(f, dict):
                    sel = f.get("css")
                    if sel:
                        self.driver.switch_to.frame(
                            self.driver.find_element(By.CSS_SELECTOR, sel))
                    else:
                        self.driver.switch_to.frame(int(f.get("index", 0)))
                else:
                    return False
            except (WebDriverException, ValueError, IndexError):
                return False
        return True

    def _pick_best(self, target: Target, visible_only: bool):
        """
        현재 프레임에서 전략을 점수 순으로 시도하고,
        여러 개가 걸리면 '보이고 + 활성이고 + 화면 위쪽'인 요소를 고른다.
        """
        for st in target.sorted_strategies():
            cands = self._find_by_strategy(st)
            if not cands:
                continue
            scored = []
            for el in cands:
                s = self._score_element(el, target)
                if s < 0 and visible_only:
                    continue
                scored.append((s, el))
            if not scored:
                continue
            scored.sort(key=lambda p: -p[0])
            return scored[0][1]
        return None

    def _find_by_strategy(self, st: Strategy) -> list:
        if st.by == BY_DEEP:
            return self._deep_find(st.value)
        try:
            found = self.driver.find_elements(st.by, st.value)
        except (WebDriverException, NoSuchElementException):
            found = []
        # 평범한 방법으로 못 찾았을 때만 Shadow DOM 안쪽을 뒤진다(느리기 때문)
        if not found and self._allow_deep and st.by == By.CSS_SELECTOR:
            found = self._deep_find(st.value)
        return found

    def _deep_find(self, css: str) -> list:
        """Shadow DOM 경계를 넘어가며 CSS 선택자로 요소를 찾는다."""
        try:
            if not self.driver.execute_script(HAS_SHADOW_JS):
                return []
            return self.driver.execute_script(DEEP_QUERY_JS, css) or []
        except (WebDriverException, NoSuchElementException):
            return []

    def _score_element(self, el, target: Target) -> int:
        """요소 하나의 적합도를 매긴다. 음수면 '쓸 수 없는 요소'."""
        try:
            if not el.is_displayed():
                return -1
            score = 100
            if not el.is_enabled():
                score -= 40
            rect = el.rect
            if rect.get("width", 0) <= 1 or rect.get("height", 0) <= 1:
                return -1
            # 화면 위쪽/왼쪽에 있을수록 사용자가 의도한 요소일 확률이 높다
            score -= min(int(rect.get("y", 0)) // 200, 20)
            if target.tag and el.tag_name.lower() == target.tag.lower():
                score += 15
            if target.text:
                txt = (el.text or el.get_attribute("value") or "").strip()
                if txt == target.text:
                    score += 25
                elif target.text in txt:
                    score += 10
            return score
        except (StaleElementReferenceException, WebDriverException):
            return -1
