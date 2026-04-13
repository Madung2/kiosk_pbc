"""
키오스크 시리얼·폴링·WS·입력 추적 공통 실행 루프.

CLI(`main.py`)와 GUI(`gui_main.py`)에서 공유합니다.
"""

from __future__ import annotations

import asyncio
import logging

from .config import config
from .device_controller import Controllerer
from .input_activity import InputActivityTracker
from .background_browser import shutdown_all_background_browsers
from .kiosk_background import run_polling_and_ws
from .kiosk_events import KioskMonitorHandlers
from .kiosk_ws import create_ws_bridge
from .light_scheduler import LightScheduler
from .serial_manager import SerialManager
from .status_monitor import StatusMonitor

logger = logging.getLogger("kiosk_runner")


async def run_kiosk(
    serial_port: str,
    serial_baudrate: int,
    *,
    stop_event: asyncio.Event | None = None,
    controller_ref: dict | None = None,
) -> None:
    """시리얼 연결 후 상태 폴링·선택적 WebSocket·입력 추적을 수행합니다.

    Args:
        serial_port: 시리얼 장치 경로 (예: COM3, /dev/ttyUSB0)
        serial_baudrate: 보드레이트
        stop_event: 설정 시 ``set()`` 될 때까지 루프를 유지하다가 정리 후 반환 (GUI용)
        controller_ref: ``{"controller": Controllerer}`` 형태로 참조를 채움 (GUI 제어용)
    """
    serial_mgr = SerialManager(port=serial_port, baudrate=serial_baudrate)

    if not serial_mgr.open():
        logger.error(f"시리얼 포트 연결 실패!")
        raise RuntimeError(f"시리얼 포트를 열 수 없습니다: {serial_port}")

    controller = Controllerer(serial_mgr)
    if controller_ref is not None:
        controller_ref["controller"] = controller

    monitor = StatusMonitor(serial_mgr)
    input_tracker = InputActivityTracker(enabled=config.input_monitor_enabled)
    light_scheduler = LightScheduler(controller)
    bridge = create_ws_bridge(
        controller, monitor, light_scheduler=light_scheduler
    )

    KioskMonitorHandlers(
        controller,
        monitor,
        input_tracker,
        ws_bridge=bridge,
        light_scheduler=light_scheduler,
    ).bind()
    try:
        input_tracker.start()
    except Exception:
        logger.exception(
            "입력 추적(pynput) 시작 실패 — INPUT_MONITOR_ENABLED를 끄거나 "
            "macOS 접근성에서 터미널/Python을 허용했는지 확인하세요."
        )
        raise

    try:
        await run_polling_and_ws(
            monitor,
            bridge,
            stop_event=stop_event,
            poll_interval=config.status_poll_interval,
            light_scheduler=light_scheduler,
            light_schedule_interval=60.0,
        )
    except asyncio.CancelledError:
        pass
    finally:
        logger.info(f"종료 중...")
        shutdown_all_background_browsers()
        monitor.stop_polling()
        input_tracker.stop()
        if bridge is not None:
            await bridge.disconnect()
        serial_mgr.close()
        if controller_ref is not None:
            controller_ref.clear()
        logger.info(f"프로그램 종료")
