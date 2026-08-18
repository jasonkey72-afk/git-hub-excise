"""
단계 등록/수정 창
==================
"무엇을(대상) / 어떻게(동작) / 어떤 값으로(값)" 세 가지만 채우면 한 단계가 완성된다.
가장 중요한 기능은 [브라우저에서 요소 선택] 버튼으로,
사용자가 화면에서 직접 클릭한 요소의 선택자를 자동으로 등록해 준다.
"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import theme as T
from ..core.models import ACTIONS, Step, action_label
from ..webauto.locator import Target, parse_target
from ..webauto import picker

FILE_ACTIONS = {"run_program", "open_file", "upload", "screenshot"}

# [지금 실행] 이 지원하는 동작. 요소를 실제로 클릭/입력해 화면을 진짜로 넘겨야
# 다음 단계를 그 화면에서 이어서 고를 수 있다(요소 선택기는 이동을 막아 두므로).
RUN_NOW_ACTIONS = {"open_browser", "open_url", "click", "input", "select", "check",
                   "hover", "key", "wait_element", "wait_text", "scroll"}


def action_choices() -> list:
    return ["%s · %s" % (info["group"], info["label"]) for info in ACTIONS.values()]


def choice_to_code(choice: str) -> str:
    for code, info in ACTIONS.items():
        if "%s · %s" % (info["group"], info["label"]) == choice:
            return code
    return "click"


def code_to_choice(code: str) -> str:
    info = ACTIONS.get(code)
    return "%s · %s" % (info["group"], info["label"]) if info else action_choices()[0]


class StepDialog(tk.Toplevel):
    """단계 하나를 편집하는 모달 창. 결과는 self.result (Step 또는 None)."""

    def __init__(self, parent, step: Step, session, scenario,
                 index: int = 0, total: int = 0, wizard: bool = False):
        super().__init__(parent)
        self.result = None
        self.session = session
        self.scenario = scenario
        self.step = step
        self.wizard = wizard
        self._target = Target.from_dict(step.target) if step.target else Target()
        self._picking = False
        self._running = False

        self.title("%d단계 등록  (%d / %d)" % (index + 1, index + 1, total)
                   if total else "단계 편집")
        self.configure(bg=T.BG)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._build()
        self._load(step)
        self._on_action_change()

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda e: self._cancel())
        self.update_idletasks()
        self._center(parent)
        self.name_entry.focus_set()

    # -- 화면 구성 ---------------------------------------------------------

    def _build(self):
        head = tk.Frame(self, bg=T.DARK)
        head.pack(fill="x")
        tk.Label(head, text=self.title(), bg=T.DARK, fg="#ffffff",
                 font=T.FONT_H2).pack(side="left", padx=18, pady=12)

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=16, pady=14)

        box = T.card(body)
        box.pack(fill="both", expand=True)
        grid = tk.Frame(box, bg=T.CARD)
        grid.pack(fill="both", expand=True, padx=18, pady=16)
        grid.columnconfigure(1, weight=1)

        row = 0
        self._label(grid, "단계 이름", row)
        self.name_entry = ttk.Entry(grid, width=52)
        self.name_entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)

        row += 1
        self._label(grid, "동작", row)
        self.action_cb = ttk.Combobox(grid, values=action_choices(), state="readonly",
                                      width=50)
        self.action_cb.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        self.action_cb.bind("<<ComboboxSelected>>", lambda e: self._on_action_change())

        row += 1
        self.target_label = self._label(grid, "대상 요소", row)
        self.target_entry = ttk.Entry(grid)
        self.target_entry.grid(row=row, column=1, sticky="ew", pady=4)
        self.pick_btn = T.Button(grid, "브라우저에서 선택", self._pick, kind="primary")
        self.pick_btn.grid(row=row, column=2, sticky="w", padx=(8, 0))

        row += 1
        self.target_hint = tk.Label(
            grid, bg=T.CARD, fg=T.MUTED, font=T.FONT_SM, justify="left",
            text="직접 적을 수도 있습니다:  text=조회   label=사번   id=userId   "
                 "#loginBtn   //button[text()='확인']")
        self.target_hint.grid(row=row, column=1, columnspan=2, sticky="w")

        row += 1
        self.target_state = tk.Label(grid, bg=T.CARD, fg=T.SUCCESS, font=T.FONT_SM,
                                     text="", justify="left", wraplength=430)
        self.target_state.grid(row=row, column=1, columnspan=2, sticky="w", pady=(2, 0))

        row += 1
        self.test_btn = T.Button(grid, "대상 확인(화면에 표시)", self._test_target)
        self.test_btn.grid(row=row, column=1, sticky="w", pady=(4, 8))
        self.clear_btn = T.Button(grid, "대상 지우기", self._clear_target)
        self.clear_btn.grid(row=row, column=2, sticky="w", padx=(8, 0), pady=(4, 8))

        row += 1
        run_now_row = tk.Frame(grid, bg=T.CARD)
        run_now_row.grid(row=row, column=1, columnspan=2, sticky="w", pady=(0, 8))
        self.run_now_btn = T.Button(run_now_row, "실제로 눌러서 화면 넘기기 (다음 단계 아님)",
                                    self._run_now, kind="warn")
        self.run_now_btn.pack(side="left")
        tk.Label(run_now_row, bg=T.CARD, fg=T.MUTED, font=T.FONT_SM, justify="left",
                 text="  이 단계를 실제로 실행해 화면을 넘깁니다. 다음 단계는\n"
                      "  아래 [다음 단계 ▶] 로 등록하세요."
                 ).pack(side="left", padx=(6, 0))

        row += 1
        self.value_label = self._label(grid, "값", row)
        self.value_entry = ttk.Entry(grid)
        self.value_entry.grid(row=row, column=1, sticky="ew", pady=4)
        self.browse_btn = T.Button(grid, "찾아보기", self._browse)
        self.browse_btn.grid(row=row, column=2, sticky="w", padx=(8, 0))

        row += 1
        tk.Label(grid, bg=T.CARD, fg=T.MUTED, font=T.FONT_SM, justify="left",
                 text="매번 바뀌는 값은 {{사번}} 처럼 적으면 실행할 때 물어봅니다. "
                      "({{오늘}}, {{어제}} 는 자동)"
                 ).grid(row=row, column=1, columnspan=2, sticky="w")

        row += 1
        ttk.Separator(grid).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)

        row += 1
        self._label(grid, "대기 시간(초)", row)
        opt = tk.Frame(grid, bg=T.CARD)
        opt.grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
        self.timeout_var = tk.StringVar(value="15")
        ttk.Spinbox(opt, from_=1, to=600, width=6, textvariable=self.timeout_var
                    ).pack(side="left")
        tk.Label(opt, text="   실행 후 쉬기(초)", bg=T.CARD, fg=T.TEXT).pack(side="left")
        self.wait_var = tk.StringVar(value="0.5")
        ttk.Spinbox(opt, from_=0, to=600, increment=0.5, width=6,
                    textvariable=self.wait_var).pack(side="left", padx=(6, 0))

        row += 1
        chk = tk.Frame(grid, bg=T.CARD)
        chk.grid(row=row, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.optional_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(chk, text="실패해도 다음 단계 계속 진행", style="Card.TCheckbutton",
                        variable=self.optional_var).pack(side="left")
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(chk, text="이 단계 사용", style="Card.TCheckbutton",
                        variable=self.enabled_var).pack(side="left", padx=(16, 0))

        row += 1
        self._label(grid, "메모", row)
        self.note_entry = ttk.Entry(grid)
        self.note_entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)

        foot = tk.Frame(self, bg=T.BG)
        foot.pack(fill="x", padx=16, pady=(0, 14))
        T.Button(foot, "취소", self._cancel).pack(side="right")
        T.Button(foot, "다음 단계 ▶" if self.wizard else "저장", self._ok,
                 kind="primary").pack(side="right", padx=(0, 8))
        self.status = tk.Label(foot, text="", bg=T.BG, fg=T.MUTED, font=T.FONT_SM)
        self.status.pack(side="left")

    def _label(self, parent, text, row):
        lb = tk.Label(parent, text=text, bg=T.CARD, fg=T.MUTED, font=T.FONT_SM)
        lb.grid(row=row, column=0, sticky="e", padx=(0, 12), pady=4)
        return lb

    def _center(self, parent):
        w, h = self.winfo_width(), self.winfo_height()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + max((parent.winfo_height() - h) // 3, 0)
        self.geometry("+%d+%d" % (max(x, 0), max(y, 0)))

    # -- 값 채우기 / 읽기 --------------------------------------------------

    def _load(self, step: Step):
        self.name_entry.insert(0, step.name)
        self.action_cb.set(code_to_choice(step.action))
        self.target_entry.insert(0, step.target_text)
        self.value_entry.insert(0, step.value)
        self.timeout_var.set(str(step.timeout or 15))
        self.wait_var.set(str(step.wait_after))
        self.optional_var.set(step.optional)
        self.enabled_var.set(step.enabled)
        self.note_entry.insert(0, step.note)
        self._show_target_state()

    def _show_target_state(self):
        if self._target.is_empty():
            self.target_state.configure(
                text="※ 브라우저에서 선택하면 여러 개의 선택자를 함께 저장해 "
                     "화면이 조금 바뀌어도 잘 찾아갑니다.", fg=T.MUTED)
        else:
            self.target_state.configure(
                text="✔ 등록됨 : %s  (선택자 %d개%s)"
                     % (self._target.desc or self._target.text or "요소",
                        len(self._target.strategies),
                        ", iframe 안" if self._target.frames else ""),
                fg=T.SUCCESS)

    def _on_action_change(self):
        code = choice_to_code(self.action_cb.get())
        info = ACTIONS.get(code, {})
        need_target = info.get("need_target", False)
        value_label = info.get("value_label", "")

        state = "normal" if need_target else "disabled"
        self.target_entry.configure(state=state)
        for btn in (self.pick_btn, self.test_btn, self.clear_btn):
            btn.configure(state=state)
        self.run_now_btn.configure(state="normal" if code in RUN_NOW_ACTIONS else "disabled")
        self.target_label.configure(fg=T.MUTED if need_target else "#c3c9d4")
        self.target_hint.configure(fg=T.MUTED if need_target else "#c3c9d4")

        if value_label:
            self.value_label.configure(text=value_label, fg=T.MUTED)
            self.value_entry.configure(state="normal")
        else:
            self.value_label.configure(text="값 (사용 안 함)", fg="#c3c9d4")
            self.value_entry.configure(state="disabled")
        self.browse_btn.configure(state="normal" if code in FILE_ACTIONS else "disabled")

        if not self.name_entry.get().strip():
            self.name_entry.delete(0, "end")
            self.name_entry.insert(0, action_label(code))

    def _browse(self):
        code = choice_to_code(self.action_cb.get())
        if code == "open_file":
            path = filedialog.askopenfilename(parent=self, title="열 파일 선택")
        elif code == "screenshot":
            path = filedialog.asksaveasfilename(parent=self, defaultextension=".png",
                                                title="캡처 저장 위치")
        elif code == "run_program":
            path = filedialog.askopenfilename(
                parent=self, title="실행 파일 선택",
                filetypes=[("실행 파일", "*.exe;*.bat;*.cmd"), ("모든 파일", "*.*")])
        else:
            path = filedialog.askopenfilename(parent=self, title="첨부할 파일 선택")
        if path:
            self.value_entry.delete(0, "end")
            self.value_entry.insert(0, path)

    def _clear_target(self):
        self._target = Target()
        self.target_entry.delete(0, "end")
        self._show_target_state()

    # -- 요소 선택 / 확인 --------------------------------------------------

    def _first_url(self) -> str:
        for st in self.scenario.steps:
            if st.action in ("open_url", "open_browser") and st.value.strip():
                return st.value.strip()
        return ""

    def _pick(self):
        """연결된 브라우저에서 사용자가 요소를 클릭할 때까지 기다린다(백그라운드)."""
        if self._picking or self._running:
            return
        self._picking = True
        self.pick_btn.configure(state="disabled", text="선택 대기중...")
        self.status.configure(text="브라우저 화면에서 요소를 클릭하세요. (ESC = 취소)",
                              fg=T.PRIMARY)
        holder = {}

        def work():
            try:
                web = self.session.require_browser()
                try:
                    # 직전 단계의 클릭이 새 창/새 탭을 열었을 수 있다(예: 링크를
                    # target=_blank 로 여는 아이콘). 픽 모드의 클릭은 실제 이동을
                    # 막아 두므로, 사용자가 [실제로 눌러서 화면 넘기기] 나 직접
                    # 클릭으로 새 탭을 열었다면 거기서 골라야 한다 -> 항상 가장
                    # 최근에 열린 탭으로 옮겨 둔 뒤 선택기를 띄운다.
                    web.switch_tab(-1)
                except Exception:
                    pass
                url = web.current_url() or ""
                if url in ("", "about:blank", "data:,") and self._first_url():
                    web.open_url(self._first_url())
                holder["target"] = web.pick_element(on_status=lambda m: None)
            except Exception as e:                     # 창을 닫았거나 취소한 경우
                holder["error"] = e

        threading.Thread(target=work, daemon=True).start()
        self.after(200, lambda: self._poll_pick(holder))

    def _poll_pick(self, holder: dict):
        if not holder:
            self.after(200, lambda: self._poll_pick(holder))
            return
        self._picking = False
        self.pick_btn.configure(state="normal", text="브라우저에서 선택")

        if "target" in holder:
            self._target = holder["target"]
            self.target_entry.delete(0, "end")
            self.target_entry.insert(0, self._target.desc or self._target.text)
            self.status.configure(text="요소를 등록했습니다.", fg=T.SUCCESS)
            self._show_target_state()
            self.lift()
            self.focus_force()
        else:
            err = holder.get("error")
            if isinstance(err, picker.PickCancelled):
                self.status.configure(text="선택을 취소했습니다.", fg=T.MUTED)
            else:
                self.status.configure(text="선택 실패: %s" % err, fg=T.DANGER)

    def _test_target(self):
        """지금 지정한 대상을 실제로 찾아 화면에 표시해 본다."""
        target = self._current_target()
        if target.is_empty():
            messagebox.showinfo("확인", "먼저 대상을 지정해 주세요.", parent=self)
            return
        self.status.configure(text="대상을 찾는 중...", fg=T.PRIMARY)
        self.update_idletasks()
        try:
            web = self.session.require_browser()
            el = web.find(target, timeout=8)
            web.highlight(el)
            self.status.configure(text="찾았습니다 : %s" % web.element_label(el),
                                  fg=T.SUCCESS)
        except Exception as e:
            self.status.configure(text="찾지 못했습니다: %s" % str(e)[:90], fg=T.DANGER)

    def _run_now(self):
        """
        지금 입력한 대로 실제로 동작을 수행한다.
        [브라우저에서 선택] 은 화면을 넘기지 않도록 클릭을 막아 두므로(사이트에
        실수로 어떤 동작을 일으키지 않기 위함), '클릭' 처럼 다음 화면으로 넘어가는
        단계를 등록한 뒤에도 브라우저는 계속 첫 화면에 머물러 있다.
        이 버튼으로 실제로 실행해 화면을 넘긴 뒤, 다음 단계는 그 화면에서 이어서
        [브라우저에서 선택] 하면 된다.
        """
        if self._picking or self._running:
            return
        code = choice_to_code(self.action_cb.get())
        if code not in RUN_NOW_ACTIONS:
            return
        info = ACTIONS.get(code, {})
        value = self.value_entry.get().strip() if str(self.value_entry["state"]) != "disabled" else ""
        try:
            timeout = float(self.timeout_var.get() or 15)
        except ValueError:
            timeout = 15.0

        target = None
        if info.get("need_target"):
            target = self._current_target()
            if target.is_empty():
                messagebox.showwarning(
                    "확인 필요", "이 동작은 대상 요소가 필요합니다.\n"
                    "[브라우저에서 선택] 을 누르거나 직접 입력해 주세요.", parent=self)
                return
        if info.get("value_label") and not value and code in ("open_url", "wait_text"):
            messagebox.showwarning("확인 필요", "'%s' 을(를) 입력해 주세요." % info["value_label"],
                                   parent=self)
            return

        self._running = True
        self.run_now_btn.configure(state="disabled", text="실행 중...")
        self.status.configure(text="실행하는 중...", fg=T.PRIMARY)
        holder = {}

        def work():
            try:
                web = self.session.require_browser()
                if code == "open_browser":
                    if value:
                        web.open_url(value)
                elif code == "open_url":
                    web.open_url(value)
                elif code == "click":
                    web.click(target, timeout)
                elif code == "input":
                    web.input_text(target, value, timeout)
                elif code == "select":
                    web.select_option(target, value, timeout)
                elif code == "check":
                    web.set_checkbox(target, value.strip() not in ("끄기", "off", "false", "0"),
                                     timeout)
                elif code == "hover":
                    web.hover(target, timeout)
                elif code == "key":
                    web.press_key(value or "enter",
                                  target if target is not None and not target.is_empty() else None,
                                  timeout)
                elif code == "wait_element":
                    web.wait_element(target, timeout)
                elif code == "wait_text":
                    web.wait_text(value, timeout)
                elif code == "scroll":
                    web.scroll(value or "bottom")
            except Exception as e:
                holder["error"] = e
            self.after(0, lambda: self._run_now_done(holder))

        threading.Thread(target=work, daemon=True).start()

    def _run_now_done(self, holder: dict):
        self._running = False
        self.run_now_btn.configure(state="normal", text="실제로 눌러서 화면 넘기기 (다음 단계 아님)")
        if "error" in holder:
            self.status.configure(text="실행 실패: %s" % str(holder["error"])[:90], fg=T.DANGER)
        else:
            self.status.configure(
                text="실행했습니다. 이 화면에서 이어서 [브라우저에서 선택] 하세요.", fg=T.SUCCESS)

    def _current_target(self) -> Target:
        text = self.target_entry.get().strip()
        if not self._target.is_empty():
            t = Target.from_dict(self._target.to_dict())
            if text:
                t.desc = text
            return t
        return parse_target(text)

    # -- 확인 / 취소 -------------------------------------------------------

    def _ok(self):
        code = choice_to_code(self.action_cb.get())
        info = ACTIONS.get(code, {})
        target_text = self.target_entry.get().strip()
        value = self.value_entry.get().strip() if str(self.value_entry["state"]) != "disabled" else ""

        if info.get("need_target") and self._target.is_empty() and not target_text:
            messagebox.showwarning(
                "확인 필요", "이 동작은 대상 요소가 필요합니다.\n"
                "[브라우저에서 선택] 을 누르거나 직접 입력해 주세요.", parent=self)
            return
        if info.get("value_label") and not value and code in (
                "open_url", "input", "select", "wait_text", "run_program",
                "open_file", "run_command", "upload", "ask", "message"):
            messagebox.showwarning("확인 필요", "'%s' 을(를) 입력해 주세요."
                                   % info["value_label"], parent=self)
            return

        step = self.step
        step.name = self.name_entry.get().strip() or action_label(code)
        step.action = code
        step.target = self._target.to_dict() if not self._target.is_empty() else {}
        step.target_text = target_text
        step.value = value
        step.timeout = float(self.timeout_var.get() or 15)
        step.wait_after = float(self.wait_var.get() or 0)
        step.optional = bool(self.optional_var.get())
        step.enabled = bool(self.enabled_var.get())
        step.note = self.note_entry.get().strip()

        self.result = step
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()
