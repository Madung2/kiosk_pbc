"""
시리얼 입력(U/D)으로 Windows 시스템 볼륨을 제어.

사용 예:
    uv run python -m kiosk_module.volume_serial_controller
"""

from __future__ import annotations

import ctypes
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import serial
from dotenv import load_dotenv


def _bootstrap_dotenv() -> None:
    """실행 파일(.exe)일 때 실행 파일 옆 .env를 우선 로드."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        load_dotenv(exe_dir / ".env", override=True)
    load_dotenv(override=False)


_bootstrap_dotenv()

VOLUME_SERIAL_PORT = (os.getenv("VOLUME_SERIAL_PORT", "COM5") or "COM5").strip()
VOLUME_SERIAL_BAUDRATE = int(os.getenv("VOLUME_SERIAL_BAUDRATE", "115200"))
VOLUME_SERIAL_TIMEOUT = float(os.getenv("VOLUME_SERIAL_TIMEOUT", "0.2"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
# 바이너리 장치 호환: 필요하면 .env에서 커스터마이즈
VOLUME_UP_HEX_CODES = {
    token.strip().lower()
    for token in os.getenv("VOLUME_UP_HEX_CODES", "1c").split(",")
    if token.strip()
}
VOLUME_DOWN_HEX_CODES = {
    token.strip().lower()
    for token in os.getenv("VOLUME_DOWN_HEX_CODES", "fc").split(",")
    if token.strip()
}


VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
KEYEVENTF_KEYUP = 0x0002


def tap_virtual_key(vk_code: int) -> None:
    """Windows 가상 키를 눌렀다 떼어 OS 볼륨을 조절."""
    user32 = ctypes.windll.user32
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


def apply_volume_command(raw: bytes, decoded: str, logger: logging.Logger) -> bool:
    """명령어를 확인하고 가상 키 입력을 통해 시스템 볼륨을 제어함."""
    cmd = decoded.strip().upper()
    raw_hex = raw.hex().lower()

    is_up: bool | None = None
    reason = ""

    # 1. 명령어 판별
    if cmd == "U" or raw_hex in VOLUME_UP_HEX_CODES:
        is_up = True
        reason = f"UP ({cmd if cmd else raw_hex})"
    elif cmd == "D" or raw_hex in VOLUME_DOWN_HEX_CODES:
        is_up = False
        reason = f"DOWN ({cmd if cmd else raw_hex})"
    else:
        return False

    # 2. 가상 키 입력 실행 (이게 가장 확실함!)
    try:
        # 가상 키를 누르면 윈도우 OS가 알아서 볼륨을 조절하고 UI를 띄움
        tap_virtual_key(VK_VOLUME_UP if is_up else VK_VOLUME_DOWN)
        
        logger.info(f"[성공] OS 볼륨 조절 키 전송: {reason}")
        
        # 만약 로그에 수치 변화를 꼭 찍고 싶다면, 
        # 키 입력 후 아주 잠깐의 지연시간을 두고 현재 볼륨을 읽어오는 로직을 추가할 수 있음
        return True
    except Exception as exc:
        logger.error(f"가상 키 입력 실패: {exc}")
        return False

def run(port: str | None = None, baudrate: int | None = None) -> None:
    if os.name != "nt":
        raise SystemExit("이 스크립트는 Windows에서만 동작합니다.")

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("volume-serial")

    resolved_port = (port or VOLUME_SERIAL_PORT).strip()
    resolved_baudrate = int(baudrate or VOLUME_SERIAL_BAUDRATE)
    logger.info("볼륨 시리얼 리스너 시작: %s @ %s", resolved_port, resolved_baudrate)

    while True:
        try:
            with serial.Serial(
                port=resolved_port,
                baudrate=resolved_baudrate,
                timeout=VOLUME_SERIAL_TIMEOUT,
            ) as ser:
                logger.info("시리얼 연결됨: %s", ser.port)
                while True:
                    raw = ser.read(1)
                    if not raw:
                        continue
                    msg = raw.decode(errors="ignore")
                    logger.info("시리얼 수신 raw=%s decoded=%r", raw.hex(), msg)
                    if apply_volume_command(raw, msg, logger):
                        logger.info("볼륨 명령 처리 완료: %r", msg.upper())
                    else:
                        logger.debug("무시된 입력: %r", msg)
        except serial.SerialException as exc:
            logger.debug("시리얼 미연결/오류(%s). 2초 후 재연결 시도", exc)
            time.sleep(2)


async def run_volume_serial_controller(port: str, baudrate: int) -> None:
    """async main에서 await 가능한 볼륨 시리얼 런처."""
    await asyncio.to_thread(run, port, baudrate)


if __name__ == "__main__":
    run()
