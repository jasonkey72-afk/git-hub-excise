"""
PPT/PPTX -> PDF 변환기 (GUI)

요구사항: Windows + Microsoft PowerPoint 설치, pywin32 (pip install pywin32)
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pythoncom
import win32com.client

PP_SAVE_AS_PDF = 32
PPTX_EXTENSIONS = {".pptx", ".ppt"}

BG = "#F4F5F9"
CARD = "#FFFFFF"
BORDER = "#E4E6EF"
TEXT = "#1F2333"
SUBTEXT = "#8A8FA3"
ACCENT = "#4C5FEA"
ACCENT_HOVER = "#3E4FD1"
ACCENT_DISABLED = "#B9C0F7"
OK = "#1FA971"
ERR = "#E5484D"


# ---------------------------------------------------------------- 변환 로직

def collect_targets(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(
        p for p in input_path.iterdir()
        if p.is_file() and p.suffix.lower() in PPTX_EXTENSIONS
    )


def convert_one(powerpoint, src: Path, out_dir: Path) -> Path:
    dst = out_dir / (src.stem + ".pdf")
    presentation = powerpoint.Presentations.Open(str(src), WithWindow=False)
    try:
        presentation.SaveAs(str(dst), PP_SAVE_AS_PDF)
    finally:
        presentation.Close()
    return dst


def convert_all(targets: list[Path], output_dir: Path | None, on_result, on_done):
    """백그라운드 스레드에서 실행. on_result/on_done은 메인 스레드로 안전하게 스케줄된다."""
    pythoncom.CoInitialize()
    try:
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        try:
            for src in targets:
                dst_dir = output_dir or src.parent
                dst_dir.mkdir(parents=True, exist_ok=True)
                try:
                    dst = convert_one(powerpoint, src, dst_dir)
                    on_result(src, dst, None)
                except Exception as e:
                    on_result(src, None, e)
        finally:
            powerpoint.Quit()
    finally:
        pythoncom.CoUninitialize()
        on_done()


# ---------------------------------------------------------------------- GUI

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PPT to PDF 변환기")
        self.geometry("560x600")
        self.minsize(520, 560)
        self.configure(bg=BG)

        self.input_path: Path | None = None
        self.output_path: Path | None = None
        self.same_as_source = tk.BooleanVar(value=True)
        self.running = False

        self._build_style()
        self._build_ui()

    # -- 스타일 --------------------------------------------------------

    def _build_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 18, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=SUBTEXT, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Path.TLabel", background=CARD, foreground=SUBTEXT, font=("Segoe UI", 9))
        style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT, font=("Segoe UI", 10))
        style.map("Card.TCheckbutton", background=[("active", CARD)])

        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        font=("Segoe UI", 11, "bold"), padding=12, borderwidth=0)
        style.map("Accent.TButton",
                  background=[("disabled", ACCENT_DISABLED), ("active", ACCENT_HOVER)],
                  foreground=[("disabled", "white")])

        style.configure("Ghost.TButton", background=CARD, foreground=TEXT,
                        font=("Segoe UI", 9), padding=8, borderwidth=1, relief="solid")
        style.map("Ghost.TButton", background=[("active", "#F0F1F7")])

        style.configure("Modern.Horizontal.TProgressbar", troughcolor=BORDER,
                        background=ACCENT, thickness=6, borderwidth=0)

    # -- 레이아웃 --------------------------------------------------------

    def _build_ui(self):
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="PPT → PDF 변환기", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="PPT/PPTX 파일 또는 폴더를 선택해 PDF로 변환합니다.",
                  style="Sub.TLabel").pack(anchor="w", pady=(4, 20))

        card = tk.Frame(outer, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        inner = ttk.Frame(card, style="Card.TFrame", padding=18)
        inner.pack(fill="both", expand=True)

        ttk.Label(inner, text="변환할 대상", style="Card.TLabel").pack(anchor="w")
        row = ttk.Frame(inner, style="Card.TFrame")
        row.pack(fill="x", pady=(6, 4))
        ttk.Button(row, text="파일 선택", style="Ghost.TButton",
                   command=self.pick_file).pack(side="left")
        ttk.Button(row, text="폴더 선택", style="Ghost.TButton",
                   command=self.pick_folder).pack(side="left", padx=(8, 0))
        self.input_label = ttk.Label(inner, text="선택된 항목 없음", style="Path.TLabel", wraplength=460)
        self.input_label.pack(anchor="w", pady=(4, 16))

        ttk.Label(inner, text="저장 위치", style="Card.TLabel").pack(anchor="w")
        ttk.Checkbutton(inner, text="원본과 같은 폴더에 저장", variable=self.same_as_source,
                        style="Card.TCheckbutton", command=self._toggle_output).pack(anchor="w", pady=(6, 4))
        out_row = ttk.Frame(inner, style="Card.TFrame")
        out_row.pack(fill="x")
        self.output_btn = ttk.Button(out_row, text="출력 폴더 선택", style="Ghost.TButton",
                                     command=self.pick_output, state="disabled")
        self.output_btn.pack(side="left")
        self.output_label = ttk.Label(inner, text="", style="Path.TLabel", wraplength=460)
        self.output_label.pack(anchor="w", pady=(4, 0))

        self.convert_btn = ttk.Button(outer, text="PDF로 변환", style="Accent.TButton",
                                      command=self.start_convert)
        self.convert_btn.pack(fill="x", pady=(20, 10))

        self.progress = ttk.Progressbar(outer, style="Modern.Horizontal.TProgressbar", mode="determinate")
        self.progress.pack(fill="x", pady=(0, 10))

        log_card = tk.Frame(outer, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        log_card.pack(fill="both", expand=True)
        self.log = tk.Text(log_card, bg=CARD, fg=TEXT, font=("Consolas", 9),
                           borderwidth=0, highlightthickness=0, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=10)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("err", foreground=ERR)
        self.log.tag_configure("info", foreground=SUBTEXT)

    # -- 이벤트 --------------------------------------------------------

    def _toggle_output(self):
        if self.same_as_source.get():
            self.output_btn.configure(state="disabled")
            self.output_path = None
            self.output_label.configure(text="")
        else:
            self.output_btn.configure(state="normal")

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="변환할 PPT/PPTX 파일 선택",
            filetypes=[("PowerPoint 파일", "*.pptx *.ppt")]
        )
        if path:
            self.input_path = Path(path)
            self.input_label.configure(text=f"파일: {self.input_path}")

    def pick_folder(self):
        path = filedialog.askdirectory(title="PPT/PPTX 파일이 있는 폴더 선택")
        if path:
            self.input_path = Path(path)
            self.input_label.configure(text=f"폴더: {self.input_path}")

    def pick_output(self):
        path = filedialog.askdirectory(title="PDF를 저장할 폴더 선택")
        if path:
            self.output_path = Path(path)
            self.output_label.configure(text=f"출력: {self.output_path}")

    def _log(self, text: str, tag: str = "info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def start_convert(self):
        if self.running:
            return
        if self.input_path is None:
            messagebox.showwarning("알림", "먼저 변환할 파일 또는 폴더를 선택하세요.")
            return

        targets = collect_targets(self.input_path)
        if not targets:
            messagebox.showwarning("알림", "변환할 PPT/PPTX 파일이 없습니다.")
            return

        self.running = True
        self.convert_btn.configure(state="disabled", text="변환 중...")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.progress.configure(maximum=len(targets), value=0)
        self._log(f"총 {len(targets)}개 파일 변환을 시작합니다.")

        def on_result(src: Path, dst: Path | None, error: Exception | None):
            self.after(0, self._handle_result, src, dst, error)

        def on_done():
            self.after(0, self._handle_done)

        threading.Thread(
            target=convert_all,
            args=(targets, self.output_path, on_result, on_done),
            daemon=True,
        ).start()

    def _handle_result(self, src: Path, dst: Path | None, error: Exception | None):
        self.progress.step(1)
        if error is None:
            self._log(f"완료  {src.name} → {dst.name}", "ok")
        else:
            self._log(f"실패  {src.name} → {error}", "err")

    def _handle_done(self):
        self.running = False
        self.convert_btn.configure(state="normal", text="PDF로 변환")
        self._log("모든 작업이 끝났습니다.")


if __name__ == "__main__":
    App().mainloop()
