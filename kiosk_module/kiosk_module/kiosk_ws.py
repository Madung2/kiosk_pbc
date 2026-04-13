"""
WebSocket 브릿지 생성 및 제어(type=control) · 조명 스케줄(type=light_time_control) 처리.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from pydantic import ValidationError

from .config import config
from .device_controller import Controllerer, PcbControlInput
from .status_monitor import StatusMonitor
from .ws_bridge import WSBridge

if TYPE_CHECKING:
    from .light_scheduler import LightScheduler

logger = logging.getLogger(__name__)

_WS_CONTROL_KEYS = frozenset(PcbControlInput.model_fields)


def handle_ws_message(
    controller: Controllerer,
    light_scheduler: "LightScheduler | None",
    data: object,
) -> None:
    """WebSocket JSON을 ``type`` 에 따라 분기 처리합니다.

    - ``control``: PCB 즉시 제어 (기존 동작).
    - ``light_time_control``: 조명 스케줄 시각 갱신 후 즉시 ``check_and_control`` 예약.
    """
    logger.info(f"[WS 수신] {data}")
    if not isinstance(data, dict):
        return

    msg_type = data.get("type")

    if msg_type == "light_time_control":
        if light_scheduler is None:
            logger.warning(f"[WS] light_time_control 수신했으나 LightScheduler가 없습니다.")
            return
        start = data.get("start")
        end = data.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            logger.warning(
                f"[WS] light_time_control: start/end 는 문자열(HH:MM)이어야 합니다: {start} / {end}"
            )
            return
        raw_scope = data.get("scope", "both")
        if isinstance(raw_scope, str):
            scope_key = raw_scope.strip().lower()
        else:
            scope_key = "both"
        if scope_key in ("all", "both", ""):
            scope = "both"
        elif scope_key == "ac":
            scope = "ac"
        elif scope_key == "dc":
            scope = "dc"
        else:
            logger.warning(
                f"[WS] light_time_control: scope는 ac, dc, both(또는 all) 중 하나여야 합니다: {raw_scope!r}"
            )
            return
        if not light_scheduler.try_update_schedule(start, end, scope=scope):
            return
        light_scheduler.schedule_check_and_control()
        return

    if msg_type != "control":
        return

    payload = {k: v for k, v in data.items() if k in _WS_CONTROL_KEYS}
    if not payload:
        logger.warning(f"[WS] type=control 이지만 제어 필드가 없습니다.")
        return
    try:
        control = PcbControlInput.model_validate(payload)
    except ValidationError as e:
        logger.error(f"[WS] 제어 메시지 검증 실패: {e}")
        return
    controller.send_control(control)


def create_ws_bridge(
    controller: Controllerer,
    monitor: StatusMonitor,
    *,
    light_scheduler: "LightScheduler | None" = None,
) -> WSBridge | None:
    """설정에 따라 ``WSBridge``를 만들고 메시지 핸들러를 연결합니다."""
    if not config.ws_enabled:
        logger.info(
            f"WebSocket 비활성화(WS_ENABLED 미설정 또는 false). "
            f"시리얼·상태 폴링만 동작합니다."
        )
        return None

    bridge = WSBridge(
        ws_url=config.ws_url,
        controller=controller,
        monitor=monitor,
        reconnect_interval=config.ws_reconnect_interval,
    )
    bridge.on_message = partial(handle_ws_message, controller, light_scheduler)
    return bridge
