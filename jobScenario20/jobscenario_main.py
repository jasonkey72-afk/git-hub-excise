"""
jobScenario 실행 진입점
========================
개발 중 실행 :  python jobscenario_main.py
배포본 실행  :  jobScenario.exe   (build.bat 으로 생성)
"""

import sys
import traceback


def main():
    try:
        from jobscenario.ui.app import main as run_app
    except ImportError as e:
        _fatal("필요한 라이브러리가 없습니다.\n\n%s\n\n"
               "명령창에서 아래를 실행해 주세요.\n"
               "    pip install -r requirements.txt" % e)
        return 1

    try:
        run_app()
    except Exception:
        _fatal("프로그램 실행 중 오류가 발생했습니다.\n\n%s"
               % traceback.format_exc(limit=5))
        return 1
    return 0


def _fatal(message: str):
    """창이 뜨기 전에 실패하면 메시지 상자로 알려준다(exe 는 콘솔이 없다)."""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("jobScenario", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
