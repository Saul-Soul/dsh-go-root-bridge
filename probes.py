#!/usr/bin/env python3
"""Root bridge 探测脚本：输出 JSON 权限报告。所有路径相对插件目录。"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SOCK = os.path.join(BASE, '.rootbridge.sock')
START = os.path.join(BASE, 'daemon', 'start.sh')

out = {}


def read_uid():
    try:
        for line in open('/proc/self/status'):
            if line.startswith('Uid'):
                return int(line.split()[1])
    except OSError:
        pass
    return None


out['uid'] = read_uid()

# ① stat 钩子探针：stat("/system/bin/su") 被内核改写到 ksud（/data 文件系统），
#    设备号与 /system 不同 ⇒ 钩子生效且本 uid 在内核授权名单中。
try:
    su = os.stat('/system/bin/su')
    sh = os.stat('/system/bin/sh')
    out['stat_hook'] = bool(su.st_dev != sh.st_dev)
    out['ksud_dev'] = hex(su.st_dev)
    out['ksud_size'] = su.st_size
except OSError as e:
    out['stat_hook'] = False
    out['stat_err'] = e.errno

# ② reboot 超级调用通道（arm64 SYS_reboot=142），子进程隔离
mini = r'''
import ctypes, json
libc = ctypes.CDLL(None, use_errno=True)
buf = ctypes.create_string_buffer(b"/tmp/x\x00", 32)
ctypes.set_errno(0)
libc.syscall(142, 0xDEADBEEF, 0xCAFEBABE, 0, buf)
print(json.dumps({"reboot_errno": ctypes.get_errno(),
                  "buf_int": int.from_bytes(buf.raw[:4], "little")}))
'''
try:
    r = subprocess.run([sys.executable, '-c', mini], capture_output=True,
                       text=True, timeout=10)
    if r.returncode == 0:
        m = json.loads(r.stdout.strip().splitlines()[-1])
        out['reboot_errno'] = m['reboot_errno']
        v = m['buf_int']
        driver = v if (0 <= v < 1024 and m['buf_int'] != 0) else None
    else:
        out['reboot_errno'] = 'crash(%d)' % r.returncode
        driver = None
except Exception as e:
    out['reboot_errno'] = 'err:%s' % type(e).__name__
    driver = None

# ③ driver fd 扫描（独立于上面的写入结果）
if driver is None:
    try:
        for n in os.listdir('/proc/self/fd'):
            try:
                if 'ksu' in os.readlink('/proc/self/fd/' + n):
                    driver = int(n)
                    break
            except OSError:
                pass
    except OSError:
        pass
out['driver_fd'] = driver

# ④ 尝试 GRANT_ROOT 提权（同样在子进程里做，避免崩溃影响）
if driver is not None:
    mini2 = r'''
import ctypes, json, os
libc = ctypes.CDLL(None, use_errno=True)
fd = int(sys.argv[1])
ret = libc.ioctl(fd, 0x4B01, 0)
uid = None
for line in open('/proc/self/status'):
    if line.startswith('Uid'):
        uid = int(line.split()[1])
print(json.dumps({"ret": ret, "uid": uid}))
'''.replace('sys.argv', '__import__("sys").argv')
    try:
        r = subprocess.run([sys.executable, '-c', mini2, str(driver)],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            m = json.loads(r.stdout.strip().splitlines()[-1])
            out['grant_ret'] = m['ret']
            out['uid_after'] = m['uid']
            out['root'] = m['uid'] == 0
            if out['root']:
                try:
                    out['host_procs'] = len([p for p in os.listdir('/proc') if p.isdigit()])
                except OSError:
                    pass
        else:
            out['grant_err'] = 'crash(%d)' % r.returncode
            out['root'] = False
    except Exception as e:
        out['grant_err'] = type(e).__name__
        out['root'] = False
else:
    out['root'] = False

# ⑤ Root 桥状态；不在线则自动拉起监督器（脱离进程组常驻）
def bridge_online():
    import socket as _s
    c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM)
    c.settimeout(3)
    c.connect(SOCK)
    c.sendall(b'{"op":"exec","cmd":"id -u"}\n')
    buf = b''
    while b'\n' not in buf:
        chunk = c.recv(4096)
        if not chunk:
            break
        buf += chunk
    c.close()
    resp = json.loads(buf.decode('utf-8', 'replace'))
    return resp.get('ok'), (resp.get('out') or '').strip()


try:
    ok, uid_out = bridge_online()
    out['bridge'] = 'online' if ok else 'stale'
    out['bridge_uid'] = uid_out
except Exception as e:
    out['bridge'] = 'offline'
    out['bridge_err'] = type(e).__name__
    # 自愈：拉起监督器（setsid 脱离，不随本进程退出）
    try:
        subprocess.Popen(['/bin/sh', START],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        import time
        for _ in range(20):  # 最多等 40s（含提权重试）
            time.sleep(2)
            try:
                ok, uid_out = bridge_online()
                if ok:
                    out['bridge'] = 'online'
                    out['bridge_uid'] = uid_out
                    out.pop('bridge_err', None)
                    break
            except Exception:
                pass
    except Exception as e2:
        out['bridge_err'] = 'respawn:' + type(e2).__name__

print(json.dumps(out))
