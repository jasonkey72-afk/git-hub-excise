"""
webauto - 재사용 가능한 웹 자동화 모듈
=======================================
jobScenario 뿐 아니라 다른 웹 자동화 프로젝트에서도 이 폴더만 복사하면
그대로 사용할 수 있도록 만들었다. (의존성: selenium)

    from jobscenario.webauto import create_driver, WebActions, pick_element

    driver = create_driver("edge")
    web = WebActions(driver)
    web.open_url("https://portal.company.com")
    web.input_text("label=사번", "20250001")
    web.click("text=조회")
"""

from .driver import create_driver, quit_driver, data_dir, app_dir, BROWSERS
from .locator import (SmartLocator, Target, Strategy, parse_target, ElementNotFound)
from .actions import WebActions
from .picker import pick_element, stop_picker, flash, PickCancelled

__all__ = [
    "create_driver", "quit_driver", "data_dir", "app_dir", "BROWSERS",
    "SmartLocator", "Target", "Strategy", "parse_target", "ElementNotFound",
    "WebActions", "pick_element", "stop_picker", "flash", "PickCancelled",
]
