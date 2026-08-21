import json
import os
import queue
import shlex
import subprocess
import threading
from pathlib import Path

import customtkinter as ctk
from tkinter import filedialog, messagebox
from git import Repo, GitCommandError, InvalidGitRepositoryError, NoSuchPathError

# PyInstaller onefile 빌드는 __file__이 실행할 때마다 새로 생성되는 임시 폴더를 가리켜서
# 그 옆에 저장하면 종료 즉시 사라진다. 사용자별 고정 폴더(APPDATA)에 저장해야 실행할 때마다 유지된다.
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "GitEasy"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "config.json"


class ToolTip:
    """마우스를 올리면 설명이 뜨는 말풍선."""

    def __init__(self, widget, text, delay=350, wraplength=320):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self.tip_window = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tw = self.tip_window = ctk.CTkToplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        label = ctk.CTkLabel(
            tw,
            text=self.text,
            justify="left",
            fg_color=("#FFFFDB", "#3a3a2e"),
            text_color=("#000000", "#FFFFFF"),
            corner_radius=6,
            wraplength=self.wraplength,
        )
        label.pack(padx=10, pady=6)

    def _hide(self, _event=None):
        self._cancel()
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


def add_tooltip(widget, text):
    return ToolTip(widget, text)


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
        """(브랜치명, 로컬여부) 목록. 원격에만 있는 브랜치도 함께 보여준다."""
        local = [h.name for h in self.repo.heads]
        seen = set(local)
        remote_only = []
        try:
            refs = self.repo.remotes.origin.refs
        except (AttributeError, IndexError):
            refs = []
        for ref in refs:
            name = ref.name.split("/", 1)[-1]
            if name == "HEAD" or name in seen:
                continue
            seen.add(name)
            remote_only.append(name)
        return [(n, True) for n in local] + [(n, False) for n in sorted(remote_only)]

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

GIT_GLOSSARY = [
    ("저장소 시작하기", [
        ("git init", "현재 폴더를 새로운 Git 저장소로 만듭니다. 새 프로젝트를 시작할 때 한 번만 사용합니다."),
        ("git clone <URL>", "GitHub 등 원격 저장소 전체를 내 컴퓨터로 복사해옵니다. UI의 '복제(Clone)' 버튼과 같은 동작입니다."),
    ]),
    ("상태 확인하기", [
        ("git status", "어떤 파일이 바뀌었는지, 스테이징 되었는지 확인합니다. UI의 '새로고침'과 파일 목록이 이 정보를 보여줍니다."),
        ("git diff", "실제로 어떤 줄이 어떻게 바뀌었는지 자세히 비교해서 보여줍니다."),
        ("git log", "지금까지의 커밋(작업 기록)을 시간순으로 보여줍니다."),
    ]),
    ("변경사항 저장하기", [
        ("git add <파일>", "특정 파일을 다음 커밋에 포함되도록 등록(스테이징)합니다. UI의 '선택 Add'와 같은 동작입니다."),
        ("git add .", "바뀐 파일을 전부 한 번에 등록합니다. UI의 '전체 Add'와 같은 동작입니다."),
        ("git restore --staged <파일>", "스테이징을 취소합니다. UI의 '선택 취소'와 같은 동작입니다."),
        ("git commit -m \"메시지\"", "스테이징된 변경사항을 저장소 기록에 하나의 커밋으로 남깁니다. UI의 '커밋' 버튼과 같은 동작입니다."),
    ]),
    ("원격 저장소와 동기화하기", [
        ("git fetch", "원격에 어떤 새 커밋이 있는지만 확인합니다. 내 파일이나 브랜치는 바뀌지 않습니다."),
        ("git pull", "원격의 최신 내용을 받아서 내 브랜치에 합칩니다. UI의 'Pull (스마트)'는 여기에 자동 stash/충돌 안내를 더한 것입니다."),
        ("git push", "내가 만든 커밋을 원격 저장소로 올립니다. UI의 'Push (스마트)'는 거부되면 자동으로 pull 후 재시도합니다."),
    ]),
    ("브랜치 다루기", [
        ("git branch", "브랜치 목록을 보여줍니다."),
        ("git checkout <브랜치>  (또는 git switch <브랜치>)", "다른 브랜치로 이동합니다. UI에서는 상단 브랜치 선택 메뉴가 이 역할을 합니다."),
        ("git merge <브랜치>", "다른 브랜치의 내용을 지금 브랜치로 합칩니다."),
    ]),
    ("임시로 치워두기", [
        ("git stash", "커밋하지 않은 변경사항을 잠깐 따로 보관해서 작업 폴더를 깨끗하게 만듭니다. UI의 'Stash 저장'과 같은 동작입니다."),
        ("git stash pop", "보관해둔 변경사항을 다시 불러옵니다. UI의 'Stash 복원'과 같은 동작입니다."),
    ]),
    ("문제 생겼을 때", [
        ("충돌(conflict)", "같은 부분을 서로 다르게 고쳤을 때 Git이 자동으로 합치지 못하는 상태입니다. 파일을 열어 <<<<<<< / ======= / >>>>>>> 표시 사이를 직접 정리한 뒤 다시 스테이징/커밋해야 합니다."),
    ]),
]

# (원문에서 찾을 키워드들, 진단명, 해결 방법) — push/pull 등에서 자주 나는 오류를 초보자 눈높이로 안내한다.
GIT_ERROR_GUIDE = [
    (["authentication failed", "could not read username", "could not read password", "terminal prompts disabled"],
     "GitHub 인증 실패",
     "GitHub 로그인 정보가 확인되지 않았습니다. GitHub는 비밀번호 로그인을 지원하지 않습니다.\n\n"
     "해결 방법:\n"
     "1) push/pull 시 로그인 창이 뜨면 브라우저로 GitHub 로그인을 완료하세요.\n"
     "2) 로그인 창이 안 뜨면 GitHub 홈페이지 > Settings > Developer settings에서\n"
     "   Personal Access Token을 발급받아, 비밀번호 입력란에 그 토큰을 붙여넣으세요."),
    (["permission denied (publickey)"],
     "SSH 키 인증 실패",
     "SSH 방식 주소(git@github.com:...)를 쓰는데 등록된 SSH 키가 없습니다.\n\n"
     "해결 방법:\n"
     "1) 저장소 URL을 https://github.com/... 형태로 바꿔서 다시 시도하거나\n"
     "2) GitHub Settings > SSH and GPG keys에 내 컴퓨터의 SSH 공개키를 등록하세요."),
    (["repository not found", "remote: not found"],
     "저장소를 찾을 수 없음",
     "저장소 주소가 틀렸거나, 비공개(private) 저장소인데 접근 권한이 없습니다.\n\n"
     "해결 방법:\n"
     "1) 저장소 URL 철자를 다시 확인하세요.\n"
     "2) private 저장소라면 저장소 소유자에게 Collaborator로 초대해달라고 요청하세요."),
    (["failed to push some refs", "non-fast-forward", "updates were rejected", "fetch first"],
     "Push 거부됨 (원격이 앞서 있음)",
     "내가 모르는 사이 원격 저장소에 다른 커밋이 먼저 올라갔습니다.\n\n"
     "해결 방법:\n"
     "'Push (스마트)' 버튼은 이 상황을 자동으로 감지해서 먼저 Pull한 뒤 다시 Push를 시도합니다.\n"
     "그래도 안 되면 아래 로그에서 충돌 여부를 확인하세요."),
    (["conflict"],
     "병합 충돌 (Conflict)",
     "같은 부분을 서로 다르게 고쳐서 Git이 자동으로 합치지 못했습니다.\n\n"
     "해결 방법:\n"
     "충돌난 파일을 열어 <<<<<<< / ======= / >>>>>>> 표시 사이 내용을 직접 정리한 뒤\n"
     "다시 Add하고 커밋하세요."),
    (["could not resolve host", "unable to access", "failed to connect", "network is unreachable"],
     "네트워크 연결 문제",
     "인터넷 연결이 끊겼거나 방화벽/프록시가 GitHub 접속을 막고 있습니다.\n\n"
     "해결 방법:\n"
     "1) 인터넷 연결 상태를 확인하세요.\n"
     "2) 회사/학교 네트워크라면 방화벽이나 프록시 설정을 확인하세요."),
    (["would be overwritten by"],
     "커밋 안 된 변경사항 충돌",
     "커밋하지 않은 변경사항이 있어서 다른 브랜치의 내용을 가져올 수 없습니다.\n\n"
     "해결 방법:\n"
     "먼저 'Stash 저장'을 누르거나, 변경사항을 Add 후 커밋한 다음 다시 시도하세요."),
    (["dubious ownership"],
     "폴더 소유권 문제",
     "Windows 보안 정책 때문에 이 폴더를 신뢰할 수 없다고 Git이 판단했습니다.\n\n"
     "해결 방법:\n"
     "아래 콘솔창에 다음을 입력해 실행하세요 (경로는 실제 저장소 경로로 바꾸세요):\n"
     "config --global --add safe.directory <저장소 경로>"),
    (["ssl certificate problem", "certificate verify failed"],
     "SSL 인증서 오류",
     "보안 소프트웨어(백신, 회사 프록시 등)가 인증서 확인을 막고 있을 수 있습니다.\n\n"
     "해결 방법:\n"
     "회사/학교 네트워크라면 관리자에게 문의하세요. 개인 PC라면 백신의 'HTTPS 검사' 기능을 잠시 꺼보세요."),
    (["not a git repository"],
     "Git 저장소가 아님",
     "지금 열려 있는 폴더가 Git 저장소가 아닙니다.\n\n"
     "해결 방법:\n"
     "상단의 '폴더 열기'로 올바른 저장소 폴더를 선택하거나, '복제(Clone)'로 새로 받아오세요."),
    (["please tell me who you are", "user.email", "user.name"],
     "Git 사용자 정보 미설정",
     "커밋하려면 Git에 이름과 이메일을 먼저 등록해야 합니다.\n\n"
     "해결 방법:\n"
     "아래 콘솔창에 다음을 한 줄씩 입력해 실행하세요:\n"
     "config --global user.name \"내이름\"\n"
     "config --global user.email \"내이메일@example.com\""),
]


def diagnose_git_error(text):
    lowered = text.lower()
    for keywords, title, explanation in GIT_ERROR_GUIDE:
        if any(k in lowered for k in keywords):
            return title, explanation
    return (
        "알 수 없는 오류",
        "이 오류에 대한 자동 안내가 준비되어 있지 않습니다.\n\n"
        "아래 '오류 원문'을 복사해서 동료나 검색엔진, 또는 AI에게 물어보면\n"
        "빠르게 원인을 찾을 수 있습니다.",
    )


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
        self._branch_display_map = {}

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

        open_btn = ctk.CTkButton(top, text="폴더 열기", width=100, command=self.on_open_folder)
        open_btn.grid(row=0, column=2, padx=5)
        add_tooltip(open_btn, "이미 내 컴퓨터에 있는 Git 저장소 폴더를 엽니다.\n(아직 GitHub에서 받은 적이 없다면 '복제(Clone)'를 먼저 사용하세요)")

        clone_btn = ctk.CTkButton(top, text="복제(Clone)", width=100, command=self.on_clone)
        clone_btn.grid(row=0, column=3, padx=5)
        add_tooltip(clone_btn, "GitHub 등 원격 저장소의 URL을 입력하면\n저장소 전체를 내 컴퓨터로 처음 복사해옵니다. (git clone과 동일)")

        help_btn = ctk.CTkButton(
            top, text="📖 Git 명령어 설명서", width=170,
            fg_color="#6d28d9", hover_color="#5b21b6", text_color="#FFFFFF",
            command=self.on_show_git_help,
        )
        help_btn.grid(row=0, column=4, padx=(5, 10))
        add_tooltip(help_btn, "자주 쓰는 Git 명령어들을 초보자 눈높이로 설명하는 창을 엽니다.")

        status = ctk.CTkFrame(self)
        status.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        ctk.CTkLabel(status, text="브랜치:").pack(side="left", padx=(10, 5), pady=8)
        self.branch_menu = ctk.CTkOptionMenu(status, values=["-"], command=self.on_branch_selected, width=160)
        self.branch_menu.pack(side="left", pady=8)
        add_tooltip(self.branch_menu, "저장소의 브랜치 목록입니다. 다른 브랜치를 선택하면\n그 브랜치로 전환됩니다. (git checkout <브랜치>와 동일)")
        self.ahead_behind_label = ctk.CTkLabel(status, text="")
        self.ahead_behind_label.pack(side="left", padx=15)
        add_tooltip(self.ahead_behind_label, "↑ 앞섬: 아직 push 안 한 내 커밋 수\n↓ 뒤처짐: 아직 pull 안 받은 원격 커밋 수")
        refresh_btn = ctk.CTkButton(status, text="새로고침", width=90, command=self.refresh_status)
        refresh_btn.pack(side="right", padx=10, pady=8)
        add_tooltip(refresh_btn, "파일 상태, 브랜치, 원격과의 앞섬/뒤처짐 정보를 다시 불러옵니다. (git status와 비슷)")

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
        self._action_button(
            stage_btns, "선택 Add →", self.on_stage_selected,
            tooltip="체크한 파일만 다음 커밋 대상으로 등록합니다. (git add <파일>과 동일)",
        ).pack(side="left")
        self._action_button(
            stage_btns, "전체 Add", self.on_stage_all,
            tooltip="변경된 모든 파일을 한 번에 커밋 대상으로 등록합니다. (git add . 와 동일)",
        ).pack(side="left", padx=5)

        ctk.CTkLabel(files_frame, text="스테이징된 파일", anchor="w").grid(row=3, column=0, sticky="new", padx=10, pady=(10, 0))
        self.staged_frame = ctk.CTkScrollableFrame(files_frame, height=150)
        self.staged_frame.grid(row=4, column=0, sticky="nsew", padx=10, pady=5)

        unstage_btns = ctk.CTkFrame(files_frame, fg_color="transparent")
        unstage_btns.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._action_button(
            unstage_btns, "← 선택 취소", self.on_unstage_selected,
            tooltip="체크한 파일을 스테이징에서 제외합니다. (git restore --staged <파일>과 동일)",
        ).pack(side="left")

        log_frame = ctk.CTkFrame(body)
        log_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        log_frame.grid_rowconfigure(1, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_frame, text="작업 로그", anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.log_box = ctk.CTkTextbox(log_frame, state="disabled")
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        console_frame = ctk.CTkFrame(log_frame, fg_color="transparent")
        console_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))
        console_frame.grid_columnconfigure(0, weight=1)

        self.command_history = []
        self.history_index = 0

        self.command_entry = ctk.CTkEntry(
            console_frame, placeholder_text="git 명령어 입력 (예: status, log --oneline -5, diff)...",
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.command_entry.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.command_entry.bind("<Return>", self.on_run_command)
        self.command_entry.bind("<Up>", self._history_prev)
        self.command_entry.bind("<Down>", self._history_next)
        add_tooltip(
            self.command_entry,
            "git bash처럼 git 명령어를 직접 입력해서 실행합니다.\n"
            "앞의 'git'은 생략해도 됩니다. (예: status, add ., commit -m \"메시지\")\n"
            "↑ / ↓ 로 이전에 입력한 명령어를 다시 불러올 수 있습니다.",
        )

        run_btn = self._action_button(console_frame, "실행", self.on_run_command, width=70)
        run_btn.grid(row=0, column=1)
        add_tooltip(run_btn, "입력한 git 명령어를 현재 저장소에서 실행합니다.")

        commit_frame = ctk.CTkFrame(self)
        commit_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=5)
        commit_frame.grid_columnconfigure(0, weight=1)
        self.commit_entry = ctk.CTkEntry(commit_frame, placeholder_text="커밋 메시지 입력...")
        self.commit_entry.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=10)
        add_tooltip(self.commit_entry, "여기에 입력한 문구가 커밋 메시지로 사용됩니다.\n예: '로그인 버그 수정'")
        self._action_button(
            commit_frame, "커밋", self.on_commit, width=100,
            tooltip="스테이징된 변경사항을 저장소 기록에 남깁니다. 왼쪽 입력창의 문구가\n커밋 메시지로 쓰입니다. (git commit -m \"메시지\"와 동일)",
        ).grid(row=0, column=1, padx=(5, 10))

        actions = ctk.CTkFrame(self)
        actions.grid(row=4, column=0, sticky="ew", padx=10, pady=(5, 10))
        self._action_button(
            actions, "Fetch", self.on_fetch,
            tooltip="원격 저장소의 최신 정보만 확인합니다. 내 파일은 바뀌지 않고\n원격에 새 커밋이 있는지만 알아냅니다. (git fetch와 동일)",
        ).pack(side="left", padx=10, pady=10)
        self._action_button(
            actions, "Pull (스마트)", self.on_pull,
            tooltip="원격의 최신 내용을 받아와 내 브랜치에 합칩니다. 커밋 안 된 변경사항이\n있으면 자동으로 stash 후 복원해줍니다. (git pull을 안전하게 자동화)",
        ).pack(side="left", padx=5)
        self._action_button(
            actions, "Push (스마트)", self.on_push,
            tooltip="내가 만든 커밋을 원격 저장소로 올립니다. 원격에 새 커밋이 있어\n거부되면 자동으로 먼저 pull한 뒤 다시 push합니다. (git push를 안전하게 자동화)",
        ).pack(side="left", padx=5)
        self._action_button(
            actions, "Stash 저장", self.on_stash_save,
            tooltip="커밋하지 않은 변경사항을 임시로 따로 보관해서\n작업 폴더를 깨끗한 상태로 되돌립니다. (git stash와 동일)",
        ).pack(side="left", padx=5)
        self._action_button(
            actions, "Stash 복원", self.on_stash_pop,
            tooltip="Stash에 보관해둔 변경사항을 다시 불러옵니다. (git stash pop과 동일)",
        ).pack(side="left", padx=5)
        sync_btn = self._action_button(
            actions, "🔄 Sync (Pull → Commit → Push)", self.on_sync,
            fg_color="#2e7d32", hover_color="#1b5e20",
            tooltip="Pull(스마트) → (메시지가 있으면) 전체 스테이징+커밋 → Push(스마트)를\n순서대로 한 번에 실행합니다. '최신화하고 내 작업 올리기'를 한 번에 끝냅니다.",
        )
        sync_btn.pack(side="right", padx=10)

    def _action_button(self, parent, text, command, tooltip=None, **kwargs):
        btn = ctk.CTkButton(parent, text=text, command=command, **kwargs)
        self.action_buttons.append(btn)
        if tooltip:
            add_tooltip(btn, tooltip)
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
        self._fetch_then_refresh()

    def _fetch_then_refresh(self):
        def task():
            try:
                self.git.fetch()
                self.log("원격 브랜치 목록을 최신화했습니다.", "info")
            except GitCommandError as e:
                self.log(f"원격 정보 갱신 실패 (오프라인이거나 접근 권한 문제일 수 있음): {e}", "warning")
            self._ui(self.refresh_status)

        self._run_in_thread(task)

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
                self._handle_git_error("복제 실패", e)

        self._run_in_thread(task)

    def on_show_git_help(self):
        win = ctk.CTkToplevel(self)
        win.title("Git 명령어 설명서")
        win.geometry("620x650")
        win.attributes("-topmost", True)

        scroll = ctk.CTkScrollableFrame(win, label_text="자주 쓰는 Git 명령어")
        scroll.pack(fill="both", expand=True, padx=15, pady=15)

        for category, items in GIT_GLOSSARY:
            ctk.CTkLabel(
                scroll, text=category, anchor="w",
                font=ctk.CTkFont(size=15, weight="bold"),
            ).pack(anchor="w", pady=(15, 5), fill="x")
            for cmd, desc in items:
                row = ctk.CTkFrame(scroll, fg_color=("gray90", "gray20"))
                row.pack(anchor="w", fill="x", pady=3)
                ctk.CTkLabel(
                    row, text=cmd, anchor="w",
                    font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                ).pack(anchor="w", padx=10, pady=(6, 0))
                ctk.CTkLabel(
                    row, text=desc, anchor="w", justify="left", wraplength=540,
                ).pack(anchor="w", padx=10, pady=(0, 6))

    # ---------- error helper ----------
    def _handle_git_error(self, title, exc):
        text = str(exc)
        self.log(f"{title}: {text.splitlines()[-1] if text.strip() else exc}", "error")
        self._ui(lambda: self.show_error_helper(title, text))

    def show_error_helper(self, title, raw_text, diagnosis=None):
        diag_title, explanation = diagnosis if diagnosis else diagnose_git_error(raw_text)

        win = ctk.CTkToplevel(self)
        win.title("오류 도우미")
        win.geometry("640x560")
        win.attributes("-topmost", True)

        ctk.CTkLabel(
            win, text=f"⚠ {title}", anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"), text_color="#e53935",
        ).pack(anchor="w", padx=15, pady=(15, 5), fill="x")

        ctk.CTkLabel(
            win, text=f"진단: {diag_title}", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=15, pady=(5, 0), fill="x")
        ctk.CTkLabel(
            win, text=explanation, anchor="w", justify="left", wraplength=600,
        ).pack(anchor="w", padx=15, pady=(5, 15), fill="x")

        ctk.CTkLabel(win, text="오류 원문 (직접 선택하거나 아래 버튼으로 복사할 수 있습니다):", anchor="w").pack(
            anchor="w", padx=15, fill="x"
        )
        text_box = ctk.CTkTextbox(win, height=180, font=ctk.CTkFont(family="Consolas", size=12))
        text_box.pack(fill="both", expand=True, padx=15, pady=(5, 10))
        text_box.insert("1.0", raw_text)

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(0, 15))

        def copy_to_clipboard():
            self.clipboard_clear()
            self.clipboard_append(raw_text)
            messagebox.showinfo("복사됨", "오류 내용이 클립보드에 복사되었습니다.", parent=win)

        ctk.CTkButton(btn_row, text="📋 오류 내용 복사", command=copy_to_clipboard).pack(side="left")
        ctk.CTkButton(btn_row, text="닫기", fg_color="gray40", hover_color="gray30", command=win.destroy).pack(side="right")

    # ---------- git console ----------
    DESTRUCTIVE_TOKENS = ("--hard", "--force", "-f", "-fd", "-fdx", "clean")

    def _looks_destructive(self, args):
        return any(a in self.DESTRUCTIVE_TOKENS for a in args)

    def on_run_command(self, event=None):
        if not self.git.repo:
            messagebox.showinfo("안내", "먼저 저장소를 열어주세요.")
            return None

        raw = self.command_entry.get().strip()
        if not raw:
            return None

        try:
            args = shlex.split(raw)
        except ValueError as e:
            messagebox.showerror("명령어 오류", f"명령어를 해석할 수 없습니다: {e}")
            return None
        if args and args[0] == "git":
            args = args[1:]
        if not args:
            return None

        self.command_entry.delete(0, "end")
        self.command_history.append(raw)
        self.history_index = len(self.command_history)

        if self._looks_destructive(args) and not messagebox.askyesno(
            "주의", f"'git {' '.join(args)}' 명령어는 되돌리기 어려운 변경을 만들 수 있습니다.\n정말 실행할까요?"
        ):
            self.log(f"$ git {' '.join(args)}  (사용자가 취소함)", "warning")
            return None

        def task():
            self.log(f"$ git {' '.join(args)}", "info")
            try:
                result = subprocess.run(
                    ["git", *args], cwd=self.git.path,
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
            except FileNotFoundError:
                self.log("git 실행 파일을 찾을 수 없습니다. Git이 설치되어 있는지 확인하세요.", "error")
                return

            if result.stdout.strip():
                self.log(result.stdout.rstrip("\n"), "info")
            if result.returncode != 0:
                err_text = result.stderr.rstrip("\n") or f"(종료 코드 {result.returncode})"
                self.log(err_text, "error")
                self._ui(lambda: self.show_error_helper(f"'git {' '.join(args)}' 실행 실패", err_text))
            elif result.stderr.strip():
                self.log(result.stderr.rstrip("\n"), "warning")
            self._ui(self.refresh_status)

        self._run_in_thread(task)
        return "break"

    def _history_prev(self, event=None):
        if not self.command_history:
            return "break"
        self.history_index = max(0, self.history_index - 1)
        self._set_command_text(self.command_history[self.history_index])
        return "break"

    def _history_next(self, event=None):
        if not self.command_history:
            return "break"
        self.history_index = min(len(self.command_history), self.history_index + 1)
        if self.history_index == len(self.command_history):
            self._set_command_text("")
        else:
            self._set_command_text(self.command_history[self.history_index])
        return "break"

    def _set_command_text(self, text):
        self.command_entry.delete(0, "end")
        self.command_entry.insert(0, text)

    # ---------- status refresh ----------
    def refresh_status(self):
        if not self.git.repo:
            return

        branch_info = self.git.branches()
        self._branch_display_map = {}
        display_values = []
        for name, is_local in branch_info:
            label = name if is_local else f"{name} (원격 전용)"
            display_values.append(label)
            self._branch_display_map[label] = name
        self.branch_menu.configure(values=display_values or ["-"])
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
    def on_branch_selected(self, label):
        branch_name = self._branch_display_map.get(label, label)
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
            self._handle_git_error("브랜치 전환 실패", e)
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
            self._handle_git_error("커밋 실패", e)
        self.refresh_status()

    def on_fetch(self):
        def task():
            try:
                self.log("Fetch 중...", "info")
                self.git.fetch()
                self.log("Fetch 완료.", "success")
            except GitCommandError as e:
                self._handle_git_error("Fetch 실패", e)
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_pull(self):
        def task():
            try:
                self.git.smart_pull(self.log)
            except GitConflictError as e:
                self._report_conflict(e)
            except GitCommandError as e:
                self._handle_git_error("Pull 실패", e)
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def on_push(self):
        def task():
            try:
                self.git.smart_push(self.log)
            except GitConflictError as e:
                self._report_conflict(e)
            except GitCommandError as e:
                self._handle_git_error("Push 실패", e)
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
                self._handle_git_error("Stash 저장 실패", e)
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
                    self._handle_git_error("Stash 복원 실패", e)
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
                self._handle_git_error("Sync 실패", e)
            self._ui(self.refresh_status)

        self._run_in_thread(task)

    def _report_conflict(self, err: GitConflictError):
        file_list = "\n".join(f"- {f}" for f in err.files)
        self.log(f"{err.stage} 중 충돌 발생! 아래 파일을 직접 확인 후 해결하세요.", "error")
        raw_text = f"{err.stage} 중 다음 파일에서 충돌이 발생했습니다:\n\n{file_list}"
        diagnosis = (
            "병합 충돌 (Conflict)",
            "같은 부분을 서로 다르게 고쳐서 Git이 자동으로 합치지 못했습니다.\n\n"
            "해결 방법:\n"
            "1) 위에 나열된 파일들을 하나씩 열어보세요.\n"
            "2) 파일 안의 <<<<<<< / ======= / >>>>>>> 표시 사이 내용을 직접 정리하세요.\n"
            "3) 정리가 끝나면 해당 파일을 다시 Add하고 커밋하세요.",
        )
        self._ui(lambda: self.show_error_helper(f"{err.stage} 중 충돌 발생", raw_text, diagnosis))

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
