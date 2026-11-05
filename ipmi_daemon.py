"""Root-owned local IPMI thermal controller for this MZ72-HB0."""
import copy
import json
import os
from pathlib import Path
import signal
import socket
import struct
import time
import subprocess
from core import DEFAULT, validate, duty_at, sample_temperatures, save_json

CONFIG = Path('/var/lib/gigabyte-fan-control/config.json')
SOCKET = '/run/gigabyte-fan-control/control.sock'
ALLOWED_UID = int(os.environ.get('FAN_CONTROL_UID', '1000'))


def ipmi(*args):
    result = subprocess.run(['/usr/bin/ipmitool', '-I', 'open', *map(str, args)], capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'ipmitool failed')
    return result.stdout.strip()


def set_duty(duty):
    """Apply the same duty to every CPU and SYS fan sensor on this board."""
    if type(duty) is not int or not 30 <= duty <= 100:
        raise ValueError('占空比必须为 30–100 的整数')
    # Firmware 12.50.09 drops CPU fan RPM at 0xff; 0xfc reaches 6600 RPM.
    pwm = min(252, (duty * 255 + 50) // 100)
    for fan_id in range(0xb8, 0xbe):
        ipmi('raw', '0x2e', '0x10', '0x0a', '0x3c', '0x00', '64', '1', hex(pwm), hex(fan_id))


def restore_auto():
    ipmi('raw', '0x2e', '0x10', '0x0a', '0x3c', '0x00', '64', '0', '0', '0')


class Controller:
    def __init__(self):
        self.config = copy.deepcopy(DEFAULT)
        self.config['enabled'] = True
        if CONFIG.exists():
            self.config = json.loads(CONFIG.read_text())
        validate(self.config)
        if type(self.config.get('enabled')) is not bool:
            raise ValueError('enabled 必须为布尔值')
        self.duty = None
        self.last_down = 0
        self.expected = set()
        self.state = {'message': '正在读取温度', 'enabled': self.config['enabled'], 'duty': None, 'rpm': '', 'error': None}
        self.rpm_time = 0
        self.refresh_time = 0

    def command(self, request):
        action = request.get('action')
        if action == 'apply':
            config = {'points': request['points'], 'enabled': True}
            validate(config)
            reading = sample_temperatures()
            self.expected = {name for name, _ in reading['sensors']}
            save_json(CONFIG, config)
            self.config = config
            self.tick()
        elif action == 'auto':
            restore_auto()
            config = {**self.config, 'enabled': False}
            save_json(CONFIG, config)
            self.config = config
            self.duty = None
            self.state.update(enabled=False, duty=None, error=None, message='已发送恢复主板自动调速命令')
        elif action != 'status':
            raise ValueError('不支持的操作')
        return {**self.state, 'points': self.config['points']}

    def tick(self):
        now = time.monotonic()
        try:
            reading = sample_temperatures()
            names = {name for name, _ in reading['sensors']}
            if self.expected and not self.expected <= names:
                raise ValueError('CPU/GPU 传感器消失')
            self.expected = names
            self.state.update(reading, sampled_at=time.time())
            if self.config['enabled']:
                target = duty_at(self.config['points'], max(reading['cpu'], reading['gpu']))
                if self.duty is not None and target < self.duty:
                    target = self.duty if now - self.last_down < 10 else max(target, self.duty - 5)
                if target != self.duty or now - self.refresh_time >= 5:
                    set_duty(target)
                    if self.duty is not None and target < self.duty:
                        self.last_down = now
                    self.duty = target
                    self.refresh_time = now
                self.state.update(message='IPMI 全部风扇曲线调速运行中', duty=self.duty, enabled=True, error=None)
            else:
                self.state.update(enabled=False, message='主板自动调速；曲线控制已停止')
        except Exception as error:
            message = f'温度/控制异常：{error}'
            if self.config['enabled']:
                try:
                    set_duty(100)
                    self.duty = 100
                    message += '；已发送全部风扇全速命令'
                except Exception as failure:
                    message += f'；全速命令也失败：{failure}'
            self.state.update(message=message, error=message, duty=self.duty, enabled=self.config['enabled'])
            print(message, flush=True)
        if now - self.rpm_time >= 10:
            try:
                self.state.update(rpm=ipmi('sdr', 'type', 'fan'), rpm_at=time.time(), rpm_error=None)
            except Exception as error:
                self.state.update(rpm_error=str(error))
            self.rpm_time = now


def main():
    controller = Controller()
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    Path(SOCKET).unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET)
    os.chown(SOCKET, ALLOWED_UID, -1)
    os.chmod(SOCKET, 0o600)
    server.listen(4)
    server.settimeout(0.2)
    next_tick = 0
    try:
        while running:
            if time.monotonic() >= next_tick:
                controller.tick()
                next_tick = time.monotonic() + 5
            try:
                client, _ = server.accept()
            except socket.timeout:
                continue
            with client:
                try:
                    _, uid, _ = struct.unpack('3i', client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                    if uid not in (0, ALLOWED_UID):
                        raise PermissionError('非授权用户')
                    client.settimeout(2)
                    data = bytearray()
                    while b'\n' not in data:
                        chunk = client.recv(4096)
                        if not chunk or len(data) + len(chunk) > 16384:
                            raise ValueError('无效请求')
                        data.extend(chunk)
                    request = json.loads(data.split(b'\n', 1)[0])
                    response = {'ok': True, 'value': controller.command(request)}
                except Exception as error:
                    response = {'ok': False, 'error': str(error)}
                try:
                    client.sendall(json.dumps(response, ensure_ascii=False).encode() + b'\n')
                except OSError:
                    pass  # A disconnected GUI does not interrupt thermal control.
    finally:
        if controller.config['enabled']:
            try:
                set_duty(100)
            except Exception as error:
                print(f'退出全速保护失败：{error}', flush=True)
        server.close()
        Path(SOCKET).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
