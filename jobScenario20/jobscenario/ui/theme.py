"""
모던 UI 테마
=============
외부 라이브러리 없이 tkinter/ttk 만으로 요즘 감각의 화면을 만든다.
색과 여백을 한곳에서 관리하므로 색상만 바꾸면 전체 분위기가 바뀐다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# 색상 팔레트
BG        = "#f4f6fb"   # 전체 배경
CARD      = "#ffffff"   # 카드 배경
BORDER    = "#e3e8f0"   # 경계선
TEXT      = "#111827"   # 기본 글자
MUTED     = "#6b7280"   # 보조 글자
PRIMARY   = "#2563eb"   # 강조(파랑)
PRIMARY_D = "#1d4ed8"
SUCCESS   = "#16a34a"
DANGER    = "#dc2626"
WARN      = "#f59e0b"
DARK      = "#1f2937"   # 헤더/로그 배경
LIGHT_TXT = "#e5e7eb"

FONT      = ("맑은 고딕", 10)
FONT_SM   = ("맑은 고딕", 9)
FONT_BOLD = ("맑은 고딕", 10, "bold")
FONT_H1   = ("맑은 고딕", 17, "bold")
FONT_H2   = ("맑은 고딕", 12, "bold")
FONT_MONO = ("Consolas", 9)


def apply_theme(root: tk.Misc) -> ttk.Style:
    """루트 창에 테마를 적용한다."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(bg=BG)
    style.configure(".", font=FONT, background=BG, foreground=TEXT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD, relief="flat")
    style.configure("Dark.TFrame", background=DARK)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=FONT_SM)
    style.configure("H1.TLabel", background=DARK, foreground="#ffffff", font=FONT_H1)
    style.configure("Sub.TLabel", background=DARK, foreground="#93c5fd", font=FONT_SM)
    style.configure("H2.TLabel", background=CARD, foreground=TEXT, font=FONT_H2)

    style.configure("TEntry", fieldbackground="#ffffff", bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, padding=5)
    style.configure("TCombobox", fieldbackground="#ffffff", padding=4)
    style.configure("TCheckbutton", background=CARD, foreground=TEXT)
    style.configure("Card.TCheckbutton", background=CARD)

    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                    foreground=TEXT, rowheight=28, borderwidth=0, font=FONT)
    style.configure("Treeview.Heading", background="#eef2f9", foreground=MUTED,
                    font=FONT_BOLD, relief="flat", padding=6)
    style.map("Treeview.Heading", background=[("active", "#e2e8f5")])
    style.map("Treeview", background=[("selected", "#dbeafe")],
              foreground=[("selected", TEXT)])

    style.configure("TSeparator", background=BORDER)
    style.configure("Horizontal.TProgressbar", background=PRIMARY,
                    troughcolor="#e5e7eb", borderwidth=0, thickness=6)
    return style


class Button(tk.Button):
    """색을 직접 지정하는 납작한 버튼(마우스를 올리면 색이 진해진다)."""

    KINDS = {
        "primary": (PRIMARY, PRIMARY_D, "#ffffff"),
        "success": (SUCCESS, "#15803d", "#ffffff"),
        "danger":  (DANGER, "#b91c1c", "#ffffff"),
        "warn":    (WARN, "#d97706", "#ffffff"),
        "ghost":   ("#eef2f9", "#dde5f3", TEXT),
        "dark":    ("#374151", "#1f2937", "#ffffff"),
    }

    def __init__(self, master, text: str, command=None, kind: str = "ghost",
                 width: int = 0, **kw):
        bg, hover, fg = self.KINDS.get(kind, self.KINDS["ghost"])
        self._bg, self._hover = bg, hover
        super().__init__(
            master, text=text, command=command, bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            font=FONT_BOLD if kind in ("primary", "success", "danger") else FONT,
            relief="flat", bd=0, padx=14, pady=7, cursor="hand2",
            highlightthickness=0, **kw
        )
        if width:
            self.configure(width=width)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, _):
        if str(self["state"]) != "disabled":
            self.configure(bg=self._hover)

    def _on_leave(self, _):
        self.configure(bg=self._bg)


def card(master, **kw) -> tk.Frame:
    """흰 배경에 얇은 테두리를 가진 카드 영역."""
    frame = tk.Frame(master, bg=CARD, highlightbackground=BORDER,
                     highlightthickness=1, bd=0, **kw)
    return frame


def section(master, title: str, subtitle: str = "") -> tk.Frame:
    """카드 + 제목 줄을 함께 만든다. 내용은 반환된 body 에 넣는다."""
    box = card(master)
    head = tk.Frame(box, bg=CARD)
    head.pack(fill="x", padx=16, pady=(12, 0))
    tk.Label(head, text=title, bg=CARD, fg=TEXT, font=FONT_H2).pack(side="left")
    if subtitle:
        tk.Label(head, text="  " + subtitle, bg=CARD, fg=MUTED,
                 font=FONT_SM).pack(side="left")
    body = tk.Frame(box, bg=CARD)
    body.pack(fill="both", expand=True, padx=16, pady=12)
    box.body = body
    return box
