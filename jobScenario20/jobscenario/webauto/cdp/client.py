"""
CDP(Chrome DevTools Protocol) 클라이언트
=========================================
브라우저와 WebSocket 으로 직접 대화한다. **드라이버(msedgedriver.exe)가 필요 없다.**

드라이버를 쓰지 않는 이유
  chromedriver/msedgedriver 는 브라우저와 주 버전이 정확히 같아야 실행을 거부한다.
  Edge 는 약 4주마다 자동 업데이트되므로, 드라이버 방식은 배포 후에도
  담당자가 계속 드라이버를 다시 배포해야 한다.
  CDP 는 브라우저에 원래 들어 있는 기능이고 버전 검사를 하지 않으므로
  이 문제 자체가 사라진다.

이 클라이언트는 잠금(RLock)으로 요청을 직렬화하므로 **여러 스레드에서 함께 써도 안전하다.**
(실행 스레드 / 요소 선택기 스레드 / UI 스레드가 같은 브라우저를 건드리는 구조에 필요하다)
"""

from __future__ import annotations

import json
import threading
import time

import websocket

# 사내 프록시가 설정돼 있어도 브라우저(localhost)로 가는 연결은 우회해야 한다
NO_PROXY_HOSTS = ["127.0.0.1", "localhost", "::1"]


class CDPError(Exception):
    """브라우저가 오류를 돌려주었거나 응답이 없을 때."""


class CDPClosed(CDPError):
    """브라우저(또는 탭)가 닫혀 연결이 끊겼을 때."""


class CDPClient:
    """
    브라우저 하나에 대한 연결.
    여러 탭(target)은 sessionId 로 구분하며, 연결은 하나만 쓴다(flatten 모드).
    """

    def __init__(self, ws_url: str, timeout: float = 30.0, on_event=None):
        self.ws_url = ws_url
        self.timeout = timeout
        self.on_event = on_event          # 이벤트 1건을 받는 함수
        self._lock = threading.RLock()
        self._next_id = 0
        self._results = {}
        self._closed = False
        try:
            self.ws = websocket.create_connection(
                ws_url, timeout=timeout, suppress_origin=True,
                http_no_proxy=NO_PROXY_HOSTS, max_size=None,
                enable_multithread=True,
            )
        except Exception as e:
            raise CDPError("브라우저에 연결하지 못했습니다: %s" % e)

    # -- 요청 / 응답 -------------------------------------------------------

    def send(self, method: str, params: dict = None, session_id: str = None,
             timeout: float = None) -> dict:
        """CDP 명령 하나를 보내고 결과(result)를 돌려준다."""
        if self._closed:
            raise CDPClosed("브라우저 연결이 이미 닫혔습니다.")
        timeout = self.timeout if timeout is None else timeout

        with self._lock:
            self._next_id += 1
            msg_id = self._next_id
            payload = {"id": msg_id, "method": method}
            if params:
                payload["params"] = params
            if session_id:
                payload["sessionId"] = session_id
            try:
                self.ws.send(json.dumps(payload))
            except Exception as e:
                self._closed = True
                raise CDPClosed("명령을 보내지 못했습니다(브라우저가 닫혔을 수 있습니다): %s" % e)

            msg = self._wait_for(msg_id, timeout)

        if "error" in msg:
            err = msg["error"]
            raise CDPError("%s 실패: %s" % (method, err.get("message", err)))
        return msg.get("result", {})

    def pump(self, seconds: float = 0.0) -> None:
        """
        쌓여 있는 이벤트를 읽어 처리한다.
        (프레임 생성/삭제, 알림창 같은 상태를 최신으로 맞출 때 사용)
        """
        if self._closed:
            return
        deadline = time.time() + max(seconds, 0.0)
        with self._lock:
            while True:
                try:
                    self.ws.settimeout(0.02 if seconds <= 0 else 0.1)
                    raw = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.time() >= deadline:
                        return
                    continue
                except Exception:
                    self._closed = True
                    return
                self._dispatch(raw)
                if time.time() >= deadline:
                    return

    def close(self) -> None:
        self._closed = True
        try:
            self.ws.close()
        except Exception:
            pass

    @property
    def closed(self) -> bool:
        return self._closed

    # -- 내부 -------------------------------------------------------------

    def _wait_for(self, msg_id: int, timeout: float) -> dict:
        """내 응답이 올 때까지 읽는다. 그 사이 도착한 이벤트는 처리해 둔다."""
        deadline = time.time() + timeout
        while True:
            if msg_id in self._results:
                return self._results.pop(msg_id)
            remaining = deadline - time.time()
            if remaining <= 0:
                raise CDPError("브라우저가 %.0f초 안에 응답하지 않았습니다." % timeout)
            try:
                self.ws.settimeout(min(remaining, 1.0))
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                self._closed = True
                raise CDPClosed("브라우저 연결이 끊겼습니다: %s" % e)
            self._dispatch(raw)

    def _dispatch(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if "id" in msg:
            self._results[msg["id"]] = msg
        elif self.on_event:
            try:
                self.on_event(msg)
            except Exception:
                pass          # 이벤트 처리 실패가 본 작업을 막으면 안 된다
