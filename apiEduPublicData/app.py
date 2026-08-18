"""
공공데이터 API 실습 - Vibe Coding 3일차
초보자를 위한 2가지 API 실습:
  1. 공휴일 조회 (공공데이터포털 - 한국천문연구원 특일 정보)
  2. 오늘의 환율 (한국수출입은행 Open API)

실행 방법:
  pip install -r requirements.txt
  streamlit run app.py
"""

import xml.etree.ElementTree as ET
from datetime import date

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="공공데이터 API 실습", page_icon="🇰🇷", layout="centered")


def get_secret(key: str) -> str:
    # secrets.toml에 키가 없어도 앱이 죽지 않도록 방어
    try:
        return st.secrets[key]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 사이드바: API 키 입력
# 공공데이터포털은 "일반 인증키(Decoding)"을 넣어야 함
# → requests가 params로 넘길 때 자동으로 URL 인코딩을 해주기 때문
# (인코딩된 키를 그대로 넣으면 이중 인코딩되어 인증 실패가 남)
# ---------------------------------------------------------------------------
st.sidebar.header("🔑 API 키 입력")
data_go_kr_key = st.sidebar.text_input(
    "공공데이터포털 서비스키 (Decoding)",
    value=get_secret("DATA_GO_KR_KEY"),
    type="password",
    help="data.go.kr 마이페이지 > 개발계정 > 일반 인증키(Decoding) 값을 붙여넣으세요.",
)
exim_key = st.sidebar.text_input(
    "한국수출입은행 인증키",
    value=get_secret("EXIM_AUTH_KEY"),
    type="password",
    help="oapi.koreaexim.go.kr 에서 발급받은 인증키를 붙여넣으세요.",
)

st.sidebar.markdown("---")
st.sidebar.caption(
    "키는 저장되지 않고 이 브라우저 세션에서만 사용됩니다.\n\n"
    "매번 입력하기 번거로우면 `.streamlit/secrets.toml` 파일에 넣어두세요."
)

st.title("🇰🇷 공공데이터 API 실습")
st.caption("Vibe Coding 3일차 오전 - 대한민국 공공데이터로 배우는 첫 API 실습")

tab1, tab2 = st.tabs(["📅 공휴일 조회", "💱 오늘의 환율"])


# ---------------------------------------------------------------------------
# 탭 1. 공휴일 조회
# ---------------------------------------------------------------------------
with tab1:
    st.subheader("이 달의 공휴일 조회")
    st.write("연도와 월을 고르면 해당 월의 공휴일 목록을 API로 가져옵니다.")

    col1, col2 = st.columns(2)
    with col1:
        year = st.number_input("연도", min_value=2000, max_value=2100, value=date.today().year, step=1)
    with col2:
        month = st.number_input("월", min_value=1, max_value=12, value=date.today().month, step=1)

    if st.button("공휴일 조회하기", type="primary"):
        if not data_go_kr_key:
            st.error("왼쪽 사이드바에 공공데이터포털 서비스키를 먼저 입력하세요.")
        else:
            url = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeSeInfo"
            params = {
                "serviceKey": data_go_kr_key,
                "solYear": str(int(year)),
                "solMonth": f"{int(month):02d}",
                "numOfRows": 50,
                "_type": "json",
            }

            with st.spinner("공공데이터포털에 요청 중..."):
                res = requests.get(url, params=params, timeout=10)

            # 일부 구형 서비스는 _type=json을 무시하고 XML을 그대로 주기도 함
            # → JSON 파싱을 시도하고, 실패하면 XML로 다시 파싱 (실제 API 연동에서 흔한 상황)
            items = []
            try:
                body = res.json()["response"]["body"]
                total_count = body.get("totalCount", 0)
                if total_count and int(total_count) > 0:
                    raw_items = body["items"]["item"]
                    items = raw_items if isinstance(raw_items, list) else [raw_items]
            except (ValueError, KeyError):
                root = ET.fromstring(res.text)
                result_code = root.findtext(".//resultCode")
                if result_code not in (None, "00", "0"):
                    st.error(f"API 오류: {root.findtext('.//resultMsg')}")
                for item in root.findall(".//item"):
                    items.append({child.tag: child.text for child in item})

            if items:
                df = pd.DataFrame(items)[["locdate", "dateName", "isHoliday"]]
                df.columns = ["날짜", "공휴일명", "공식휴일 여부"]
                df["날짜"] = pd.to_datetime(df["날짜"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
                st.success(f"{int(year)}년 {int(month)}월 공휴일 {len(df)}건")
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("해당 월에는 공휴일이 없습니다.")


# ---------------------------------------------------------------------------
# 탭 2. 오늘의 환율
# ---------------------------------------------------------------------------
with tab2:
    st.subheader("환율 조회")
    st.write("조회일을 고르면 통화별 매매기준율을 API로 가져옵니다. (주말·공휴일은 데이터가 없습니다)")

    search_date = st.date_input("조회 날짜", value=date.today())

    if st.button("환율 조회하기", type="primary"):
        if not exim_key:
            st.error("왼쪽 사이드바에 수출입은행 인증키를 먼저 입력하세요.")
        else:
            url = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
            params = {
                "authkey": exim_key,
                "searchdate": search_date.strftime("%Y%m%d"),
                "data": "AP01",
            }

            with st.spinner("수출입은행에 요청 중..."):
                res = requests.get(url, params=params, timeout=10)

            data = res.json()

            if not data:
                st.warning("해당 날짜는 영업일이 아니거나 데이터가 없습니다. 평일 날짜로 다시 시도해보세요.")
            elif data[0].get("result") != 1:
                st.error("인증키를 확인해주세요. (result 코드가 1이 아닙니다)")
            else:
                df = pd.DataFrame(data)
                df = df[["cur_unit", "cur_nm", "ttb", "tts", "deal_bas_r"]]
                df.columns = ["통화코드", "국가/통화", "살 때(TTB)", "팔 때(TTS)", "매매기준율"]

                # 콤마가 섞인 문자열(예: "1,320.50")을 숫자로 변환
                for col in ["살 때(TTB)", "팔 때(TTS)", "매매기준율"]:
                    df[col] = df[col].str.replace(",", "").astype(float)

                st.success(f"{search_date} 환율 {len(df)}건")
                st.dataframe(df, use_container_width=True, hide_index=True)

                major = df[df["통화코드"].isin(["USD", "JPY(100)", "EUR", "CNH"])]
                if not major.empty:
                    st.bar_chart(major.set_index("통화코드")["매매기준율"])
