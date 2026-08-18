"""
Visual Studio Code / Python / PowerShell 7.x Autodown
1단계 : 실제 웹사이트에서 사용자가 클릭할 버튼/링크를 그대로 찾아 클릭해서 설치파일 다운로드
2단계 : 사용자 선택에 의한 설치 시작

필요 패키지: pip install selenium  (Microsoft Edge 브라우저 필요, 드라이버는 Selenium이 자동 설치)
"""

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
TEMP_SUFFIXES = (".crdownload", ".tmp", ".download")
WAIT_TIMEOUT = 20


def make_driver(download_dir: Path, startup_timeout: int = 30):
    download_dir = download_dir.resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    opts = EdgeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--log-level=3")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    opts.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(download_dir),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
        },
    )

    # webdriver.Edge()가 드라이버/브라우저 버전 불일치 등으로 응답 없이 멈추는
    # 경우가 있어, 별도 스레드에서 생성하고 시간 제한을 걸어 무한 대기를 막는다.
    result = {}

    def _create():
        try:
            result["driver"] = webdriver.Edge(options=opts)
        except Exception as e:
            result["error"] = e

    creator = threading.Thread(target=_create, daemon=True)
    creator.start()
    creator.join(startup_timeout)
    if creator.is_alive():
        raise TimeoutError(
            f"브라우저 실행이 {startup_timeout}초 내에 끝나지 않았습니다. "
            "Edge 브라우저/드라이버 버전을 확인해 주세요."
        )
    if "error" in result:
        raise result["error"]

    driver = result["driver"]
    driver.set_page_load_timeout(60)
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(download_dir)}
        )
    except Exception:
        pass  # prefs 설정만으로도 대부분의 경우 다운로드가 동작한다
    return driver


def wait_for_download(dest_dir: Path, before: set, progress_cb=None, timeout: int = 1800) -> Path:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = set(dest_dir.iterdir()) if dest_dir.exists() else set()
        new_files = [f for f in (current - before) if f.is_file()]
        finished = [f for f in new_files if f.suffix not in TEMP_SUFFIXES]
        if finished:
            return finished[0]
        in_progress = [f for f in new_files if f.suffix in TEMP_SUFFIXES]
        if in_progress and progress_cb:
            size_mb = in_progress[0].stat().st_size / (1024 * 1024)
            progress_cb(f"다운로드 중... {size_mb:.1f} MB")
        time.sleep(0.5)
    raise TimeoutError("다운로드가 제한 시간 내에 끝나지 않았습니다.")


def click_download_vscode(driver, dest_dir: Path, progress_cb=None) -> Path:
    before = set(dest_dir.iterdir()) if dest_dir.exists() else set()
    if progress_cb:
        progress_cb("사이트 접속 중...")
    driver.get("https://code.visualstudio.com/download?_exp_download=fb315fc982")

    if progress_cb:
        progress_cb("'Windows' 버튼 클릭...")
    btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.element_to_be_clickable((By.ID, "download-alt-win"))
    )
    btn.click()
    return wait_for_download(dest_dir, before, progress_cb)


def click_download_python(driver, dest_dir: Path, progress_cb=None) -> Path:
    before = set(dest_dir.iterdir()) if dest_dir.exists() else set()
    if progress_cb:
        progress_cb("사이트 접속 중...")
    driver.get("https://www.python.org/")

    if progress_cb:
        progress_cb("'Downloads' 클릭...")
    downloads_link = WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/downloads/"]'))
    )
    driver.get(downloads_link.get_attribute("href"))

    if progress_cb:
        progress_cb("'Windows' 클릭...")
    windows_link = WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'a[href="/downloads/windows/"]'))
    )
    driver.get(windows_link.get_attribute("href"))

    if progress_cb:
        progress_cb("최신 버전 확인 중...")
    latest_el = WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "Latest Python 3 Release"))
    )
    version = latest_el.text.split("Python ")[-1].strip()

    release_link = driver.find_element(By.XPATH, f"//a[starts-with(text(), 'Python {version} - ')]")
    entry = release_link.find_element(By.XPATH, "./..")

    if progress_cb:
        progress_cb(f"Python {version} · 'Windows installer (64-bit)' 클릭...")
    installer_link = entry.find_element(By.PARTIAL_LINK_TEXT, "Windows installer (64-bit)")
    installer_link.click()
    return wait_for_download(dest_dir, before, progress_cb)


def click_download_powershell(driver, dest_dir: Path, progress_cb=None) -> Path:
    before = set(dest_dir.iterdir()) if dest_dir.exists() else set()
    if progress_cb:
        progress_cb("사이트 접속 중...")
    driver.get(
        "https://learn.microsoft.com/ko-kr/powershell/scripting/install/"
        "install-powershell-on-windows?view=powershell-7.6"
    )

    if progress_cb:
        progress_cb("'...win-x64.msi' 링크 클릭...")
    link = WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "win-x64.msi"))
    )
    link.click()
    return wait_for_download(dest_dir, before, progress_cb)


PROGRAMS = [
    {
        "key": "vscode",
        "name": "Visual Studio Code",
        "click_fn": click_download_vscode,
        "source_page": "https://code.visualstudio.com/download?_exp_download=fb315fc982",
        "matched_link": "페이지의 'Windows' 버튼을 그대로 클릭",
    },
    {
        "key": "python",
        "name": "Python",
        "click_fn": click_download_python,
        "source_page": "https://www.python.org/downloads/",
        "matched_link": "Downloads → Windows → 최신 버전의 'Windows installer (64-bit)' 클릭",
    },
    {
        "key": "powershell",
        "name": "PowerShell 7",
        "click_fn": click_download_powershell,
        "source_page": "https://learn.microsoft.com/ko-kr/powershell/scripting/install/install-powershell-on-windows?view=powershell-7.6",
        "matched_link": "문서에 안내된 'win-x64.msi' 링크를 그대로 클릭",
    },
]


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

BG = "#1e1f26"
PANEL = "#2a2b36"
ACCENT = "#5b8cff"
TEXT = "#e8e9ee"
SUBTEXT = "#9a9db0"
OK = "#4cc38a"
ERR = "#ff6b6b"


class CustomCheck(tk.Label):
    """Native tk.Checkbutton's indicator box doesn't render reliably on a
    dark background, so draw the check state as text instead."""

    def __init__(self, parent, initial=True, command=None, **kwargs):
        self.var = tk.BooleanVar(value=initial)
        self.command = command
        super().__init__(
            parent, text="☑" if initial else "☐", font=("Segoe UI Symbol", 15),
            fg=ACCENT if initial else SUBTEXT, bg=PANEL, cursor="hand2", **kwargs,
        )
        self.bind("<Button-1>", self._toggle)

    def _toggle(self, event=None):
        self.var.set(not self.var.get())
        self._refresh()
        if self.command:
            self.command()

    def _refresh(self):
        checked = self.var.get()
        self.config(text="☑" if checked else "☐", fg=ACCENT if checked else SUBTEXT)


class ProgramRow:
    def __init__(self, parent, program, on_change):
        self.program = program
        self.path = None
        self.state = "대기"

        self.frame = tk.Frame(parent, bg=PANEL)
        self.frame.pack(fill="x", padx=16, pady=6)

        self.check = CustomCheck(self.frame, initial=True, command=on_change)
        self.check.grid(row=0, column=0, rowspan=3, padx=(12, 8), pady=10)
        self.var = self.check.var

        self.name_label = tk.Label(
            self.frame, text=program["name"], bg=PANEL, fg=TEXT,
            font=("Segoe UI", 11, "bold"), anchor="w",
        )
        self.name_label.grid(row=0, column=1, sticky="w", pady=(10, 0))

        self.source_label = tk.Label(
            self.frame, text=f"출처: {program['source_page'].split('/')[2]}  ·  {program['matched_link']}",
            bg=PANEL, fg=ACCENT, font=("Segoe UI", 8, "underline"), anchor="w", cursor="hand2",
            wraplength=320, justify="left",
        )
        self.source_label.grid(row=1, column=1, sticky="w")
        self.source_label.bind("<Button-1>", lambda e: webbrowser.open(program["source_page"]))

        self.status_label = tk.Label(
            self.frame, text="대기 중", bg=PANEL, fg=SUBTEXT, font=("Segoe UI", 9), anchor="w",
        )
        self.status_label.grid(row=2, column=1, sticky="w", pady=(0, 10))

        self.progress = ttk.Progressbar(
            self.frame, orient="horizontal", mode="indeterminate", length=160,
            style="Accent.Horizontal.TProgressbar",
        )
        self.progress.grid(row=0, column=2, rowspan=3, padx=16, pady=10)

        self.frame.grid_columnconfigure(1, weight=1)

    def set_status(self, text):
        self.status_label.config(text=text, fg=SUBTEXT)

    def start_progress(self):
        self.progress.start(12)

    def set_done(self, path):
        self.path = path
        self.state = "완료"
        self.progress.stop()
        self.progress["mode"] = "determinate"
        self.progress["value"] = 100
        self.status_label.config(text=f"완료 · {path.name}", fg=OK)

    def set_error(self, message):
        self.state = "실패"
        self.progress.stop()
        self.status_label.config(text=f"실패 · {message}", fg=ERR)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Dev Tools Autodown")
        self.root.configure(bg=BG)
        self.root.geometry("560x460")
        self.root.resizable(False, False)

        self.msg_queue = queue.Queue()
        self.rows = {}
        self.downloading = False

        self._build_style()
        self._build_layout()
        self.root.after(80, self._poll_queue)

        if not SELENIUM_AVAILABLE:
            self.download_btn.config(state="disabled")
            self._show_banner(
                "selenium 패키지가 설치되어 있지 않습니다. 터미널에서 "
                "'pip install selenium' 실행 후 다시 시작해 주세요."
            )

    def _build_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=PANEL, background=ACCENT, bordercolor=PANEL,
            lightcolor=ACCENT, darkcolor=ACCENT, thickness=8,
        )

    def _build_layout(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=20, pady=(20, 4))
        self.header = header
        tk.Label(
            header, text="개발 도구 자동 설치", bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header, text="실제 브라우저로 사이트에 접속해 안내된 버튼/링크를 그대로 클릭합니다",
            bg=BG, fg=SUBTEXT, font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        self.banner_label = tk.Label(
            self.root, text="", bg=BG, fg=ERR, font=("Segoe UI", 8), anchor="w",
            wraplength=520, justify="left",
        )

        list_panel = tk.Frame(self.root, bg=BG)
        list_panel.pack(fill="both", expand=True, padx=8, pady=10)
        for program in PROGRAMS:
            row = ProgramRow(list_panel, program, self._on_selection_change)
            self.rows[program["key"]] = row

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=20, pady=(4, 20))

        self.download_btn = tk.Button(
            footer, text="다운로드 시작", command=self.start_download,
            bg=ACCENT, fg="white", activebackground="#4a78e0", activeforeground="white",
            font=("Segoe UI", 10, "bold"), bd=0, relief="flat", padx=14, pady=8,
            cursor="hand2",
        )
        self.download_btn.pack(side="left")

        self.install_btn = tk.Button(
            footer, text="설치 시작", command=self.start_install,
            bg="#3a3c4a", fg=TEXT, activebackground="#454758", activeforeground=TEXT,
            font=("Segoe UI", 10, "bold"), bd=0, relief="flat", padx=14, pady=8,
            cursor="hand2", state="disabled",
        )
        self.install_btn.pack(side="left", padx=8)

    def _show_banner(self, text):
        self.banner_label.config(text=text)
        self.banner_label.pack(fill="x", padx=20, pady=(0, 4), after=self.header)

    def _on_selection_change(self):
        pass

    # -- download ---------------------------------------------------------
    def start_download(self):
        if self.downloading or not SELENIUM_AVAILABLE:
            return
        selected = [p for p in PROGRAMS if self.rows[p["key"]].var.get()]
        if not selected:
            return

        self.downloading = True
        self.download_btn.config(state="disabled", text="다운로드 중...")
        self.install_btn.config(state="disabled")
        for p in selected:
            self.rows[p["key"]].start_progress()

        thread = threading.Thread(target=self._download_worker, args=(selected,), daemon=True)
        thread.start()

    def _download_worker(self, selected):
        driver = None
        try:
            try:
                driver = make_driver(DOWNLOAD_DIR)
            except Exception as e:
                for program in selected:
                    self.msg_queue.put(("error", program["key"], f"브라우저 실행 실패: {e}"))
                return

            for program in selected:
                key = program["key"]

                def cb(text, key=key):
                    self.msg_queue.put(("status", key, text))

                try:
                    path = program["click_fn"](driver, DOWNLOAD_DIR, progress_cb=cb)
                    self.msg_queue.put(("done", key, path))
                except Exception as e:
                    self.msg_queue.put(("error", key, str(e)))
        finally:
            if driver:
                driver.quit()
            self.msg_queue.put(("all_done", None, None))

    def _poll_queue(self):
        try:
            while True:
                kind, key, value = self.msg_queue.get_nowait()
                if kind == "status":
                    self.rows[key].set_status(value)
                elif kind == "done":
                    self.rows[key].set_done(value)
                elif kind == "error":
                    self.rows[key].set_error(value)
                elif kind == "all_done":
                    self.downloading = False
                    self.download_btn.config(state="normal", text="다운로드 시작")
                    if any(r.path for r in self.rows.values()):
                        self.install_btn.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    # -- install ------------------------------------------------------------
    def start_install(self):
        for row in self.rows.values():
            if row.path and row.var.get():
                os.startfile(str(row.path))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
