"""
GPS SMART KIOSK 통신 모듈 - 메인 엔트리포인트

실행 방법:
    uv sync
    uv run python main.py

.exe 빌드 (예시):
    uv sync --group dev
    uv run pyinstaller --onefile --name kiosk_module main.py
    → dist/kiosk_module.exe (실행 파일과 같은 폴더의 .env를 읽음; 변경 후 재시작)
"""

import asyncio
import logging
import sys

from kiosk_module.config import config
from kiosk_module.kiosk_runner import run_kiosk
from kiosk_module.serial_manager import SerialManager
from kiosk_module.volume_serial_controller import run_volume_serial_controller


def setup_logging():
    """로깅 설정."""
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def resolve_serial_port() -> str:
    """환경설정의 포트 문자열을 실제 장치 경로로 해석."""
    found = SerialManager.resolve_port_choice(
        config.serial_port,
        config.serial_port_description_keyword,
    )
    if found is None:
        kw = (config.serial_port_description_keyword or "").strip() or "USB"
        raise SystemExit(
            f"자동 포트 검색 실패: 설명에 {kw!r} 가 포함된 포트가 없습니다."
        )
    return found


def resolve_volume_serial_port() -> str:
    """볼륨 전용 시리얼 포트를 실제 장치 경로로 해석."""
    found = SerialManager.resolve_port_choice(
        config.volume_serial_port,
        config.serial_port_description_keyword,
    )
    if found is None:
        kw = (config.serial_port_description_keyword or "").strip() or "USB"
        raise SystemExit(
            f"볼륨 자동 포트 검색 실패: 설명에 {kw!r} 가 포함된 포트가 없습니다."
        )
    return found


async def main():
    setup_logging()
    logger = logging.getLogger("main")

    logger.info(f"{'=' * 50}")
    logger.info(f"설정: {config}")
    logger.info(f"{'=' * 50}")

    port = resolve_serial_port()
    if port != config.serial_port.strip():
        logger.info(f"시리얼 포트(자동): {port}")

    volume_port = resolve_volume_serial_port()
    if volume_port != config.volume_serial_port.strip():
        logger.info(f"볼륨 시리얼 포트(자동): {volume_port}")

    await asyncio.gather(
        run_kiosk(port, config.serial_baudrate, stop_event=None, controller_ref=None),
        run_volume_serial_controller(volume_port, config.volume_serial_baudrate),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n프로그램이 종료되었습니다.")
