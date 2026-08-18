"""
jobScenario 메인 화면
======================
왼쪽 : 저장된 업무 시나리오 목록
오른쪽 : 시나리오 제목 / 단계 목록 / 실행 / 실행 기록

사용 순서
  ① [새 업무 만들기] 로 단계 수(n) 입력  ②단계마다 웹사이트·프로그램 등록
  ③ 마지막에 업무 제목 입력 → 저장       ④[전체 일괄 실행] 또는 [단계별 실행]
"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog, filedialog

from . import theme as T
from .step_dialog import StepDialog
from ..core import storage, programs
from ..core.models import Scenario, Step, action_label
from ..core.runner import Runner, Session, READY, RUNNING, DONE, FAILED, SKIPPED
from ..webauto.driver import data_dir, diagnose, BROWSERS, BROWSER_NAME
from ..webauto.cdp import list_open_windows

APP_TITLE = "jobScenario - 반복 업무 자동화"

# 연결 방식(엔진). 기본은 드라이버가 필요 없는 CDP 직접 연결.
ENGINE_CDP = "드라이버 없이(권장)"
ENGINE_SELENIUM = "selenium 드라이버"
ENGINE_CODE = {ENGINE_CDP: "cdp", ENGINE_SELENIUM: "selenium"}
ENGINE_LABEL = {v: k for k, v in ENGINE_CODE.items()}
SETTINGS = data_dir() / "settings.json"


class JobScenarioApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1220x860")
        self.minsize(1040, 720)
        T.apply_theme(self)

        self.scenario = Scenario()
        self.current_path = None
        self.session = Session(log=self._enqueue_log)
        self.runner = None
        self.run_thread = None
        self.events = queue.Queue()
        self.settings = self._load_settings()

        self._build()
        self._refresh_list()
        self._refresh_steps()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ==================================================================
    # 화면 구성
    # ==================================================================

    def _build(self):
        self._build_header()
        self._build_homebar()
        self._build_statusbar()      # 남은 공간을 body 가 가져가므로 먼저 자리를 잡는다

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=14, pady=(12, 10))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_main(body)

    def _build_header(self):
        head = tk.Frame(self, bg=T.DARK, height=68)
        head.pack(fill="x")
        head.pack_propagate(False)

        left = tk.Frame(head, bg=T.DARK)
        left.pack(side="left", padx=20)
        tk.Label(left, text="jobScenario", bg=T.DARK, fg="#ffffff",
                 font=T.FONT_H1).pack(anchor="w", pady=(10, 0))
        tk.Label(left, text="반복되는 사내 업무를 단계로 등록하고 한 번에 실행합니다",
                 bg=T.DARK, fg="#93c5fd", font=T.FONT_SM).pack(anchor="w")

        right = tk.Frame(head, bg=T.DARK)
        right.pack(side="right", padx=20)
        tk.Label(right, text="연결 방식", bg=T.DARK, fg="#cbd5e1",
                 font=T.FONT_SM).pack(side="left", padx=(0, 6))
        self.engine_var = tk.StringVar(value=self.settings.get("engine", ENGINE_CDP))
        eng = ttk.Combobox(right, values=[ENGINE_CDP, ENGINE_SELENIUM], state="readonly",
                           width=18, textvariable=self.engine_var)
        eng.pack(side="left", padx=(0, 14))
        eng.bind("<<ComboboxSelected>>", lambda e: self._on_engine_change())
        tk.Label(right, text="브라우저", bg=T.DARK, fg="#cbd5e1",
                 font=T.FONT_SM).pack(side="left", padx=(0, 6))
        self.browser_var = tk.StringVar(value=self.settings.get("browser", "edge"))
        cb = ttk.Combobox(right, values=list(BROWSERS), state="readonly", width=8,
                          textvariable=self.browser_var)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_browser_change())
        self.select_browser_btn = T.Button(right, "브라우저 선택", self._select_browser,
                                           kind="dark")
        self.select_browser_btn.pack(side="left", padx=(10, 0))
        self.close_browser_btn = T.Button(right, "닫기", self._close_browser, kind="dark")
        self.close_browser_btn.pack(side="left", padx=(6, 0))

    def _build_homebar(self):
        """
        시작 홈페이지: 연결된 브라우저를 이 주소로 이동시키는 바로가기.
        언제든 바꿀 수 있고(재지정), 바꾼 값은 다음 실행에도 그대로 남는다.
        """
        bar = tk.Frame(self, bg="#eef2f9", height=42)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="시작 홈페이지", bg="#eef2f9", fg=T.MUTED,
                 font=T.FONT_SM).pack(side="left", padx=(20, 6))
        self.home_url_var = tk.StringVar(value=self.settings.get("home_url", "www.naver.com"))
        home_entry = ttk.Entry(bar, textvariable=self.home_url_var, width=40)
        home_entry.pack(side="left", padx=(0, 8))
        home_entry.bind("<Return>", lambda e: self._on_home_url_change())
        home_entry.bind("<FocusOut>", lambda e: self._on_home_url_change())
        T.Button(bar, "지금 이 주소로 이동", self._goto_home_url).pack(side="left")
        tk.Label(bar, text="먼저 '브라우저 선택' 으로 연결한 뒤, 이 주소로 이동할 수 있습니다.",
                 bg="#eef2f9", fg="#9aa3b2", font=T.FONT_SM).pack(side="left", padx=(14, 0))

    def _build_sidebar(self, parent):
        box = T.section(parent, "저장된 업무", "시나리오 목록")
        box.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        box.configure(width=280)
        box.grid_propagate(False)
        wrap = box.body

        self.list_box = tk.Listbox(wrap, font=T.FONT, bd=0, highlightthickness=1,
                                   highlightbackground=T.BORDER, activestyle="none",
                                   selectbackground="#dbeafe", selectforeground=T.TEXT)
        self.list_box.pack(fill="both", expand=True)
        self.list_box.bind("<Double-Button-1>", lambda e: self._load_selected())

        btns = tk.Frame(wrap, bg=T.CARD)
        btns.pack(fill="x", pady=(10, 0))
        T.Button(btns, "새 업무 만들기", self._new_wizard, kind="primary").pack(fill="x")
        row = tk.Frame(btns, bg=T.CARD)
        row.pack(fill="x", pady=(6, 0))
        T.Button(row, "불러오기", self._load_selected).pack(side="left", expand=True, fill="x")
        T.Button(row, "삭제", self._delete_selected).pack(side="left", expand=True,
                                                          fill="x", padx=(6, 0))
        row2 = tk.Frame(btns, bg=T.CARD)
        row2.pack(fill="x", pady=(6, 0))
        T.Button(row2, "파일 열기", self._import_file).pack(side="left", expand=True, fill="x")
        T.Button(row2, "내보내기", self._export_file).pack(side="left", expand=True,
                                                          fill="x", padx=(6, 0))

    def _build_main(self, parent):
        main = tk.Frame(parent, bg=T.BG)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=3)
        main.rowconfigure(3, weight=2)

        # --- 시나리오 정보 ---
        info = T.section(main, "업무 제목 (Scenario 명)", "모든 단계를 등록한 뒤 제목을 정합니다")
        info.grid(row=0, column=0, sticky="ew")
        f = info.body
        f.columnconfigure(1, weight=1)
        tk.Label(f, text="제목", bg=T.CARD, fg=T.MUTED, font=T.FONT_SM
                 ).grid(row=0, column=0, sticky="e", padx=(0, 10))
        self.title_entry = ttk.Entry(f, font=("맑은 고딕", 12))
        self.title_entry.grid(row=0, column=1, sticky="ew", pady=(0, 6))
        T.Button(f, "저장", self._save, kind="success").grid(row=0, column=2, padx=(10, 0))
        tk.Label(f, text="설명", bg=T.CARD, fg=T.MUTED, font=T.FONT_SM
                 ).grid(row=1, column=0, sticky="e", padx=(0, 10))
        self.desc_entry = ttk.Entry(f)
        self.desc_entry.grid(row=1, column=1, sticky="ew")

        # --- 단계 목록 ---
        steps = T.section(main, "단계 등록", "더블클릭하면 수정합니다")
        steps.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        sf = steps.body

        bar = tk.Frame(sf, bg=T.CARD)
        bar.pack(fill="x", pady=(0, 8))
        T.Button(bar, "단계 수(n) 입력", self._set_step_count, kind="primary").pack(side="left")
        T.Button(bar, "단계 추가", self._add_step).pack(side="left", padx=(6, 0))
        T.Button(bar, "수정", self._edit_step).pack(side="left", padx=(6, 0))
        T.Button(bar, "삭제", self._remove_step).pack(side="left", padx=(6, 0))
        T.Button(bar, "▲", self._move_up, width=3).pack(side="left", padx=(12, 0))
        T.Button(bar, "▼", self._move_down, width=3).pack(side="left", padx=(4, 0))
        T.Button(bar, "복제", self._duplicate_step).pack(side="left", padx=(12, 0))

        cols = ("no", "name", "action", "target", "value", "state")
        self.tree = ttk.Treeview(sf, columns=cols, show="headings", selectmode="browse",
                                 height=9)
        for key, text, width, anchor in (
                ("no", "번호", 44, "center"),
                ("name", "단계 이름", 160, "w"),
                ("action", "동작", 130, "w"),
                ("target", "대상", 210, "w"),
                ("value", "값", 180, "w"),
                ("state", "상태", 70, "center")):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=44, anchor=anchor,
                             stretch=key in ("name", "target", "value"))
        vs = ttk.Scrollbar(sf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Double-Button-1>", lambda e: self._edit_step())
        self.tree.tag_configure(DONE, foreground=T.SUCCESS)
        self.tree.tag_configure(FAILED, foreground=T.DANGER)
        self.tree.tag_configure(RUNNING, foreground=T.PRIMARY)
        self.tree.tag_configure(SKIPPED, foreground=T.MUTED)
        self.tree.tag_configure("off", foreground="#b6bcc9")

        # --- 실행 ---
        run = T.section(main, "실행", "전체를 한 번에 돌리거나, 한 단계씩 확인하며 실행합니다")
        run.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        rf = run.body
        self.run_all_btn = T.Button(rf, "▶  전체 일괄 실행", self._run_all, kind="primary")
        self.run_all_btn.pack(side="left")
        self.run_one_btn = T.Button(rf, "▷  선택 단계 실행", self._run_selected)
        self.run_one_btn.pack(side="left", padx=(8, 0))
        self.run_from_btn = T.Button(rf, "▷▷  선택 단계부터 실행", self._run_from_selected)
        self.run_from_btn.pack(side="left", padx=(8, 0))
        self.stop_btn = T.Button(rf, "■  중지", self._stop, kind="danger")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.stop_btn.configure(state="disabled")
        self.progress = ttk.Progressbar(rf, mode="determinate", length=180)
        self.progress.pack(side="right")
        T.Button(rf, "실패 기록 열기", self._open_failures).pack(side="right", padx=(0, 10))

        # --- 로그 ---
        logbox = T.section(main, "실행 기록")
        logbox.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        lf = logbox.body
        self.log_text = tk.Text(lf, height=7, bg="#111827", fg="#e5e7eb", bd=0,
                                font=T.FONT_MONO, insertbackground="#e5e7eb",
                                highlightthickness=0, wrap="word", state="disabled")
        ls = ttk.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ls.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        ls.pack(side="right", fill="y")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg="#e9edf5", height=26)
        bar.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="준비되었습니다.")
        tk.Label(bar, textvariable=self.status_var, bg="#e9edf5", fg=T.MUTED,
                 font=T.FONT_SM).pack(side="left", padx=14, pady=3)
        tk.Label(bar, text="저장 위치: %s" % storage.scenarios_dir(), bg="#e9edf5",
                 fg="#9aa3b2", font=T.FONT_SM).pack(side="right", padx=14)

    # ==================================================================
    # 설정 / 로그
    # ==================================================================

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        try:
            SETTINGS.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        except OSError:
            pass

    def _on_browser_change(self):
        self.scenario.browser = self.browser_var.get()
        self.settings["browser"] = self.scenario.browser
        self._save_settings()

    def _on_home_url_change(self):
        value = self.home_url_var.get().strip() or "www.naver.com"
        self.home_url_var.set(value)
        self.settings["home_url"] = value
        self._save_settings()

    def _goto_home_url(self):
        self._on_home_url_change()
        if not self.session.browser_alive:
            messagebox.showinfo("안내", "먼저 '브라우저 선택' 으로 사용할 브라우저를 "
                                "연결해 주세요.", parent=self)
            return
        url = self.home_url_var.get()
        self._status("입력한 주소로 이동하는 중입니다...")

        def work():
            error = None
            try:
                self.session.web.open_url(url)
            except Exception as e:
                error = e
            self.after(0, lambda: self._on_goto_home_done(error))

        threading.Thread(target=work, daemon=True).start()

    def _on_goto_home_done(self, error: Exception | None):
        if error is not None:
            messagebox.showerror("이동 실패", str(error), parent=self)
            self._status("주소 이동에 실패했습니다.")
        else:
            self._status("이동했습니다.")

    def _on_engine_change(self):
        """연결 방식을 바꾸면 지금 연결된 브라우저는 닫고 다음에 다시 선택해야 한다."""
        label = self.engine_var.get()
        self.settings["engine"] = label
        self._save_settings()
        self.scenario.engine = ENGINE_CODE.get(label, "cdp")
        if self.session.browser_alive:
            self._close_browser(reason="연결 방식을 바꿨습니다.")
        self._status("연결 방식: %s" % label)

    def _enqueue_log(self, msg: str):
        self.events.put(("log", msg))

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), msg))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _status(self, msg: str):
        self.status_var.set(msg)

    def _drain_events(self):
        """작업 스레드가 보낸 사건을 화면에 반영한다(UI 스레드 전용)."""
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self._log(ev[1])
                elif kind == "step":
                    self._set_step_state(ev[1], ev[2], ev[3] if len(ev) > 3 else "")
                elif kind == "progress":
                    self.progress["value"] = ev[1]
                elif kind == "done":
                    self._on_run_finished(ev[1])
                elif kind == "notify":
                    messagebox.showinfo("안내", ev[1], parent=self)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    # ==================================================================
    # 시나리오 목록
    # ==================================================================

    def _refresh_list(self):
        self.list_box.delete(0, "end")
        self._items = storage.list_scenarios()
        for title, count, modified, _ in self._items:
            self.list_box.insert("end", " %s  (%d단계)" % (title, count))
        if not self._items:
            self.list_box.insert("end", " 저장된 업무가 없습니다")

    def _selected_item(self):
        sel = self.list_box.curselection()
        if not sel or not self._items:
            return None
        idx = sel[0]
        return self._items[idx] if idx < len(self._items) else None

    def _load_selected(self):
        item = self._selected_item()
        if not item:
            return
        self._open_scenario(item[3])

    def _open_scenario(self, path):
        try:
            self.scenario = storage.load(path)
        except Exception as e:
            messagebox.showerror("불러오기 실패", str(e), parent=self)
            return
        self.current_path = Path(path)
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, self.scenario.title)
        self.desc_entry.delete(0, "end")
        self.desc_entry.insert(0, self.scenario.description)
        self.browser_var.set(self.scenario.browser or "edge")
        self.engine_var.set(ENGINE_LABEL.get(self.scenario.engine or "cdp", ENGINE_CDP))
        self._refresh_steps()
        self._status("'%s' 을(를) 불러왔습니다. (%d단계)"
                     % (self.scenario.title, len(self.scenario.steps)))
        self._log("업무를 불러왔습니다: %s" % self.scenario.title)

    def _delete_selected(self):
        item = self._selected_item()
        if not item:
            return
        if not messagebox.askyesno("삭제 확인", "'%s' 업무를 삭제할까요?" % item[0],
                                   parent=self):
            return
        storage.delete(item[3])
        self._refresh_list()
        self._status("삭제했습니다.")

    def _import_file(self):
        path = filedialog.askopenfilename(parent=self, title="시나리오 파일 열기",
                                          filetypes=[("시나리오", "*.json")])
        if path:
            self._open_scenario(path)

    def _export_file(self):
        if not self.scenario.steps:
            messagebox.showinfo("안내", "내보낼 단계가 없습니다.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json", title="시나리오 파일로 저장",
            initialfile="%s.json" % storage.safe_name(self.title_entry.get()),
            filetypes=[("시나리오", "*.json")])
        if path:
            self._collect()
            storage.save(self.scenario, path)
            self._status("내보냈습니다: %s" % path)

    # ==================================================================
    # 단계 편집
    # ==================================================================

    def _collect(self):
        self.scenario.title = self.title_entry.get().strip()
        self.scenario.description = self.desc_entry.get().strip()
        self.scenario.browser = self.browser_var.get()
        self.scenario.engine = ENGINE_CODE.get(self.engine_var.get(), "cdp")

    def _refresh_steps(self):
        self.tree.delete(*self.tree.get_children())
        for i, st in enumerate(self.scenario.steps):
            self.tree.insert("", "end", iid=str(i), values=(
                i + 1, st.name or "(이름 없음)", action_label(st.action),
                st.display_target(), st.value, "" if st.enabled else "사용 안 함"),
                tags=() if st.enabled else ("off",))
        self.progress["maximum"] = max(len(self.scenario.steps), 1)
        self.progress["value"] = 0

    def _set_step_state(self, index: int, state: str, message: str = ""):
        iid = str(index)
        if not self.tree.exists(iid):
            return
        vals = list(self.tree.item(iid, "values"))
        vals[5] = state
        if state in (FAILED, SKIPPED) and message:
            # 실패/건너뜀 이유를 목록에서 바로 볼 수 있게 '값' 칸에 보여 준다.
            vals[4] = message.splitlines()[0]
        elif state in (READY, DONE) and 0 <= index < len(self.scenario.steps):
            # 이전 실행에서 남겨 둔 실패 이유가 있으면 원래 설정값으로 되돌린다.
            vals[4] = self.scenario.steps[index].value
        self.tree.item(iid, values=vals, tags=(state,))
        self.tree.see(iid)
        if state in (DONE, SKIPPED, FAILED):
            self.progress["value"] = index + 1

    def _selected_index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _edit_dialog(self, index: int, wizard: bool = False) -> bool:
        self._collect()
        step = self.scenario.steps[index]
        dlg = StepDialog(self, step, self.session, self.scenario,
                         index=index, total=len(self.scenario.steps), wizard=wizard)
        self.wait_window(dlg)
        if dlg.result is None:
            return False
        self.scenario.steps[index] = dlg.result
        self._refresh_steps()
        self.tree.selection_set(str(index))
        return True

    def _set_step_count(self):
        """요구사항: 사용자가 'n단계' 를 직접 입력하게 한다."""
        n = simpledialog.askinteger(
            "단계 수 입력", "이 업무는 몇 단계로 이루어져 있나요?  (n)",
            parent=self, minvalue=1, maxvalue=100,
            initialvalue=max(len(self.scenario.steps), 3))
        if not n:
            return None
        cur = len(self.scenario.steps)
        if n > cur:
            for i in range(cur, n):
                self.scenario.steps.append(Step(name="%d단계" % (i + 1)))
        elif n < cur:
            if not messagebox.askyesno(
                    "확인", "단계 수를 %d개로 줄이면 뒤쪽 %d개 단계가 삭제됩니다. 계속할까요?"
                            % (n, cur - n), parent=self):
                return None
            del self.scenario.steps[n:]
        self._refresh_steps()
        self._status("%d단계로 구성했습니다. 각 단계를 등록해 주세요." % n)
        return n

    def _new_wizard(self):
        """새 업무 만들기 : n단계 입력 → 단계별 등록 → 마지막에 제목 입력."""
        if self.scenario.steps and not messagebox.askyesno(
                "새 업무", "현재 편집 중인 내용을 지우고 새로 만들까요?", parent=self):
            return
        self.scenario = Scenario(browser=self.browser_var.get())
        self.current_path = None
        self.title_entry.delete(0, "end")
        self.desc_entry.delete(0, "end")
        self._refresh_steps()

        n = self._set_step_count()
        if not n:
            return

        for i in range(n):
            if not self._edit_dialog(i, wizard=True):
                self._status("단계 등록을 중단했습니다. 나머지는 나중에 채울 수 있습니다.")
                return

        # 모든 단계 완료 → 업무 제목 입력
        title = simpledialog.askstring(
            "업무 제목", "모든 단계를 등록했습니다.\n어떤 업무인가요? (Scenario 명)",
            parent=self)
        if title:
            self.title_entry.delete(0, "end")
            self.title_entry.insert(0, title.strip())
            self._save()
        else:
            self._status("제목을 입력하면 저장됩니다.")

    def _add_step(self):
        self.scenario.steps.append(Step(name="%d단계" % (len(self.scenario.steps) + 1)))
        idx = len(self.scenario.steps) - 1
        if not self._edit_dialog(idx):
            self.scenario.steps.pop()
            self._refresh_steps()

    def _edit_step(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("안내", "수정할 단계를 선택해 주세요.", parent=self)
            return
        self._edit_dialog(idx)

    def _remove_step(self):
        idx = self._selected_index()
        if idx is None:
            return
        del self.scenario.steps[idx]
        self._refresh_steps()

    def _duplicate_step(self):
        idx = self._selected_index()
        if idx is None:
            return
        copy = Step.from_dict(self.scenario.steps[idx].to_dict())
        copy.name = copy.name + " (복사)"
        self.scenario.steps.insert(idx + 1, copy)
        self._refresh_steps()
        self.tree.selection_set(str(idx + 1))

    def _move_up(self):
        idx = self._selected_index()
        if idx is None or idx == 0:
            return
        steps = self.scenario.steps
        steps[idx - 1], steps[idx] = steps[idx], steps[idx - 1]
        self._refresh_steps()
        self.tree.selection_set(str(idx - 1))

    def _move_down(self):
        idx = self._selected_index()
        if idx is None or idx >= len(self.scenario.steps) - 1:
            return
        steps = self.scenario.steps
        steps[idx + 1], steps[idx] = steps[idx], steps[idx + 1]
        self._refresh_steps()
        self.tree.selection_set(str(idx + 1))

    def _save(self):
        self._collect()
        if not self.scenario.title:
            title = simpledialog.askstring("업무 제목", "업무 제목(Scenario 명)을 입력해 주세요.",
                                           parent=self)
            if not title:
                return
            self.title_entry.delete(0, "end")
            self.title_entry.insert(0, title.strip())
            self.scenario.title = title.strip()
        if not self.scenario.steps:
            messagebox.showinfo("안내", "등록된 단계가 없습니다.", parent=self)
            return
        path = storage.save(self.scenario, self.current_path)
        self.current_path = path
        self._refresh_list()
        self._status("저장했습니다: %s" % path.name)
        self._log("저장 완료: %s (%d단계)" % (self.scenario.title, len(self.scenario.steps)))

    # ==================================================================
    # 실행
    # ==================================================================

    def _set_browser_buttons(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.select_browser_btn.configure(state=state)
        self.close_browser_btn.configure(state=state)

    def _select_browser(self):
        """
        열려 있는 Edge/Chrome 창을 찾아 목록으로 보여 주고, 사용자가 고른 창에
        연결한다. 더 이상 자동화 전용으로 새 브라우저를 열지 않는다 -- 사용자가
        이미 쓰고 있는 화면(로그인 등)을 그대로 이어서 쓸 수 있게 하기 위함이다.

        다만 CDP 로 붙으려면 디버깅 포트가 필요한데, 평소처럼 켠 브라우저는
        그 포트가 없다. 그래서 고른 창을 잠깐 닫았다가 디버깅 포트를 켜서 즉시
        다시 연다('마지막 세션 복원' 으로 열려 있던 탭을 되살린다). 이 과정에서
        진짜로 그 브라우저가 한 번 닫히므로, 시작 전에 반드시 사용자 확인을 받는다.
        """
        if getattr(self, "_browser_busy", False):
            return
        windows = [w for w in list_open_windows() if w.get("browser") in BROWSERS]
        if not windows:
            messagebox.showinfo(
                "안내", "열려 있는 Edge 또는 Chrome 창을 찾지 못했습니다.\n"
                      "자동화할 브라우저를 먼저 열어 주세요.", parent=self)
            return
        window = self._ask_window_choice(windows)
        if window is None:
            return
        if not messagebox.askyesno(
                "브라우저 선택",
                "선택한 창에 연결하려면 그 브라우저를 잠깐 껐다가 다시 켜야 합니다.\n\n"
                "· 창 제목: %s\n"
                "· 열려 있던 탭은 대개 그대로 복원되지만, 저장하지 않은 내용은 "
                "사라질 수 있습니다. 먼저 저장해 주세요.\n\n계속할까요?"
                % window["title"], parent=self):
            return

        self._collect()
        scenario = self.scenario
        self._browser_busy = True
        self._set_browser_buttons(True)
        self._status("선택한 브라우저에 연결하는 중입니다... (창이 잠깐 닫혔다 다시 열립니다)")

        def work():
            error = None
            try:
                self.session.attach_browser(scenario, window)
            except Exception as e:
                error = e
            self.after(0, lambda: self._on_select_browser_done(error))

        threading.Thread(target=work, daemon=True).start()

    def _ask_window_choice(self, windows: list) -> dict | None:
        """열려 있는 브라우저 창 목록에서 하나를 고르는 작은 창."""
        dlg = tk.Toplevel(self)
        dlg.title("브라우저 선택")
        dlg.configure(bg=T.BG)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        tk.Label(dlg, text="연결할 브라우저 창을 선택하세요.", bg=T.BG, fg=T.TEXT,
                 font=T.FONT).pack(padx=16, pady=(14, 6), anchor="w")
        box = tk.Listbox(dlg, width=72, height=min(max(len(windows), 3), 10),
                         font=T.FONT, activestyle="none")
        box.pack(padx=16, pady=(0, 8), fill="both", expand=True)
        for w in windows:
            box.insert("end", "[%s] %s" % (BROWSER_NAME.get(w["browser"], w["browser"]),
                                           w["title"]))
        box.selection_set(0)

        result = {}

        def choose():
            sel = box.curselection()
            if sel:
                result["window"] = windows[sel[0]]
            dlg.destroy()

        def cancel():
            dlg.destroy()

        box.bind("<Double-Button-1>", lambda e: choose())
        foot = tk.Frame(dlg, bg=T.BG)
        foot.pack(fill="x", padx=16, pady=(0, 14))
        T.Button(foot, "취소", cancel).pack(side="right")
        T.Button(foot, "선택", choose, kind="primary").pack(side="right", padx=(0, 8))
        dlg.protocol("WM_DELETE_WINDOW", cancel)

        dlg.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - dlg.winfo_width()) // 2
        y = self.winfo_rooty() + max((self.winfo_height() - dlg.winfo_height()) // 3, 0)
        dlg.geometry("+%d+%d" % (max(x, 0), max(y, 0)))
        self.wait_window(dlg)
        return result.get("window")

    def _on_select_browser_done(self, error: Exception | None):
        self._browser_busy = False
        self._set_browser_buttons(False)
        if error is not None:
            messagebox.showerror("브라우저 연결 실패", self._driver_help(error), parent=self)
            self._status("브라우저 연결에 실패했습니다.")
        else:
            self._status("브라우저를 연결했습니다.")
            self._log("브라우저를 연결했습니다.")

    def _close_browser(self, reason: str = ""):
        if getattr(self, "_browser_busy", False):
            return
        self._browser_busy = True
        self._set_browser_buttons(True)
        self._status("브라우저를 닫는 중입니다...")

        def work():
            self.session.close_browser()
            self.after(0, lambda: self._on_close_browser_done(reason))

        threading.Thread(target=work, daemon=True).start()

    def _on_close_browser_done(self, reason: str):
        self._browser_busy = False
        self._set_browser_buttons(False)
        self._status("브라우저를 닫았습니다.")
        self._log(("%s 브라우저를 닫았습니다." % reason) if reason else "브라우저를 닫았습니다.")

    def _driver_help(self, e: Exception) -> str:
        """실패 원인을 연결 방식에 맞게 안내한다."""
        browser = self.browser_var.get()
        if ENGINE_CODE.get(self.engine_var.get()) == "cdp":
            name = "Microsoft Edge" if browser == "edge" else "Google Chrome"
            return ("브라우저를 실행하지 못했습니다.\n\n%s\n\n"
                    "이 방식은 드라이버가 필요 없으므로 버전 문제는 아닙니다.\n"
                    "확인해 주세요\n"
                    " · %s 가 설치되어 있는지\n"
                    " · 설치 경로가 특이하면 환경변수 JOBSCN_BROWSER_PATH 에\n"
                    "   실행 파일 경로를 지정할 수 있습니다.\n"
                    " · 보안 프로그램이 디버깅 포트 연결을 막고 있지 않은지"
                    % (str(e)[:300], name))
        return diagnose(browser, e)

    def _open_failures(self):
        """마지막으로 실패한 화면이 저장된 폴더를 연다."""
        folder = self.session.last_failure_dir or (data_dir() / "failures")
        if not Path(folder).exists():
            messagebox.showinfo("안내", "아직 저장된 실패 기록이 없습니다.", parent=self)
            return
        try:
            programs.open_path(str(folder))
        except Exception as e:
            messagebox.showerror("열기 실패", str(e), parent=self)

    def _busy(self, running: bool):
        state = "disabled" if running else "normal"
        for btn in (self.run_all_btn, self.run_one_btn, self.run_from_btn):
            btn.configure(state=state)
        self.stop_btn.configure(state="normal" if running else "disabled")

    def _prepare_vars(self) -> bool:
        """{{변수}} 로 적어 둔 값을 실행 전에 한 번씩 물어본다."""
        for name in self.scenario.required_vars():
            if name in self.session.variables:
                continue
            answer = simpledialog.askstring("값 입력", "'%s' 값을 입력해 주세요." % name,
                                            parent=self,
                                            initialvalue=self.session.variables.get(name, ""))
            if answer is None:
                return False
            self.session.variables[name] = answer
        return True

    def _make_runner(self) -> Runner:
        return Runner(
            self.scenario, self.session,
            log=self._enqueue_log,
            on_step=lambda i, s, m="": self.events.put(("step", i, s, m)),
            ask=self._ask_from_thread,
            notify=lambda msg: self.events.put(("notify", msg)),
        )

    def _ask_from_thread(self, name: str):
        """작업 스레드에서 사용자 입력을 받아야 할 때 UI 스레드에 요청한다."""
        holder, done = {}, threading.Event()

        def ask():
            holder["value"] = simpledialog.askstring(
                "값 입력", "'%s' 값을 입력해 주세요." % name, parent=self)
            done.set()

        self.after(0, ask)
        done.wait(600)
        return holder.get("value")

    def _start(self, work, count: int):
        if self.run_thread and self.run_thread.is_alive():
            messagebox.showinfo("안내", "이미 실행 중입니다.", parent=self)
            return
        self._collect()
        if not self.scenario.steps:
            messagebox.showinfo("안내", "등록된 단계가 없습니다.", parent=self)
            return
        if not self._prepare_vars():
            return

        self._busy(True)
        self.progress["maximum"] = max(count, 1)
        self.progress["value"] = 0
        self.runner = self._make_runner()

        def run():
            ok = False
            try:
                ok = work(self.runner)
            except Exception:
                self._enqueue_log("예상하지 못한 오류가 발생했습니다.\n%s"
                                  % traceback.format_exc(limit=3))
            finally:
                self.events.put(("done", ok))

        self.run_thread = threading.Thread(target=run, daemon=True)
        self.run_thread.start()

    def _on_run_finished(self, ok: bool):
        self._busy(False)
        self._status("실행을 마쳤습니다." if ok else "실행이 중단되었습니다. 기록을 확인해 주세요.")

    def _run_all(self):
        self._log("=" * 50)
        self._log("전체 일괄 실행: %s (%d단계)"
                  % (self.scenario.title or "(제목 없음)", len(self.scenario.steps)))
        for i in range(len(self.scenario.steps)):
            self._set_step_state(i, READY)
        self._start(lambda r: r.run_all(0), len(self.scenario.steps))

    def _run_selected(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("안내", "실행할 단계를 선택해 주세요.", parent=self)
            return
        self._log("단계별 실행: %d단계" % (idx + 1))
        self._start(lambda r: r.run_one(idx), 1)

    def _run_from_selected(self):
        idx = self._selected_index()
        if idx is None:
            messagebox.showinfo("안내", "시작할 단계를 선택해 주세요.", parent=self)
            return
        self._log("%d단계부터 실행합니다." % (idx + 1))
        for i in range(idx, len(self.scenario.steps)):
            self._set_step_state(i, READY)
        self._start(lambda r: r.run_all(idx), len(self.scenario.steps) - idx)

    def _stop(self):
        if self.runner:
            self.runner.stop()
            self._status("중지를 요청했습니다. 현재 단계가 끝나면 멈춥니다.")

    # ==================================================================

    def _on_close(self):
        if self.run_thread and self.run_thread.is_alive():
            if not messagebox.askyesno("종료", "실행 중입니다. 정말 종료할까요?", parent=self):
                return
            if self.runner:
                self.runner.stop()
        self.session.close_browser()
        self.destroy()


def main():
    app = JobScenarioApp()
    app.mainloop()
