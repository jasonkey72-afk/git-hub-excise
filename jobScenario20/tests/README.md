# 테스트

실제 브라우저를 띄워 웹 자동화가 정말 동작하는지 확인합니다. (총 59건)

```bat
pip install -r ..\requirements.txt

:: 드라이버가 필요 없는 기본 엔진
python tests\test_cdp.py         :: 드라이버 없는 엔진 20건
python tests\test_runner.py      :: 시나리오 실행 / 저장 / 실패 기록 25건

:: 예비용 selenium 엔진 (드라이버 필요)
set JOBSCN_TEST_ENGINE=selenium
python tests\test_webauto.py     :: selenium 엔진 14건
python tests\test_runner.py      :: 같은 검사를 selenium 으로 25건
```

`test_runner.py` 는 **두 엔진에서 같은 검사를 돌립니다.** 엔진을 바꿔도 결과가
같아야 하므로, 새 기능을 넣을 때 두 엔진의 동작 차이를 잡아내는 안전망이 됩니다.

기본값은 사내 환경과 같은 **Edge** 입니다. 아래 환경변수로 바꿀 수 있습니다.

| 환경변수 | 설명 |
|---|---|
| `JOBSCN_TEST_ENGINE` | `cdp`(기본, 드라이버 없음) / `selenium` |
| `JOBSCN_TEST_BROWSER` | `edge`(기본) / `chrome` |
| `JOBSCN_TEST_HEADLESS` | `1` 이면 화면 없이 실행 |
| `JOBSCN_BROWSER_PATH` | 브라우저 실행 파일 경로(자동 탐지가 안 될 때만, cdp 엔진) |
| `JOBSCN_TEST_PORT` | 이미 디버깅 포트로 열린 브라우저에 연결(cdp 엔진) |
| `JOBSCN_TEST_ATTACH` | `127.0.0.1:9222` — 이미 실행 중인 브라우저에 연결(selenium 엔진) |
| `JOBSCN_TEST_DRIVER` | 드라이버 실행 파일 경로(selenium 엔진) |

`tests/pages/portal.html` 이 사내 포털을 흉내 낸 시험용 화면입니다.
표 안의 입력창, 글자만 있는 버튼, iframe 안의 결재 화면처럼
**실제로 자동화가 자주 실패하는 구조**를 일부러 담아 두었습니다.
Shadow DOM 을 쓰는 웹 컴포넌트(`<my-widget>`)도 포함되어 있습니다.

새 기능을 추가하면 검증을 함께 넣어 주세요.
엔진 양쪽에 영향이 있으면 `test_runner.py` 에 넣어 두 엔진에서 모두 확인되게 하세요.
