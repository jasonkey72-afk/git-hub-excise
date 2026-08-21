import json
import queue
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox
from git import Repo, GitCommandError, InvalidGitRepositoryError, NoSuchPathError

CONFIG_PATH = Path(__file__).with_name("_gitCommand_config.json")


class GitConflictError(Exception):
    def __init__(self, files, stage):
        self.files = files
        self.stage = stage
        super().__init__(f"{stage} 중 충돌 발생: {', '.join(files)}")


class GitService:
    def __init__(self):
        self.repo = None

    def open(self, path):
        self.repo = Repo(path)

    def clone(self, url, dest):
        self.repo = Repo.clone_from(url, dest)

    @property
    def path(self):
        return self.repo.working_dir if self.repo else None

    def current_branch(self):
        try:
            return self.repo.active_branch.name
        except TypeError:
            return "(detached HEAD)"

    def branches(self):
        return [h.name for h in self.repo.heads]

    def checkout(self, branch_name):
        self.repo.git.checkout(branch_name)

    def is_dirty(self):
        return self.repo.is_dirty(untracked_files=True)

    def status_lists(self):
        staged = [(d.a_path or d.b_path, d.change_type) for d in self.repo.index.diff("HEAD")]
        unstaged = [(d.a_path or d.b_path, d.change_type) for d in self.repo.index.diff(None)]
        untracked = [(p, "U") for p in self.repo.untracked_files]
        return staged, unstaged, untracked

    def ahead_behind(self):
        branch = self.repo.active_branch
        tracking = branch.tracking_branch()
        if not tracking:
            return None, None
        ahead = sum(1 for _ in self.repo.iter_commits(f"{tracking}..{branch}"))
        behind = sum(1 for _ in self.repo.iter_commits(f"{branch}..{tracking}"))
        return ahead, behind

    def stage(self, paths):
        if paths:
            self.repo.git.add(paths)

    def stage_all(self):
        self.repo.git.add(A=True)

    def unstage(self, paths):
        if paths:
            self.repo.git.restore("--staged", *paths)

    def commit(self, message):
        self.repo.index.commit(message)

    def fetch(self):
        self.repo.remotes.origin.fetch()

    def stash_save(self, message):
        self.repo.git.stash("push", "-u", "-m", message)

    def stash_pop(self):
        self.repo.git.stash("pop")

    def unmerged_files(self):
        return list(self.repo.index.unmerged_blobs().keys())

    def smart_pull(self, log):
        stashed = False
        if self.is_dirty():
            log("커밋되지 않은 변경사항이 있어 임시 저장(stash)합니다...", "info")
            self.stash_save("auto-stash before pull")
            stashed = True

        pull_error = None
        try:
            log("원격 저장소에서 pull 중...", "info")
            self.repo.remotes.origin.pull()
        except GitCommandError as e:
            pull_error = e

        if stashed:
            log("임시 저장했던 변경사항을 복원합니다...", "info")
            try:
                self.stash_pop()
            except GitCommandError as e:
                conflicts = self.unmerged_files()
                if conflicts:
                    raise GitConflictError(conflicts, "stash 복원") from e
                raise

        conflicts = self.unmerged_files()
        if conflicts:
            raise GitConflictError(conflicts, "pull")
        if pull_error:
            raise pull_error
        log("pull 완료.", "success")

    def smart_push(self, log):
        try:
            log("원격 저장소로 push 중...", "info")
            self.repo.remotes.origin.push()
            log("push 완료.", "success")
            return
        except GitCommandError as e:
            msg = str(e)
            if not any(k in msg for k in ("rejected", "non-fast-forward", "fetch first", "behind")):
                raise
            log("원격에 새 커밋이 있어 push가 거부되었습니다. 먼저 pull을 진행합니다...", "warning")

        self.smart_pull(log)
        log("다시 push를 시도합니다...", "info")
        self.repo.remotes.origin.push()
        log("push 완료.", "success")

    def sync(self, log, commit_message=None):
        self.smart_pull(log)

        if self.is_dirty():
            if commit_message:
                log("모든 변경사항을 스테이징합니다...", "info")
                self.stage_all()
                log(f"커밋 생성: {commit_message}", "info")
                self.commit(commit_message)
            else:
                log("커밋되지 않은 변경사항이 있지만 커밋 메시지가 없어 커밋을 건너뜁니다.", "warning")

        ahead, _ = self.ahead_behind()
        if ahead:
            self.smart_push(log)
        else:
            log("원격으로 보낼 새 커밋이 없습니다.", "info")
        log("동기화(Sync) 완료.", "success")


CHANGE_LABEL = {"A": "[추가]", "M": "[수정]", "D": "[삭제]", "R": "[이름변경]", "U": "[신규]"}
LOG_PREFIX = {"info": "  ", "success": "✔ ", "warning": "⚠ ", "error": "✖ "}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Git 쉽게 쓰기")
        self.geometry("1080x720")
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.git = GitService()
        self.log_queue = queue.Queue()
        self.busy = False
        self.action_buttons = []

        self._build_layout()
        self.after(100, self._poll_log_queue)

        last_path = self._load_last_path()
        if last_path and Path(last_path).exists():
            self._try_open(last_path)

    # ---------- layout ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="저장소:").grid(row=0, column=0, padx=(10, 5), pady=10)
        self.path_label = ctk.CTkLabel(top, text="(열린 저장소 없음)", anchor="w")
        self.path_label.grid(row=0, column=1, sticky="ew", pady=10)
        ctk.CTkButton(top, text="폴더 열기", width=100, command=self.on_open_folder).grid(row=0, column=2, padx=5)
        ctk.CTkButton(top, text="복제(Clone)", width=100, command=self.on_clone).grid(row=0, column=3, padx=(5, 10))

        status = ctk.CTkFrame(self)
        status.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        ctk.CTkLabel(status, text="브랜치:").pack(side="left", padx=(10, 5), pady=8)
        self.branch_menu = ctk.CTkOptionMenu(status, values=["-"], command=self.on_branch_selected, width=160)
        self.branch_menu.pack(side="left", pady=8)
        self.ahead_behind_label = ctk.CTkLabel(status, text="")
        self.ahead_behind_label.pack(side="left", padx=15)
        ctk.CTkButton(status, text="새로고침", width=90, command=self.refresh_status).pack(side="right", padx=10, pady=8)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=10, pady=5)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        files_frame = ctk.CTkFrame(body)
        files_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        files_frame.grid_rowconfigure(1, weight=1)
        files_frame.grid_rowconfigure(3, weight=1)
        files_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(files_frame, text="변경된 파일 (스테이징 안 됨)", anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.unstaged_frame = ctk.CTkScrollableFrame(files_frame, height=200)
        self.unstaged_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)

        stage_btns = ctk.CTkFrame(files_frame, fg_color="transparent")
        stage_btns.grid(row=2, column=0, sticky="ew", padx=10)
        self._action_button(stage_btns, "선택 스테이징 →", self.on_stage_selected).pack(side="left")
        self._action_button(stage_btns, "전체 스테이징", self.on_stage_all).pack(side="left", padx=5)

        ctk.CTkLabel(files_frame, text="스테이징된 파일", anchor="w").grid(row=3, column=0, sticky="new", padx=10, pady=(10, 0))
        self.staged_frame = ctk.CTkScrollableFrame(files_frame, height=150)
        self.staged_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=5)

        unstage_btns = ctk.CTkFrame(files_frame, fg_color="transparent")
        unstage_btns.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._action_button(unstage_btns, "← 선택 취소", self.on_unstage_selected).pack(side="left")

        log_frame = ctk.CTkFrame(body)
        log_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="작업 로그", anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.log_box = ctk.CTkTextbox(log_frame, state="disabled")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        commit_frame = ctk.CTkFrame(self)
        commit_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        commit_frame.grid_columnconfigure(0, weight=1)
        self.commit_entry = ctk.CTkEntry(commit_frame, placeholder_text="커밋 메시지 입력...")
        self.commit_entry.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=10)
        self._action_button(commit_frame, "커밋", self.on_commit, width=100).grid(row=0, column=1, padx=(5, 10))

        actions = ctk.CTkFrame(self)
        actions.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))
        self._action_button(actions, "Fetch", self.on_fetch).pack(side="left", padx=10, pady=10)
        self._action_button(actions, "Pull (스마트)", self.on_pull).pack(side="left", padx=5)
        self._action_button(actions, "Push (스마트)", self.on_push).pack(side="left", padx=5)
        self._action_button(actions, "Stash 저장", self.on_stash_save).pack(side="left", padx=5)
        self._action_button(actions, "Stash 복원", self.on_stash_pop).pack(side="left", padx=5)
        sync_btn = self._action_button(
            actions, "🔄 Sync (Pull → Commit → Push)", self.on_sync,
            fg_color="#2e7d32", hover_color="#1b5e20",
        )
        sync_btn.pack(side="right", padx=10)

    def _action_button(self, parent, text, command, **kwargs):
        btn = ctk.CTkButton(parent, text=text, command=command, **kwargs)
        self.action_buttons.append(btn)
        return btn

    # ---------- logging ----------
    def log(self, message, level="info"):
        self.log_queue.put((message, level))

    def _poll_log_queue(self):
        while not self.log_queue.empty():
            message, level = self.log_queue.get_nowait()
            self.log_box.configure(state="normal")
            self.log_box.insert("end", LOG_PREFIX.get(level, "  ") + message + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(100, self._poll_log_queue)

    # ---------- repo open / clone ----------
    def on_open_folder(self):
        path = filedialog.askdirectory(title="Git 저장소 폴더 선택")
        if path:
            self._try_open(path)

    def _try_open(self, path):
        try:
            self.git.open(path)
        except InvalidGitRepositoryError:
            if messagebox.askyesno("Git 저장소 아님", f"'{path}'는 Git 저장소가 아닙니다.\n이 폴더를 새 저장소로 초기화(git init)할까요?"):
                Repo.init(path)
                self.git.open(path)
            else:
                return
        except NoSuchPathError:
            messagebox.showerror("오류", "폴더를 찾을 수 없습니다.")
            return

        self.path_label.configure(text=self.git.path)
        self._save_last_path(self.git.path)
        self.log(f"저장소 열림: {self.git.path}", "success")
        self.refresh_status()

    def on_clone(self):
        dialog = ctk.CTkInputDialog(text="복제할 저장소 URL을 입력하세요:", title="Git Clone")
        url = dialog.get_input()
        if not url:
            return
        dest_parent = filedialog.askdirectory(title="저장할 위치(부모 폴더) 선택")
        if not dest_parent:
            return
        repo_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        dest = str(Path(dest_parent) / repo_name)

        def task():
            self.log(f"'{url}' 복제 중... ({dest})", "info")
            try:
                self.git.clone(url, dest)
                self.log("복제 완료.", "success")
                self._save_last_path(dest)
                self._ui(lambda: self.path_label.configure(text=self.git.path))
                self._ui(self.refresh_status)
            except GitCommandError as e:
                self.log(f"복제 실패: {e}", "error")

        self._run_in_thread(task)

    # ---------- status refresh ----------
    def refresh_status(self):
        if not self.git.repo:
            return

        branches = self.git.branches()
        self.branch_menu.configure(values=branches or ["-"])
        self.branch_menu.set(self.git.current_branch())

        ahead, behind = self.git.ahead_behind()
        if ahead is None:
            self.ahead_behind_label.configure(text="(원격 추적 브랜치 없음)")
        else:
            self.ahead_behind_label.configure(text=f"↑ 앞섬 {ahead}   ↓ 뒤처짐 {behind}")

        for frame in (self.unstaged_frame, self.staged_frame):
            for child in frame.winfo_children():
                child.destroy()

        staged, unstaged, untracked = self.git.status_lists()
        self.unstaged_vars = {}
        for path, ctype in unstaged + untracked:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(self.unstaged_frame, text=f"{CHANGE_LABEL.get(ctype, '')} {path}", variable=var)
            cb.pack(anchor="w", pady=2)
            self.unstaged_vars[path] = var

        self.staged_vars = {}
        for path, ctype in staged:
            var = ctk.BooleanVar(value=False)
            cb = ctk.CTkCheckBox(self.staged_frame, text=f"{CHANGE_LABEL.get(ctype, '')} {path}", variable=var)
            cb.pack(anchor="w", pady=2)
            self.staged_vars[path] = var

    def _selected(self, var_map):
        return [path for path, var in var_map.items() if var.get()]

    # ---------- button handlers ----------
    def on_branch_selected(self, branch_name):
        if branch_name == self.git.current_branch():
            return
        if self.git.is_dirty() and not messagebox.askyesno(
            "커밋되지 않은 변경사항", "커밋되지 않은 변경사항이 있습니다. 그래도 브랜치를 전환할까요?"
        ):
            self.branch_menu.set(self.git.current_branch())
            return
        try:
            self.git.checkout(branch_name)
            self.log(f"'{branch_name}' 브랜치로 전환했습니다.", "success")
        except GitCommandError as e:
            messagebox.showerror("브랜치 전환 실패", str(e))
        self.refresh_status()

    def on_stage_selected(self):
        paths = self._selected(self.unstaged_vars)
        if not paths:
            messagebox.showinfo("안내", "스테이징할 파일을 선택하세요.")
            return
        self.git.stage(paths)
        self.log(f"{len(paths)}개 파일 스테이징함.", "success")
        self.refresh_status()

    def on_stage_all(self):
        self.git.stage_all()
        self.log("모든 변경사항을 스테이징했습니다.", "success")
        self.refresh_status()

    def on_unstage_selected(self):
        paths = self._selected(self.staged_vars)
        if not paths:
            messagebox.showinfo("안내", "스테이징 취소할 파일을 선택하세요.")
            return
        self.git.unstage(paths)
        self.log(f"{len(paths)}개 파일 스테이징 취소함.", "info")
        self.refresh_status()

    def on_commit(self):
        message = self.commit_entry.get().strip()
        if not message:
            messagebox.showinfo("안내", "커밋 메시지를 입력하세요.")
            return
        try:
            self.git.commit(message)
            self.log(f"커밋 완료: {message}", "success")
            self.commit_entry.delete(0, "end")
        except Exception as e:
            messagebox.showerror("커밋 실패", str(e))
        self.refresh_status()

    def on_fetch(self):
        def task():
            try:
                self.log("Fetch 중...", "info")
                self.git.fetch()
                self.log("Fetch 완료.", "success")
            except GitCommandError as e:
                self.log(f"Fetch 실패: {e}", "error")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_pull(self):
        def task():
            try:
                self.git.smart_pull(self.log)
            except GitConflictError as e:
                self._report_conflict(e)
            except GitCommandError as e:
                self.log(f"Pull 실패: {e}", "error")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_push(self):
        def task():
            try:
                self.git.smart_push(self.log)
            except GitConflictError as e:
                self._report_conflict(e)
            except GitCommandError as e:
                self.log(f"Push 실패: {e}", "error")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_stash_save(self):
        if not self.git.is_dirty():
            messagebox.showinfo("안내", "저장할 변경사항이 없습니다.")
            return

        def task():
            try:
                self.git.stash_save("manual stash")
                self.log("변경사항을 stash에 저장했습니다.", "success")
            except GitCommandError as e:
                self.log(f"Stash 저장 실패: {e}", "error")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_stash_pop(self):
        def task():
            try:
                self.git.stash_pop()
                self.log("Stash를 복원했습니다.", "success")
            except GitCommandError as e:
                conflicts = self.git.unmerged_files()
                if conflicts:
                    self._report_conflict(GitConflictError(conflicts, "stash 복원"))
                else:
                    self.log(f"Stash 복원 실패: {e}", "error")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_sync(self):
        message = self.commit_entry.get().strip() or None

        def task():
            try:
                self.git.sync(self.log, commit_message=message)
                self._ui(lambda: self.commit_entry.delete(0, "end"))
            except GitConflictError as e:
                self._report_conflict(e)
            except GitCommandError as e:
                self.log(f"Sync 실패: {e}", "error")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def _report_conflict(self, err: GitConflictError):
        file_list = "\n".join(f"- {f}" for f in err.files)
        self.log(f"{err.stage} 중 충돌 발생! 아래 파일을 직접 확인 후 해결하세요.", "error")
        self._ui(lambda: messagebox.showwarning(
            "충돌 발생",
            f"{err.stage} 중 다음 파일에서 충돌이 발생했습니다:\n\n{file_list}\n\n"
            "파일을 열어 <<<<<<< / ======= / >>>>>>> 표시를 직접 정리한 뒤\n"
            "다시 스테이징하고 커밋해주세요.",
        ))

    # ---------- thread / busy helpers ----------
    def _run_in_thread(self, task):
        if self.busy:
            messagebox.showinfo("안내", "다른 작업이 진행 중입니다. 잠시 기다려주세요.")
            return
        self.busy = True
        for btn in self.action_buttons:
            btn.configure(state="disabled")

        def wrapper():
            try:
                task()
            finally:
                self._ui(self._end_busy)

        threading.Thread(target=wrapper, daemon=True).start()

    def _end_busy(self):
        self.busy = False
        for btn in self.action_buttons:
            btn.configure(state="normal")

    def _ui(self, fn):
        self.after(0, fn)

    # ---------- config persistence ----------
    def _load_last_path(self):
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("last_path")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_last_path(self, path):
        CONFIG_PATH.write_text(json.dumps({"last_path": path}, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    App().mainloop()
