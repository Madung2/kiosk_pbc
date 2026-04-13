"""
사람 감지(person_detected) + 좌/우 버튼 감지 테스트 스크립트.

실행 예시:
    python test_person_buttons.py --port COM3 --interval 0.2
    python test_person_buttons.py --port AUTO --port-keyword USB
"""

# ===== 복붙용 실행 명령어 샘플 (Windows PowerShell) =====
# cd c:\Users\user\Desktop\kiosk\kiosk_module
#
# 1) 기본 실행 (.env 값 사용)
# python .\test_person_buttons.py
#
# 2) COM 포트 직접 지정
# python .\test_person_buttons.py --port COM3
#
# 3) 자동 포트 검색 (설명에 USB 포함된 포트)
# python .\test_person_buttons.py --port AUTO --port-keyword USB
#
# 4) 폴링 주기/타임아웃 조정
# python .\test_person_buttons.py --port COM3 --interval 0.1 --timeout 0.7
#
# 5) 보레이트 지정
# python .\test_person_buttons.py --port COM3 --baud 115200

from __future__ import annotations

import argparse
import time

from kiosk_module.config import config
from kiosk_module.protocol import FrameBuilder, FrameParser, StatusResponse
from kiosk_module.serial_manager import SerialManager


def _resolve_port(port_raw: str, keyword: str) -> str:
    resolved = SerialManager.resolve_port_choice(port_raw, keyword)
    if resolved is None:
        raise SystemExit(
            f"자동 포트 검색 실패: 설명에 {keyword!r} 가 포함된 포트가 없습니다."
        )
    return resolved


def _print_snapshot(status: StatusResponse) -> None:
    print(
        "[상태] "
        f"person={status.person_detected} "
        f"left={status.button_left_status} "
        f"right={status.button_right_status}"
    )


def run_test(port: str, baudrate: int, interval: float, timeout: float) -> None:
    serial_mgr = SerialManager(port=port, baudrate=baudrate, timeout=timeout)
    if not serial_mgr.open():
        raise SystemExit(f"시리얼 포트 연결 실패: {port}")

    print(f"[연결됨] {port} @ {baudrate}bps")
    print("종료하려면 Ctrl+C 를 누르세요.")

    prev_status: StatusResponse | None = None
    req = FrameBuilder.build_status_request_frame()

    try:
        while True:
            raw = serial_mgr.send_and_receive(req, timeout=timeout)
            if raw is None:
                print("[경고] 상태 응답 없음 (타임아웃)")
                time.sleep(interval)
                continue

            status = FrameParser.parse_status_response(raw)
            if status is None:
                print(f"[경고] 상태 파싱 실패: {raw.hex(' ')}")
                time.sleep(interval)
                continue

            if prev_status is None:
                _print_snapshot(status)
            else:
                # 사람 감지 변화 감지
                if prev_status.person_detected == 0 and status.person_detected == 1:
                    print("[EVENT] person_detected")
                elif prev_status.person_detected == 1 and status.person_detected == 0:
                    print("[EVENT] person_not_detected")

                # 버튼 0 -> 1 눌림 엣지 감지
                left_edge = (
                    prev_status.button_left_status == 0
                    and status.button_left_status == 1
                )
                right_edge = (
                    prev_status.button_right_status == 0
                    and status.button_right_status == 1
                )
                if left_edge:
                    print("[EVENT] left_button_pressed")
                if right_edge:
                    print("[EVENT] right_button_pressed")

                if left_edge or right_edge:
                    _print_snapshot(status)

            prev_status = status
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n테스트를 종료합니다.")
    finally:
        serial_mgr.close()
        print("[연결 해제]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="사람 감지 + 좌/우 버튼 감지 테스트"
    )
    parser.add_argument(
        "--port",
        default=config.serial_port,
        help="시리얼 포트 (예: COM3, AUTO). 기본: .env의 SERIAL_PORT",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=config.serial_baudrate,
        help="보레이트 (기본: .env의 SERIAL_BAUDRATE)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="폴링 주기(초, 기본 0.2)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.5,
        help="응답 타임아웃(초, 기본 0.5)",
    )
    parser.add_argument(
        "--port-keyword",
        default=config.serial_port_description_keyword,
        help="AUTO 검색용 포트 설명 키워드 (기본: .env 값)",
    )
    args = parser.parse_args()

    port = _resolve_port(args.port, args.port_keyword)
    run_test(
        port=port,
        baudrate=args.baud,
        interval=args.interval,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
