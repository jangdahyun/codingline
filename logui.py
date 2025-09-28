# codingline/logui.py
# -*- coding: utf-8 -*-
"""
logger 기반 '로고(박스) + 단계 로그' 유틸
- print 사용 X, logging.Logger 로만 출력
- 위치정보(파일명:라인 Class.func()) 자동 첨부
- 어느 파일/클래스에서 호출했는지 로그로 바로 파악 가능
"""

from __future__ import annotations
import logging
import os
import inspect
import json
from typing import Any, Optional, Dict

# 프로세스 내 '한 번만 배너'를 위한 키-플래그 저장소
_banner_once_flags: Dict[str, bool] = {}

def _loc(self_obj: object | None, depth: int = 2) -> str:
    """
    호출 위치를 '파일명:라인 Class.func()' 형식으로 반환.
    depth=2 → _loc ← 호출함수(배너/스텝) ← 호출자(사용자 코드)
    """
    frame = inspect.stack()[depth]
    fname = os.path.basename(frame.filename)
    line = frame.lineno
    func = frame.function
    cls = type(self_obj).__name__ if self_obj is not None else None
    sig = f"{cls}.{func}" if cls else func
    return f"{fname}:{line} {sig}()"

def log_banner(logger: logging.Logger,
               title: str,
               subtitle: Optional[str] = None,
               *,
               level: int = logging.INFO,
               self_obj: object | None = None) -> None:
    """
    3줄 박스 배너를 logger 로 남깁니다. (위치정보 포함)
    """
    t = title if not subtitle else f"{title} — {subtitle}"
    where = _loc(self_obj, depth=2)
    text = f"{t}  @ {where}"
    width = max(30, len(text) + 6)
    line = "─" * width
    logger.log(level, "┌%s┐", line)
    pad = (width - len(text)) // 2
    logger.log(level, "│%s%s%s│", " " * pad, text, " " * (width - len(text) - pad))
    logger.log(level, "└%s┘", line)

def log_banner_once(logger: logging.Logger,
                    key: str,
                    title: str,
                    subtitle: Optional[str] = None,
                    *,
                    level: int = logging.INFO,
                    self_obj: object | None = None) -> None:
    """
    같은 key 에 대해 프로세스 당 한 번만 배너를 찍습니다.
    - 예: 서버 시작/최초 WS 연결 시 1회
    """
    if _banner_once_flags.get(key):
        return
    _banner_once_flags[key] = True
    log_banner(logger, title, subtitle, level=level, self_obj=self_obj)

def log_step(logger: logging.Logger,
             status: str,
             detail: Optional[str] = None,
             data: Any = None,
             *,
             level: int = logging.INFO,
             self_obj: object | None = None) -> None:
    """
    단계/데이터를 한 줄로 남깁니다. (위치정보 포함)
    """
    where = _loc(self_obj, depth=2)
    head = f"[단계] {status}" + (f" ({detail})" if detail else "") + f"  @ {where}"
    if data is None:
        logger.log(level, head)
        return
    try:
        if isinstance(data, (dict, list)):
            s = json.dumps(data, ensure_ascii=False)
        else:
            s = str(data)
    except Exception as e:
        s = f"<<표시 오류:{e}>>"
    logger.log(level, "%s  └ 데이터: %s", head, s)
