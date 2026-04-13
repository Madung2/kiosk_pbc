"""
시간대별 조명 자동 스케줄러.

로컬 시각 기준으로 AC 조명·DC(디밍) 조명에 **서로 다른** ON 구간을 둘 수 있습니다.
구간 밖에서는 해당 채널만 OFF로내며, 도어·스피커 등은 ``send_control`` 부분 갱신으로 유지합니다.

타임존은 OS 로컬 시각(``datetime.now()``)을 사용합니다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Literal

from .device_controller import Controllerer, PcbControlInput
from .protocol import LightMode

logger = logging.getLogger(__name__)

# DC 밝기: ``Controllerer.all_on`` 과 동일하게 프로토콜 상한(0~10) 사용
_SCHEDULE_ON_DC_BRIGHTNESS = 10

# "HH:MM" 형식 (선행 0 허용)
_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")

_ScheduleScope = Literal["ac", "dc", "both"]


class LightScheduler:
    """AC·DC 각각 시작·종료 시각에 따라 ``Controllerer``로 조명을 전송합니다."""

    def __init__(
        self,
        controller: Controllerer,
        *,
        ac_enabled: bool = True,
        ac_start_time: str = "06:00",
        ac_end_time: str = "00:00",
        dc_enabled: bool = True,
        dc_start_time: str = "06:00",
        dc_end_time: str = "00:00",
    ) -> None:
        self._controller = controller
        self._ac_enabled = ac_enabled
        self._dc_enabled = dc_enabled
        self._ac_start_time = ac_start_time
        self._ac_end_time = ac_end_time
        self._dc_start_time = dc_start_time
        self._dc_end_time = dc_end_time
        self._last_ac_lit: bool | None = None
        self._last_dc_lit: bool | None = None

    @property
    def ac_enabled(self) -> bool:
        return self._ac_enabled

    @property
    def dc_enabled(self) -> bool:
        return self._dc_enabled

    @property
    def ac_start_time(self) -> str:
        return self._ac_start_time

    @property
    def ac_end_time(self) -> str:
        return self._ac_end_time

    @property
    def dc_start_time(self) -> str:
        return self._dc_start_time

    @property
    def dc_end_time(self) -> str:
        return self._dc_end_time

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

    @classmethod
    def is_valid_schedule(cls, start: str, end: str) -> bool:
        """``start``·``end`` 가 모두 ``HH:MM`` 으로 파싱 가능한지."""
        return cls._parse_hhmm(start) is not None and cls._parse_hhmm(end) is not None

    @staticmethod
    def _to_minutes(h: int, m: int) -> int:
        return h * 60 + m

    @staticmethod
    def _normalize_end_minutes(end_min: int, start_min: int) -> int:
        """종료 시각 분 단위 보정.

        ``end``가 자정 ``00:00``이고 시작이 그날 0시가 아니면 **24:00(1440분)** 으로 해석합니다.
        """
        if end_min == 0 and start_min > 0:
            return 24 * 60
        return end_min

    def _is_within_schedule(self, now_min: int, start_min: int, end_min: int) -> bool:
        """현재 시각(분)이 [시작, 종료) 활성 구간에 들어가는지 판정."""
        end_adj = self._normalize_end_minutes(end_min, start_min)

        if start_min < end_adj:
            return start_min <= now_min < end_adj
        if start_min > end_adj:
            return now_min >= start_min or now_min < end_adj
        return False

    def _in_range_for(self, now_min: int, start_s: str, end_s: str) -> bool | None:
        """스케줄 안이면 ``True``, 밖이면 ``False``, 시각 파싱 실패 시 ``None``."""
        sp = self._parse_hhmm(start_s)
        ep = self._parse_hhmm(end_s)
        if sp is None or ep is None:
            return None
        start_min = self._to_minutes(sp[0], sp[1])
        end_min = self._to_minutes(ep[0], ep[1])
        return self._is_within_schedule(now_min, start_min, end_min)

    def try_update_schedule(
        self,
        start: str,
        end: str,
        *,
        scope: _ScheduleScope = "both",
    ) -> bool:
        """WebSocket 등에서 받은 문자열로 스케줄을 갱신. 파싱 실패 시 ``False``.

        ``scope``: ``ac`` | ``dc`` | ``both`` (AC·DC 동일 구간).
        비활성 채널은 시각만 건너뛰고, 둘 다 갱신되지 않으면 ``False``.
        """
        sp = self._parse_hhmm(start)
        ep = self._parse_hhmm(end)
        if sp is None or ep is None:
            logger.warning(
                f"[LightScheduler] 스케줄 파싱 실패 — start={start!r} end={end!r} (HH:MM 형식 필요)"
            )
            return False
        ns = f"{sp[0]:02d}:{sp[1]:02d}"
        ne = f"{ep[0]:02d}:{ep[1]:02d}"

        updated = False
        if scope in ("ac", "both") and self._ac_enabled:
            self._ac_start_time = ns
            self._ac_end_time = ne
            self._last_ac_lit = None
            updated = True
            logger.info(f"[LightScheduler] AC 스케줄 갱신: {ns}~{ne}")
        elif scope in ("ac", "both"):
            logger.warning("[LightScheduler] AC 스케줄이 비활성이라 시각 갱신을 건너뜁니다.")

        if scope in ("dc", "both") and self._dc_enabled:
            self._dc_start_time = ns
            self._dc_end_time = ne
            self._last_dc_lit = None
            updated = True
            logger.info(f"[LightScheduler] DC 스케줄 갱신: {ns}~{ne}")
        elif scope in ("dc", "both"):
            logger.warning("[LightScheduler] DC 스케줄이 비활성이라 시각 갱신을 건너뜁니다.")

        return updated

    def schedule_check_and_control(self) -> None:
        """실행 중인 asyncio 루프에서 ``check_and_control`` 코루틴을 예약합니다."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "[LightScheduler] schedule_check_and_control: 실행 중인 asyncio 루프 없음 — 생략"
            )
            return
        loop.create_task(self.check_and_control())

    @staticmethod
    def _channel_unchanged(want: bool | None, last: bool | None) -> bool:
        """해당 채널을 스케줄러가 다루지 않으면(``want is None``) 변화 없음으로 본다."""
        if want is None:
            return True
        return last is not None and last == want

    async def check_and_control(self) -> None:
        """활성 채널별로 구간을 판정해 AC·DC만 부분 갱신 전송."""
        now = datetime.now().timetuple()
        now_min = self._to_minutes(now.tm_hour, now.tm_min)
        tlabel = f"{now.tm_hour:02d}:{now.tm_min:02d}"

        want_ac: bool | None = None
        if self._ac_enabled:
            want_ac = self._in_range_for(now_min, self._ac_start_time, self._ac_end_time)
            if want_ac is None:
                logger.error(
                    f"[LightScheduler] AC 스케줄 시각 오류 — "
                    f"{self._ac_start_time!r}~{self._ac_end_time!r}"
                )
                return

        want_dc: bool | None = None
        if self._dc_enabled:
            want_dc = self._in_range_for(now_min, self._dc_start_time, self._dc_end_time)
            if want_dc is None:
                logger.error(
                    f"[LightScheduler] DC 스케줄 시각 오류 — "
                    f"{self._dc_start_time!r}~{self._dc_end_time!r}"
                )
                return

        if want_ac is None and want_dc is None:
            return

        logger.info(
            f"[LightScheduler] 판정: 현재={tlabel}, "
            f"AC={self._ac_start_time}~{self._ac_end_time} 활성={want_ac} | "
            f"DC={self._dc_start_time}~{self._dc_end_time} 활성={want_dc}"
        )

        if self._channel_unchanged(want_ac, self._last_ac_lit) and self._channel_unchanged(
            want_dc, self._last_dc_lit
        ):
            logger.debug("[LightScheduler] 이전 판정과 동일 — 전송 생략")
            return

        fields: dict = {}
        if want_ac is not None:
            mode_on = LightMode.ON if want_ac else LightMode.OFF
            fields["ac_light1"] = mode_on
            fields["ac_light2"] = mode_on
            self._last_ac_lit = want_ac
        if want_dc is not None:
            if want_dc:
                fields["dc_light1"] = LightMode.ON
                fields["dc_light2"] = LightMode.ON
                fields["dc_light_brightness1"] = _SCHEDULE_ON_DC_BRIGHTNESS
                fields["dc_light_brightness2"] = _SCHEDULE_ON_DC_BRIGHTNESS
            else:
                fields["dc_light1"] = LightMode.OFF
                fields["dc_light2"] = LightMode.OFF
                fields["dc_light_brightness1"] = 0
                fields["dc_light_brightness2"] = 0
            self._last_dc_lit = want_dc

        control = PcbControlInput(**fields)
        ok = self._controller.send_control(control)
        ac_msg = f"AC={'ON' if want_ac else 'OFF'}" if want_ac is not None else "AC=(스케줄 없음)"
        dc_msg = f"DC={'ON' if want_dc else 'OFF'}" if want_dc is not None else "DC=(스케줄 없음)"
        logger.info(
            f"[LightScheduler] 조명 전송 {ac_msg}, {dc_msg} ({'성공' if ok else '실패'})"
        )
