"""
JDONE Smart Pole — PyQt5: .env 대응 변수 입력 + 시리얼 연결 + 통신 로그

의존성:
    uv sync --group gui

실행:
    uv run python gui_main.py
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Optional

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kiosk_module.config import config
from kiosk_module.kiosk_runner import run_kiosk
from kiosk_module.serial_manager import SerialManager

# ─── 카푸친 모카 컬러셋 ───
CTP_MOCHA = {
    "background": "#1E1E2E",
    "surface0": "#181825",
    "surface1": "#11111B",
    "text": "#CDD6F4",
    "subtext0": "#A6ADC8",
    "overlay0": "#6C7086",
    "lavender": "#B4BEFE",
    "sapphire": "#74C7EC",
    "base": "#1E1E2E",
}

# macOS에는 Segoe UI가 없고, Windows에는 SF Pro가 없음 → 플랫폼별 스택으로 qt.qpa.fonts 경고·지연 방지
if sys.platform == "darwin":
    _UI_FONT = "'Helvetica Neue', 'Arial', sans-serif"
    _MONO_FONT = "'Menlo', 'Monaco', 'Consolas', monospace"
elif sys.platform == "win32":
    _UI_FONT = "'Segoe UI', 'Tahoma', sans-serif"
    _MONO_FONT = "'Consolas', 'Courier New', monospace"
else:
    _UI_FONT = "'Ubuntu', 'DejaVu Sans', 'Arial', sans-serif"
    _MONO_FONT = "'DejaVu Sans Mono', 'Consolas', monospace"

QSS = f"""
    QMainWindow {{ background-color: {CTP_MOCHA['background']}; }}
    QWidget {{ color: {CTP_MOCHA['text']}; font-family: {_UI_FONT}; font-size: 10pt; }}

    QFrame#ControlFrame {{ background-color: {CTP_MOCHA['surface0']}; border-radius: 10px; padding: 10px; }}
    QFrame#LogFrame {{ background-color: {CTP_MOCHA['surface1']}; border-radius: 10px; }}

    QLabel {{ color: {CTP_MOCHA['lavender']}; font-weight: bold; }}
    QLabel#SubTitle {{ color: {CTP_MOCHA['subtext0']}; font-weight: normal; }}

    QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: {CTP_MOCHA['surface1']};
        border: 1px solid {CTP_MOCHA['overlay0']};
        border-radius: 5px; padding: 5px; color: {CTP_MOCHA['text']};
    }}
    QComboBox::drop-down {{ border: 0px; }}
    QComboBox QAbstractItemView {{ background-color: {CTP_MOCHA['surface1']}; selection-background-color: {CTP_MOCHA['overlay0']}; }}

    QPushButton {{
        background-color: {CTP_MOCHA['lavender']}; color: {CTP_MOCHA['base']};
        border-radius: 5px; padding: 10px; font-weight: bold;
    }}
    QPushButton:hover {{ background-color: {CTP_MOCHA['sapphire']}; }}
    QPushButton:pressed {{ background-color: {CTP_MOCHA['overlay0']}; }}
    QPushButton#DisconnectBtn {{ background-color: {CTP_MOCHA['surface1']}; color: {CTP_MOCHA['text']}; border: 1px solid {CTP_MOCHA['overlay0']}; }}
    QPushButton#SecondaryBtn {{
        background-color: {CTP_MOCHA['surface1']}; color: {CTP_MOCHA['text']};
        border: 1px solid {CTP_MOCHA['overlay0']}; font-weight: normal;
    }}

    QGroupBox {{
        color: {CTP_MOCHA['lavender']};
        font-weight: bold;
        border: 1px solid {CTP_MOCHA['overlay0']};
        border-radius: 8px;
        margin-top: 8px;
        padding-top: 8px;
    }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}

    QCheckBox {{ color: {CTP_MOCHA['text']}; font-weight: normal; }}

    QTextEdit {{
        background-color: transparent; border: 0px;
        color: {CTP_MOCHA['text']}; font-family: {_MONO_FONT}; font-size: 9pt;
    }}
"""


class QtLogHandler(logging.Handler):
    """백그라운드 스레드에서 온 로그를 Qt 슬롯으로 넘깁니다."""

    def __init__(self, emit_slot):
        super().__init__()
        self._emit_slot = emit_slot

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._emit_slot(msg)
        except Exception:
            self.handleError(record)


class KioskWorker(QThread):
    """asyncio 이벤트 루프에서 ``run_kiosk`` 실행."""

    log_line = pyqtSignal(str)
    failed = pyqtSignal(str)
    finished_clean = pyqtSignal()

    def __init__(self, port: str, baud: int):
        super().__init__()
        self._port = port
        self._baud = baud
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._controller_ref: dict = {}
        self._log_handler: Optional[QtLogHandler] = None

    def request_stop(self) -> None:
        if self._loop and self._stop_event and not self._stop_event.is_set():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def run(self) -> None:
        root = logging.getLogger()
        self._log_handler = QtLogHandler(self.log_line.emit)
        self._log_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                "%H:%M:%S",
            )
        )
        self._log_handler.setLevel(logging.DEBUG)
        root.addHandler(self._log_handler)
        prev_level = root.level
        root.setLevel(min(prev_level, logging.DEBUG))

        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._stop_event = asyncio.Event()
            self._loop.run_until_complete(
                run_kiosk(
                    self._port,
                    self._baud,
                    stop_event=self._stop_event,
                    controller_ref=self._controller_ref,
                )
            )
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            if self._log_handler is not None:
                root.removeHandler(self._log_handler)
            root.setLevel(prev_level)
            if self._loop is not None:
                self._loop.close()
                self._loop = None
            self.finished_clean.emit()


def _float_spin(v: float, min_v: float, max_v: float, step: float) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(min_v, max_v)
    s.setSingleStep(step)
    s.setDecimals(1)
    s.setValue(float(v))
    return s


class KioskApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JDONE Kiosk — 환경 변수 & 연결")
        self.resize(1040, 640)

        self._worker: Optional[KioskWorker] = None

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(20)

        control_frame = QFrame()
        control_frame.setObjectName("ControlFrame")
        control_layout = QVBoxLayout(control_frame)
        control_layout.setSpacing(12)

        title_label = QLabel("제어 모듈 설정")
        title_label.setStyleSheet("font-size: 14pt;")
        control_layout.addWidget(title_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_inner = QWidget()
        scroll.setWidget(scroll_inner)
        form_root = QVBoxLayout(scroll_inner)

        # ─── 시리얼 ───
        serial_box = QGroupBox("시리얼")
        serial_outer = QVBoxLayout(serial_box)
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("포트:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        port_row.addWidget(self.port_combo, stretch=1)
        self.refresh_ports_btn = QPushButton("목록 새로고침")
        self.refresh_ports_btn.setObjectName("SecondaryBtn")
        self.refresh_ports_btn.setToolTip(
            "키워드가 비어 있으면 모든 시리얼 포트를, 있으면 설명에 키워드가 들어간 포트만 드롭다운에 채웁니다."
        )
        port_row.addWidget(self.refresh_ports_btn)
        serial_outer.addLayout(port_row)

        kw_row = QHBoxLayout()
        kw_row.addWidget(QLabel("포트 설명 필터 (비우면 전체):"))
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("예: USB, CP210, CH340 — 비우면 전체 목록")
        kw_row.addWidget(self.keyword_edit, stretch=1)
        serial_outer.addLayout(kw_row)

        auto_hint = QLabel(
            "위 필터로 좁힌 포트가 드롭다운에 나옵니다. 연결 시 포트가 비어 있거나 AUTO이면 필터에 맞는 첫 번째 장치를 씁니다."
        )
        auto_hint.setObjectName("SubTitle")
        auto_hint.setWordWrap(True)
        serial_outer.addWidget(auto_hint)

        baud_row = QHBoxLayout()
        baud_row.addWidget(QLabel("SERIAL_BAUDRATE:"))
        self.baud_spin = QSpinBox()
        self.baud_spin.setRange(9600, 921600)
        self.baud_spin.setSingleStep(300)
        baud_row.addWidget(self.baud_spin)
        baud_row.addStretch()
        serial_outer.addLayout(baud_row)
        form_root.addWidget(serial_box)

        # ─── .env 필드 (Config와 1:1) ───
        env_box = QGroupBox(
            "환경 변수 (연결 시 config에 반영 · 연결 후 아래에 실제 적용값이 표시됨)"
        )
        env_form = QFormLayout(env_box)
        env_form.setLabelAlignment(Qt.AlignRight)

        self.kiosk_id_edit = QLineEdit()
        env_form.addRow("KIOSK_ID:", self.kiosk_id_edit)

        self.ws_enabled_cb = QCheckBox("WS_ENABLED")
        env_form.addRow(self.ws_enabled_cb)

        self.ws_url_edit = QLineEdit()
        env_form.addRow("WS_URL:", self.ws_url_edit)

        self.ws_reconnect_spin = _float_spin(5.0, 0.5, 600.0, 1.0)
        env_form.addRow("WS_RECONNECT_INTERVAL:", self.ws_reconnect_spin)

        self.status_poll_spin = _float_spin(2.0, 0.2, 60.0, 0.5)
        env_form.addRow("STATUS_POLL_INTERVAL:", self.status_poll_spin)

        self.vacant_idle_spin = _float_spin(20.0, 1.0, 3600.0, 5.0)
        env_form.addRow("VACANT_IDLE_CLOSE_SECONDS:", self.vacant_idle_spin)

        self.auto_open_person_cb = QCheckBox("AUTO_OPEN_DOOR_ON_PERSON")
        env_form.addRow(self.auto_open_person_cb)

        self.input_monitor_cb = QCheckBox("INPUT_MONITOR_ENABLED")
        env_form.addRow(self.input_monitor_cb)

        self.meet_url_edit = QLineEdit()
        env_form.addRow("MEET_WEB_URL:", self.meet_url_edit)

        self.browser_cmd_edit = QLineEdit()
        env_form.addRow("KIOSK_BROWSER_CMD:", self.browser_cmd_edit)

        self.browser_timeout_spin = QSpinBox()
        self.browser_timeout_spin.setRange(1, 86400)
        env_form.addRow("BACKGROUND_BROWSER_TIMEOUT_SECONDS:", self.browser_timeout_spin)

        self.log_level_combo = QComboBox()
        for lv in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.log_level_combo.addItem(lv)
        env_form.addRow("LOG_LEVEL:", self.log_level_combo)

        form_root.addWidget(env_box)
        form_root.addStretch()

        control_layout.addWidget(scroll)

        btn_layout = QHBoxLayout()
        self.connect_btn = QPushButton("연결")
        self.disconnect_btn = QPushButton("끊기")
        self.disconnect_btn.setObjectName("DisconnectBtn")
        self.disconnect_btn.setEnabled(False)
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        control_layout.addLayout(btn_layout)

        main_layout.addWidget(control_frame, stretch=2)

        log_frame = QFrame()
        log_frame.setObjectName("LogFrame")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(10, 10, 10, 10)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("통신 로그"))
        sub = QLabel("로그: 고정폭 9pt")
        sub.setObjectName("SubTitle")
        log_header.addWidget(sub, 0, Qt.AlignRight)
        log_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.append(
            "[INFO] 환경 변수를 입력한 뒤 연결을 누르면 실행 중 config에 반영됩니다."
        )
        log_layout.addWidget(self.log_text)

        main_layout.addWidget(log_frame, stretch=3)

        self.setStyleSheet(QSS)

        self.refresh_ports_btn.clicked.connect(self._populate_ports)
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)

        self._sync_ui_from_config()

    def _sync_ui_from_config(self) -> None:
        """메모리 ``config``에 들어 있는 값(실제 사용·적용 기준)을 위젯에 그대로 표시."""
        self.keyword_edit.setText(config.serial_port_description_keyword)
        self.baud_spin.setValue(int(config.serial_baudrate))
        self._populate_ports(select_device=config.serial_port)
        self.kiosk_id_edit.setText(config.kiosk_id)
        self.ws_enabled_cb.setChecked(config.ws_enabled)
        self.ws_url_edit.setText(config.ws_url)
        self.ws_reconnect_spin.setValue(float(config.ws_reconnect_interval))
        self.status_poll_spin.setValue(float(config.status_poll_interval))
        self.vacant_idle_spin.setValue(float(config.vacant_idle_close_seconds))
        self.auto_open_person_cb.setChecked(config.auto_open_door_on_person)
        self.input_monitor_cb.setChecked(config.input_monitor_enabled)
        self.meet_url_edit.setText(config.meet_web_url)
        self.browser_cmd_edit.setText(config.kiosk_browser_cmd)
        self.browser_timeout_spin.setValue(
            int(config.background_browser_timeout_seconds)
        )
        lv = (config.log_level or "INFO").strip().upper()
        idx = self.log_level_combo.findText(lv, Qt.MatchFixedString)
        self.log_level_combo.setCurrentIndex(max(0, idx))

    def _push_ui_to_config(self) -> None:
        """위젯 값 → 전역 ``config`` (디스크 .env는 수정하지 않음)."""
        config.kiosk_id = self.kiosk_id_edit.text().strip()
        config.serial_port = self._resolved_port()
        config.serial_baudrate = int(self.baud_spin.value())
        config.serial_port_description_keyword = self.keyword_edit.text().strip()
        config.ws_enabled = self.ws_enabled_cb.isChecked()
        config.ws_url = self.ws_url_edit.text().strip() or "ws://localhost:8080/ws"
        config.ws_reconnect_interval = float(self.ws_reconnect_spin.value())
        config.status_poll_interval = float(self.status_poll_spin.value())
        config.vacant_idle_close_seconds = float(self.vacant_idle_spin.value())
        config.auto_open_door_on_person = self.auto_open_person_cb.isChecked()
        config.input_monitor_enabled = self.input_monitor_cb.isChecked()
        config.meet_web_url = self.meet_url_edit.text().strip()
        config.kiosk_browser_cmd = self.browser_cmd_edit.text().strip()
        config.background_browser_timeout_seconds = float(
            self.browser_timeout_spin.value()
        )
        config.log_level = self.log_level_combo.currentText().strip().upper() or "INFO"
        lvl = getattr(logging, config.log_level, logging.INFO)
        logging.getLogger().setLevel(lvl)

    def _append_log(self, text: str) -> None:
        self.log_text.append(text)
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def _populate_ports(self, select_device: Optional[str] = None) -> None:
        if select_device is not None:
            current = select_device
        else:
            current = self.port_combo.currentData()
            if current is None and self.port_combo.currentText():
                current = self.port_combo.currentText().strip()
        kw = self.keyword_edit.text().strip()
        self.port_combo.clear()
        for dev, label in SerialManager.list_port_entries_filtered(kw or None):
            self.port_combo.addItem(label, dev)
        if current:
            self._select_port_if_present(str(current))
        elif self.port_combo.count() == 0:
            self.port_combo.addItem("(포트 없음)", "")

    def _select_port_if_present(self, device: str) -> None:
        for i in range(self.port_combo.count()):
            if self.port_combo.itemData(i) == device:
                self.port_combo.setCurrentIndex(i)
                return
        self.port_combo.setEditText(device)

    def _resolved_port(self) -> str:
        dev = self.port_combo.currentData()
        if dev:
            return str(dev)
        return self.port_combo.currentText().strip()

    def _on_connect(self) -> None:
        raw = self._resolved_port()
        kw = self.keyword_edit.text().strip() or "USB"
        port = SerialManager.resolve_port_choice(raw, kw)
        if port is None:
            QMessageBox.warning(
                self,
                "자동 포트 검색 실패",
                "포트를 비우거나 AUTO로 두면 키워드로 시리얼을 찾습니다.\n"
                f"지금 키워드 {kw!r}에 맞는 포트가 없습니다. 키워드를 바꾸거나 "
                "목록에서 장치를 직접 선택하세요.",
            )
            return
        auto_picked = (raw or "").strip().upper() == "AUTO" or not (raw or "").strip()
        if auto_picked:
            self._select_port_if_present(port)
            self._append_log(f"[INFO] 자동 포트 검색(키워드 {kw!r}) → {port}")
        self._push_ui_to_config()
        self._sync_ui_from_config()
        if self._worker and self._worker.isRunning():
            return

        self._worker = KioskWorker(port, int(config.serial_baudrate))
        self._worker.log_line.connect(self._append_log)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished_clean.connect(self._on_worker_finished)
        self._worker.start()

        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.refresh_ports_btn.setEnabled(False)
        self.keyword_edit.setEnabled(False)
        self.port_combo.setEnabled(False)
        self.baud_spin.setEnabled(False)
        self._append_log(f"[INFO] 연결 시도: {port} @ {config.serial_baudrate}")
        self._append_log(f"[INFO] 적용된 설정: {config}")

    def _on_disconnect(self) -> None:
        if self._worker and self._worker.isRunning():
            self._append_log("[INFO] 연결 종료 요청…")
            self._worker.request_stop()

    def _on_worker_failed(self, msg: str) -> None:
        self._append_log(f"[ERROR] {msg}")
        QMessageBox.critical(self, "오류", msg)

    def _on_worker_finished(self) -> None:
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.refresh_ports_btn.setEnabled(True)
        self.keyword_edit.setEnabled(True)
        self.port_combo.setEnabled(True)
        self.baud_spin.setEnabled(True)
        self._worker = None
        self._sync_ui_from_config()
        self._append_log("[INFO] 통신 스레드 종료")


def main() -> int:
    argv = sys.argv[:]
    if "-platformtheme" not in argv:
        argv = argv + ["-platformtheme", "flat"]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    app = QApplication(argv)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(CTP_MOCHA["background"]))
    palette.setColor(QPalette.WindowText, QColor(CTP_MOCHA["text"]))
    app.setPalette(palette)

    # 터미널에서 Ctrl+C 시 Qt 이벤트 루프가 SIGINT를 삼키는 경우 완화
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    _sig_timer = QTimer()
    _sig_timer.start(200)
    _sig_timer.timeout.connect(lambda: None)

    window = KioskApp()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
