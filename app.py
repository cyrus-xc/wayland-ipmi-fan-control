"""Tray curve editor for the local IPMI thermal controller."""
import copy
import json
import os
import subprocess
import signal
import queue
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, QThread, Signal, QLockFile, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSystemTrayIcon, QMenu, QMessageBox)
from core import STATE, DEFAULT, save_json, validate
from ipmi_client import request


class Worker(QThread):
    """Read controller state and apply curves without blocking the GUI."""
    update = Signal(dict)

    def __init__(self):
        super().__init__()
        self.commands = queue.Queue()

    def send(self, kind, payload=None):
        self.commands.put((kind, payload))

    def run(self):
        next_sample = 0
        while True:
            try:
                kind, payload = self.commands.get(timeout=0.2)
                if kind == 'quit':
                    return
                try:
                    self.update.emit(request(kind, **(payload or {})))
                except Exception as error:
                    self.update.emit({'message': f'控制服务操作失败：{error}', 'error': str(error)})
            except queue.Empty:
                pass
            if time.monotonic() >= next_sample:
                try:
                    self.update.emit(request())
                except Exception as error:
                    self.update.emit({'message': f'IPMI 控制服务不可达：{error}', 'error': str(error)})
                next_sample = time.monotonic() + 3


class Curve(QWidget):
    """Drag constrained curve points in temperature/PWM coordinates."""
    changed = Signal()
    def __init__(self, points):
        super().__init__()
        self.points = copy.deepcopy(points)
        self.selected = None
        self.live = None
        self.setMinimumSize(540, 280)
        self.setMouseTracking(True)
        self.setToolTip('拖动圆点调整温度和占空比；末点固定 100%，不高于 90°C')

    def area(self):
        return QRectF(48, 22, self.width() - 72, self.height() - 63)

    def position(self, temperature, duty):
        r = self.area()
        return QPointF(r.left() + temperature / 110 * r.width(), r.bottom() - duty / 100 * r.height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor('#121b28'))
        p.setPen(QColor('#263549'))
        for duty in (0, 25, 50, 75, 100):
            a, b = self.position(0, duty), self.position(110, duty)
            p.drawLine(a, b)
            p.setPen(QColor('#94a3b8'))
            p.drawText(QPointF(8, a.y() + 4), str(duty))
            p.setPen(QColor('#263549'))
        for temp in (0, 20, 40, 60, 80, 100):
            a = self.position(temp, 0)
            p.setPen(QColor('#94a3b8'))
            p.drawText(QPointF(a.x() - 7, a.y() + 20), str(temp))
        p.drawText(QPointF(self.width() - 48, self.height() - 5), '°C')
        p.drawText(QPointF(8, 14), 'PWM %')
        if self.live is not None:
            p.setPen(QPen(QColor('#f4ba62'), 1, Qt.PenStyle.DashLine))
            p.drawLine(self.position(self.live, 0), self.position(self.live, 100))
        p.setPen(QPen(QColor('#52d6bb'), 3))
        points = [[0, self.points[0][1]]] + self.points + [[110, 100]]
        for left, right in zip(points, points[1:]):
            p.drawLine(self.position(*left), self.position(*right))
        for temp, duty in self.points:
            pos = self.position(temp, duty)
            p.setBrush(QColor('#52d6bb'))
            p.drawEllipse(pos, 6, 6)
            p.setPen(QColor('#e2e8f0'))
            p.drawText(QPointF(pos.x() - 24, pos.y() - 12), f'{temp}° / {duty}%')
            p.setPen(QPen(QColor('#52d6bb'), 3))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            distances = [(self.position(*point) - event.position()).manhattanLength() for point in self.points]
            index = min(range(len(distances)), key=distances.__getitem__)
            self.selected = index if distances[index] < 24 else None

    def mouseMoveEvent(self, event):
        if self.selected is None:
            return
        i, r = self.selected, self.area()
        t = round((event.position().x() - r.left()) / r.width() * 110)
        d = round((r.bottom() - event.position().y()) / r.height() * 100)
        lo = self.points[i - 1] if i else [-1, 30]
        hi = self.points[i + 1] if i + 1 < len(self.points) else [91, 100]
        self.points[i] = [max(lo[0] + 1, min(hi[0] - 1, t)), 100 if i == len(self.points) - 1 else max(lo[1], min(hi[1], d))]
        self.changed.emit()
        self.update()

    def mouseReleaseEvent(self, event):
        self.selected = None


class Window(QWidget):
    """Single floating tool window; closing hides it while the tray remains."""
    def __init__(self):
        super().__init__()
        self.exiting = False
        self.setWindowTitle('风扇控制')
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(700, 650)
        self.config = copy.deepcopy(DEFAULT)
        config_error = None
        if (STATE / 'config.json').exists():
            try:
                self.config = json.loads((STATE / 'config.json').read_text())
                validate(self.config)
            except Exception as error:
                self.config = copy.deepcopy(DEFAULT)
                config_error = str(error)
        self.setStyleSheet('QWidget { background:#182231; color:#e2e8f0; font-size:13px; } QPushButton { background:#2b4056; padding:8px 14px; border-radius:5px; } QPushButton:disabled {color:#64748b;} ')
        layout = QVBoxLayout(self)
        title = QLabel('CPU / GPU 最高温度 → 风扇曲线')
        title.setStyleSheet('font-size:20px;font-weight:600;')
        layout.addWidget(title)
        self.temperatures = QLabel('正在读取温度…')
        layout.addWidget(self.temperatures)
        self.curve = Curve(self.config['points'])
        layout.addWidget(self.curve)
        self.dirty = QLabel('控制全部 CPU / SYS 风扇；拖动温度点后点击保存并应用。')
        self.curve.changed.connect(lambda: self.dirty.setText('曲线有未保存的修改'))
        layout.addWidget(self.dirty)
        buttons = QHBoxLayout()
        save = QPushButton('保存并应用')
        save.clicked.connect(self.apply)
        buttons.addWidget(save)
        auto = QPushButton('恢复主板自动调速')
        auto.clicked.connect(lambda: self.worker.send('auto'))
        buttons.addWidget(auto)
        layout.addLayout(buttons)
        self.status = QLabel('正在连接本机 IPMI 调速服务…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.rpm = QLabel('正在读取实际风扇转速…')
        self.rpm.setWordWrap(True)
        layout.addWidget(self.rpm)
        note = QLabel('全部风扇共用最高温度曲线。最高档使用实测正常的 99% 硬件值。\n后台服务开机启动；退出托盘或关闭窗口后仍会继续调速。')
        note.setStyleSheet('color:#a3b3c6;font-size:12px;')
        note.setWordWrap(True)
        layout.addWidget(note)
        icon = QIcon(str(Path(__file__).with_name('fan-control.svg')))
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip('IPMI 全部风扇控制')
        menu = QMenu()
        menu.addAction('打开风扇控制', self.open)
        menu.addSeparator()
        menu.addAction('恢复主板自动调速', lambda: self.worker.send('auto'))
        menu.addAction('退出托盘（后台继续调速）', self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()
        self.initial_curve = True
        self.worker = Worker()
        self.worker.update.connect(self.refresh)
        self.worker.finished.connect(self.finish_exit)
        self.worker.start()
        if config_error:
            QMessageBox.warning(self, '配置无效', f'已显示默认曲线，原文件未覆盖：{config_error}')
        if '--tray' not in sys.argv or not QSystemTrayIcon.isSystemTrayAvailable():
            self.show()

    def settings(self):
        config = {'points': copy.deepcopy(self.curve.points)}
        validate(config)
        return config

    def save(self):
        try:
            config = self.settings()
            save_json(STATE / 'config.json', config)
            self.dirty.setText('曲线已保存')
            return config
        except Exception as error:
            QMessageBox.warning(self, '无法保存', str(error))
            return None

    def apply(self):
        config = self.save()
        if config is not None:
            self.status.setText('正在通过本机 IPMI 应用曲线…')
            self.worker.send('apply', {'points': config['points']})

    def refresh(self, state):
        if self.initial_curve and 'points' in state:
            self.curve.points = copy.deepcopy(state['points'])
            self.initial_curve = False
            self.curve.update()
        self.status.setText(state['message'])
        self.status.setStyleSheet('color:#f4ba62;' if state.get('error') else 'color:#52d6bb;')
        if 'cpu' in state:
            highest = max(state['cpu'], state['gpu'])
            duty = state.get('duty')
            mode = f'IPMI 占空比 {duty}%' if state.get('enabled') and duty is not None else '主板自动调速'
            self.temperatures.setText(f'CPU {state["cpu"]:.1f}°C    GPU {state["gpu"]:.1f}°C    最高 {highest:.1f}°C    {mode}')
            self.temperatures.setToolTip('\n'.join(f'{name}: {temp:.1f}°C' for name, temp in state['sensors']))
            self.curve.live = highest
            self.curve.update()
            self.tray.setToolTip(f'风扇控制 · {highest:.1f}°C · {mode}')
        if state.get('rpm'):
            self.rpm.setText('实际风扇转速：\n' + state['rpm'])
        if state.get('rpm_error'):
            self.rpm.setText('实际转速读取失败：' + state['rpm_error'])

    def open(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def quit(self):
        self.status.setText('正在退出…')
        self.worker.send('quit')

    def finish_exit(self):
        self.exiting = True
        self.tray.hide()
        QApplication.instance().quit()

    def showEvent(self, event):
        super().showEvent(event)
        if os.environ.get('HYPRLAND_INSTANCE_SIGNATURE') and os.environ.get('QT_QPA_PLATFORM') != 'offscreen':
            QTimer.singleShot(300, self.float_window)

    def float_window(self):
        try:
            clients = json.loads(subprocess.check_output(['hyprctl', 'clients', '-j'], timeout=2))
            for client in clients:
                if client.get('pid') == os.getpid() and not client.get('floating'):
                    address = json.dumps('address:' + client['address'])
                    commands = [f'hl.dsp.window.float({{window={address}, action="enable"}})', f'hl.dsp.window.resize({{window={address}, x=700, y=650}})']
                    for command in commands:
                        result = subprocess.run(['hyprctl', 'dispatch', command], timeout=2, check=True, capture_output=True, text=True)
                        if result.stdout.strip() != 'ok':
                            raise ValueError(result.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.dirty.setText(f'窗口浮动设置失败，可用 Hyprland 浮动快捷键：{error}')

    def closeEvent(self, event):
        if self.exiting:
            event.accept()
        elif QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            event.ignore()
            self.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('gigabyte-fan-control')
    app.setWindowIcon(QIcon(str(Path(__file__).with_name('fan-control.svg'))))
    app.setDesktopFileName('gigabyte-fan-control')
    app.setQuitOnLastWindowClosed(False)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = QLockFile(str(STATE / 'instance.lock'))
    if not lock.tryLock(100):
        pid, _, _ = lock.getLockInfo()
        try:
            if pid <= 0:
                raise ProcessLookupError('无法识别运行实例')
            os.kill(pid, signal.SIGUSR1)
        except OSError as error:
            QMessageBox.warning(None, '风扇控制', f'无法打开运行中的窗口：{error}')
        return 0
    window = Window()
    signal.signal(signal.SIGTERM, lambda *_: window.quit())
    signal.signal(signal.SIGINT, lambda *_: window.quit())
    signal.signal(signal.SIGUSR1, lambda *_: window.open())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(500)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
