"""
jobScenario - 사내 반복 업무 자동화
====================================
웹사이트 접속 + 사내 프로그램 실행을 '단계'로 등록해 두고
전체 일괄 실행 또는 단계별 실행으로 재현한다.

구성
  jobscenario/webauto : 웹 자동화 공용 모듈(다른 프로젝트에 그대로 재사용)
  jobscenario/core    : 시나리오 모델 / 저장 / 실행 엔진
  jobscenario/ui      : 모던 tkinter 화면
"""

__version__ = "1.2.0"
