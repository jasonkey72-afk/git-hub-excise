"""
cdp - 드라이버 없이 브라우저를 직접 조작하는 엔진
==================================================
msedgedriver.exe / chromedriver.exe 가 **필요 없다.**
설치된 Edge(또는 Chrome)를 디버깅 포트로 띄워 CDP 로 직접 대화하므로
"브라우저와 드라이버 버전이 맞지 않아 실행 실패" 라는 문제가 사라진다.

    from jobscenario.webauto.cdp import CDPActions

    web = CDPActions.start("edge", user_data_dir=r"C:\\...\\edge_profile")
    web.open_url("https://portal.company.com")
    web.input_text("label=사번", "20250001")
    web.click("text=조회")
"""

from .actions import CDPActions
from .client import CDPClient, CDPError, CDPClosed
from .launcher import launch, find_browser, attach_existing
from .page import CDPBrowser, CDPPage, CDPElement, ElementNotFound
from .windows import list_open_windows

__all__ = [
    "CDPActions", "CDPClient", "CDPError", "CDPClosed",
    "launch", "find_browser", "attach_existing", "list_open_windows",
    "CDPBrowser", "CDPPage", "CDPElement", "ElementNotFound",
]
