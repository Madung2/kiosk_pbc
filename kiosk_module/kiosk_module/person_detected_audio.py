from __future__ import annotations

import ctypes
import logging
import threading
import time
import sys
from pathlib import Path

from .config import config, runtime_base_dir

logger = logging.getLogger(__name__)


def _resolve_mp3_path() -> Path:
    raw = (config.person_detected_mp3_path or "").strip()
    if not raw:
        raw = "person_detected.mp3"
    path = Path(raw)
    if not path.is_absolute():
        path = runtime_base_dir() / path
    return path


def _ensure_person_detected_mp3() -> Path | None:
    mp3_path = _resolve_mp3_path()
    if mp3_path.exists():
        return mp3_path
    if not config.person_detected_tts_autogen:
        logger.warning(f"사람 감지 음원 파일 없음(자동 생성 비활성): {mp3_path}")
        return None
    if not config.person_detected_tts_text:
        logger.warning(
            f"사람 감지 음원 파일 없음 + TTS 텍스트 미설정: {mp3_path}"
        )
        return None
    try:
        from gtts import gTTS
    except Exception:
        logger.exception(
            "gTTS를 가져오지 못했습니다. `pip install gTTS` 후 다시 실행하세요."
        )
        return None

    try:
        mp3_path.parent.mkdir(parents=True, exist_ok=True)
        gTTS(
            text=config.person_detected_tts_text,
            lang=config.person_detected_tts_lang or "ko",
        ).save(str(mp3_path))
        logger.info(f"사람 감지 음원 생성 완료: {mp3_path}")
        return mp3_path
    except Exception:
        logger.exception(f"사람 감지 음원 생성 실패: {mp3_path}")
        return None


def _mci_send(command: str) -> int:
    return ctypes.windll.winmm.mciSendStringW(command, None, 0, None)


def _mci_query(command: str, size: int = 128) -> tuple[int, str]:
    buf = ctypes.create_unicode_buffer(size)
    rc = ctypes.windll.winmm.mciSendStringW(command, buf, size, None)
    return rc, buf.value


def _play_mp3_windows(path: Path) -> None:
    if sys.platform != "win32":
        logger.warning(f"mp3 재생은 Windows에서만 지원됩니다: {path}")
        return

    alias = f"pd_audio_{time.monotonic_ns()}"
    escaped = str(path).replace('"', '\\"')
    try:
        open_rc = _mci_send(f'open "{escaped}" type mpegvideo alias {alias}')
        if open_rc != 0:
            logger.warning(f"mp3 open 실패(code={open_rc}): {path}")
            return
        play_rc = _mci_send(f"play {alias} from 0")
        if play_rc != 0:
            logger.warning(f"mp3 play 실패(code={play_rc}): {path}")
            return
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            rc, mode = _mci_query(f"status {alias} mode")
            if rc != 0 or mode.lower() != "playing":
                break
            time.sleep(0.1)
    finally:
        _mci_send(f"close {alias}")


def play_person_detected_audio_async() -> bool:
    mp3_path = _ensure_person_detected_mp3()
    if mp3_path is None:
        return False

    t = threading.Thread(
        target=_play_mp3_windows,
        args=(mp3_path,),
        daemon=True,
    )
    t.start()
    return True
