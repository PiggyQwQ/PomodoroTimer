#!/usr/bin/env python3
"""番茄钟 · Desktop Pomodoro Timer (PyQt6)

UI aesthetic — "宣纸朱砂" (rice paper & vermilion): a warm paper ground with
subtle radial depth, ink-toned text, and vermilion as the dominant accent,
cohesive with the 汉仪心海行楷 calligraphy display font.
"""

import sys
import json
import os
import math
from datetime import datetime
from collections import defaultdict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QTabWidget, QDialog, QSpinBox,
    QFormLayout, QSystemTrayIcon, QMenu, QScrollArea, QFrame,
    QSizePolicy, QDialogButtonBox,
)
from PyQt6.QtCore import (
    QTimer, Qt, pyqtSignal, QRectF, QPointF, QPropertyAnimation, QEasingCurve,
)
from PyQt6.QtGui import (
    QFont, QFontDatabase, QPainter, QPen, QColor, QIcon, QPixmap, QAction,
)

try:
    import winsound
    _HAS_SOUND = True
except ImportError:
    _HAS_SOUND = False

# ── Palette ──────────────────────────────────────────────────────────────────
# "宣纸朱砂" — warm rice-paper ground, vermilion focus accent, ink-toned text.

PAPER       = "#F1E8D6"   # warm rice-paper ground
PAPER_LIGHT = "#FBF5E7"   # raised surfaces / cards / inputs
PAPER_SUNK  = "#E3D6BA"   # inset — the timer track
INK         = "#2C2620"   # primary text
INK_SOFT    = "#8A7C64"   # secondary text
LINE        = "#D6C6A4"   # hairline borders
VERMILION   = "#C4452C"   # 朱砂 — focus / primary accent
JADE        = "#5B7A55"   # 短休
INDIGO      = "#3E5871"   # 长休


def _darken(hex_color: str, amount: int = 116) -> str:
    """Return a darker shade of hex_color, for pressed/hover states."""
    return QColor(hex_color).darker(amount).name()

# ── Config ───────────────────────────────────────────────────────────────────

DATA_FILE = os.path.expanduser("~/.pomodoro_data.json")
FOCUS, SHORT, LONG = "focus", "short", "long"
MODES = {
    FOCUS: ("专注", VERMILION, "focus_min"),
    SHORT: ("短休", JADE,      "short_min"),
    LONG:  ("长休", INDIGO,    "long_min"),
}
DEFAULTS = {"focus_min": 25, "short_min": 5, "long_min": 15, "long_after": 4}

# ── Fonts ────────────────────────────────────────────────────────────────────

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UI_FONT = "Segoe UI"
BODY_FONT = "Cambria"      # refined serif for body text — pairs with the
                           # calligraphy display font and the paper aesthetic
DIGIT_FONT = "Consolas"    # monospace timer readout — no width jitter per tick
TITLE_FONT_FILE = "汉仪心海行楷W.ttf"   # HanYi calligraphy font, used for titles
# Resolved at startup by load_title_font(). Falls back to BODY_FONT when the
# calligraphy font is neither bundled in fonts/ nor installed on the system.
TITLE_FONT = BODY_FONT


def load_title_font() -> str:
    """Resolve the title font family, preferring a bundled copy then the system.

    汉仪心海行楷 is a commercial typeface, so the .ttf is intentionally not
    committed to the repo (see fonts/README.md). The app degrades gracefully
    to BODY_FONT. Must be called after a QApplication exists.
    """
    global TITLE_FONT
    bundled = os.path.join(APP_DIR, "fonts", TITLE_FONT_FILE)
    if os.path.exists(bundled):
        fid = QFontDatabase.addApplicationFont(bundled)
        families = QFontDatabase.applicationFontFamilies(fid)
        if families:
            TITLE_FONT = families[0]
            return TITLE_FONT
    for family in QFontDatabase.families():
        if family.startswith("汉仪心海行楷"):
            TITLE_FONT = family
            return TITLE_FONT
    return TITLE_FONT

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
        self.setMinimumSize(248, 248)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._progress = 1.0
        self._text = "25:00"
        self._label = "专注"
        self._color = QColor(VERMILION)

    def set_state(self, progress: float, text: str, color: str, label: str):
        self._progress = max(0.0, min(1.0, progress))
        self._text = text
        self._color = QColor(color)
        self._label = label
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height()) - 26
        cx, cy = self.width() / 2, self.height() / 2
        x, y = cx - side / 2, cy - side / 2
        pw = max(9, side / 18)
        ring = QRectF(x, y, side, side)

        # inset track
        p.setPen(QPen(QColor(PAPER_SUNK), pw,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(ring, 0, 360 * 16)

        # progress arc, sweeping clockwise from the top
        if self._progress > 0.001:
            p.setPen(QPen(self._color, pw,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, -int(360 * 16 * self._progress))

            # a filled dot marking the leading end of the arc
            end_deg = 90 - 360 * self._progress
            end_rad = math.radians(end_deg)
            r = side / 2
            dot = QPointF(cx + r * math.cos(end_rad), cy - r * math.sin(end_rad))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._color)
            p.drawEllipse(dot, pw * 0.62, pw * 0.62)

        # countdown digits — monospace, no jitter as digits change
        p.setPen(QColor(INK))
        p.setFont(QFont(DIGIT_FONT, max(28, int(side / 6.4))))
        p.drawText(QRectF(0, cy - side * 0.5, self.width(), side * 0.86),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                   self._text)

        # mode label beneath the digits
        p.setPen(QColor(INK_SOFT))
        p.setFont(QFont(BODY_FONT, max(11, int(side / 22))))
        p.drawText(QRectF(0, cy + side * 0.13, self.width(), side * 0.22),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   self._label)

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
        root.setContentsMargins(28, 16, 28, 22)
        root.setSpacing(14)

        # app title + decorative seal-rule
        title = QLabel("番茄钟")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(QFont(TITLE_FONT, 38))
        title.setStyleSheet(f"color: {VERMILION};")
        root.addWidget(title)

        rule = QFrame()
        rule.setFixedSize(48, 2)
        rule.setStyleSheet(f"background: {VERMILION}; border: none;")
        root.addWidget(rule, alignment=Qt.AlignmentFlag.AlignHCenter)

        # mode pills
        row = QHBoxLayout()
        row.setSpacing(10)
        self._mode_btns: dict[str, QPushButton] = {}
        for key, (name, _, _) in MODES.items():
            btn = QPushButton(name)
            btn.setFixedHeight(36)
            btn.setFont(QFont(BODY_FONT, 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._apply_mode(k))
            self._mode_btns[key] = btn
            row.addWidget(btn)
        root.addLayout(row)

        # circular timer
        self._circle = CircularTimer()
        root.addWidget(self._circle, 1, alignment=Qt.AlignmentFlag.AlignCenter)

        # task input
        self._task = QLineEdit()
        self._task.setPlaceholderText("此刻专注于……")
        self._task.setFixedHeight(40)
        self._task.setFont(QFont(BODY_FONT, 13))
        self._task.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._task)

        # control buttons
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)
        self._btn_start = QPushButton("开始")
        self._btn_start.setFixedHeight(48)
        self._btn_start.setFont(QFont(BODY_FONT, 15, QFont.Weight.Bold))
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.clicked.connect(self._toggle)

        self._btn_reset = QPushButton("重置")
        self._btn_reset.setFixedHeight(48)
        self._btn_reset.setFont(QFont(BODY_FONT, 13))
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {INK_SOFT};"
            f" border: 1.3px solid {LINE}; border-radius: 12px; }}"
            f"QPushButton:hover {{ color: {INK}; border-color: {INK_SOFT}; }}"
        )
        self._btn_reset.clicked.connect(self._reset)

        ctrl.addWidget(self._btn_start, 2)
        ctrl.addWidget(self._btn_reset, 1)
        root.addLayout(ctrl)

        # today counter
        self._lbl_count = QLabel()
        self._lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_count.setFont(QFont(BODY_FONT, 12))
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
                    f"QPushButton {{ background: {color}; color: {PAPER_LIGHT};"
                    f" border: none; border-radius: 18px; font-weight: 600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {INK_SOFT};"
                    f" border: 1.3px solid {LINE}; border-radius: 18px; }}"
                    f"QPushButton:hover {{ color: {INK}; border-color: {INK_SOFT}; }}"
                )

    def _refresh_circle(self):
        m, s = self._remain_sec // 60, self._remain_sec % 60
        prog = self._remain_sec / self._total_sec if self._total_sec else 1.0
        name, color, _ = MODES[self._mode]
        self._circle.set_state(prog, f"{m:02d}:{s:02d}", color, name)

    def _refresh_count(self):
        n = self.data.today_focus_count()
        self._focus_count = max(self._focus_count, n)
        self._lbl_count.setText(
            f'<span style="color:{INK_SOFT};">今日已收获 </span>'
            f'<span style="color:{VERMILION}; font-size:16px;">{n}</span>'
            f'<span style="color:{INK_SOFT};"> 个番茄 🍅</span>'
        )

    def _set_start_btn(self, text: str, color: str):
        self._btn_start.setText(text)
        self._btn_start.setStyleSheet(
            f"QPushButton {{ background: {color}; color: {PAPER_LIGHT};"
            f" border: none; border-radius: 12px; }}"
            f"QPushButton:hover {{ background: {_darken(color)}; }}"
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
        root.setContentsMargins(28, 18, 28, 22)
        root.setSpacing(10)

        lbl = QLabel("历史记录")
        lbl.setFont(QFont(TITLE_FONT, 26))
        lbl.setStyleSheet(f"color: {INK};")
        root.addWidget(lbl)

        rule = QFrame()
        rule.setFixedHeight(2)
        rule.setStyleSheet(f"background: {LINE}; border: none;")
        root.addWidget(rule)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._content = QWidget()
        self._content.setStyleSheet("background: transparent;")
        self._cl = QVBoxLayout(self._content)
        self._cl.setSpacing(7)
        self._cl.addStretch()
        self._scroll.setWidget(self._content)
        root.addWidget(self._scroll)

    def refresh(self):
        while self._cl.count() > 1:
            item = self._cl.takeAt(0)
            if item.widget():
                item.widget().setParent(None)   # drop from view immediately
                item.widget().deleteLater()

        by_date = self.data.sessions_by_date()
        if not by_date:
            empty = QLabel("还没有记录，种下第一个番茄吧 🍅")
            empty.setFont(QFont(BODY_FONT, 13))
            empty.setStyleSheet(f"color: {INK_SOFT};")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cl.insertWidget(0, empty)
            return

        idx = 0
        for date_str, sessions in list(by_date.items())[:30]:
            focus_sessions = [s for s in sessions if s["mode"] == FOCUS]
            task_counts: dict = defaultdict(int)
            for s in focus_sessions:
                task_counts[s["task"]] += 1

            hdr = QLabel(
                f'<span style="color:{INK};">{date_str}</span>'
                f'<span style="color:{INK_SOFT};">　专注 </span>'
                f'<span style="color:{VERMILION};">{len(focus_sessions)}</span>'
                f'<span style="color:{INK_SOFT};"> 个</span>'
            )
            hdr.setFont(QFont(BODY_FONT, 13, QFont.Weight.Bold))
            hdr.setStyleSheet("padding: 10px 0 2px 2px;")
            self._cl.insertWidget(idx, hdr)
            idx += 1

            for task, count in sorted(task_counts.items(), key=lambda x: -x[1]):
                card = QFrame()
                card.setStyleSheet(
                    f"QFrame {{ background: {PAPER_LIGHT};"
                    f" border: 1px solid {LINE}; border-radius: 10px; }}"
                )
                cl = QHBoxLayout(card)
                cl.setContentsMargins(14, 10, 14, 10)
                tl = QLabel(task)
                tl.setFont(QFont(BODY_FONT, 12))
                tl.setStyleSheet(f"color:{INK}; border:none; background:transparent;")
                nl = QLabel(f"🍅 ×{count}")
                nl.setFont(QFont(BODY_FONT, 12))
                nl.setStyleSheet(f"color:{VERMILION}; border:none; background:transparent;")
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
        self.setFixedWidth(300)
        self.setModal(True)
        self.setStyleSheet(f"""
            QDialog {{ background: {PAPER}; }}
            QLabel {{ color: {INK}; font-family: '{BODY_FONT}'; font-size: 13px; }}
            QSpinBox {{
                background: {PAPER_LIGHT}; border: 1.3px solid {LINE};
                border-radius: 8px; padding: 3px 8px; color: {INK};
                font-family: '{BODY_FONT}'; font-size: 13px;
            }}
            QSpinBox:focus {{ border-color: {VERMILION}; }}
            QSpinBox::up-button, QSpinBox::down-button {{
                width: 16px; border: none; background: transparent;
            }}
            QPushButton {{
                background: {PAPER_LIGHT}; border: 1.3px solid {LINE};
                border-radius: 8px; padding: 6px 18px; color: {INK};
                font-family: '{BODY_FONT}'; font-size: 13px;
            }}
            QPushButton:hover {{ border-color: {INK_SOFT}; }}
            QPushButton:default {{
                background: {VERMILION}; color: {PAPER_LIGHT};
                border-color: {VERMILION};
            }}
            QPushButton:default:hover {{ background: {_darken(VERMILION)}; }}
        """)

        form = QFormLayout(self)
        form.setSpacing(14)
        form.setContentsMargins(24, 24, 24, 22)

        def spin(val: int, lo: int, hi: int, suffix: str) -> QSpinBox:
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(val)
            s.setSuffix(suffix)
            s.setFixedHeight(36)
            s.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
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

def _tomato_icon() -> QIcon:
    """A small painted tomato — vermilion body with a jade calyx."""
    px = QPixmap(64, 64)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(VERMILION))
    p.drawEllipse(QRectF(7, 20, 50, 40))
    p.setBrush(QColor(JADE))
    p.drawEllipse(QRectF(20, 8, 14, 12))
    p.drawEllipse(QRectF(30, 8, 14, 12))
    p.end()
    return QIcon(px)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.data = DataManager()
        self.setWindowTitle("番茄钟")
        self.setWindowIcon(_tomato_icon())
        self.resize(430, 600)
        self.setMinimumSize(390, 540)
        self._setup_ui()
        self._setup_tray()
        self._apply_style()
        self._play_intro()

    def _setup_ui(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

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
        self._tray = QSystemTrayIcon(_tomato_icon(), self)
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

    def _play_intro(self):
        """A single, well-orchestrated page-load: a soft fade-in on first show."""
        self.setWindowOpacity(0.0)
        self._intro = QPropertyAnimation(self, b"windowOpacity")
        self._intro.setDuration(460)
        self._intro.setStartValue(0.0)
        self._intro.setEndValue(1.0)
        self._intro.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._intro.start()

    def _apply_style(self):
        self.setStyleSheet(f"""
            QMainWindow {{
                background: qradialgradient(cx:0.5, cy:0.28, radius:1.2,
                    fx:0.5, fy:0.22, stop:0 #F8F1DF, stop:1 #E6D8BA);
            }}
            TimerWidget, StatsWidget {{ background: transparent; }}
            QTabWidget::pane {{ border: none; background: transparent; }}
            QTabBar {{ qproperty-drawBase: 0; background: transparent; }}
            QTabBar::tab {{
                background: transparent;
                padding: 9px 26px; margin-right: 2px;
                color: {INK_SOFT};
                border: none; border-bottom: 2px solid transparent;
                font-family: '{BODY_FONT}'; font-size: 14px;
            }}
            QTabBar::tab:selected {{
                color: {VERMILION}; border-bottom: 2px solid {VERMILION};
            }}
            QTabBar::tab:hover:!selected {{ color: {INK}; }}
            QMenuBar {{ background: transparent; }}
            QMenuBar::item {{
                padding: 5px 14px; color: {INK_SOFT};
                font-family: '{BODY_FONT}'; background: transparent;
            }}
            QMenuBar::item:selected {{
                background: {PAPER_LIGHT}; border-radius: 6px; color: {VERMILION};
            }}
            QMenu {{
                background: {PAPER_LIGHT}; border: 1px solid {LINE}; padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px; color: {INK}; font-family: '{BODY_FONT}';
            }}
            QMenu::item:selected {{
                background: {PAPER}; color: {VERMILION}; border-radius: 4px;
            }}
            QLineEdit {{
                background: {PAPER_LIGHT}; border: 1.4px solid {LINE};
                border-radius: 10px; padding: 0 12px; color: {INK};
                selection-background-color: {VERMILION};
                selection-color: {PAPER_LIGHT};
            }}
            QLineEdit:focus {{ border: 1.4px solid {VERMILION}; }}
            QScrollBar:vertical {{
                width: 7px; background: transparent; margin: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {LINE}; border-radius: 3px; min-height: 32px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {INK_SOFT}; }}
            QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
            QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
            QToolTip {{
                background: {INK}; color: {PAPER};
                border: none; padding: 5px 8px;
            }}
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
    load_title_font()
    app.setFont(QFont(BODY_FONT, 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
