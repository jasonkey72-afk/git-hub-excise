"""
시나리오 저장소
================
시나리오는 사람이 읽을 수 있는 JSON 파일 하나로 저장된다.
파일을 그대로 동료에게 보내주면 같은 자동화를 바로 쓸 수 있다.
저장 위치: %LOCALAPPDATA%\\jobScenario\\scenarios
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .models import Scenario
from ..webauto.driver import data_dir

_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')


def scenarios_dir() -> Path:
    d = data_dir() / "scenarios"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(title: str) -> str:
    name = _BAD_CHARS.sub("_", (title or "무제").strip())
    return name[:60] or "무제"


def path_for(title: str) -> Path:
    return scenarios_dir() / ("%s.json" % safe_name(title))


def save(scenario: Scenario, path: Path | str = None) -> Path:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not scenario.created:
        scenario.created = now
    scenario.modified = now

    target = Path(path) if path else path_for(scenario.title)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(scenario.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load(path: Path | str) -> Scenario:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Scenario.from_dict(data)


def list_scenarios() -> list:
    """[(제목, 단계수, 수정일시, 경로), ...] 를 최근 수정 순으로 돌려준다."""
    items = []
    for p in scenarios_dir().glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            items.append((d.get("title") or p.stem,
                          len(d.get("steps", [])),
                          d.get("modified", ""),
                          p))
        except (OSError, ValueError):
            continue
    items.sort(key=lambda x: x[2], reverse=True)
    return items


def delete(path: Path | str) -> bool:
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False
