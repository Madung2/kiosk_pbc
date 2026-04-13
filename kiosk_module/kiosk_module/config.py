"""
설정 관리

.env 파일에서 환경변수를 읽어 설정값으로 사용.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    """모듈 설정."""

    # 시리얼 포트 (빈 값 또는 AUTO이면 ``SERIAL_PORT_DESCRIPTION_KEYWORD``로 자동 검색)
    serial_port: str = os.getenv("SERIAL_PORT", "COM3")
    serial_baudrate: int = int(os.getenv("SERIAL_BAUDRATE", "115200"))
    serial_port_description_keyword: str = os.getenv(
        "SERIAL_PORT_DESCRIPTION_KEYWORD", "USB"
    )

    # 백엔드에서 키오스크 구분 (예: WS 이벤트 ``PERSON_DETECTED`` 페이로드)
    kiosk_id: str = (os.getenv("KIOSK_ID", "") or "").strip()

    # WebSocket (PCB 제어·상태는 시리얼이 본통; 백엔드 연동할 때만 켜면 됨)
    ws_enabled: bool = _env_bool("WS_ENABLED", default=False)
    ws_url: str = os.getenv("WS_URL", "ws://localhost:8080/ws")
    ws_reconnect_interval: float = float(
        os.getenv("WS_RECONNECT_INTERVAL", "5.0")
    )

    # 상태 폴링 (초)
    status_poll_interval: float = float(
        os.getenv("STATUS_POLL_INTERVAL", "2.0")
    )

    # 사람 없음 + 입력 유휴 시 자동 도어 닫기 (초).
    # 전제: INPUT_MONITOR_ENABLED=true 이고 pynput이 동작해야 유휴 시간이 증가함(기본 20초).
    vacant_idle_close_seconds: float = float(
        os.getenv("VACANT_IDLE_CLOSE_SECONDS", "20.0")
    )

    # 사람 최초 감지 시 PCB 스피커 ON + WS 이벤트(``event``·``kiosk_id`` 등, 백엔드 음성 등).
    auto_open_door_on_person: bool = _env_bool(
        "AUTO_OPEN_DOOR_ON_PERSON", default=True
    )

    # 키보드·마우스 전역 감지. false면 유휴 시간이 항상 0으로 취급되어 자동 도어 닫기가 동작하지 않음.
    input_monitor_enabled: bool = _env_bool(
        "INPUT_MONITOR_ENABLED", default=True
    )

    # 오른쪽 버튼 전용 Meet/웹 URL(백그라운드 브라우저).
    meet_web_url: str = (os.getenv("MEET_WEB_URL", "") or "").strip()
    # 비우면 macOS/Windows 기본 Chrome 경로 시도. 예: '"/path/Google Chrome" --new-window {url}'
    kiosk_browser_cmd: str = (os.getenv("KIOSK_BROWSER_CMD", "") or "").strip()
    background_browser_timeout_seconds: float = float(
        os.getenv("BACKGROUND_BROWSER_TIMEOUT_SECONDS", "300")
    )

    # 로그
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def __repr__(self):
        return (
            f"Config(\n"
            f"  kiosk_id={self.kiosk_id!r},\n"
            f"  serial={self.serial_port}@{self.serial_baudrate}, "
            f"port_kw={self.serial_port_description_keyword!r},\n"
            f"  ws_enabled={self.ws_enabled}, url={self.ws_url},\n"
            f"  poll={self.status_poll_interval}s,\n"
            f"  vacant_idle_close={self.vacant_idle_close_seconds}s,\n"
            f"  auto_door_person={self.auto_open_door_on_person},\n"
            f"  input_monitor={self.input_monitor_enabled},\n"
            f"  meet_url_set={bool(self.meet_web_url)},\n"
            f"  browser_timeout={self.background_browser_timeout_seconds}s,\n"
            f"  log={self.log_level}\n"
            f")"
        )


# 싱글톤 인스턴스
config = Config()
