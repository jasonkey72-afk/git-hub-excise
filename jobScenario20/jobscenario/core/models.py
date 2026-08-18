"""
시나리오 데이터 모델
=====================
시나리오(Scenario) = 제목 + 단계(Step) 목록.
단계 하나 = "무엇을(대상) 어떻게(동작) 어떤 값으로(값)".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# 동작(Action) 목록 — UI 의 선택 항목과 실행 엔진이 함께 참조한다.
#   need_target : 화면에서 대상 요소를 지정해야 하는가
#   value_label : 값 입력칸의 이름 (빈 문자열이면 값 없음)
# ---------------------------------------------------------------------------

ACTIONS = {
    # --- 웹 사이트 ---
    "open_browser": {"label": "브라우저 열기",       "group": "웹", "need_target": False, "value_label": "이동할 주소(선택, 비우면 지금 화면 유지)"},
    "open_url":     {"label": "웹사이트 열기",       "group": "웹", "need_target": False, "value_label": "주소(URL)"},
    "click":        {"label": "클릭",                "group": "웹", "need_target": True,  "value_label": ""},
    "input":        {"label": "입력창에 입력",       "group": "웹", "need_target": True,  "value_label": "입력할 값"},
    "select":       {"label": "목록에서 선택",       "group": "웹", "need_target": True,  "value_label": "선택할 항목"},
    "check":        {"label": "체크박스 켜기/끄기",  "group": "웹", "need_target": True,  "value_label": "켜기/끄기"},
    "hover":        {"label": "마우스 올리기(메뉴)", "group": "웹", "need_target": True,  "value_label": ""},
    "key":          {"label": "키 입력",             "group": "웹", "need_target": False, "value_label": "키(enter, tab, f5...)"},
    "upload":       {"label": "파일 첨부",           "group": "웹", "need_target": True,  "value_label": "첨부할 파일 경로"},
    "wait_element": {"label": "요소가 나타날 때까지 대기", "group": "웹", "need_target": True, "value_label": ""},
    "wait_text":    {"label": "글자가 나타날 때까지 대기", "group": "웹", "need_target": False, "value_label": "기다릴 글자"},
    "get_text":     {"label": "값 읽어 변수에 저장", "group": "웹", "need_target": True,  "value_label": "저장할 변수명"},
    "switch_tab":   {"label": "탭 전환",             "group": "웹", "need_target": False, "value_label": "탭 번호(-1=마지막)"},
    "close_tab":    {"label": "탭 닫기",             "group": "웹", "need_target": False, "value_label": ""},
    "alert":        {"label": "알림창 확인/취소",    "group": "웹", "need_target": False, "value_label": "확인/취소"},
    "scroll":       {"label": "스크롤",              "group": "웹", "need_target": False, "value_label": "top / bottom / 숫자"},
    "screenshot":   {"label": "화면 캡처 저장",      "group": "웹", "need_target": False, "value_label": "저장 파일 경로"},
    "script":       {"label": "자바스크립트 실행",   "group": "웹", "need_target": False, "value_label": "스크립트"},
    # --- 사내 프로그램 / 기타 ---
    "run_program":  {"label": "프로그램 실행",       "group": "프로그램", "need_target": False, "value_label": "실행 파일 경로"},
    "open_file":    {"label": "파일/폴더 열기",      "group": "프로그램", "need_target": False, "value_label": "파일 또는 폴더 경로"},
    "run_command":  {"label": "명령 실행",           "group": "프로그램", "need_target": False, "value_label": "명령어"},
    "sleep":        {"label": "잠시 기다리기",       "group": "프로그램", "need_target": False, "value_label": "초"},
    "ask":          {"label": "사용자에게 값 입력받기", "group": "프로그램", "need_target": False, "value_label": "변수명"},
    "message":      {"label": "안내 메시지 표시",    "group": "프로그램", "need_target": False, "value_label": "메시지"},
}

ACTION_LABELS = {code: info["label"] for code, info in ACTIONS.items()}
LABEL_TO_ACTION = {info["label"]: code for code, info in ACTIONS.items()}


def action_label(code: str) -> str:
    return ACTION_LABELS.get(code, code)


# ---------------------------------------------------------------------------
# 변수 치환 : {{사번}}, {{오늘}} 처럼 매번 바뀌는 값을 넣을 수 있다.
# ---------------------------------------------------------------------------

_VAR_PAT = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def builtin_vars() -> dict:
    now = datetime.now()
    return {
        "오늘": now.strftime("%Y-%m-%d"),
        "today": now.strftime("%Y-%m-%d"),
        "오늘8": now.strftime("%Y%m%d"),
        "어제": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        "이번달": now.strftime("%Y-%m"),
        "지금": now.strftime("%H:%M:%S"),
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def expand_vars(text: str, variables: dict) -> str:
    """문자열 안의 {{변수}} 를 실제 값으로 바꾼다."""
    if not text or "{{" not in text:
        return text or ""
    table = dict(builtin_vars())
    table.update(variables or {})

    def sub(m):
        key = m.group(1)
        return str(table.get(key, m.group(0)))

    return _VAR_PAT.sub(sub, text)


def find_vars(text: str) -> list:
    """문자열에서 사용된 변수 이름을 모두 뽑아낸다."""
    if not text:
        return []
    known = set(builtin_vars())
    return [v for v in _VAR_PAT.findall(text) if v not in known]


# ---------------------------------------------------------------------------
# 단계 / 시나리오
# ---------------------------------------------------------------------------

@dataclass
class Step:
    """시나리오의 한 단계."""

    name: str = ""                       # 단계 이름 (예: "그룹웨어 로그인")
    action: str = "click"                # ACTIONS 의 키
    target: dict = field(default_factory=dict)   # 웹 요소 지정(Target.to_dict())
    target_text: str = ""                # 사람이 직접 입력한 대상 표현 (예: text=조회)
    value: str = ""                      # 입력값 / 주소 / 경로 등
    timeout: float = 15.0                # 대상을 기다릴 최대 시간(초)
    wait_after: float = 0.5              # 단계 실행 후 쉬는 시간(초)
    optional: bool = False               # True 면 실패해도 다음 단계로 진행
    enabled: bool = True                 # False 면 건너뛴다
    note: str = ""                       # 메모

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Step":
        s = Step()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                setattr(s, k, v)
        s.timeout = float(s.timeout or 15.0)
        s.wait_after = float(s.wait_after or 0)
        return s

    def display_target(self) -> str:
        """목록에 보여줄 대상 설명."""
        if self.target_text:
            return self.target_text
        desc = (self.target or {}).get("desc", "")
        return desc or "-"

    def summary(self) -> str:
        parts = [action_label(self.action)]
        tgt = self.display_target()
        if tgt and tgt != "-":
            parts.append("[%s]" % tgt)
        if self.value:
            parts.append("= %s" % self.value)
        return " ".join(parts)


@dataclass
class Scenario:
    """업무 하나를 자동화하는 단계 묶음."""

    title: str = ""                      # 업무 제목 (Scenario 명)
    description: str = ""
    browser: str = "edge"
    engine: str = "cdp"                  # cdp = 드라이버 불필요(기본), selenium = 예비
    steps: list = field(default_factory=list)
    keep_browser: bool = True            # 실행 후 브라우저를 열어 둘지
    use_profile: bool = True             # 로그인 유지용 전용 프로필 사용
    created: str = ""
    modified: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "browser": self.browser,
            "engine": self.engine,
            "keep_browser": self.keep_browser,
            "use_profile": self.use_profile,
            "created": self.created,
            "modified": self.modified,
            "steps": [s.to_dict() for s in self.steps],
        }

    @staticmethod
    def from_dict(d: dict) -> "Scenario":
        sc = Scenario()
        for k in ("title", "description", "browser", "engine", "created", "modified"):
            if d.get(k) is not None:
                setattr(sc, k, d[k])
        sc.keep_browser = bool(d.get("keep_browser", True))
        sc.use_profile = bool(d.get("use_profile", True))
        sc.steps = [Step.from_dict(s) for s in d.get("steps", [])]
        return sc

    def required_vars(self) -> list:
        """실행 전에 사용자에게 물어봐야 하는 변수 목록."""
        found, seen = [], set()
        for st in self.steps:
            for v in find_vars(st.value) + find_vars(st.target_text):
                if v not in seen:
                    seen.add(v)
                    found.append(v)
        # ask 단계에서 만들어지는 변수는 물어볼 필요가 없다
        made = {st.value.strip() for st in self.steps if st.action in ("ask", "get_text")}
        return [v for v in found if v not in made]
