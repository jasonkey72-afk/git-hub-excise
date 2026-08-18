"""
webauto 모듈 검증 (실제 브라우저 사용)
=======================================
실행:  python tests/test_webauto.py

tests/pages/portal.html 을 사내 포털처럼 꾸며 두고,
라벨 옆 입력창 / 글자 버튼 / iframe 안 요소 / 요소 선택기까지 실제로 확인한다.
"""

import json
import sys

from conftest_driver import make_driver, MAIN_PAGE, Report, assert_eq  # noqa: E402

from jobscenario.webauto.actions import WebActions
from jobscenario.webauto.locator import Target, ElementNotFound
from jobscenario.webauto import picker

r = Report("webauto")
driver = make_driver()
web = WebActions(driver, log=lambda m: None)

try:
    web.open_url(MAIN_PAGE)
    print("페이지 로드:", driver.title)

    # 표 구조에서 라벨 옆 입력창 찾기
    r.check("label=사번 입력", lambda: (
        web.input_text("label=사번", "20250001"),
        driver.switch_to.default_content(),
        assert_eq(driver.find_element("id", "empno").get_attribute("value"), "20250001"),
    ))

    # placeholder 로 입력창 찾기
    r.check("placeholder 입력", lambda: (
        web.input_text("검색어 입력", "자동화"),
        assert_eq(driver.find_element("id", "q").get_attribute("value"), "자동화"),
    ))

    # 화면에 보이는 글자로 버튼 클릭
    r.check("text=조회 클릭", lambda: (
        web.click("text=조회"),
        assert_eq(driver.find_element("id", "res").text, "조회 완료"),
    ))

    # 콤보박스 선택
    r.check("select 선택", lambda: (
        web.select_option("id=dept", "영업부"),
        assert_eq(driver.find_element("id", "dept").get_attribute("value"), "d2"),
    ))

    # iframe 안의 버튼을 프레임 정보 없이 자동 탐색
    r.check("iframe 자동 탐색 클릭", lambda: (
        driver.switch_to.default_content(),
        web.click("text=승인"),
        assert_eq(driver.find_element("id", "st").text, "승인됨"),
    ))

    # iframe 안 라벨 옆 입력창
    r.check("iframe 안 label 입력", lambda: (
        driver.switch_to.default_content(),
        web.input_text("label=메모", "확인함"),
        assert_eq(driver.find_element("id", "memo").get_attribute("value"), "확인함"),
    ))

    r.check("wait_text", lambda: (
        driver.switch_to.default_content(),
        web.wait_text("사내 포털", 5),
    ))

    picked = {}

    def picker_flow():
        """사용자가 마우스로 클릭한 것과 같은 이벤트를 만들어 선택자 수집을 확인한다."""
        driver.switch_to.default_content()
        driver.execute_script(picker.PICKER_JS)
        driver.execute_script(
            "document.querySelector('[data-testid=save-btn]')"
            ".dispatchEvent(new MouseEvent('click',{bubbles:true}));")
        raw = driver.execute_script(picker.READ_JS)
        assert raw, "요소 선택기가 클릭을 잡지 못했습니다"
        info = json.loads(raw)
        assert_eq(info["tag"], "button")
        assert_eq(info["text"], "저장하기")
        assert_eq(info["data"].get("data-testid"), "save-btn")
        target = picker.build_target(info, [])
        assert len(target.strategies) >= 3, target.strategies
        assert_eq(web.find(target, timeout=5).get_attribute("data-testid"), "save-btn")
        picked["target"] = target

    r.check("요소 선택기 수집 + 재탐색", picker_flow)

    # 저장했다가 불러와도 같은 요소를 찾아야 한다
    r.check("Target JSON 저장/복원", lambda: assert_eq(
        web.find(Target.from_dict(json.loads(json.dumps(picked["target"].to_dict()))),
                 timeout=5).text, "저장하기"))

    def picker_in_frame():
        """iframe 안에서 고른 요소는 프레임 경로까지 함께 복원되어야 한다."""
        driver.switch_to.default_content()
        driver.switch_to.frame(driver.find_elements("css selector", "iframe")[0])
        driver.execute_script(picker.PICKER_JS)
        driver.execute_script(
            "document.getElementById('approve')"
            ".dispatchEvent(new MouseEvent('click',{bubbles:true}));")
        target = picker.build_target(json.loads(driver.execute_script(picker.READ_JS)), [0])
        driver.switch_to.default_content()
        assert_eq(web.find(target, timeout=5).get_attribute("id"), "approve")

    r.check("iframe 안 요소 선택 + 프레임 경로 복원", picker_in_frame)

    def not_found():
        """없는 요소는 제한 시간 안에 사용자가 이해할 수 있는 오류를 내야 한다."""
        driver.switch_to.default_content()
        try:
            web.click("text=존재하지않는버튼XYZ", timeout=2)
        except ElementNotFound as e:
            assert "찾지 못했습니다" in str(e), str(e)
            return
        raise AssertionError("오류가 발생해야 하는데 성공했습니다")

    r.check("없는 요소 -> 친절한 오류", not_found)

    # --- Shadow DOM (최신 웹 컴포넌트) ---
    def shadow_pick():
        """Shadow DOM 안을 클릭하면 껍데기가 아니라 진짜 요소를 잡아야 한다."""
        driver.switch_to.default_content()
        driver.execute_script(picker.PICKER_JS)
        driver.execute_script(
            "var b = document.querySelector('my-widget').shadowRoot"
            ".getElementById('deepBtn');"
            "b.dispatchEvent(new MouseEvent('click',{bubbles:true,composed:true}));")
        info = json.loads(driver.execute_script(picker.READ_JS))
        assert_eq(info["tag"], "button")            # my-widget 이 아니라 button
        assert_eq(info["id"], "deepBtn")
        assert info.get("shadow"), "Shadow DOM 경로가 수집되지 않았습니다"
        picked["shadow"] = picker.build_target(info, [])

    r.check("Shadow DOM 안 요소 선택", shadow_pick)

    r.check("Shadow DOM 안 요소 클릭", lambda: (
        driver.switch_to.default_content(),
        web.click(picked["shadow"]),
        assert_eq(driver.execute_script(
            "return document.querySelector('my-widget').shadowRoot"
            ".getElementById('deepSt').textContent;"), "내부확인됨"),
    ))

    r.check("Shadow DOM 안 입력창에 입력", lambda: (
        driver.switch_to.default_content(),
        web.input_text("deep=#code", "A-1234"),
        assert_eq(driver.execute_script(
            "return document.querySelector('my-widget').shadowRoot"
            ".getElementById('code').value;"), "A-1234"),
    ))
finally:
    try:
        driver.quit()
    except Exception:
        pass

sys.exit(r.finish())
