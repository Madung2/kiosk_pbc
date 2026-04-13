"""
폴링 태스크와 WebSocket 연결 태스크를 함께 실행·중지합니다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .status_monitor import StatusMonitor
from .ws_bridge import WSBridge

if TYPE_CHECKING:
    from .light_scheduler import LightScheduler

logger = logging.getLogger(__name__)


async def _light_schedule_loop(
    scheduler: "LightScheduler",
    interval_sec: float,
) -> None:
    """스케줄러를 주기적으로 실행 (취소 시 종료).

    한 번의 점검에서 예외가 나도 태스크를 끝내지 않습니다. 그렇지 않으면
    ``run_polling_and_ws``의 ``FIRST_COMPLETED`` 대기에서 조명 태스크만 먼저
    완료된 것으로 처리되어 폴링·시리얼 전체가 같이 멈춥니다.
    """
    try:
        while True:
            try:
                await scheduler.check_and_control()
            except Exception:
                logger.exception("조명 스케줄 점검(check_and_control) 실패 — 다음 주기까지 계속합니다.")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        logger.debug("조명 스케줄 루프 취소됨")
        raise


async def run_polling_and_ws(
    monitor: StatusMonitor,
    bridge: WSBridge | None,
    *,
    stop_event: asyncio.Event | None,
    poll_interval: float,
    light_scheduler: "LightScheduler | None" = None,
    light_schedule_interval: float = 60.0,
) -> None:
    """상태 폴링을 시작하고, 선택적으로 WS ``connect()`` 루프를 병행합니다.

    ``stop_event``가 있으면 이벤트가 set되거나 하위 태스크가 끝날 때까지 대기한 뒤
    폴링 중지·WS 연결 해제·나머지 태스크를 취소합니다.
    """
    poll_task = asyncio.create_task(
        monitor.start_polling(interval=poll_interval)
    )
    ws_task: asyncio.Task | None = None
    if bridge is not None:
        ws_task = asyncio.create_task(bridge.connect())

    light_task: asyncio.Task | None = None
    if light_scheduler is not None:
        light_task = asyncio.create_task(
            _light_schedule_loop(light_scheduler, light_schedule_interval)
        )

    if stop_event is None:
        tasks: list[asyncio.Task] = [poll_task]
        if ws_task is not None:
            tasks.append(ws_task)
        if light_task is not None:
            tasks.append(light_task)
        await asyncio.gather(*tasks)
        return

    stop_task = asyncio.create_task(stop_event.wait())
    wait_set: set[asyncio.Task] = {poll_task, stop_task}
    if ws_task is not None:
        wait_set.add(ws_task)
    if light_task is not None:
        wait_set.add(light_task)

    done, pending = await asyncio.wait(
        wait_set,
        return_when=asyncio.FIRST_COMPLETED,
    )
    monitor.stop_polling()
    if bridge is not None:
        await bridge.disconnect()
    for t in pending:
        t.cancel()
    for t in pending:
        try:
            await t
        except asyncio.CancelledError:
            pass
    for t in done:
        if t is not stop_task and not t.cancelled():
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "폴링/WebSocket/조명 태스크 중 하나가 예외로 끝나 연결 루프를 정리합니다."
                )
                raise
