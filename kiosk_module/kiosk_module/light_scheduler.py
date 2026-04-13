"""
시간대별 조명 자동 스케줄러.

로컬 시각 기준으로 시작·종료 시각(HH:MM) 사이에만 조명을 켜고, 그 밖에서는 끕니다.
실제 PCB 전송은 ``Controllerer.send_control`` → ``SerialManager.send`` 경로로 이루어집니다.

타임존은 OS 로컬 시각(``datetime.now()``)을 사용합니다. 필요하면 이후 ``zoneinfo``로
명시적 타임존을 도입할 수 있습니다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from .device_controller import Controllerer, PcbControlInput
from .protocol import LightMode

logger = logging.getLogger(__name__)

# DC 밝기: ``Controllerer.all_on`` 과 동일하게 프로토콜 상한(0~10) 사용
_SCHEDULE_ON_DC_BRIGHTNESS = 10

# "HH:MM" 형식 (선행 0 허용)
_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


class LightScheduler:
    """시작·종료 시각에 따라 조명 ON/OFF를 ``Controllerer``로 전송하는 스케줄러."""

    def __init__(
        self,
        controller: Controllerer,
        *,
        start_time: str = "06:00",
        end_time: str = "00:00",
    ) -> None:
        """
        Args:
            controller: PCB 제어기 (내부에서 ``SerialManager.send``까지 호출됨).
            start_time: 구간 시작 "HH:MM".
            end_time: 구간 종료 "HH:MM". ``00:00``은 아래 규칙으로 해석될 수 있음.
        """
        self._controller = controller
        self._start_time = start_time
        self._end_time = end_time
        # 직전 스케줄 판정(중복 프레임 전송 완화)
        self._last_scheduled_lit: bool | None = None

    @property
    def start_time(self) -> str:
        return self._start_time

    @start_time.setter
    def start_time(self, value: str) -> None:
        self._start_time = value

    @property
    def end_time(self) -> str:
        return self._end_time

    @end_time.setter
    def end_time(self, value: str) -> None:
        self._end_time = value

    @staticmethod
    def _parse_hhmm(s: str) -> tuple[int, int] | None:
        """문자열을 (시, 분)으로 파싱. 실패 시 ``None``."""
        if not isinstance(s, str):
            return None
        m = _HHMM_RE.match(s)
        if not m:
            return None
        h, mn = int(m.group(1)), int(m.group(2))
        if not (0 <= h <= 23 and 0 <= mn <= 59):
            return None
        return h, mn

    @staticmethod
    def _to_minutes(h: int, m: int) -> int:
        return h * 60 + m

    @staticmethod
    def _normalize_end_minutes(end_min: int, start_min: int) -> int:
        """종료 시각 분 단위 보정.

        ``end``가 자정 ``00:00``이고 시작이 그날 0시가 아니면, 흔히 "당일 자정까지"
        를 의미하므로 **24:00(1440분)** 으로 해석합니다.

        예: 06:00 ~ 00:00 → 당일 06:00 이상 자정 미만 ON.
        반면 22:00 ~ 02:00는 종료가 02:00이므로 이 특례가 적용되지 않고 야간 구간으로 처리됩니다.
        """
        if end_min == 0 and start_min > 0:
            return 24 * 60
        return end_min

    def _is_within_schedule(self, now_min: int, start_min: int, end_min: int) -> bool:
        """현재 시각(분)이 [시작, 종료) 의미의 활성 구간에 들어가는지 판정.

        - **같은 날 구간** (보정 후 ``start < end``): ``start <= now < end``.
        - **자정 넘김** (``start > end``): ``now >= start`` 이거나 ``now < end``
          (예: 22:00~02:00 → 22:00 이후 또는 02:00 이전).
        - **시작==종료** (0길이): OFF로 취급.
        """
        end_adj = self._normalize_end_minutes(end_min, start_min)

        if start_min < end_adj:
            return start_min <= now_min < end_adj
        if start_min > end_adj:
            return now_min >= start_min or now_min < end_adj
        return False

    def try_update_schedule(self, start: str, end: str) -> bool:
        """WebSocket 등에서 받은 문자열로 스케줄을 갱신. 파싱 실패 시 ``False``."""
        sp = self._parse_hhmm(start)
        ep = self._parse_hhmm(end)
        if sp is None or ep is None:
            logger.warning(
                f"[LightScheduler] 스케줄 파싱 실패 — start={start!r} end={end!r} (HH:MM 형식 필요)"
            )
            return False
        # 표시용으로 정규화 (예: 6:0 → 06:00)
        self._start_time = f"{sp[0]:02d}:{sp[1]:02d}"
        self._end_time = f"{ep[0]:02d}:{ep[1]:02d}"
        self._last_scheduled_lit = None  # 새 구간으로 즉시 재판정·전송
        logger.info(
            f"[LightScheduler] 스케줄 갱신: start_time={self._start_time} end_time={self._end_time}"
        )
        return True

    def schedule_check_and_control(self) -> None:
        """실행 중인 asyncio 루프에서 ``check_and_control`` 코루틴을 예약합니다.

        WebSocket ``on_message`` 등 동기 콜백에서 사용합니다.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                f"[LightScheduler] schedule_check_and_control: 실행 중인 asyncio 루프 없음 — 생략"
            )
            return
        loop.create_task(self.check_and_control())

    async def check_and_control(self) -> None:
        """현재 시각이 스케줄 구간 안이면 조명 ON, 아니면 조명만 OFF.

        도어·스피커 등 다른 필드는 ``send_control`` 부분 갱신으로 유지합니다.
        """
        now = datetime.now().timetuple()
        now_min = self._to_minutes(now.tm_hour, now.tm_min)

        sp = self._parse_hhmm(self._start_time)
        ep = self._parse_hhmm(self._end_time)
        if sp is None or ep is None:
            logger.error(
                f"[LightScheduler] 내부 스케줄이 잘못됨 — start={self._start_time!r} end={self._end_time!r}"
            )
            return

        start_min = self._to_minutes(sp[0], sp[1])
        end_min = self._to_minutes(ep[0], ep[1])
        in_range = self._is_within_schedule(now_min, start_min, end_min)

        logger.info(
            f"[LightScheduler] 판정: 현재={now.tm_hour:02d}:{now.tm_min:02d} ({now_min}분), "
            f"구간={self._start_time}~{self._end_time}, 활성={in_range}"
        )

        if self._last_scheduled_lit is not None and self._last_scheduled_lit == in_range:
            logger.debug(
                f"[LightScheduler] 이전 판정과 동일(활성={in_range}) — 전송 생략"
            )
            return
        self._last_scheduled_lit = in_range

        if in_range:
            control = PcbControlInput(
                ac_light1=LightMode.ON,
                ac_light2=LightMode.ON,
                dc_light1=LightMode.ON,
                dc_light2=LightMode.ON,
                dc_light_brightness1=_SCHEDULE_ON_DC_BRIGHTNESS,
                dc_light_brightness2=_SCHEDULE_ON_DC_BRIGHTNESS,
            )
            ok = self._controller.send_control(control)
            logger.info(
                f"[LightScheduler] 스케줄 구간 내 → 조명 ON 전송 ({'성공' if ok else '실패'})"
            )
        else:
            control = PcbControlInput(
                ac_light1=LightMode.OFF,
                ac_light2=LightMode.OFF,
                dc_light1=LightMode.OFF,
                dc_light2=LightMode.OFF,
                dc_light_brightness1=0,
                dc_light_brightness2=0,
            )
            ok = self._controller.send_control(control)
            logger.info(
                f"[LightScheduler] 스케줄 구간 밖 → 조명 OFF 전송 ({'성공' if ok else '실패'})"
            )
