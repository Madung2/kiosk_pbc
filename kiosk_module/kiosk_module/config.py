"""
설정 관리

.env 파일에서 환경변수를 읽어 설정값으로 사용.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bootstrap_dotenv() -> None:
    """PyInstaller .exe는 CWD가 불안정할 수 있어, 실행 파일 옆 ``.env``를 우선 로드."""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        load_dotenv(exe_dir / ".env", override=True)
    # 개발: 기존과 같이 CWD 기준 탐색(상위 디렉터리 포함). 이미 설정된 키는 덮어쓰지 않음.
    load_dotenv(override=False)


_bootstrap_dotenv()


def runtime_base_dir() -> Path:
    """실행 기준 디렉터리(.env 상대 경로 기준)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _light_schedule_hhmm(env_name: str, legacy_name: str | None, default: str) -> str:
    """채널 전용 HH:MM이 있으면 사용, 없으면 ``LIGHT_SCHEDULE_START``/``END`` 로 폴백."""
    ch = (os.getenv(env_name) or "").strip()
    if ch:
        return ch
    if legacy_name:
        leg = (os.getenv(legacy_name) or "").strip()
        if leg:
            return leg
    return default


@dataclass
class Config:
    """모듈 설정."""

    # 시리얼 포트 (빈 값 또는 AUTO이면 ``SERIAL_PORT_DESCRIPTION_KEYWORD``로 자동 검색)
    serial_port: str = os.getenv("SERIAL_PORT", "COM3")
    serial_baudrate: int = int(os.getenv("SERIAL_BAUDRATE", "115200"))
    serial_port_description_keyword: str = os.getenv(
        "SERIAL_PORT_DESCRIPTION_KEYWORD", "USB"
    )
    volume_serial_port: str = os.getenv("VOLUME_SERIAL_PORT", "COM5")
    volume_serial_baudrate: int = int(os.getenv("VOLUME_SERIAL_BAUDRATE", "115200"))

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
    person_detected_mp3_path: str = (
        os.getenv("PERSON_DETECTED_MP3_PATH", "person_detected.mp3") or ""
    ).strip()
    person_detected_tts_text: str = (
        os.getenv("PERSON_DETECTED_TTS_TEXT", "") or os.getenv("tts_text", "") or ""
    ).strip()
    person_detected_tts_lang: str = (
        os.getenv("PERSON_DETECTED_TTS_LANG", "ko") or "ko"
    ).strip()
    person_detected_tts_autogen: bool = _env_bool(
        "PERSON_DETECTED_TTS_AUTOGEN", default=True
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

    # 조명 자동 스케줄 (로컬 시각 HH:MM). AC·DC(디밍) 각각 구간 **안**에서만 ON, 밖에서는 OFF.
    # 채널별 시각을 비우면 구 ``LIGHT_SCHEDULE_START`` / ``LIGHT_SCHEDULE_END`` 로 폴백(하위 호환).
    light_schedule_enabled: bool = _env_bool("LIGHT_SCHEDULE_ENABLED", default=True)
    light_schedule_ac_enabled: bool = _env_bool("LIGHT_SCHEDULE_AC_ENABLED", default=True)
    light_schedule_dc_enabled: bool = _env_bool("LIGHT_SCHEDULE_DC_ENABLED", default=True)
    light_schedule_ac_start: str = _light_schedule_hhmm(
        "LIGHT_SCHEDULE_AC_START", "LIGHT_SCHEDULE_START", "06:00"
    )
    light_schedule_ac_end: str = _light_schedule_hhmm(
        "LIGHT_SCHEDULE_AC_END", "LIGHT_SCHEDULE_END", "00:00"
    )
    light_schedule_dc_start: str = _light_schedule_hhmm(
        "LIGHT_SCHEDULE_DC_START", "LIGHT_SCHEDULE_START", "06:00"
    )
    light_schedule_dc_end: str = _light_schedule_hhmm(
        "LIGHT_SCHEDULE_DC_END", "LIGHT_SCHEDULE_END", "00:00"
    )

    # 로그
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def __repr__(self):
        return (
            f"Config(\n"
            f"  kiosk_id={self.kiosk_id!r},\n"
            f"  serial={self.serial_port}@{self.serial_baudrate}, "
            f"port_kw={self.serial_port_description_keyword!r},\n"
            f"  volume_serial={self.volume_serial_port}@{self.volume_serial_baudrate},\n"
            f"  ws_enabled={self.ws_enabled}, url={self.ws_url},\n"
            f"  poll={self.status_poll_interval}s,\n"
            f"  vacant_idle_close={self.vacant_idle_close_seconds}s,\n"
            f"  auto_door_person={self.auto_open_door_on_person},\n"
            f"  person_mp3={self.person_detected_mp3_path!r}, "
            f"tts_text_set={bool(self.person_detected_tts_text)}, "
            f"tts_autogen={self.person_detected_tts_autogen},\n"
            f"  input_monitor={self.input_monitor_enabled},\n"
            f"  meet_url_set={bool(self.meet_web_url)},\n"
            f"  browser_timeout={self.background_browser_timeout_seconds}s,\n"
            f"  light_schedule={self.light_schedule_enabled} "
            f"AC={self.light_schedule_ac_enabled} {self.light_schedule_ac_start}~{self.light_schedule_ac_end} "
            f"DC={self.light_schedule_dc_enabled} {self.light_schedule_dc_start}~{self.light_schedule_dc_end},\n"
            f"  log={self.log_level}\n"
            f")"
        )


# 싱글톤 인스턴스
config = Config()
