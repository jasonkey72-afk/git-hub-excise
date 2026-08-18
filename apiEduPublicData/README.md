# 공공데이터 API 실습 (Vibe Coding 3일차 오전)

프로그래밍 초보자가 실제 API를 호출해서 화면에 결과를 띄워보는 첫 실습입니다.
Streamlit을 사용해 HTML/CSS/JS 없이 Python 코드만으로 웹 UI를 만듭니다.

## 실습 내용

1. **공휴일 조회** — 공공데이터포털의 "특일 정보" API로 원하는 연/월의 공휴일 목록 조회
2. **오늘의 환율** — 한국수출입은행 Open API로 날짜별 통화 환율 조회

두 API 모두 파라미터가 단순해서(연/월, 날짜) 초보자가 요청/응답 구조를 이해하기 쉽습니다.

## 1. API 키 발급받기

### 공휴일 조회용 키
1. https://www.data.go.kr 회원가입 후 로그인
2. "특일 정보" 검색 → **한국천문연구원_특일 정보** 활용신청
3. 승인 후 마이페이지 > 개발계정에서 **일반 인증키(Decoding)** 값 복사
   - ⚠️ Encoding 키가 아니라 **Decoding** 키를 써야 합니다. (Encoding 키를 쓰면 이중 인코딩되어 인증 오류가 납니다)

### 환율 조회용 키
1. https://www.koreaexim.go.kr/ir/HPHKIR019M01 (수출입은행 Open API) 접속해 신청
2. 승인 후 발급된 인증키 복사 (별도 인코딩 처리 필요 없음)

## 2. 설치 및 실행

```bash
cd apiEduPublicData
pip install -r requirements.txt
streamlit run app.py
```

브라우저가 자동으로 열리며 `http://localhost:8501` 에서 앱을 확인할 수 있습니다.

## 3. 키 입력 방법 (둘 중 하나)

- **수업 중 빠른 방법**: 실행된 화면 왼쪽 사이드바에 키를 직접 붙여넣기
- **매번 입력이 귀찮다면**: `.streamlit/secrets.toml.example` 을 `.streamlit/secrets.toml` 로 복사하고 키를 채워넣으면 실행 시 자동으로 입력된 상태로 시작합니다 (이 파일은 git에 올리지 않기)

## 강의 진행 팁

- 먼저 API 키 없이 앱을 실행해서 "키를 입력하세요" 에러 메시지부터 보여주면, 인증이 왜 필요한지 자연스럽게 설명할 수 있습니다.
- `requests.get(url, params=params)` 한 줄이 실제로 무엇을 하는지 (요청 URL 조립 → 서버 응답 → JSON 파싱) 브라우저 개발자도구 Network 탭과 함께 보여주면 이해가 빠릅니다.
- 환율 API는 주말/공휴일에 데이터가 없다는 점(`result != 1`)을 실제로 확인시켜, API가 "항상 성공하지 않는다"는 걸 체감하게 하면 좋습니다.
