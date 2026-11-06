#!/usr/bin/env python3
"""Root 桥守护进程：提权后监听 unix socket，提供全盘文件操作。
宿主路径用 "host:" 前缀（内部经 /proc/1/root/ 穿透 proot 视图）。
协议：每连接一个 JSON 请求 → 一个 JSON 响应。"""
import base64
import ctypes
import json
import os
import socket
import subprocess
import sys

SOCK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.rootbridge.sock')
HOST_PREFIX = '/proc/1/root'


def read_uid():
    for line in open('/proc/self/status'):
        if line.startswith('Uid'):
            return int(line.split()[1])


def escalate():
    """在本进程内：reboot 取 driver fd → ioctl GRANT_ROOT，重试直到 uid==0。
    （proot 对 reboot 的拦截是间歇性的，多试几次；crash 由外层监督重启）"""
    libc = ctypes.CDLL(None, use_errno=True)
    for attempt in range(10):
        try:
            buf = ctypes.create_string_buffer(b"/tmp/x\x00", 32)
            libc.syscall(142, 0xDEADBEEF, 0xCAFEBABE, 0, buf)
            v = int.from_bytes(buf.raw[:4], 'little')
            if 0 <= v < 1024:
                libc.ioctl(v, 0x4B01, 0)  # KSU_IOCTL_GRANT_ROOT
                if read_uid() == 0:
                    return True
        except Exception:
            pass
    return read_uid() == 0


def real(path):
    """提权后视图切到宿主侧，容器路径会失效：优先直取，失败则经 /proc/1/root 穿透。"""
    if path.startswith('host:'):
        return HOST_PREFIX + path[5:]
    p = path
    if not os.path.lexists(p):
        alt = HOST_PREFIX + p
        if os.path.lexists(alt):
            return alt
    return p


def op_list(path):
    p = real(path)
    items = []
    for name in sorted(os.listdir(p))[:500]:
        full = os.path.join(p, name)
        try:
            st = os.lstat(full)
            items.append({'name': name, 'dir': os.path.isdir(full) and not os.path.islink(full),
                          'size': st.st_size})
        except OSError:
            items.append({'name': name, 'err': True})
    return {'ok': True, 'items': items}


def op_read(path, max_bytes=4 * 1024 * 1024):
    with open(real(path), 'rb') as f:
        data = f.read(max_bytes)
    return {'ok': True, 'b64': base64.b64encode(data).decode(), 'size': len(data),
            'trunc': len(data) == max_bytes}


def op_write(path, b64):
    p = real(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(base64.b64decode(b64))
    return {'ok': True}


def op_stat(path):
    st = os.stat(real(path))
    return {'ok': True, 'size': st.st_size, 'mode': oct(st.st_mode), 'mtime': st.st_mtime}


def op_mkdir(path):
    os.makedirs(real(path), exist_ok=True)
    return {'ok': True}


def op_rm(path):
    p = real(path)
    if os.path.isdir(p) and not os.path.islink(p):
        import shutil
        shutil.rmtree(p)
    else:
        os.remove(p)
    return {'ok': True}


def op_exec(cmd, timeout=30):
    r = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=timeout)
    return {'ok': True, 'out': (r.stdout + r.stderr)[:200000]}


OPS = {'list': lambda a: op_list(a['path']),
       'read': lambda a: op_read(a['path'], a.get('max', 4 * 1024 * 1024)),
       'write': lambda a: op_write(a['path'], a['b64']),
       'stat': lambda a: op_stat(a['path']),
       'mkdir': lambda a: op_mkdir(a['path']),
       'rm': lambda a: op_rm(a['path']),
       'exec': lambda a: op_exec(a['cmd'], a.get('timeout', 30))}


def handle(req):
    op = req.get('op')
    fn = OPS.get(op)
    if fn is None:
        return {'ok': False, 'err': 'unknown op'}
    try:
        return fn(req)
    except Exception as e:
        return {'ok': False, 'err': '%s: %s' % (type(e).__name__, e)}


def main():
    # 先在容器视图里建好 socket（提权后 mount 视图会切换，但已打开的 fd 依然有效）
    try:
        os.remove(SOCK)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(8)
    if not escalate():
        print(json.dumps({'bridge': 'escalate-failed'}), flush=True)
        sys.exit(1)
    print(json.dumps({'bridge': 'online', 'uid': read_uid(), 'sock': SOCK}), flush=True)
    sys.stdout.flush()
    while True:
        conn, _ = srv.accept()
        try:
            conn.settimeout(120)
            buf = b''
            while b'\n' not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 32 * 1024 * 1024:
                    break
            if buf.strip():
                resp = handle(json.loads(buf.decode('utf-8', 'replace')))
            else:
                resp = {'ok': False, 'err': 'empty'}
            conn.sendall((json.dumps(resp, ensure_ascii=False) + '\n').encode())
        except Exception as e:
            try:
                conn.sendall((json.dumps({'ok': False, 'err': str(e)[:200]}) + '\n').encode())
            except OSError:
                pass
        finally:
            conn.close()


if __name__ == '__main__':
    main()
