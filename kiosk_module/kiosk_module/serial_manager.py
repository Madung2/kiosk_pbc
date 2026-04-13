"""
시리얼 통신 매니저

PCB와의 RS232 시리얼 포트 연결/해제, 프레임 송수신을 담당.
asyncio 기반으로 동작하여 WebSocket 브릿지와 함께 사용 가능.
"""

import asyncio
import logging
from typing import Callable, Optional

import serial
import serial.tools.list_ports

from .protocol import STX, ETX, FrameParser

logger = logging.getLogger(__name__)


class SerialManager:
    """PCB와의 시리얼 통신을 관리하는 클래스.

    Usage:
        manager = SerialManager(port="COM3")
        manager.open()
        manager.send(frame_bytes)
        response = manager.receive()
        manager.close()

    비동기 사용:
        await manager.start_reading(on_frame_callback)
        await manager.stop_reading()
    """

    # 사양서 기준 통신 설정
    DEFAULT_BAUDRATE = 115200
    DEFAULT_BYTESIZE = serial.EIGHTBITS
    DEFAULT_STOPBITS = serial.STOPBITS_ONE
    DEFAULT_PARITY = serial.PARITY_NONE
    DEFAULT_TIMEOUT = 1.0  # 읽기 타임아웃 (초)

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = DEFAULT_BAUDRATE,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

        self._serial: Optional[serial.Serial] = None
        self._read_task: Optional[asyncio.Task] = None
        self._running = False
        self._recv_buffer = b""

    # ──────────────────────────────────────────
    # 연결 관리
    # ──────────────────────────────────────────
    def open(self) -> bool:
        """시리얼 포트를 열어 PCB와 연결.

        Returns:
            True = 연결 성공, False = 실패
        """
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=self.DEFAULT_BYTESIZE,
                stopbits=self.DEFAULT_STOPBITS,
                parity=self.DEFAULT_PARITY,
                timeout=self.timeout,
            )
            logger.info(f"시리얼 포트 열림: {self.port} @ {self.baudrate}bps")
            return True
        except serial.SerialException as e:
            logger.error(f"시리얼 포트 열기 실패: {e}")
            return False

    def close(self):
        """시리얼 포트 닫기."""
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info(f"시리얼 포트 닫힘: {self.port}")
        self._serial = None

    @property
    def is_connected(self) -> bool:
        """시리얼 포트가 열려있는지 확인."""
        return self._serial is not None and self._serial.is_open

    # ──────────────────────────────────────────
    # 동기 송수신 (간단한 제어/테스트용)
    # ──────────────────────────────────────────
    def send(self, frame: bytes) -> bool:
        """프레임을 PCB로 전송.

        Args:
            frame: 전송할 프레임 (STX ~ ETX)

        Returns:
            True = 전송 성공
        """
        if not self.is_connected:
            logger.error(f"시리얼 포트가 연결되지 않았습니다")
            return False

        try:
            written = self._serial.write(frame)
            self._serial.flush()
            logger.debug(f"TX ({written}B): {frame.hex(' ')}")
            return True
        except serial.SerialException as e:
            logger.error(f"전송 실패: {e}")
            return False

    def receive(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """PCB로부터 프레임 하나를 수신 (동기, 블로킹).

        Args:
            timeout: 대기 시간 (초). None이면 기본 타임아웃 사용.

        Returns:
            수신된 프레임 bytes 또는 None (타임아웃)
        """
        if not self.is_connected:
            return None

        old_timeout = self._serial.timeout
        if timeout is not None:
            self._serial.timeout = timeout

        try:
            buf = self._recv_buffer

            while True:
                chunk = self._serial.read(256)
                if not chunk:
                    break  # 타임아웃

                buf += chunk
                frames, buf = FrameParser.extract_frames(buf)

                if frames:
                    self._recv_buffer = buf
                    frame = frames[0]
                    logger.debug(f"RX ({len(frame)}B): {frame.hex(' ')}")
                    return frame

            self._recv_buffer = buf
            return None
        finally:
            self._serial.timeout = old_timeout

    def send_and_receive(
        self, frame: bytes, timeout: float = 0.5
    ) -> Optional[bytes]:
        """프레임 전송 후 응답을 기다림 (동기).

        Args:
            frame: 전송할 프레임
            timeout: 응답 대기 시간 (초)

        Returns:
            응답 프레임 또는 None
        """
        if not self.send(frame):
            return None
        return self.receive(timeout=timeout)

    # ──────────────────────────────────────────
    # 비동기 수신 루프 (이벤트 기반)
    # ──────────────────────────────────────────
    async def start_reading(
        self, on_frame: Callable[[bytes], None], interval: float = 0.01
    ):
        """비동기 수신 루프 시작.

        수신된 프레임마다 on_frame 콜백을 호출.

        Args:
            on_frame: 프레임 수신 시 호출할 콜백 함수
            interval: 폴링 간격 (초)
        """
        if not self.is_connected:
            logger.error(f"수신 루프 시작 실패: 시리얼 미연결")
            return

        self._running = True
        logger.info(f"비동기 수신 루프 시작")

        while self._running:
            try:
                if self._serial and self._serial.in_waiting > 0:
                    chunk = self._serial.read(self._serial.in_waiting)
                    self._recv_buffer += chunk

                    frames, self._recv_buffer = FrameParser.extract_frames(
                        self._recv_buffer
                    )

                    for frame in frames:
                        logger.debug(f"RX ({len(frame)}B): {frame.hex(' ')}")
                        try:
                            on_frame(frame)
                        except Exception as e:
                            logger.error(f"프레임 콜백 에러: {e}")

            except serial.SerialException as e:
                logger.error(f"수신 에러: {e}")
                self._running = False
                break

            await asyncio.sleep(interval)

        logger.info(f"비동기 수신 루프 종료")

    def stop_reading(self):
        """비동기 수신 루프 중지."""
        self._running = False

    # ──────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────
    @staticmethod
    def list_port_entries_filtered(description_keyword: str | None = None) -> list[tuple[str, str]]:
        """콤보박스용 목록. 키워드가 비면 전체 포트, 있으면 설명에 부분 일치하는 것만 (대소문자 무시)."""
        needle = (description_keyword or "").strip().lower()
        rows: list[tuple[str, str]] = []
        for p in serial.tools.list_ports.comports():
            label = f"{p.device} — {p.description or 'Serial'}"
            if not needle:
                rows.append((p.device, label))
            elif needle in (p.description or "").lower():
                rows.append((p.device, label))
        return rows

    @staticmethod
    def find_pcb_port(description_keyword: str = "USB") -> Optional[str]:
        """포트 설명에 키워드가 포함된 시리얼 장치 **첫 번째** (대소문자 무시).

        Args:
            description_keyword: ``list_ports``의 ``description`` 부분 문자열 (예: ``USB``, ``CP210``)

        Returns:
            장치 경로(예: ``COM3``, ``/dev/ttyUSB0``) 또는 없으면 ``None``
        """
        if not (description_keyword or "").strip():
            return None
        rows = SerialManager.list_port_entries_filtered(description_keyword)
        return rows[0][0] if rows else None

    @staticmethod
    def resolve_port_choice(
        port_raw: str,
        description_keyword: str,
    ) -> Optional[str]:
        """CLI/GUI 공통: 포트가 비었거나 ``AUTO``이면 설명 키워드로 장치를 찾고, 아니면 ``port_raw``를 씁니다.

        Returns:
            장치 경로. 자동 검색이 필요했는데 없으면 ``None``.
        """
        raw = (port_raw or "").strip()
        if raw.upper() == "AUTO" or raw == "":
            kw = (description_keyword or "").strip() or "USB"
            return SerialManager.find_pcb_port(kw)
        return raw

    @staticmethod
    def list_ports() -> list[str]:
        """사용 가능한 시리얼 포트 목록 반환."""
        ports = serial.tools.list_ports.comports()
        return [p.device for p in ports]

    @staticmethod
    def list_port_entries() -> list[tuple[str, str]]:
        """콤보박스용 (device, 표시용 라벨) 전체 목록."""
        return SerialManager.list_port_entries_filtered(None)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self):
        status = "연결됨" if self.is_connected else "미연결"
        return f"SerialManager(port={self.port!r}, {status})"
