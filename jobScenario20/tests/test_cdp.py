"""
드라이버 없는 CDP 엔진 검증
============================
실행:  python tests\\test_cdp.py

msedgedriver.exe / chromedriver.exe 를 **전혀 쓰지 않고**
설치된 브라우저에 직접 연결해 같은 자동화가 되는지 확인한다.
selenium 판(test_webauto.py)과 같은 항목을 검사하므로 두 엔진을 비교할 수 있다.
"""

import json
import os
import sys

from conftest_driver import MAIN_PAGE, Report, assert_eq  # noqa: E402

from jobscenario.webauto.cdp import CDPActions
from jobscenario.webauto.locator import ElementNotFound, parse_target
from jobscenario.webauto import picker

r = Report("cdp (드라이버 없음)")

web = CDPActions.start(
    browser=os.environ.get("JOBSCN_TEST_BROWSER", "edge"),
    headless=os.environ.get("JOBSCN_TEST_HEADLESS") == "1",
    port=int(os.environ.get("JOBSCN_TEST_PORT", "0") or 0),
    log=lambda m: None,
)

try:
    web.open_url(MAIN_PAGE)
    print("페이지 로드:", web.title(), "| 엔진:", web.engine_name)

    r.expect("드라이버 없이 연결됨", web.alive)
    r.expect("제목 읽기", web.title() == "사내 포털 테스트", web.title())
    r.expect("주소 읽기", "portal.html" in web.current_url(), web.current_url())

    # 표 구조에서 라벨 옆 입력창
    r.check("label=사번 입력", lambda: (
        web.input_text("label=사번", "20250001"),
        assert_eq(web.get_text("id=empno"), "20250001"),
    ))

    r.check("placeholder 입력", lambda: (
        web.input_text("검색어 입력", "자동화"),
        assert_eq(web.get_text("id=q"), "자동화"),
    ))

    # 실제 마우스 이벤트로 클릭되는지
    r.check("text=조회 클릭(실제 마우스 이벤트)", lambda: (
        web.click("text=조회"),
        assert_eq(web.get_text("#res"), "조회 완료"),
    ))

    r.check("select 선택", lambda: (
        web.select_option("id=dept", "영업부"),
        assert_eq(web.run_script("document.getElementById('dept').value"), "d2"),
    ))

    # iframe : 프레임을 옮겨 다니지 않고 각 컨텍스트에서 바로 찾는다
    r.check("iframe 자동 탐색 클릭", lambda: (
        web.click("text=승인"),
        assert_eq(web.get_text("id=st"), "승인됨"),
    ))

    r.check("iframe 안 label 입력", lambda: (
        web.input_text("label=메모", "확인함"),
        assert_eq(web.get_text("id=memo"), "확인함"),
    ))

    r.check("wait_text(iframe 포함 전 프레임 검사)", lambda: (
        web.wait_text("사내 포털", 5),
        web.wait_text("결재 화면", 5),
    ))

    # Shadow DOM
    r.check("Shadow DOM 입력", lambda: (
        web.input_text("deep=#code", "A-1234"),
        assert_eq(web.run_script(
            "document.querySelector('my-widget').shadowRoot"
            ".getElementById('code').value"), "A-1234"),
    ))

    r.check("Shadow DOM 클릭", lambda: (
        web.click("deep=#deepBtn"),
        assert_eq(web.run_script(
            "document.querySelector('my-widget').shadowRoot"
            ".getElementById('deepSt').textContent"), "내부확인됨"),
    ))

    # 요소 선택기 : 수집한 선택자를 저장했다가 다시 찾을 수 있어야 한다
    picked = {}

    def picker_flow():
        page = web.page
        for _, ctx in page.frame_contexts():
            page.evaluate(picker.PICKER_JS, context_id=ctx)
        page.evaluate("document.querySelector('[data-testid=save-btn]')"
                      ".dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        raw = page.evaluate(picker.READ_JS_EXPR, context_id=page.main_context)
        assert raw, "요소 선택기가 클릭을 잡지 못했습니다"
        info = json.loads(raw)
        assert_eq(info["tag"], "button")
        target = picker.build_target(info, [])
        assert_eq(web.find(target, timeout=5).info()["text"], "저장하기")
        picked["target"] = target
        web.stop_picker()

    r.check("요소 선택기 수집 + 재탐색", picker_flow)

    r.check("Target JSON 저장/복원", lambda: assert_eq(
        web.find(json.loads(json.dumps(picked["target"].to_dict())),
                 timeout=5).info()["text"], "저장하기"))

    # 화면 캡처 / HTML (실패 기록에 쓰인다)
    def capture():
        import tempfile
        from pathlib import Path
        out = Path(tempfile.mkdtemp()) / "shot.png"
        web.save_screenshot(str(out))
        assert out.stat().st_size > 1000, "캡처 파일이 비어 있습니다"
        assert "사내 포털" in web.page_source(), "HTML 을 읽지 못했습니다"

    r.check("화면 캡처 + HTML 저장", capture)

    # 키 입력
    r.check("키 입력", lambda: (
        web.input_text("id=q", "엔터확인"),
        web.press_key("enter"),
        assert_eq(web.get_text("id=q"), "엔터확인"),
    ))

    # 스크롤 / 자바스크립트
    r.check("스크롤", lambda: web.scroll("bottom"))
    r.check("자바스크립트 실행", lambda: assert_eq(web.run_script("1+1"), 2))

    # 없는 요소는 제한 시간 안에 사용자 언어로 실패해야 한다
    def not_found():
        try:
            web.click("text=존재하지않는버튼XYZ", timeout=2)
        except ElementNotFound as e:
            assert "찾지 못했습니다" in str(e), str(e)
            return
        raise AssertionError("오류가 발생해야 하는데 성공했습니다")

    r.check("없는 요소 -> 친절한 오류", not_found)

    # 새 탭 추적
    def tabs():
        web.run_script("window.open('inner.html', '_blank')")
        web.switch_tab(-1)                      # -1 = 가장 새로 열린 탭
        assert "inner.html" in web.current_url(), web.current_url()
        assert_eq(web.get_text("id=approve"), "승인")
        web.close_tab()
        assert "portal.html" in web.current_url(), web.current_url()

    r.check("새 탭 전환 / 닫기", tabs)
finally:
    try:
        web.quit()
    except Exception:
        pass

sys.exit(r.finish())
