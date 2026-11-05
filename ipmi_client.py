"""Client of the fixed-command local thermal controller."""
import json
import socket


def request(action='status', **fields):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(35)
        client.connect('/run/gigabyte-fan-control/control.sock')
        client.sendall(json.dumps({'action': action, **fields}).encode() + b'\n')
        data = bytearray()
        while b'\n' not in data:
            chunk = client.recv(8192)
            if not chunk or len(data) + len(chunk) > 100000:
                raise RuntimeError('控制服务响应中断')
            data.extend(chunk)
    reply = json.loads(data.split(b'\n', 1)[0])
    if not reply['ok']:
        raise RuntimeError(reply['error'])
    return reply['value']
