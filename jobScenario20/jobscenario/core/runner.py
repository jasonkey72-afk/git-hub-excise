"""
시나리오 실행 엔진
===================
같은 엔진으로 두 가지 실행 방식을 모두 지원한다.
  · 전체 일괄 실행 : 1단계부터 마지막 단계까지 한 번에
  · 단계별 실행    : 선택한 한 단계만 (브라우저는 그대로 열어 두므로 이어서 실행 가능)

브라우저는 Session 이 들고 있으며, 사용자가 닫기 전까지 유지된다.
따라서 "3단계에서 실패 -> 고친 뒤 3단계만 다시 실행" 이 가능하다.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

from ..webauto.driver import create_driver, data_dir
from ..webauto.actions import WebActions
from ..webauto.cdp import CDPActions
from ..webauto.cdp.launcher import attach_existing
from ..webauto.locator import Target, parse_target, ElementNotFound
from . import programs
from .models import Scenario, Step, expand_vars, action_label

# 단계 상태
READY, RUNNING, DONE, FAILED, SKIPPED = "대기", "실행중", "완료", "실패", "건너뜀"


class StepError(Exception):
    """단계 실행 실패. 사용자에게 그대로 보여줄 메시지를 담는다."""


class Session:
    """브라우저와 변수를 담아 두는 실행 세션(단계별 실행 사이에도 유지된다)."""

    def __init__(self, log=None):
        self.web = None                   # 실행 엔진(CDPActions 또는 WebActions)
        self.variables = {}
        self.log = log or (lambda msg: None)
        self.last_failure_dir = None      # 마지막으로 실패 기록을 남긴 폴더
        self._browser_lock = threading.Lock()

    @property
    def browser_alive(self) -> bool:
        return self.web is not None and self.web.alive

    def require_browser(self):
        """
        지금 연결된 브라우저를 돌려준다. 없으면 오류를 낸다.

        더 이상 브라우저를 자동으로 새로 열지 않는다(사용자가 이미 쓰고 있는
        창을 몰래 새 창으로 바꿔치기하지 않기 위함). 먼저 화면 위쪽의
        '브라우저 선택' 으로 사용할 브라우저(열려 있는 창)를 연결해 두어야 한다.
        """
        if not self.browser_alive:
            raise StepError("연결된 브라우저가 없습니다. 먼저 '브라우저 선택' 으로 "
                            "사용할 브라우저를 골라 주세요.")
        return self.web

    def attach_browser(self, scenario: Scenario, window: dict):
        """
        사용자가 화면에서 고른, 이미 열려 있는 브라우저 창에 연결한다.
        ('브라우저 선택' 버튼의 실제 동작. 여러 스레드가 동시에 부를 수 있으므로
        기존 브라우저를 새로 띄울 때와 같은 잠금을 쓴다.)
        """
        with self._browser_lock:
            self.close_browser()
            engine = (scenario.engine or "cdp").lower()
            downloads = str(data_dir() / "downloads")

            if engine == "cdp":
                self.web = CDPActions.attach(window, download_dir=downloads, log=self.log)
            else:
                self.log("브라우저(%s)를 다시 시작해 연결합니다... (selenium 엔진)"
                         % window.get("browser", "edge"))
                client, proc, port = attach_existing(window, log=self.log)
                client.close()          # selenium 이 자체 세션으로 다시 붙으므로 필요 없다
                driver = create_driver(browser=window.get("browser", "edge"),
                                       attach_port=port, download_dir=downloads,
                                       log=self.log)
                self.web = WebActions(driver, process=proc, log=self.log)
        return self.web

    @property
    def driver(self):
        """예전 코드 호환용(selenium 엔진일 때만 값이 있다)."""
        return getattr(self.web, "driver", None)

    def capture_failure(self, step_no: int, step_name: str, reason: str = "",
                        step: Step = None) -> str:
        """
        실패한 순간의 화면·HTML 과 함께 **실패 이유**를 남긴다.
        "어제는 됐는데 오늘 안 된다" 를 나중에 확인할 수 있는 유일한 단서가 된다.
        무엇을(동작/대상/값) 찾다가 왜 실패했는지 정보.txt 에 그대로 적어 두어야
        화면·HTML 만 보고 원인을 추측하지 않아도 된다.
        저장에 실패해도 원래 오류를 가리면 안 되므로 예외는 삼킨다.
        """
        if not self.browser_alive:
            return ""
        safe = re.sub(r'[\\/:*?"<>|]', "_", step_name or "단계")[:30]
        folder = data_dir() / "failures" / ("%s_%d단계_%s"
                                           % (time.strftime("%Y%m%d_%H%M%S"), step_no, safe))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.web.save_screenshot(str(folder / "화면.png"))
            (folder / "화면.html").write_text(self.web.page_source(), encoding="utf-8")

            lines = [
                "시각: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                "단계: %d단계 %s" % (step_no, step_name),
                "실패 이유: %s" % (reason.strip() if reason and reason.strip()
                                 else "(알 수 없음)"),
                "주소: %s" % self.web.current_url(),
                "제목: %s" % self.web.title(),
                "엔진: %s" % getattr(self.web, "engine_name", "?"),
            ]
            if step is not None:
                lines.append("")
                lines.append("── 이 단계의 설정 ──")
                lines.append("동작: %s" % action_label(step.action))
                if step.target_text:
                    lines.append("직접 적은 대상: %s" % step.target_text)
                strategies = (step.target or {}).get("strategies") or []
                if strategies:
                    lines.append("등록된 선택자 (%d개, 점수 높은 순으로 시도):" % len(strategies))
                    for s in sorted(strategies, key=lambda s: -int(s.get("score", 0))):
                        lines.append("  - [%s] %s = %s (점수 %s)"
                                     % (s.get("kind", "?"), s.get("by", "?"),
                                        s.get("value", ""), s.get("score", "?")))
                if step.value:
                    lines.append("값: %s" % step.value)
                lines.append("대기시간(timeout): %s초" % step.timeout)

            (folder / "정보.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.last_failure_dir = folder
            return str(folder)
        except Exception:
            return ""

    def close_browser(self):
        if self.web is not None:
            self.web.quit()
        self.web = None


class Runner:
    """시나리오 한 개를 실행한다. UI 는 콜백으로 진행 상황을 받는다."""

    def __init__(self, scenario: Scenario, session: Session,
                 log=None, on_step=None, ask=None, notify=None):
        self.scenario = scenario
        self.session = session
        self.log = log or (lambda msg: None)
        self.on_step = on_step or (lambda i, status, msg="": None)
        self.ask = ask or (lambda name: "")
        self.notify = notify or (lambda msg: None)
        self.stop_flag = threading.Event()

    # -- 실행 흐름 ---------------------------------------------------------

    def stop(self):
        self.stop_flag.set()

    def _interruptible_sleep(self, seconds: float):
        """
        '중지' 는 단계 사이에서만 확인하므로, 대기 시간이 긴 sleep/실행 후 쉬기
        단계에서는 그 시간이 다 지나야 멈추는 것처럼 보였다. 짧게 나눠 자면서
        중간에도 중지 요청을 확인한다.
        """
        end = time.time() + max(seconds, 0)
        while time.time() < end and not self.stop_flag.is_set():
            time.sleep(min(0.2, end - time.time()))

    def run_all(self, start: int = 0, end: int = None) -> bool:
        """start 단계부터 end 단계까지 순서대로 실행한다."""
        steps = self.scenario.steps
        end = len(steps) if end is None else min(end, len(steps))
        ok = True
        began = time.time()

        for i in range(start, end):
            if self.stop_flag.is_set():
                self.log("사용자가 실행을 중지했습니다.")
                ok = False
                break
            if not self.run_one(i):
                ok = False
                if not steps[i].optional:
                    break

        self.log("─" * 50)
        self.log("%s (총 %.1f초)" % ("실행을 마쳤습니다." if ok else "실행이 중단되었습니다.",
                                    time.time() - began))
        return ok

    def run_one(self, index: int) -> bool:
        """한 단계만 실행한다. 단계별 실행 버튼도 이 함수를 쓴다."""
        step = self.scenario.steps[index]
        no = index + 1

        if not step.enabled:
            self.on_step(index, SKIPPED)
            self.log("[%d단계] %s → 사용 안 함, 건너뜁니다." % (no, step.name or action_label(step.action)))
            return True

        self.on_step(index, RUNNING)
        self.log("[%d단계] %s" % (no, step.name or step.summary()))
        try:
            result = self._execute(step)
            if step.wait_after:
                self._interruptible_sleep(min(step.wait_after, 60))
            self.on_step(index, DONE, result or "")
            if result:
                self.log("        → %s" % result)
            return True
        except Exception as e:                       # 실행 중 모든 오류를 사용자 언어로
            msg = self._friendly(e, step)
            if step.optional:
                self.on_step(index, SKIPPED, msg)
                self.log("        △ %s (선택 단계라 계속 진행합니다)" % msg)
                return True
            self.on_step(index, FAILED, msg)
            self.log("        ✕ %s" % msg)
            saved = self.session.capture_failure(no, step.name, reason=msg, step=step)
            if saved:
                self.log("        · 실패한 화면을 저장했습니다: %s" % saved)
            return False

    # -- 단계 하나 실행 ----------------------------------------------------

    def _execute(self, step: Step) -> str:
        action = step.action
        value = expand_vars(step.value, self.session.variables)

        # 브라우저가 필요한 동작인지 판단한다
        if action in ("run_program", "open_file", "run_command", "sleep", "ask", "message"):
            return self._execute_local(action, step, value)

        web = self.session.require_browser()
        target = self._target_of(step)

        if action == "open_browser":
            # 이미 열려 있는 브라우저가 있으면 그대로 쓰고(활성화된 브라우저로
            # 자동 전환), 주소를 적어 뒀을 때만 그쪽으로 이동한다.
            if value:
                web.open_url(value)
            return web.title()[:60]
        if action == "open_url":
            web.open_url(value)
            return web.title()[:60]
        if action == "click":
            web.click(target, step.timeout)
            return ""
        if action == "input":
            web.input_text(target, value, step.timeout)
            return ""
        if action == "select":
            web.select_option(target, value, step.timeout)
            return ""
        if action == "check":
            web.set_checkbox(target, value.strip() not in ("끄기", "off", "false", "0"),
                             step.timeout)
            return ""
        if action == "hover":
            web.hover(target, step.timeout)
            return ""
        if action == "key":
            web.press_key(value or "enter", target if not target.is_empty() else None,
                          step.timeout)
            return ""
        if action == "upload":
            web.upload_file(target, value, step.timeout)
            return ""
        if action == "wait_element":
            web.wait_element(target, step.timeout)
            return ""
        if action == "wait_text":
            web.wait_text(value, step.timeout)
            return ""
        if action == "get_text":
            text = web.get_text(target, step.timeout)
            name = (value or "결과").strip()
            self.session.variables[name] = text
            return "%s = %s" % (name, text[:60])
        if action == "switch_tab":
            web.switch_tab(int(value or -1))
            return ""
        if action == "close_tab":
            web.close_tab()
            return ""
        if action == "alert":
            web.handle_alert(value.strip() not in ("취소", "cancel", "dismiss"))
            return ""
        if action == "scroll":
            web.scroll(value or "bottom")
            return ""
        if action == "screenshot":
            path = value or str(data_dir() / "capture" / ("%s.png" % time.strftime("%Y%m%d_%H%M%S")))
            return web.screenshot(path)
        if action == "script":
            out = web.run_script(value)
            return "" if out is None else str(out)[:120]

        raise StepError("알 수 없는 동작입니다: %s" % action)

    def _execute_local(self, action: str, step: Step, value: str) -> str:
        if action == "run_program":
            return programs.run_program(value)
        if action == "open_file":
            return programs.open_path(value)
        if action == "run_command":
            return programs.run_command(value, step.timeout or 300)
        if action == "sleep":
            self._interruptible_sleep(min(float(value or 1), 600))
            return ""
        if action == "ask":
            name = (value or "입력값").strip()
            answer = self.ask(name)
            if answer is None:
                raise StepError("입력이 취소되었습니다.")
            self.session.variables[name] = answer
            return "%s = %s" % (name, answer)
        if action == "message":
            self.notify(value)
            return ""
        raise StepError("알 수 없는 동작입니다: %s" % action)

    # -- 보조 -------------------------------------------------------------

    def _target_of(self, step: Step) -> Target:
        """등록해 둔 요소 정보를 우선 쓰고, 없으면 사용자가 적은 표현을 해석한다."""
        if step.target and step.target.get("strategies"):
            t = Target.from_dict(step.target)
            if step.target_text:
                t.desc = step.target_text
            return t
        return parse_target(expand_vars(step.target_text, self.session.variables))

    def _friendly(self, e: Exception, step: Step) -> str:
        if isinstance(e, ElementNotFound):
            return ("화면에서 대상을 찾지 못했습니다: %s\n"
                    "  (화면이 늦게 뜨면 '대기시간'을 늘리거나, "
                    "'요소 선택' 으로 대상을 다시 지정해 주세요)" % step.display_target())
        if isinstance(e, (StepError, ValueError, FileNotFoundError, RuntimeError)):
            return str(e)
        return "%s: %s" % (type(e).__name__, str(e)[:200])
