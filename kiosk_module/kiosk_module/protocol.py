"""
GPS SMART KIOSK LED CONTROL 프로토콜 정의

프레임 공통:
    STX(0x02) | COMMAND(1B) | DATA(가변) | BCC(1B) | ETX(0x03)

상태 응답 (Command ``S``) DATA 바이트 순서 (각 1바이트, 총 11바이트):
    AC1 | AC2 | DC1 | DC2 | DC밝기1 | DC밝기2 | DOOR | 스피커 | 사람검지 | 좌버튼 | 우버튼

제어 명령 (Command ``L``) DATA 바이트 순서 (각 1바이트):
    AC조명1 | AC조명2 | DC조명1 | DC조명2 | DC밝기1 | DC밝기2 | DOOR | 스피커
    (DC밝기1/2는 송신 시 DC_BRIGHTNESS_MIN~DC_BRIGHTNESS_MAX로 클램프)

즉 전체: STX | 'L' | AC1 | AC2 | DC1 | DC2 | DC밝기1 | DC밝기2 | DOOR | SPK | BCC | ETX

BCC 계산:
    COMMAND부터 BCC 직전까지 모든 바이트를 XOR
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# ──────────────────────────────────────────────
# 프로토콜 상수
# ──────────────────────────────────────────────
STX = 0x02
ETX = 0x03

# ``bytes.find``는 int를 받지 않으므로 1바이트 패턴 재사용
_STX_B = bytes((STX,))
_ETX_B = bytes((ETX,))

# Command 'L' DC 밝기 필드 (1바이트, 프로토콜 상 0~10 스텝)
DC_BRIGHTNESS_MIN = 0
DC_BRIGHTNESS_MAX = 10

CMD_CONTROL = ord("L")  # 관제 → PCB: 조명/도어/스피커 장치 제어
CMD_STATUS = ord("S")  # 관제 ↔ PCB: 상태 요청/응답
CMD_GPS_REQ = ord("T")  # 관제 → PCB: GPS 정보 요청 (OPTION)
CMD_GPS_POS = ord("P")  # 관제 → PCB: GPS 위치 요청 (OPTION)

DUMMY_BYTE = 0x00

# ──────────────────────────────────────────────
# 조명/장치 제어 값 (Command 'L' DATA 필드)
# ──────────────────────────────────────────────
class LightMode(IntEnum):
    """조명 동작 모드"""
    OFF = 0
    ON = 1
    DIMMING = 2  # DC 조명만 가능
    NO_CHANGE = 9


class DoorAction(IntEnum):
    """도어 동작"""
    OFF = 0
    OPEN = 1
    CLOSE = 2
    NO_CHANGE = 9


class SpeakerMode(IntEnum):
    """스피커 모드"""
    OFF = 0
    MAIN = 1
    NO_CHANGE = 9


@dataclass
class PcbControlState:
    """Command 'L' 제어 데이터 전체 (프레임 DATA에 그대로 대응).

    ``Controllerer`` 등에서 최종 합쳐진 상태를 넘길 때 사용합니다.
    """

    ac_light1: LightMode = LightMode.OFF
    ac_light2: LightMode = LightMode.OFF
    dc_light1: LightMode = LightMode.OFF
    dc_light2: LightMode = LightMode.OFF
    dc_light_brightness1: int = 0
    dc_light_brightness2: int = 0
    door: DoorAction = DoorAction.OFF
    speaker: SpeakerMode = SpeakerMode.OFF


# ──────────────────────────────────────────────
# BCC 계산
# ──────────────────────────────────────────────
def calc_bcc(data: bytes) -> int:
    """COMMAND부터 BCC 직전까지의 바이트를 XOR하여 BCC 계산.

    Args:
        data: COMMAND + DATA 바이트열 (STX/ETX 제외)

    Returns:
        XOR 결과값 (0~255)
    """
    bcc = 0
    for b in data:
        bcc ^= b
    return bcc


def _clamp_dc_brightness(value: int) -> int:
    """DC 조명 밝기를 프로토콜 허용 범위로 제한."""
    return max(DC_BRIGHTNESS_MIN, min(DC_BRIGHTNESS_MAX, value))


# ──────────────────────────────────────────────
# 프레임 빌더 (송신: 보낼 패킷 조립)
# ──────────────────────────────────────────────
class FrameBuilder:
    """PCB로 보낼 시리얼 프레임을 조립하는 클래스."""

    @staticmethod
    def _assemble_frame(payload: bytes) -> bytes:
        """COMMAND+DATA 바이트열에 BCC를 계산해 STX·ETX로 감싼 전송 프레임을 만든다."""
        bcc = calc_bcc(payload)
        return bytes([STX]) + payload + bytes([bcc, ETX])

    @staticmethod
    def build_control_frame(control: PcbControlState) -> bytes:
        """조명/장치 제어 프레임 생성 (Command 'L').

        Args:
            control: 제어 상태 (기본값이 필요하면 ``PcbControlState()``를 넘깁니다).

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        b1 = _clamp_dc_brightness(control.dc_light_brightness1)
        b2 = _clamp_dc_brightness(control.dc_light_brightness2)

        payload = bytes([
            CMD_CONTROL,
            int(control.ac_light1),
            int(control.ac_light2),
            int(control.dc_light1),
            int(control.dc_light2),
            b1,
            b2,
            int(control.door),
            int(control.speaker),
        ])

        return FrameBuilder._assemble_frame(payload)

    @staticmethod
    def build_status_request_frame() -> bytes:
        """상태 요청 프레임 생성 (Command 'S').

        PCB에 현재 상태를 물어볼 때 사용.
        DUMMY 바이트 0x00 포함.

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        payload = bytes([CMD_STATUS, DUMMY_BYTE])
        return FrameBuilder._assemble_frame(payload)

    @staticmethod
    def build_gps_request_frame() -> bytes:
        """GPS 정보 요청 프레임 생성 (Command 'T', OPTION).

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        payload = bytes([CMD_GPS_REQ, DUMMY_BYTE, DUMMY_BYTE])
        return FrameBuilder._assemble_frame(payload)

    @staticmethod
    def build_gps_position_request_frame() -> bytes:
        """GPS 위치 정보 요청 프레임 생성 (Command 'P', OPTION).

        Returns:
            전송용 bytes (STX ~ ETX)
        """
        payload = bytes([CMD_GPS_POS, DUMMY_BYTE])
        return FrameBuilder._assemble_frame(payload)


# ──────────────────────────────────────────────
# 응답 데이터 구조체
# ──────────────────────────────────────────────
# Command 'S' DATA 순서(각 1바이트): AC1 AC2 DC1 DC2 DC밝기1 DC밝기2 DOOR SPK 사람 좌버튼 우버튼
_Status01 = Annotated[int, Field(ge=0, le=1)]
_StatusLight = Annotated[int, Field(ge=0, le=2)]  # LightMode (DC는 DIMMING=2)
_StatusDoor = Annotated[int, Field(ge=0, le=2)]  # DoorAction


class StatusResponse(BaseModel):
    """PCB 상태 응답 (Command ``S`` DATA 11바이트).

    동작/검지류는 0 또는 1로 오는 전제(명세 다이어그램). DC 조명 동작·도어는 제어 명세와 같이 0~2.
    DC 밝기는 송신과 동일하게 ``DC_BRIGHTNESS_MIN``~``DC_BRIGHTNESS_MAX``(0~10).
    """

    model_config = ConfigDict(extra="forbid")

    ac_light_status1: _Status01
    ac_light_status2: _Status01
    dc_light_status1: _StatusLight
    dc_light_status2: _StatusLight
    dc_light_brightness1: Annotated[int, Field(ge=DC_BRIGHTNESS_MIN, le=DC_BRIGHTNESS_MAX)]
    dc_light_brightness2: Annotated[int, Field(ge=DC_BRIGHTNESS_MIN, le=DC_BRIGHTNESS_MAX)]
    door_status: _StatusDoor
    speaker_status: _Status01
    person_detected: _Status01
    button_left_status: _Status01
    button_right_status: _Status01


@dataclass(frozen=True)
class ButtonPressEvent:
    """좌/우 버튼 중 하나 이상이 0→눌림 엣지일 때의 스냅샷.

    ``StatusMonitor``는 동일 폴링 주기 안에서 좌·우 엣지를 합쳐 한 번만 콜백합니다.
    """

    left_pressed: bool
    right_pressed: bool
    left_just_pressed: bool
    right_just_pressed: bool


# ──────────────────────────────────────────────
# 프레임 파서 (수신: 받은 패킷 해석)
# ──────────────────────────────────────────────
class FrameParser:
    """PCB에서 수신한 시리얼 프레임을 파싱하는 클래스."""

    @staticmethod
    def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
        """버퍼에서 완전한 프레임들을 추출.

        STX로 시작하고 ETX로 끝나는 프레임을 찾아냄.

        Args:
            buffer: 수신 버퍼

        Returns:
            (추출된 프레임 목록, 아직 처리하지 못한 버퍼 꼬리)
        """
        frames: list[bytes] = []
        start = 0
        n = len(buffer)

        while start < n:
            stx = buffer.find(_STX_B, start)
            if stx == -1:
                return frames, buffer[start:]
            etx = buffer.find(_ETX_B, stx + 1)
            if etx == -1:
                return frames, buffer[stx:]
            frames.append(buffer[stx : etx + 1])
            start = etx + 1

        return frames, buffer[start:]

    @staticmethod
    def validate_frame(frame: bytes) -> bool:
        """프레임의 STX/ETX/BCC 유효성 검증.

        Args:
            frame: STX ~ ETX 포함된 전체 프레임

        Returns:
            True = 유효, False = 불량
        """
        if len(frame) < 4:  # 최소: STX + CMD + BCC + ETX
            return False
        if frame[0] != STX or frame[-1] != ETX:
            return False

        # BCC 검증: COMMAND ~ BCC 직전
        payload = frame[1:-2]  # STX, BCC, ETX 제외
        expected_bcc = calc_bcc(payload)
        actual_bcc = frame[-2]

        return expected_bcc == actual_bcc

    @staticmethod
    def _frame_data(frame: bytes) -> bytes:
        """COMMAND 다음 ~ BCC 직전 (순수 DATA)."""
        return frame[2:-2]

    @staticmethod
    def get_command(frame: bytes) -> int:
        """프레임에서 COMMAND 바이트 추출."""
        if len(frame) < 4:
            raise ValueError("프레임이 너무 짧습니다")
        return frame[1]

    @staticmethod
    def parse_status_response(frame: bytes) -> Optional[StatusResponse]:
        """상태 응답 프레임 파싱 (Command 'S' 응답).

        Args:
            frame: 수신된 전체 프레임 (STX ~ ETX)

        Returns:
            StatusResponse 또는 None (파싱 실패 시)
        """
        if not FrameParser.validate_frame(frame):
            return None

        cmd = frame[1]
        if cmd != CMD_STATUS:
            return None

        data = FrameParser._frame_data(frame)
        if len(data) < 11:
            return None

        try:
            return StatusResponse(
                ac_light_status1=data[0],
                ac_light_status2=data[1],
                dc_light_status1=data[2],
                dc_light_status2=data[3],
                dc_light_brightness1=data[4],
                dc_light_brightness2=data[5],
                door_status=data[6],
                speaker_status=data[7],
                person_detected=data[8],
                button_left_status=data[9],
                button_right_status=data[10],
            )
        except ValidationError:
            return None

