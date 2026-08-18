"""
시나리오 저장 / 실행 엔진 검증 (실제 브라우저 사용)
====================================================
실행:  python tests/test_runner.py

전체 일괄 실행, 단계별 실행, 선택 단계 건너뛰기, 실패 시 중단,
변수 치환, JSON 저장·복원까지 실제 동작으로 확인한다.
"""

import json
import sys
import tempfile
from pathlib import Path

from conftest_driver import make_engine, test_engine, MAIN_PAGE, Report  # noqa: E402

from jobscenario.core import storage
from jobscenario.core.models import Scenario, Step, expand_vars, find_vars
from jobscenario.core.runner import Runner, Session, DONE, SKIPPED
from jobscenario.webauto import picker

r = Report("runner (%s 엔진)" % test_engine())

logs, states, notes = [], {}, []
session = Session(log=logs.append)
session.web = make_engine(log=logs.append)
web = session.web

try:
    # 사용자가 요소 선택기로 잡았을 때와 같은 형태의 대상을 만든다
    web.open_url(MAIN_PAGE)
    web.run_script(picker.PICKER_JS)
    web.run_script("document.getElementById('searchBtn')"
                   ".dispatchEvent(new MouseEvent('click',{bubbles:true}))")
    picked = picker.build_target(json.loads(web.run_script(picker.READ_JS)), [])

    scenario = Scenario(title="일일 실적 조회", engine=test_engine(), steps=[
        Step(name="포털 열기", action="open_url", value=MAIN_PAGE, wait_after=0),
        Step(name="사번 입력", action="input", target_text="label=사번",
             value="{{사번}}", wait_after=0),
        Step(name="부서 선택", action="select", target_text="id=dept",
             value="영업부", wait_after=0),
        Step(name="조회 클릭", action="click", target=picked.to_dict(),
             target_text="조회 버튼", wait_after=0),
        Step(name="결과 확인", action="wait_text", value="조회 완료", timeout=5,
             wait_after=0),
        Step(name="결과 저장", action="get_text", target_text="#res", value="결과",
             wait_after=0),
        Step(name="iframe 승인", action="click", target_text="text=승인", wait_after=0),
        Step(name="안내", action="message", value="완료: {{결과}}", wait_after=0),
        Step(name="없는 버튼(선택 단계)", action="click", target_text="text=없는버튼QQ",
             timeout=2, optional=True, wait_after=0),
        Step(name="사용 안 함 단계", action="click", target_text="text=조회",
             enabled=False, wait_after=0),
    ])

    r.expect("실행 전 물어볼 변수 추출", scenario.required_vars() == ["사번"],
             scenario.required_vars())

    # --- 전체 일괄 실행 ---
    session.variables["사번"] = "20250001"
    runner = Runner(scenario, session, log=logs.append,
                    on_step=lambda i, s, m="": states.__setitem__(i, s),
                    ask=lambda n: "x", notify=notes.append)
    ok = runner.run_all(0)

    r.expect("전체 일괄 실행 성공", ok, [l for l in logs if "✕" in l])
    r.expect("사번이 실제로 입력됨", web.get_text("id=empno") == "20250001")
    r.expect("부서 선택됨",   # return 을 붙인 형태는 두 엔진에서 모두 동작한다
             web.run_script("return document.getElementById('dept').value") == "d2")
    r.expect("선택기로 등록한 대상 클릭됨", web.get_text("#res") == "조회 완료")
    r.expect("값 읽어 변수에 저장", session.variables.get("결과") == "조회 완료",
             session.variables)
    r.expect("안내 메시지에 변수 치환", notes == ["완료: 조회 완료"], notes)
    r.expect("실패해도 계속(선택 단계)", states.get(8) == SKIPPED, states.get(8))
    r.expect("사용 안 함 단계 건너뜀", states.get(9) == SKIPPED, states.get(9))
    r.expect("모든 필수 단계 완료",
             all(states.get(i) == DONE for i in range(8)), states)

    # --- 단계별 실행 (브라우저가 유지되어 이어서 실행된다) ---
    web.run_script("document.getElementById('empno').value = ''")
    one = Runner(scenario, session, log=logs.append,
                 on_step=lambda i, s, m="": states.__setitem__(i, s), ask=lambda n: "x")
    r.expect("단계별 실행(2단계만)", one.run_one(1))
    r.expect("단계별 실행 결과가 화면에 반영됨",
             web.get_text("id=empno") == "20250001")

    # --- 필수 단계가 실패하면 즉시 중단 ---
    bad = Scenario(title="실패 확인", engine=test_engine(), steps=[
        Step(name="없는 버튼", action="click", target_text="text=없는버튼QQ",
             timeout=2, wait_after=0),
        Step(name="실행되면 안 되는 단계", action="message", value="hi", wait_after=0),
    ])
    bad_states = {}
    r2 = Runner(bad, session, log=logs.append,
                on_step=lambda i, s, m="": bad_states.__setitem__(i, s),
                notify=notes.append)
    r.expect("실패 시 뒤 단계까지 중단",
             r2.run_all(0) is False and 1 not in bad_states, bad_states)

    # --- 실패한 순간의 화면이 자동으로 남는가 ---
    saved = session.last_failure_dir
    r.expect("실패 기록 폴더 생성", saved is not None and Path(saved).is_dir(), saved)
    if saved:
        names = sorted(p.name for p in Path(saved).iterdir())
        r.expect("화면 캡처 저장", "화면.png" in names, names)
        r.expect("HTML 저장", "화면.html" in names, names)
        r.expect("정보 파일에 주소·단계 기록",
                 "없는 버튼" in (Path(saved) / "정보.txt").read_text(encoding="utf-8"))

    # --- 저장 / 불러오기 ---
    tmp = Path(tempfile.mkdtemp()) / "scn.json"
    path = storage.save(scenario, tmp)
    loaded = storage.load(path)
    r.expect("저장/불러오기 단계 수", len(loaded.steps) == len(scenario.steps))
    r.expect("저장/불러오기 제목", loaded.title == "일일 실적 조회")
    r.expect("저장/불러오기 선택자 보존",
             loaded.steps[3].target["strategies"] == picked.to_dict()["strategies"])
    r.expect("사람이 읽을 수 있는 JSON",
             "일일 실적 조회" in path.read_text(encoding="utf-8"))

    # --- 변수 치환 ---
    r.expect("내장 변수", expand_vars("{{오늘}}", {}) != "{{오늘}}")
    r.expect("사용자 변수", expand_vars("A{{x}}B", {"x": "1"}) == "A1B")
    r.expect("모르는 변수는 그대로 둔다", expand_vars("{{없음}}", {}) == "{{없음}}")
    r.expect("변수 이름 추출", find_vars("{{a}}/{{오늘}}/{{b}}") == ["a", "b"])
finally:
    try:
        session.close_browser()
    except Exception:
        pass

sys.exit(r.finish())
