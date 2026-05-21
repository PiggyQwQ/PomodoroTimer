#!/usr/bin/env python3
"""番茄钟 · Desktop Pomodoro Timer (PyQt6)"""

import sys
import json
import os
from datetime import datetime
from collections import defaultdict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTabWidget, QDialog, QSpinBox,
    QFormLayout, QSystemTrayIcon, QMenu, QScrollArea, QFrame,
    QSizePolicy, QDialogButtonBox,
)
from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPen, QColor, QIcon, QPixmap, QAction

try:
    import winsound
    _HAS_SOUND = True
except ImportError:
    _HAS_SOUND = False

# ── Config ───────────────────────────────────────────────────────────────────

DATA_FILE = os.path.expanduser("~/.pomodoro_data.json")
FOCUS, SHORT, LONG = "focus", "short", "long"
MODES = {
    FOCUS: ("专注", "#E74C3C", "focus_min"),
    SHORT: ("短休", "#27AE60", "short_min"),
    LONG:  ("长休", "#2980B9", "long_min"),
}
DEFAULTS = {"focus_min": 25, "short_min": 5, "long_min": 15, "long_after": 4}

# ── Data ─────────────────────────────────────────────────────────────────────

class DataManager:
    def __init__(self):
        self.settings = dict(DEFAULTS)
        self.sessions: list = []
        self._load()

    def _load(self):
        if not os.path.exists(DATA_FILE):
            return
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                d = json.load(f)
            self.settings.update(d.get("settings", {}))
            self.sessions = d.get("sessions", [])
        except Exception:
            pass

    def save(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"settings": self.settings, "sessions": self.sessions},
                      f, ensure_ascii=False, indent=2)

    def add_session(self, task: str, mode: str, minutes: int):
        now = datetime.now()
        self.sessions.append({
            "ts": now.isoformat(timespec="seconds"),
            "date": now.strftime("%Y-%m-%d"),
            "task": task.strip() or "未命名",
            "mode": mode,
            "min": minutes,
        })
        self.save()

    def today_focus_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return sum(1 for s in self.sessions if s["date"] == today and s["mode"] == FOCUS)

    def sessions_by_date(self) -> dict:
        by_date: dict = defaultdict(list)
        for s in self.sessions:
            by_date[s["date"]].append(s)
        return dict(sorted(by_date.items(), reverse=True))

# ── Circular Timer Widget ────────────────────────────────────────────────────

class CircularTimer(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(220, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._progress = 1.0
        self._text = "25:00"
        self._color = QColor("#E74C3C")
        self._bg = QColor("#E8E8E8")

    def set_state(self, progress: float, text: str, color: str):
        self._progress = max(0.0, min(1.0, progress))
        self._text = text
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height()) - 20
        x, y = (self.width() - side) // 2, (self.height() - side) // 2
        pw = max(10, side // 14)

        p.setPen(QPen(self._bg, pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(x, y, side, side)

        if self._progress > 0.001:
            p.setPen(QPen(self._color, pw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(x, y, side, side, 90 * 16, -int(360 * 16 * self._progress))

        p.setPen(QPen(QColor("#2C3E50")))
        p.setFont(QFont("Segoe UI", max(22, side // 6), QFont.Weight.Bold))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)

# ── Timer Tab ────────────────────────────────────────────────────────────────

class TimerWidget(QWidget):
    session_done = pyqtSignal(str, str, int)  # task, mode, minutes

    def __init__(self, data: DataManager):
        super().__init__()
        self.data = data
        self._mode = FOCUS
        self._total_sec = self._remain_sec = 0
        self._running = False
        self._focus_count = data.today_focus_count()

        self._qtimer = QTimer(self)
        self._qtimer.setInterval(1000)
        self._qtimer.timeout.connect(self._tick)

        self._build_ui()
        self._apply_mode(FOCUS)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 8, 20, 16)
        root.setSpacing(10)

        # mode buttons
        row = QHBoxLayout()
        row.setSpacing(8)
        self._mode_btns: dict[str, QPushButton] = {}
        for key, (name, _, _) in MODES.items():
            btn = QPushButton(name)
            btn.setFixedHeight(34)
            btn.setFont(QFont("Segoe UI", 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._apply_mode(k))
            self._mode_btns[key] = btn
            row.addWidget(btn)
        root.addLayout(row)

        # circular timer
        self._circle = CircularTimer()
        root.addWidget(self._circle, alignment=Qt.AlignmentFlag.AlignCenter)

        # task input
        self._task = QLineEdit()
        self._task.setPlaceholderText("输入任务名称...")
        self._task.setFixedHeight(38)
        self._task.setFont(QFont("Segoe UI", 13))
        self._task.setStyleSheet(
            "border: 1.5px solid #DDD; border-radius: 8px; padding: 0 10px; background: white;"
        )
        root.addWidget(self._task)

        # control buttons
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self._btn_start = QPushButton("开始")
        self._btn_start.setFixedHeight(46)
        self._btn_start.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.clicked.connect(self._toggle)

        self._btn_reset = QPushButton("重置")
        self._btn_reset.setFixedHeight(46)
        self._btn_reset.setFont(QFont("Segoe UI", 13))
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.setStyleSheet(
            "background: #ECF0F1; color: #2C3E50; border-radius: 8px;"
        )
        self._btn_reset.clicked.connect(self._reset)

        ctrl.addWidget(self._btn_start, 2)
        ctrl.addWidget(self._btn_reset, 1)
        root.addLayout(ctrl)

        # today counter
        self._lbl_count = QLabel()
        self._lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_count.setFont(QFont("Segoe UI", 12))
        self._lbl_count.setStyleSheet("color: #888;")
        root.addWidget(self._lbl_count)

        self._refresh_count()

    def _apply_mode(self, mode: str):
        if self._running:
            self._qtimer.stop()
            self._running = False
        self._mode = mode
        _, color, setting = MODES[mode]
        self._total_sec = self._remain_sec = self.data.settings[setting] * 60
        self._refresh_buttons()
        self._refresh_circle()
        self._set_start_btn("开始", color)

    def _toggle(self):
        _, color, _ = MODES[self._mode]
        if self._running:
            self._qtimer.stop()
            self._running = False
            self._set_start_btn("继续", color)
        else:
            self._running = True
            self._qtimer.start()
            self._set_start_btn("暂停", color)

    def _reset(self):
        self._apply_mode(self._mode)

    def _tick(self):
        self._remain_sec -= 1
        self._refresh_circle()
        if self._remain_sec <= 0:
            self._qtimer.stop()
            self._running = False
            self._on_complete()

    def _on_complete(self):
        _, _, setting = MODES[self._mode]
        self.session_done.emit(self._task.text(), self._mode, self.data.settings[setting])

        if _HAS_SOUND:
            try:
                for freq, dur in [(523, 150), (659, 150), (784, 300)]:
                    winsound.Beep(freq, dur)
            except Exception:
                pass

        if self._mode == FOCUS:
            self._focus_count += 1
            next_mode = LONG if self._focus_count % self.data.settings["long_after"] == 0 else SHORT
        else:
            next_mode = FOCUS

        self._refresh_count()
        self._apply_mode(next_mode)

    def _refresh_buttons(self):
        for k, btn in self._mode_btns.items():
            _, color, _ = MODES[k]
            if k == self._mode:
                btn.setStyleSheet(
                    f"background: {color}; color: white; border-radius: 8px; font-weight: bold;"
                )
            else:
                btn.setStyleSheet(
                    "background: #ECF0F1; color: #555; border-radius: 8px;"
                )

    def _refresh_circle(self):
        m, s = self._remain_sec // 60, self._remain_sec % 60
        prog = self._remain_sec / self._total_sec if self._total_sec else 1.0
        _, color, _ = MODES[self._mode]
        self._circle.set_state(prog, f"{m:02d}:{s:02d}", color)

    def _refresh_count(self):
        n = self.data.today_focus_count()
        self._focus_count = max(self._focus_count, n)
        self._lbl_count.setText(f"今日专注：{n} 个番茄 🍅")

    def _set_start_btn(self, text: str, color: str):
        self._btn_start.setText(text)
        self._btn_start.setStyleSheet(
            f"background: {color}; color: white; border-radius: 8px;"
        )

    def refresh_settings(self):
        self._apply_mode(self._mode)
        self._refresh_count()

# ── Stats Tab ────────────────────────────────────────────────────────────────

class StatsWidget(QWidget):
    def __init__(self, data: DataManager):
        super().__init__()
        self.data = data

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 12, 20, 20)
        root.setSpacing(8)

        lbl = QLabel("历史记录")
        lbl.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        lbl.setStyleSheet("color: #2C3E50;")
        root.addWidget(lbl)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._content = QWidget()
        self._cl = QVBoxLayout(self._content)
        self._cl.setSpacing(6)
        self._cl.addStretch()
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

    def refresh(self):
        while self._cl.count() > 1:
            item = self._cl.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        by_date = self.data.sessions_by_date()
        if not by_date:
            empty = QLabel("还没有记录，开始第一个番茄吧！")
            empty.setStyleSheet("color: #AAA; font-size: 14px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cl.insertWidget(0, empty)
            return

        idx = 0
        for date_str, sessions in list(by_date.items())[:30]:
            focus_sessions = [s for s in sessions if s["mode"] == FOCUS]
            task_counts: dict = defaultdict(int)
            for s in focus_sessions:
                task_counts[s["task"]] += 1

            hdr = QLabel(f"{date_str}    专注 {len(focus_sessions)} 个番茄")
            hdr.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
            hdr.setStyleSheet("color: #2C3E50; padding: 6px 0 2px 0;")
            self._cl.insertWidget(idx, hdr)
            idx += 1

            for task, count in sorted(task_counts.items(), key=lambda x: -x[1]):
                card = QFrame()
                card.setStyleSheet(
                    "QFrame{background:white;border-radius:8px;border:1px solid #EEE;}"
                )
                cl = QHBoxLayout(card)
                cl.setContentsMargins(12, 8, 12, 8)
                tl = QLabel(task)
                tl.setFont(QFont("Segoe UI", 12))
                tl.setStyleSheet("color:#2C3E50;border:none;")
                nl = QLabel(f"🍅 ×{count}")
                nl.setFont(QFont("Segoe UI", 12))
                nl.setStyleSheet("color:#E74C3C;border:none;")
                cl.addWidget(tl)
                cl.addStretch()
                cl.addWidget(nl)
                self._cl.insertWidget(idx, card)
                idx += 1

# ── Settings Dialog ──────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setFixedWidth(290)
        self.setModal(True)

        form = QFormLayout(self)
        form.setSpacing(12)
        form.setContentsMargins(20, 20, 20, 20)

        def spin(val: int, lo: int, hi: int, suffix: str) -> QSpinBox:
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSuffix(suffix)
            s.setFixedHeight(34)
            return s

        self._focus = spin(settings["focus_min"], 1, 60, " 分钟")
        self._short = spin(settings["short_min"], 1, 30, " 分钟")
        self._long  = spin(settings["long_min"],  1, 60, " 分钟")
        self._after = spin(settings["long_after"], 1, 10, " 个后长休")

        form.addRow("专注时长：", self._focus)
        form.addRow("短休时长：", self._short)
        form.addRow("长休时长：", self._long)
        form.addRow("长休间隔：", self._after)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def get_settings(self) -> dict:
        return {
            "focus_min":  self._focus.value(),
            "short_min":  self._short.value(),
            "long_min":   self._long.value(),
            "long_after": self._after.value(),
        }

# ── Main Window ──────────────────────────────────────────────────────────────

def _make_icon(color: str) -> QIcon:
    px = QPixmap(32, 32)
    px.fill(QColor(color))
    return QIcon(px)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.data = DataManager()
        self.setWindowTitle("番茄钟")
        self.resize(420, 580)
        self.setMinimumSize(380, 520)
        self._setup_ui()
        self._setup_tray()
        self._apply_style()

    def _setup_ui(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.setFont(QFont("Segoe UI", 13))

        self._timer = TimerWidget(self.data)
        self._timer.session_done.connect(self._on_session_done)
        tabs.addTab(self._timer, "计时器")

        self._stats = StatsWidget(self.data)
        tabs.addTab(self._stats, "统计")
        tabs.currentChanged.connect(lambda i: self._stats.refresh() if i == 1 else None)

        act = QAction("⚙ 设置", self)
        act.triggered.connect(self._open_settings)
        self.menuBar().addAction(act)

        self.setCentralWidget(tabs)

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(_make_icon("#E74C3C"), self)
        menu = QMenu()
        menu.addAction("显示窗口", self._show_window)
        menu.addSeparator()
        menu.addAction("退出", QApplication.instance().quit)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip("番茄钟")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def _apply_style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #FAFAFA; font-family: 'Segoe UI'; }
            QTabWidget::pane { border: none; }
            QTabBar::tab {
                padding: 8px 22px; font-size: 13px; color: #999;
                border-bottom: 2px solid transparent;
            }
            QTabBar::tab:selected { color: #2C3E50; border-bottom-color: #E74C3C; }
            QMenuBar { background: #FAFAFA; }
            QMenuBar::item { padding: 4px 12px; }
            QMenuBar::item:selected { background: #ECF0F1; border-radius: 4px; }
            QScrollBar:vertical { width: 6px; background: transparent; }
            QScrollBar::handle:vertical { background: #CCC; border-radius: 3px; }
        """)

    def _on_session_done(self, task: str, mode: str, minutes: int):
        self.data.add_session(task, mode, minutes)
        self._timer._refresh_count()
        if mode == FOCUS:
            self._tray.showMessage("番茄钟", "专注完成！好好休息一下 ☕",
                                   QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            self._tray.showMessage("番茄钟", "休息结束，继续专注！",
                                   QSystemTrayIcon.MessageIcon.Information, 3000)

    def _open_settings(self):
        dlg = SettingsDialog(self.data.settings, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.data.settings.update(dlg.get_settings())
            self.data.save()
            self._timer.refresh_settings()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage("番茄钟", "已最小化到系统托盘，右键图标可退出",
                               QSystemTrayIcon.MessageIcon.Information, 2000)

# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("番茄钟")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
