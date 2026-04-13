"""
StatusMonitor·입력 추적에 연결되는 키오스크 비즈니스 이벤트 핸들러.
"""

from __future__ import annotations

import json
import logging
import time

from .background_browser import (
    launch_background_browser,
    shutdown_background_browser,
)
from .config import config
from .device_controller import Controllerer
from .input_activity import InputActivityTracker
from .protocol import ButtonPressEvent, StatusResponse
from .light_scheduler import LightScheduler
from .person_detected_audio import play_person_detected_audio_async
from .status_monitor import StatusMonitor
from .ws_bridge import WSBridge

logger = logging.getLogger(__name__)

_INPUT_LOG_THROTTLE_SEC = 2.0

SESSION_MEET_WEB = "meet_web"

_PERSON_DETECTED_EVENT = "PERSON_DETECTED"


def person_detected_ws_payload() -> dict[str, object]:
    """사람 최초 재실 시 백엔드로 보내는 WebSocket JSON 본문."""
    return {
        "event": _PERSON_DETECTED_EVENT,
        "kiosk_id": config.kiosk_id,
    }


class KioskMonitorHandlers:
    """폴링 상태·사람 감지·버튼·입력 유휴에 따른 제어 로직."""

    def __init__(
        self,
        controller: Controllerer,
        monitor: StatusMonitor,
        input_tracker: InputActivityTracker,
        ws_bridge: WSBridge | None = None,
        light_scheduler: LightScheduler | None = None,
    ) -> None:
        self._controller = controller
        self._monitor = monitor
        self._input_tracker = input_tracker
        self._ws_bridge = ws_bridge
        self._light_scheduler = light_scheduler
        self._vacancy_idle_closed = False
        self._input_log_at = 0.0
        # 사람이 없었다가 다시 들어올 때만 환영(스피커·WS) 1회
        self._person_welcome_done_for_presence = False

    def bind(self) -> None:
        self._input_tracker.on_activity = self._on_input_activity
        self._monitor.on_status_received = self.on_status_received
        self._monitor.on_status_changed = self.on_status_changed
        self._monitor.on_person_detected = self.on_person_detected
        self._monitor.on_button_pressed = self.on_button_pressed

    def _on_input_activity(self) -> None:
        now = time.monotonic()
        if now - self._input_log_at < _INPUT_LOG_THROTTLE_SEC:
            return
        self._input_log_at = now
        logger.info(f"[이벤트] 키보드/마우스 입력 감지")

    def on_status_received(self, status: StatusResponse) -> None:
        self._controller.apply_pcb_status(status)

        if status.person_detected:
            self._vacancy_idle_closed = False
            self._welcome_person_once_per_presence()
        else:
            self._person_welcome_done_for_presence = False
            self._shutdown_meet_web_browser_on_absence()

        if not status.person_detected:
            self._maybe_close_door_on_vacancy_idle()

    def on_status_changed(self, _status: StatusResponse) -> None:
        logger.info(f"[이벤트] 상태 변화: {self._monitor.to_dict()}")

    def on_person_detected(self, detected: bool) -> None:
        logger.info(
            f"[이벤트] 사람 감지: {'감지됨' if detected else '없음'}"
        )
        if detected and self._light_scheduler is not None:
            self._light_scheduler.schedule_check_and_control()

    def on_button_pressed(self, event: ButtonPressEvent) -> None:
        """PCB 버튼 0→눌림 엣진; 좌·우 로그는 분리하고 조합별 동작은 라우터가 맡김."""
        if event.left_just_pressed:
            self._on_left_button_clicked()
        if event.right_just_pressed:
            self._on_right_button_clicked()
        self._route_button_press_actions(event.left_pressed, event.right_pressed)

    def _on_left_button_clicked(self) -> None:
        logger.info(f"[이벤트] 왼쪽 버튼 클릭됨")

    def _on_right_button_clicked(self) -> None:
        logger.info(f"[이벤트] 오른쪽 버튼 클릭됨")

    def _route_button_press_actions(self, left: bool, right: bool) -> None:
        if left and right:
            self._close_door_on_both_buttons()
        elif left:
            self._open_door_on_left_only()
        elif right:
            self._open_guidance_center_on_right_only()

    ###############################################
    ######           실제 기능             ##########
    ###############################################

    def _welcome_person_once_per_presence(self) -> None:
        """재실 구간당 1회: 스피커 ON + WebSocket ``PERSON_DETECTED``.

        첫 폴링부터 ``on_status_received``에서 호출됩니다.
        ``config.auto_open_door_on_person``이 꺼져 있으면 아무 것도 하지 않습니다.
        """
        if not config.auto_open_door_on_person:
            return
        if self._person_welcome_done_for_presence:
            return
        self._person_welcome_done_for_presence = True
        self._controller.set_speaker(True)
        play_person_detected_audio_async()
        if self._ws_bridge is not None:
            ws_body = person_detected_ws_payload()
            self._ws_bridge.schedule_send(ws_body)
            logger.info(
                f"[동작] 사람 감지 구간 시작 → 음향 ON, WS "
                f"{json.dumps(ws_body, ensure_ascii=False)}"
            )
        else:
            logger.info(
                f"[동작] 사람 감지 구간 시작 → 음향 ON (WebSocket 비활성화로 이벤트 미전송)"
            )

    def _shutdown_meet_web_browser_on_absence(self) -> None:
        if shutdown_background_browser(SESSION_MEET_WEB):
            logger.info(f"[동작] 사람 없음 → 백그라운드 브라우저 종료")

    def _maybe_close_door_on_vacancy_idle(self) -> None:
        idle = self._input_tracker.seconds_since_activity()
        if idle < config.vacant_idle_close_seconds or self._vacancy_idle_closed:
            return
        self._controller.close_door()
        self._controller.set_speaker(False)
        self._vacancy_idle_closed = True
        logger.info(
            f"사람 없음 + 입력 유휴 {idle:.1f}s 이상 → 도어 닫기 & 음향 중지"
        )

    def _open_door_on_left_only(self) -> None:
        self._controller.open_door()
        logger.info(f"[동작] 왼쪽만 눌림 → 도어 오픈")

    def _open_guidance_center_on_right_only(self) -> None:
        launch_background_browser(
            config.meet_web_url,
            session_key=SESSION_MEET_WEB,
            timeout_sec=config.background_browser_timeout_seconds,
            browser_cmd_template=config.kiosk_browser_cmd,
        )
        logger.info(f"[동작] 오른쪽만 눌림 → Meet/웹 URL(설정 시)")

    def _close_door_on_both_buttons(self) -> None:
        self._controller.close_door()
        logger.info(f"[동작] 양쪽 동시 눌림 → 도어 클로즈")
