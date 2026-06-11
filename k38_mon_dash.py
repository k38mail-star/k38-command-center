#!/usr/bin/env python3
"""K38 Command Center v0.5.0 — 全设备监控 · Prometheus /metrics · 多设备 · 网络 · 告警"""
import subprocess, os, re, time, threading, json, signal
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime
from collections import deque

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

PASS = "169401"
E2 = "jager-dgx-2@192.168.3.45"
D1 = "jager-dgx@192.168.3.55"
D1_SSH = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {D1}"
SSH_BASE = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {E2}"

M38 = "jagerm3uitra@192.168.3.29"
M4 = "jagerstudiom4max@192.168.3.46"
M38_SSH = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {M38}"
M4_SSH = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {M4}"
HISTORY = 150
history = {k: deque(maxlen=HISTORY) for k in [
    "mac_cpu","mac_mem","d1_gpu","d1_mem","d1_temp","d2_gpu","d2_mem","d2_temp",
    "m38_cpu","m38_mem","m4_cpu","m4_mem"
]}
_hidden_downloads = set()
_ssubcribers = []
_lock = threading.Lock()
_dl_bytes_prev = 0
_dl_bytes_ts = 0

def s(c, to=4):
    try:
        p = subprocess.Popen(c, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
        out, _ = p.communicate(timeout=to)
        return out.decode()
    except subprocess.TimeoutExpired:
        try: os.killpg(p.pid, signal.SIGKILL)
        except: pass
        return ""
    except:
        return ""

def collect():
    data = {"ts": time.time(), "devices": {}}

    # ── 十六万 Mac ──
    try:
        cpu = s("top -l 1 -n 0 | grep CPU", to=2)
        cpu_pct = float(re.search(r'(\d+\.?\d*)%', cpu).group(1)) if cpu and '%' in cpu else 0.0
        mem_raw = s("vm_stat", to=2)
        pagesize = 16384
        free = active = wired = 0
        for line in mem_raw.split("\n"):
            if "page size" in line:
                pagesize = int(re.search(r'(\d+)', line).group(1))
            elif "Pages free" in line:
                free = int(re.search(r'(\d+)', line).group(1))
            elif "Pages active" in line:
                active = int(re.search(r'(\d+)', line).group(1))
            elif "Pages wired" in line:
                wired = int(re.search(r'(\d+)', line).group(1))
        mu = (active + wired) * pagesize / 1e9
        mt = (free + active + wired) * pagesize / 1e9
        disk_raw = s("df -m /", to=2)
        dl = disk_raw.strip().split("\n")[-1].split()
        dsk_pct = dl[4] if len(dl) > 4 else "?"
        dsk_used = f"{int(dl[2])//1024}G" if len(dl) > 2 else "?"
        dsk_total = f"{int(dl[1])//1024}G" if len(dl) > 1 else "?"
        gpu_info = s("system_profiler SPDisplaysDataType 2>/dev/null | grep -E 'Chip|VRAM|Resolution' | head -4", to=3)
        mac_gpu = gpu_info.strip() if gpu_info else "M3 Ultra 512GB"
        data["devices"]["mac"] = {
            "cpu": cpu_pct, "mem_used": mu, "mem_total": mt,
            "disk_pct": dsk_pct,
            "disk_used": dsk_used,
            "disk_total": dsk_total,
            "gpu_info": mac_gpu, "online": True
        }
        with _lock:
            history["mac_cpu"].append(cpu_pct)
            history["mac_mem"].append(mu/mt*100 if mt else 0)
    except:
        data["devices"]["mac"] = {"online": False}

    # ── 大傻 ──
    try:
        g1 = s(f"""{D1_SSH} 'nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm --format=csv,noheader,nounits'""", to=4)
        p1 = g1.strip().split(",") if g1.strip() else ["0"]*7
        ml_raw = s(f"""{D1_SSH} 'free -h' """, to=3).strip()
        ml = re.search(r'Mem:\s+(\S+)\s+(\S+)', ml_raw)
        ml = [ml.group(1), ml.group(2)] if ml else ["?","?"]
        ld_raw = s(f"""{D1_SSH} 'uptime' """, to=3).strip()
        ld = re.search(r'load average[s]?:\s*(.+)', ld_raw)
        ld = ld.group(1) if ld else "-"
        ud1 = s(f"""{D1_SSH} 'uptime -p' """, to=3).strip()
        d1 = {
            "gpu": p1[0].strip(), "gmem": p1[1].strip(), "mem_u": p1[2].strip(),
            "mem_t": p1[3].strip(), "temp": p1[4].strip(), "power": p1[5].strip(),
            "clock": p1[6].strip(), "sys_used": ml[0] if ml else "?", "sys_total": ml[1] if len(ml)>1 else "?",
            "load": ld, "uptime": ud1, "online": True
        }
        data["devices"]["d1"] = d1
        with _lock:
            history["d1_gpu"].append(float(d1["gpu"]) if d1["gpu"].isdigit() else 0)
            history["d1_mem"].append(float(d1["gmem"]) if d1["gmem"].isdigit() else 0)
            history["d1_temp"].append(float(d1["temp"]) if d1["temp"].isdigit() else 0)
    except: data["devices"]["d1"] = {"online": False}

    # ── 二傻 ──
    try:
        g2 = s(f"""{SSH_BASE} 'nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm --format=csv,noheader,nounits'""", to=4)
        p2 = g2.strip().split(",") if g2.strip() else ["0"]*7
        ml2_raw = s(f"""{SSH_BASE} 'free -h' """, to=3).strip()
        ml2 = re.search(r"Mem:\s+(\S+)\s+(\S+)", ml2_raw)
        ml2 = [ml2.group(1), ml2.group(2)] if ml2 else ["?","?"]
        ud2 = s(f"{SSH_BASE} 'uptime -p'", to=3).strip()
        d2 = {
            "gpu": p2[0].strip(), "gmem": p2[1].strip(), "mem_u": p2[2].strip(),
            "mem_t": p2[3].strip(), "temp": p2[4].strip(), "power": p2[5].strip(),
            "clock": p2[6].strip(), "sys_used": ml2[0] if ml2 else "?", "sys_total": ml2[1] if len(ml2)>1 else "?",
            "uptime": ud2, "online": True
        }
        data["devices"]["d2"] = d2
        with _lock:
            history["d2_gpu"].append(float(d2["gpu"]) if d2["gpu"].isdigit() else 0)
            history["d2_mem"].append(float(d2["gmem"]) if d2["gmem"].isdigit() else 0)
            history["d2_temp"].append(float(d2["temp"]) if d2["temp"].isdigit() else 0)
    except: data["devices"]["d2"] = {"online": False}

    # ── 三万八 ──
    try:
        raw38 = s(f"{M38_SSH} 'top -l 1 -n 0 | grep CPU'", to=4)
        cpu38_pct = float(re.search(r'(\d+\.?\d*)%', raw38).group(1)) if raw38 and '%' in raw38 else 0.0
        mem38_raw = s(f"{M38_SSH} 'vm_stat'", to=4)
        pgsz38 = 16384; f38 = a38 = w38 = 0
        for line in mem38_raw.split("\n"):
            if "page size" in line: pgsz38 = int(re.search(r'(\d+)', line).group(1))
            elif "Pages free" in line: f38 = int(re.search(r'(\d+)', line).group(1))
            elif "Pages active" in line: a38 = int(re.search(r'(\d+)', line).group(1))
            elif "Pages wired" in line: w38 = int(re.search(r'(\d+)', line).group(1))
        mu38 = (a38 + w38) * pgsz38 / 1e9
        mt38 = (f38 + a38 + w38) * pgsz38 / 1e9
        data["devices"]["m38"] = {"cpu": cpu38_pct, "mem_used": mu38, "mem_total": mt38, "online": True}
        with _lock:
            history["m38_cpu"].append(cpu38_pct)
            history["m38_mem"].append(mu38/mt38*100 if mt38 else 0)
    except:
        data["devices"]["m38"] = {"online": False}

    # ── 小四 ──
    try:
        raw4 = s(f"{M4_SSH} 'top -l 1 -n 0 | grep CPU'", to=4)
        cpu4_pct = float(re.search(r'(\d+\.?\d*)%', raw4).group(1)) if raw4 and '%' in raw4 else 0.0
        mem4_raw = s(f"{M4_SSH} 'vm_stat'", to=4)
        pgsz4 = 16384; f4 = a4 = w4 = 0
        for line in mem4_raw.split("\n"):
            if "page size" in line: pgsz4 = int(re.search(r'(\d+)', line).group(1))
            elif "Pages free" in line: f4 = int(re.search(r'(\d+)', line).group(1))
            elif "Pages active" in line: a4 = int(re.search(r'(\d+)', line).group(1))
            elif "Pages wired" in line: w4 = int(re.search(r'(\d+)', line).group(1))
        mu4 = (a4 + w4) * pgsz4 / 1e9
        mt4 = (f4 + a4 + w4) * pgsz4 / 1e9
        data["devices"]["m4"] = {"cpu": cpu4_pct, "mem_used": mu4, "mem_total": mt4, "online": True}
        with _lock:
            history["m4_cpu"].append(cpu4_pct)
            history["m4_mem"].append(mu4/mt4*100 if mt4 else 0)
    except:
        data["devices"]["m4"] = {"online": False}

    # ── 200G ──
    try:
        po = s(f"""{D1_SSH} 'ping -c1 -W1 192.168.100.102 2>/dev/null | grep time=' """, to=3)
        m = re.search(r"time=([\d.]+)", po)
        data["link200"] = {"latency": float(m.group(1)) if m else None, "up": bool(m)}
    except: data["link200"] = {"latency": None, "up": False}

    # ── 公网ping（全设备轮询，取最低延迟）──
    ping_hosts = [("baidu_ms","baidu.com"),("ytb_ms","youtube.com"),("github_ms","github.com"),("google_ms","google.com"),("yahoo_hk_ms","yahoo.com.hk")]
    ping_sources = [
        ("本地", "", "curl"),
        ("大傻", D1_SSH, "ping"),
        ("二傻", SSH_BASE, "ping"),
        ("三万八", M38_SSH, "curl"),
        ("小四", M4_SSH, "curl"),
    ]
    curl_urls = {"baidu_ms":"baidu.com","ytb_ms":"youtube.com","github_ms":"github.com","google_ms":"google.com","yahoo_hk_ms":"yahoo.com.hk"}
    ping_urls = {"baidu_ms":"baidu.com","ytb_ms":"youtube.com","github_ms":"github.com","google_ms":"google.com","yahoo_hk_ms":"yahoo.com.hk"}
    pub = {}
    pub_all = {}
    for sname, ssh_prefix, method in ping_sources:
        urls = curl_urls if method == "curl" else ping_urls
        for tag, host in urls.items():
            try:
                if method == "curl":
                    if ssh_prefix:
                        cmd = f"{ssh_prefix} 'curl -o /dev/null -s -w \"%{{time_total}}\" https://{host} --connect-timeout 3 --max-time 4' "
                    else:
                        cmd = f"curl -o /dev/null -s -w \"%{{time_total}}\" https://{host} --connect-timeout 3 --max-time 4"
                    o = s(cmd, to=5)
                    v = float(o.strip()) if o.strip() else None
                else:
                    if ssh_prefix:
                        o = s(f"{ssh_prefix} 'ping -c1 -W2 {host} 2>/dev/null | grep time=' ", to=4)
                    else:
                        o = s(f"ping -c1 -W2 {host} 2>/dev/null | grep time=' ", to=4)
                    m = re.search(r"time=([\d.]+)", o)
                    v = float(m.group(1)) if m else None
                if v is not None:
                    ms = round(v * 1000 if method == "curl" else v, 1)
                    if tag not in pub_all:
                        pub_all[tag] = {}
                    pub_all[tag][sname] = ms
            except:
                pass
    # 取各目标最低值
    for tag, details in pub_all.items():
        vals = [v for v in details.values() if v is not None]
        if vals:
            pub[tag] = min(vals)
    data["public_ping"] = pub
    data["public_ping_all"] = pub_all

    # ── 下载：日志模式 ──
    global _dl_bytes_prev, _dl_bytes_ts
    dls = {}
    total_bytes = float(0)
    for src, tag in [(s(f"""{D1_SSH} 'cat /tmp/k38_wan_dl.log 2>/dev/null; echo "---"; cat /tmp/k38_t5_download.log 2>/dev/null' """, to=2), None)]:
        for line in src.split("\n"):
            line = line.strip()
            if not line or line == "---": continue
            if "Downloading" in line and "%" in line:
                m_fn=re.search(r'\[([^\]]+)\]',line); m_pct=re.search(r'(\d+)%',line)
                m_spd=re.search(r'([\d.]+)\s*(MB/s|KB/s)',line)
                m_sz=re.search(r'([\d.]+)([GMK])/([\d.]+)([GMK])',line)
                if m_fn and m_pct:
                    fn=m_fn.group(1).strip()
                    if not any(fn.endswith(e) for e in ('.safetensors','.pth','.bin','.tar')): continue
                    pct=int(m_pct.group(1))
                    spd=float(m_spd.group(1)) if m_spd else 0
                    spd_mb=spd/1024 if m_spd and m_spd.group(2)=="KB/s" else spd
                    if m_sz:
                        cur=float(m_sz.group(1))
                        cur_b=cur*(1024**3) if m_sz.group(2)=="G" else cur*(1024**2) if m_sz.group(2)=="M" else cur*1024
                        total_bytes += cur_b
                    dls[fn]={"pct":pct,"speed":spd_mb,"done":pct>=100,"tag":"HF"}
            elif "DONE" in line:
                m_fn=re.search(r'DONE:\s*(\S+)',line)
                if m_fn and m_fn.group(1):
                    fn=os.path.basename(m_fn.group(1))
                    if any(fn.endswith(e) for e in ('.safetensors','.pth','.bin')):
                        dls[fn]={"pct":100,"speed":0,"done":True,"tag":"HF"}
    now = time.time()
    if total_bytes > 0 and _dl_bytes_prev > 0:
        delta_bytes = total_bytes - _dl_bytes_prev
        delta_sec = now - _dl_bytes_ts if _dl_bytes_ts else 2
        real_speed = delta_bytes / delta_sec / 1024**2 if delta_sec > 0 else sum(d["speed"] for d in dls.values() if not d["done"])
    else:
        real_speed = 0
    _dl_bytes_prev = total_bytes
    _dl_bytes_ts = float(now)

    # ── 下载追踪：k38-dltrack统一JSON（主）+ 进程检测（备）──
    _dl_cache = getattr(collect, '_dl_cache', {})
    active_procs = []
    now_s = time.time()
    
    # 方案A：读dltrack JSON（精确进度）
    dl_json = s(f"{D1_SSH} 'cat /tmp/k38_dl_progress.json 2>/dev/null || echo null' ", to=3).strip()
    dl_report = json.loads(dl_json) if dl_json and dl_json != "null" else None
    
    if dl_report:
        # v2格式：active_files + active_procs
        active_files = dl_report.get("active_files") or []
        active_procs_raw = dl_report.get("active_procs") or []
        
        for f in active_files:
            fn = f.get("file", "...")
            ext = os.path.splitext(fn)[1].lower()
            tag_map = {'.sh': 'shell', '.safetensors': 'hf', '.pth': 'hf', '.bin': 'hf',
                       '.tar': 'tar', '.mp4': 'video', '.pt': 'model', '.git': 'git'}
            tag = tag_map.get(ext, ext.lstrip('.') if ext else 'file')
            active_procs.append({
                "tag": tag,
                "file": fn,
                "pct": f.get("pct", 0),
                "speed_mb": f.get("speed_mb", 0),
                "size_mb": f.get("size_mb", 0),
                "status": f.get("status", "downloading"),
                "is_run": f.get("status") in ("downloading", "finishing"),
            })
        for p in active_procs_raw:
            # 合并，避免重复
            key = p.get("tag","") + ":" + p.get("file","")
            if not any(a["file"] == p.get("file") for a in active_procs):
                active_procs.append({
                    "tag": p.get("tag", "?"),
                    "file": p.get("file", "..."),
                    "pct": 50,
                    "speed_mb": 0,
                    "size_mb": 0,
                    "status": "detected",
                    "cmd": p.get("cmd", ""),
                    "is_run": True,
                })
        
        # 缓存
        collect._dl_cache = {
            a['file']: {'pct': a['pct'], 'ts': now_s, 'file': a['file']}
            for a in active_procs
        }
        data["dltrack_ok"] = True
    else:
        data["dltrack_ok"] = False
        # 方案B：dltrack无数据时，直接进程检测（备选）
        dl_pats = [('wget', r'wget\s'), ('pip', r'pip\s+(install|download)\s'),
                   ('git', r'git\s+clone\s'), ('docker', r'docker\s+pull\s'),
                   ('hf', r'huggingface-cli\s')]
        raw_procs = s(f"{D1_SSH} 'ps aux 2>/dev/null | grep -E \"wget|pip (install|download)|git clone|docker pull|huggingface-cli\" | grep -v grep | grep -v curl | grep -v sshpass | grep -v k38_mon_dash | head -10' ", to=3).strip()
        seen = {}
        for line in raw_procs.strip().split("\n"):
            line = line.strip()
            if not line or line.find("curl") >= 0: continue
            parts = line.split(None, 10)
            cmd = parts[-1] if len(parts) > 10 else (parts[-1] if parts else "")
            if not cmd: continue
            for label, pat in dl_pats:
                if re.search(pat, cmd):
                    entry = {"tag": label, "file": "...", "pct": 0, "speed_mb": 0, "status": "detected", "cmd": cmd[:100], "is_run": True}
                    if label == 'git':
                        m_repo = re.search(r'https?://[^\s]+', cmd)
                        repo = m_repo.group(0).rstrip("'").rstrip('"') if m_repo else ""
                        entry["file"] = repo.rstrip('.git').split('/')[-1] if repo else "cloning"
                        entry["pct"] = 30
                    elif label == 'pip':
                        m_pkg = re.search(r'(?:install|download)\s+(\S+)', cmd)
                        entry["file"] = m_pkg.group(1) if m_pkg else "package"
                    else:
                        m_url = re.search(r'https?://[^\s]+', cmd)
                        if m_url:
                            url = m_url.group(0).rstrip("'").rstrip('"')
                            fname = url.split("/")[-1].split("?")[0]
                            if fname: entry["file"] = fname
                    key = label + ":" + entry["file"]
                    if key not in seen:
                        seen[key] = entry
                    break
        active_procs = list(seen.values())
        # 用缓存补全
        for k, v in _dl_cache.items():
            if now_s - v['ts'] < 60:  # 缓存60秒
                active_procs.append(v)
    
    data["active_downloads"] = active_procs
    
    # ── 任务备注 ──
    notes = []
    if active_procs:
        labels = sorted(set(p["tag"] for p in active_procs))
        notes.append(f"📥 {', '.join(labels)}")
    if dl_report and dl_report.get("dltrack"):
        notes.append("🎯 dltrack")
    inf = s(f"{D1_SSH} 'ps aux | grep -E \"inference.py|ralph\" | grep -v grep' ", to=3).strip()
    data["inference"] = inf if inf else None
    if inf: notes.append("🔄 推理")
    data["task_notes"] = notes
    
    # ── 过滤隐藏 + 自动清理 ──
    with _lock:
        for k in list(dls.keys()):
            if k in _hidden_downloads:
                del dls[k]
        for k in list(_hidden_downloads):
            if k not in dls:
                _hidden_downloads.discard(k)
    data["downloads"] = dls
    data["dl_real_speed"] = real_speed

    # ── 文件输出 ──
    fl = s(f"""{D1_SSH} 'find /home/jager-dgx/k38_output -name "*.mp4" -newer /tmp/k38_mon_watch -type f 2>/dev/null | head -6' """, to=3).strip()
    data["files"] = [os.path.basename(f) for f in fl.split("\n")] if fl else []

    # ── 告警 ──
    alerts = []
    for dev_id, dev in data["devices"].items():
        if dev.get("online"):
            try:
                t = float(dev.get("temp", 0))
                if t > 85: alerts.append({"level":"critical","dev":dev_id,"msg":f"GPU温度 {t}°C"})
                elif t > 75: alerts.append({"level":"warning","dev":dev_id,"msg":f"GPU温度 {t}°C"})
            except: pass
    if data["link200"].get("latency") and data["link200"]["latency"] > 10:
        alerts.append({"level":"warning","dev":"link200","msg":"200G延迟升高"})
    elif data["link200"].get("up") == False:
        alerts.append({"level":"critical","dev":"link200","msg":"200G断开"})
    data["alerts"] = alerts
    return data

HTML = r'''<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>K38 Command Center</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@500&family=JetBrains+Mono&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#050510;color:#aab;font-family:'JetBrains Mono',monospace;overflow-x:hidden}
canvas#bg{position:fixed;top:0;left:0;width:100%;height:100%;z-index:0;opacity:.3}
#app{position:relative;z-index:1;max-width:1320px;margin:0 auto;padding:12px}
.header{text-align:center;padding:10px 0 6px}
.header h1{font-family:'Orbitron',sans-serif;font-size:22px;color:#0ff;letter-spacing:4px;text-shadow:0 0 20px #0ff6}
.header .sub{font-size:10px;color:#334;letter-spacing:2px;margin-top:3px}
.topbar{display:flex;justify-content:space-between;align-items:center;padding:4px 0;font-size:9px;color:#335}
.topbar .indicator{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px}
.topbar .indicator.live{background:#0f0;box-shadow:0 0 4px #0f0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:10px;margin:8px 0}
.card{background:#08081a;border:1px solid #1a1a3a;border-radius:6px;padding:10px;box-shadow:0 0 15px #0006}
.card-title{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:bold;margin-bottom:6px;letter-spacing:1px}
.metric{font-size:11px;color:#889;line-height:1.6}
.metric b{color:#bbc}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.gauge{text-align:center}
.gauge-label{font-size:9px;color:#556;margin-top:2px;text-transform:uppercase}
.sparkline{opacity:.8}
.alert-bar{display:flex;gap:6px;margin:6px 0;flex-wrap:wrap}
.alert{font-size:10px;padding:3px 8px;border-radius:4px;font-weight:bold}
.alert.warning{background:#330;color:#ff0;border:1px solid #660}
.alert.critical{background:#300;color:#f44;border:1px solid #600}
.dl-row{display:flex;justify-content:space-between;align-items:center;padding:2px 0;font-size:10px}
.dl-bar{font-size:9px;font-family:monospace}
.badge{display:inline-block;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:bold}
.badge-up{background:#020;color:#0f0;border:1px solid #060}
.badge-down{background:#200;color:#f44;border:1px solid #400}
.net-card{text-align:center;min-height:30px}
.net-row{display:flex;gap:12px;justify-content:center;align-items:center;padding:2px 0;font-size:11px}
.net-pub{display:flex;flex-wrap:wrap;gap:4px;justify-content:center;margin-top:5px;padding-top:5px;border-top:1px solid rgba(26,26,58,.6)}
.net-badge{display:inline-flex;align-items:center;gap:3px;padding:2px 6px;border-radius:4px;font-size:8px;font-family:monospace;white-space:nowrap}
.net-fast{background:rgba(34,197,94,0.13);color:rgba(34,197,94,0.9)}
.net-mid{background:rgba(234,179,8,0.13);color:rgba(234,179,8,0.9)}
.net-slow{background:rgba(239,68,68,0.13);color:rgba(239,68,68,0.9)}
.net-na{background:rgba(255,255,255,0.03);color:#445}
.pub-matrix-wrap{overflow:hidden;max-height:0;transition:max-height .3s ease;margin:0 auto;max-width:500px}
.pub-matrix-wrap.open{max-height:300px}
.pub-detail{background:rgba(0,0,0,.3);border:1px solid #2a2a5a;border-radius:6px;padding:8px 12px;margin-top:4px}
.pub-detail-empty{background:rgba(0,0,0,.2);border:1px solid #2a2a5a;border-radius:6px;padding:8px;text-align:center;color:#556;font-size:10px;margin-top:4px}
.pub-dtitle{color:#889;font-size:9px;letter-spacing:2px;margin-bottom:6px;text-align:center}
.pub-ditem{display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid rgba(26,26,58,.4)}
.pub-ditem:last-child{border-bottom:none}
.pub-dname{color:#889;font-size:11px}
.status-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px;font-size:10px;margin-top:4px}
.meta{text-align:center;color:#223;font-size:10px;margin-top:10px}
.section{font-size:10px;color:#334;text-transform:uppercase;letter-spacing:3px;padding:8px 0 4px;border-bottom:1px solid #1a1a3a;margin-bottom:6px}
.scanline{position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.03) 2px,rgba(0,0,0,.03) 4px)}
.dismiss-btn{background:none;border:1px solid #443;color:#665;border-radius:3px;padding:1px 6px;font-size:9px;cursor:pointer;margin-left:6px}
.dismiss-btn:hover{background:#300;color:#f66;border-color:#844}
.dismiss-all{background:none;border:1px solid #335;color:#558;font-size:9px;padding:2px 8px;border-radius:3px;cursor:pointer}
.dismiss-all:hover{background:#220;color:#fa0;border-color:#860}
.proc-bar{display:flex;gap:8px;flex-wrap:wrap;margin:4px 0;font-size:10px}
.proc-tag{background:#1a1a3a;color:#0ff;padding:2px 8px;border-radius:3px;border:1px solid #2a2a5a}
</style></head><body>
<canvas id="bg"></canvas><div class="scanline"></div>
<div id="app">
<div class="header"><h1>K38 COMMAND CENTER</h1><div class="sub">人机共生 · 全设备监控 · <span id="clock">--:--:--</span></div></div>
<div class="topbar">
<span id="conn-status"><span class="indicator live"></span> 实时</span>
<span id="alert-count" style="color:#334"></span>
<span id="task-notes" style="color:#0ff;font-size:10px"></span>
<span>v0.5.0</span>
</div>
<div id="alerts"></div>
<div class="grid" id="devices"></div>
<div id="link200"></div>
<div id="inference"></div>
<div id="downloads"></div>
<div id="files"></div>
<div class="meta">K38 Command Center v0.5.0 · 点击 ✅ 可隐藏已完成下载</div>
</div>
<script>
const $=id=>document.getElementById(id);
// BG particles
const cv=document.getElementById("bg"),c=cv.getContext("2d");
let pts=[];
function rs(){cv.width=window.innerWidth;cv.height=window.innerHeight}rs();window.addEventListener("resize",rs);
for(let i=0;i<80;i++)pts.push({x:Math.random()*cv.width,y:Math.random()*cv.height,vx:(Math.random()-.5)*.4,vy:(Math.random()-.5)*.4,r:Math.random()*1.5+.5});
(function d(){c.clearRect(0,0,cv.width,cv.height);c.strokeStyle="#0ff1";c.lineWidth=.5;
for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){let p=pts[i],q=pts[j],dx=p.x-q.x,dy=p.y-q.y;if(dx*dx+dy*dy<1e4){c.beginPath();c.moveTo(p.x,p.y);c.lineTo(q.x,q.y);c.stroke()}}
for(let p of pts){p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>cv.width)p.vx*=-1;if(p.y<0||p.y>cv.height)p.vy*=-1;c.fillStyle="#0ff";c.beginPath();c.arc(p.x,p.y,p.r,0,6.28);c.fill()}requestAnimationFrame(d)})();

function dismiss(fn){
    fetch("/api/v1/clear-downloads",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keys:[fn]})});
    render(getLastData());
}
function dismissAll(){
    let d=getLastData();if(!d||!d.downloads)return;
    let keys=Object.keys(d.downloads).filter(k=>d.downloads[k].done);
    if(keys.length)fetch("/api/v1/clear-downloads",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keys:keys})});
}
function clearActiveProcs(){
    fetch("/api/v1/clear-all-downloads",{method:"POST"});
    render(getLastData());
}
let _lastData=null;
function getLastData(){return _lastData;}
async function poll(){
    try{
        let r=await fetch("/api/v1/metrics");
        let d=await r.json();
        _lastData=d;
        render(d);
    }catch(e){}
    setTimeout(poll, 2500);
}
poll();

function gtemp(v){
    let t=parseFloat(v);if(isNaN(t))return "#556";
    if(t<50)return "#0ff";if(t<75)return "#ff0";return "#f44";
}
function sg(id,val,max,label,color,sz=72){
    let pct=Math.min(Math.max(val/Math.max(max,1)*100,0),100);
    let cx=sz/2,cy=sz/2,r=sz/2-7,sw=sz/8;
    let circ=2*Math.PI*r,dash=circ*pct/100;
    return '<svg width="'+sz+'" height="'+sz+'" viewBox="0 0 '+sz+' '+sz+'"><circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="#111" stroke-width="'+sw+'"/>'
        +'<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="'+color+'" stroke-width="'+sw+'" stroke-dasharray="'+dash+' '+Math.ceil(circ-dash)+'" stroke-linecap="round" transform="rotate(-90 '+cx+' '+cy+')" style="filter:drop-shadow(0 0 4px '+color+')"/>'
        +'<text x="'+cx+'" y="'+(cy-4)+'" text-anchor="middle" fill="#eee" font-size="'+(sz*.2)+'px" font-family="monospace" font-weight="bold">'+Math.round(pct)+'%</text>'
        +'<text x="'+cx+'" y="'+(cy+11)+'" text-anchor="middle" fill="#556" font-size="'+(sz*.12)+'px">'+label+'</text></svg>';
}
function sln(vals,color,w=140,h=22){
    if(vals.length<2)return '<svg width="'+w+'" height="'+h+'"></svg>';
    let mn=Math.min(...vals),mx=Math.max(...vals);if(mn===mx){mn-=1;mx+=1}
    let rng=mx-mn,pts=[],n=vals.length;
    vals.forEach((v,i)=>{let x=i/(n-1)*(w-4)+2,y=h-2-(v-mn)/rng*(h-4);pts.push(x.toFixed(1)+","+y.toFixed(1));});
    return '<svg width="'+w+'" height="'+h+'"><polyline points="'+pts.join(" ")+'" fill="none" stroke="'+color+'" stroke-width="1.5" opacity=".7"/></svg>';
}
function macOnlyCard(id,name,ip,spec,color,dev,hist){
    if(!dev.online)return '<div class="card" style="border-top:2px solid '+color+'"><div class="card-title" style="color:'+color+'">'+name+'</div><div style="color:#f44;font-size:12px">OFFLINE</div><div style="color:#556;font-size:9px;margin-top:4px">'+ip+'</div></div>';
    let cpu=dev.cpu||0,mu=dev.mem_used||0,mt=dev.mem_total||96;
    return '<div class="card" style="border-top:2px solid '+color+'"><div class="card-title" style="color:'+color+'">'+name+' <span style="color:#556;font-size:10px;font-weight:normal">'+spec+' · '+ip+'</span></div><div class="row">'+sg("c_"+id,cpu,100,"CPU",color,64)+'<div><div class="metric">CPU: <b>'+cpu.toFixed(0)+'%</b></div><div class="metric">内存: <b>'+mu.toFixed(0)+'</b>/'+mt.toFixed(0)+'GB</div></div></div><div style="margin-top:4px">'+sln(hist||[],color,340,22)+'</div></div>';
}
function devcard(id,name,ip,spec,color,dev){
    if(!dev.online)return '<div class="card" style="border-top:2px solid '+color+'"><div class="card-title" style="color:'+color+'">'+name+'</div><div style="color:#f44;font-size:12px">OFFLINE</div></div>';
    let gpu=parseFloat(dev.gpu)||0,gmem=parseFloat(dev.gmem)||0,mut=dev.mem_t=="[N/A]"||dev.mem_t=="0"?131072:parseFloat(dev.mem_t)||131072;
    let tc=gtemp(dev.temp),po=parseFloat(dev.power)||0;
    let clk="";if(dev.clock&&dev.clock!="[N/A]")clk=' · ⏱ '+(parseFloat(dev.clock).toFixed(0))+'MHz';
    return '<div class="card" style="border-top:2px solid '+color+'">'
        +'<div class="card-title" style="color:'+color+'">'+name+' <span style="color:#556;font-size:10px;font-weight:normal">'+spec+' · '+ip+'</span></div>'
        +'<div class="row"><div class="gauge">'+sg("g_"+id,gpu,100,"GPU",color,64)+'<div class="gauge-label">GPU</div></div>'
        +'<div class="gauge">'+sg("m_"+id,gmem/mut*100,100,"VRAM",color,64)+'<div class="gauge-label">VRAM</div></div>'
        +'<div class="gauge">'+sg("t_"+id,parseFloat(dev.temp)||0,100,"TEMP",tc,64)+'<div class="gauge-label">TEMP</div></div>'
        +'<div style="flex:1"><div class="metric">GPU: <b>'+dev.gpu+'%</b> · 显存 <b>'+dev.mem_u+'/'+dev.mem_t+'</b>MB</div>'
        +'<div class="metric">🌡 '+dev.temp+'°C · ⚡ '+dev.power+'W'+clk+'</div>'
        +'<div class="metric">系统: '+(dev.sys_used||'?')+'/'+(dev.sys_total||'?')+' · '+(dev.load||'')+' · '+(dev.uptime||'')+'</div></div></div>'
        +'<div class="row" style="margin-top:4px;gap:6px">'+sln(window["hist_"+id+"_gpu"]||[],color,130,18)+sln(window["hist_"+id+"_temp"]||[],"#f80",130,18)+'</div></div>';
}
function togglePubMatrix(tag){
  var w=$("pub-matrix-wrap");
  if(!w)return;
  var data=window._pubAll||{};
  var devNames={"本地":"十六万","大傻":"大傻","二傻":"二傻","三万八":"三万八","小四":"小四"};
  var svcNames={"baidu_ms":"百度","ytb_ms":"YouTube","github_ms":"GitHub","google_ms":"Google","yahoo_hk_ms":"Yahoo"};
  if(w._active===tag){
    w._active=null;w.innerHTML="";w.classList.remove("open");
    window._activePubTag=null;
    return;
  }
  var details=data[tag];
  if(!details){w.innerHTML='<div class="pub-detail-empty">暂无数据</div>';w.classList.add("open");w._active=tag;window._activePubTag=tag;return;}
  var names=["本地","大傻","二傻","三万八","小四"];
  var rows="";
  for(var ni=0;ni<names.length;ni++){
    var n=names[ni];var v=details[n];
    if(typeof v==="number"){
      var cl=v<50?"net-fast":v<200?"net-mid":"net-slow";
      rows+='<div class="pub-ditem"><span class="pub-dname">'+(devNames[n]||n)+'</span><span class="net-badge '+cl+'">'+v.toFixed(0)+"ms</span></div>";
    }else{
      rows+='<div class="pub-ditem"><span class="pub-dname">'+(devNames[n]||n)+'</span><span class="net-badge net-na">N/A</span></div>';
    }
  }
  w.innerHTML='<div class="pub-detail"><div class="pub-dtitle">'+(svcNames[tag]||tag)+' 各设备延迟</div>'+rows+'</div>';
  w.classList.add("open");
  w._active=tag;
  window._activePubTag=tag;
}
function render(d){
    $("clock").textContent=new Date(d.ts*1000).toLocaleTimeString("zh-CN",{hour12:false});
    // Task notes
    $("task-notes").innerHTML=(d.task_notes||[]).map(n=>'<span style="margin:0 4px">'+n+'</span>').join("");
    // Alerts
    let ahtml="";
    if(d.alerts&&d.alerts.length>0){
        ahtml='<div class="alert-bar">'+d.alerts.map(function(a){return '<span class="alert '+a.level+'">'+(a.level=="critical"?"🚨":"⚠")+' '+a.dev+': '+a.msg+'</span>';}).join("")+'</div>';
        $("alert-count").innerHTML='<span style="color:'+(d.alerts.some(function(a){return a.level=="critical";})?'#f44':'#ff0')+'">'+d.alerts.length+'告警</span>';
    }else{$("alert-count").textContent="✓ 正常"}
    $("alerts").innerHTML=ahtml;
    // Devices
    let mc=d.devices.mac||{},d1=d.devices.d1||{},d2=d.devices.d2||{},m38=d.devices.m38||{},m4=d.devices.m4||{};
    let mc_cpu=[];try{mc_cpu=JSON.parse($("hist_mac_cpu")?.value||"[]");}catch(e){}
    mc_cpu.push(mc.cpu||0);if(mc_cpu.length>150)mc_cpu=mc_cpu.slice(-150);
    window.hist_mac_cpu=mc_cpu;
    window.hist_d1_gpu=window.hist_d1_gpu||[];window.hist_d1_gpu.push(parseFloat(d1.gpu)||0);if(window.hist_d1_gpu.length>150)window.hist_d1_gpu=window.hist_d1_gpu.slice(-150);
    window.hist_d1_temp=window.hist_d1_temp||[];window.hist_d1_temp.push(parseFloat(d1.temp)||0);if(window.hist_d1_temp.length>150)window.hist_d1_temp=window.hist_d1_temp.slice(-150);
    window.hist_d2_gpu=window.hist_d2_gpu||[];window.hist_d2_gpu.push(parseFloat(d2.gpu)||0);if(window.hist_d2_gpu.length>150)window.hist_d2_gpu=window.hist_d2_gpu.slice(-150);
    window.hist_d2_temp=window.hist_d2_temp||[];window.hist_d2_temp.push(parseFloat(d2.temp)||0);if(window.hist_d2_temp.length>150)window.hist_d2_temp=window.hist_d2_temp.slice(-150);
    window.hist_m38_cpu=window.hist_m38_cpu||[];window.hist_m38_cpu.push(m38.cpu||0);if(window.hist_m38_cpu.length>150)window.hist_m38_cpu=window.hist_m38_cpu.slice(-150);
    window.hist_m4_cpu=window.hist_m4_cpu||[];window.hist_m4_cpu.push(m4.cpu||0);if(window.hist_m4_cpu.length>150)window.hist_m4_cpu=window.hist_m4_cpu.slice(-150);
    let dm='<div class="grid">';
    dm+='<div class="card" style="border-top:2px solid #0ff"><div class="card-title" style="color:#0ff">🖥 十六万 <span style="color:#556;font-size:10px;font-weight:normal">M3 Ultra 512GB · 192.168.3.47</span></div><div class="row">'+sg("mc_cpu",mc.cpu||0,100,"CPU","#0ff",64)+'<div><div class="metric">CPU: <b>'+(mc.cpu||0).toFixed(0)+'%</b></div><div class="metric">内存: <b>'+(mc.mem_used||0).toFixed(0)+'</b>/'+(mc.mem_total||512).toFixed(0)+'GB</div><div class="metric">磁盘: '+(mc.disk_pct||"?")+' ('+(mc.disk_used||"?")+'/'+(mc.disk_total||"?")+')</div><div class="metric" style="font-size:9px;color:#445">'+(mc.gpu_info||"M3 Ultra")+'</div></div></div><div style="margin-top:4px">'+sln(mc_cpu,"#0ff",340,22)+'</div></div>';
    dm+=devcard("d1","🔥 大傻","192.168.3.55","DGX Spark","#f0f",d1);
    dm+=devcard("d2","💧 二傻","192.168.3.45","DGX Spark","#0fa",d2);
    dm+=macOnlyCard("m38","💼 三万八","192.168.3.29","M3 Ultra 96GB","#e80",m38,window.hist_m38_cpu);
    dm+=macOnlyCard("m4","📱 小四","192.168.3.46","M4 Max 128GB","#08f",m4,window.hist_m4_cpu);
    dm+="</div>";$("devices").innerHTML=dm;
    // NETWORK
    let lk=d.link200||{};
    let lkc=lk.up?(lk.latency||999)<1?"#0f0":"#ff0":"#f44";
    let lkt=lk.up?lk.latency.toFixed(2)+"ms":"DOWN";
    let pp=d.public_ping||{};
    let ppa=d.public_ping_all||{};
    let devKeys={"本地":"十六万","大傻":"大傻","二傻":"二傻","三万八":"三万八","小四":"小四"};
    let svcs=[["baidu_ms","百度",50,200],["ytb_ms","YouTube",200,500],["github_ms","GitHub",50,200],["google_ms","Google",100,300],["yahoo_hk_ms","Yahoo",50,200]];
    let pubHtml="";
    let hasDetails=false;
    for(let si=0;si<svcs.length;si++){
      let sk=svcs[si];let v=pp[sk[0]];let g=sk[2],y=sk[3];
      if(typeof v==="number"){
        let cl=v<g?"net-fast":v<y?"net-mid":"net-slow";
        pubHtml+='<span class="net-badge '+cl+'" onclick="togglePubMatrix(\''+sk[0]+'\')" style="cursor:pointer">'+sk[1]+' <b>'+Math.round(v)+"ms</b></span>";
      }else{
        pubHtml+='<span class="net-badge net-na" onclick="togglePubMatrix(\''+sk[0]+'\')" style="cursor:pointer">'+sk[1]+" N/A</span>";
      }
      if(ppa[sk[0]]) hasDetails=true;
    }
    let netHtml='<div class="card net-card" style="padding:8px"><div class="net-row"><span style="font-weight:bold;color:'+lkc+';font-size:13px">⚡</span><span style="font-family:monospace;font-size:9px;color:#fa0;letter-spacing:1px">200G</span><span style="color:#556;font-size:9px">spark-9051 ↔ spark-9797</span><span style="font-weight:bold;font-size:13px;color:'+lkc+'">'+lkt+'</span></div>'+(pubHtml?'<div class="net-pub">'+pubHtml+'</div>':'')+'</div>';
    if(hasDetails){
      netHtml+='<div id="pub-matrix-wrap" class="pub-matrix-wrap"></div>';
      window._pubAll=ppa;
      window._activePubTag=window._activePubTag||null;
      if(window._activePubTag && ppa[window._activePubTag]){
        setTimeout(function(){togglePubMatrix(window._activePubTag);},50);
      }
    }
    $("link200").innerHTML=netHtml;
    // Inference
    if(d.inference){
        $("inference").innerHTML='<div class="card" style="border-color:#f80"><div class="card-title" style="color:#f80">🔄 推理活跃</div><div class="metric" style="font-size:10px">'+d.inference.replace(/</g,"&lt;").slice(0,200)+'</div></div>';
    } else {
        $("inference").innerHTML='<div class="card" style="border-color:#222;opacity:.4"><div class="card-title" style="color:#334">⏸ 无推理任务</div></div>';
    }
    // Downloads
    let dls=d.downloads||{};
    let dlHtml="";
    if(Object.keys(dls).length || (d.active_downloads||[]).length){
        dlHtml='<div class="card" style="border-color:#f80"><div class="card-title" style="color:#f80">📥 下载监控</div>';
        // Active processes (always shown when present)
        let apr=d.active_downloads||[];
        if(apr.length){
            dlHtml+='<div class="proc-bar"><span style="color:#fa0;font-size:10px">● 活跃进程:</span>';
            let seen={};
            for(let p of apr){if(!seen[p.tag]){seen[p.tag]=true;dlHtml+='<span class="proc-tag">'+p.tag+'</span>';}}
            dlHtml+='</div>';
            for(let p of apr){
                let desc=p.file||p.cmd.slice(0,60);
                let pbar='';
                if(p.pct>0&&p.pct<100){
                    let bw=Math.floor(p.pct/5),bg='\u2588'.repeat(bw)+'\u2591'.repeat(20-bw);
                    pbar='<div style="margin:3px 0 0 0;color:#0f0;font-size:10px">'+bg+' <span style="color:#fa0;font-weight:bold">'+p.pct+'%</span></div>';
                    dlHtml+='<div style="margin:4px 0;padding:4px 6px;background:#0c0c20;border-radius:4px;border:1px solid #1a1a3a"><div style="font-size:10px;color:#bbc">\u2b07 '+desc+'</div>'+pbar+'</div>';
                }else if(p.pct==100||p.pct===0){
                    dlHtml+='<div style="margin:2px 0;padding:3px 6px;background:#0a0a1a;border-radius:3px"><span style="font-size:10px;color:#484">\u2705 '+desc+'</span></div>';
                }else{
                    dlHtml+='<div style="margin:2px 0;padding:3px 6px;background:#0a0a1a;border-radius:3px"><span style="font-size:10px;color:#887">\u23f3 '+desc+'</span></div>';
                }
            }
        }
        // Log-based progress (if any)
        let tp=0,dn=0,tn=Object.keys(dls).length,ts=0;
        for(let k in dls){tp+=dls[k].pct;if(dls[k].done)dn++;if(!dls[k].done)ts+=dls[k].speed}
        if(tn>0){
            tp/=Math.max(tn,1);
            let tbw=Math.floor(tp/4),tbar="█".repeat(tbw)+"░".repeat(25-tbw);
            let real_spd=d.dl_real_speed||0;
            let spd_text=real_spd>0?"📡 "+real_spd.toFixed(1)+"MB/s":"∑"+ts.toFixed(1)+"MB/s";
            let eta="";if(real_spd>0&&tp<100){let etm=54*(100-tp)/100*1024/(real_spd*60);eta=etm>=60?"⏳"+(etm/60).toFixed(1)+"h":"⏳"+etm.toFixed(0)+"min";}
            dlHtml+='<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #222"><span style="font-size:12px;color:#0ff;font-weight:bold">总计 '+tp.toFixed(0)+'%</span><span style="font-size:10px;color:#556;margin-left:8px">'+dn+'/'+tn+' · '+spd_text+'</span><span style="font-size:10px;color:#fa0;margin-left:8px">'+eta+'</span><div style="color:#0ff;font-size:10px;margin-top:2px">'+tbar+'</div></div>';
            let items=Object.entries(dls).sort(function(a,b){return (a[1].done-b[1].done)||(a[1].pct-b[1].pct);});
            for(let [fn,inf] of items){
                if(inf.done){
                    dlHtml+='<div style="font-size:10px;color:#464;display:flex;align-items:center;justify-content:space-between"><span>✅ ['+(inf.tag||"")+'] '+fn.substring(0,48)+'</span><button class="dismiss-btn" onclick="dismiss(\''+fn.replace(/[^a-zA-Z0-9._-]/g,"")+'\')">✕</button></div>';
                } else {
                    let bw2=Math.floor(inf.pct/5),bar2="█".repeat(bw2)+"░".repeat(20-bw2);
                    dlHtml+='<div style="margin:3px 0"><div class="dl-row"><span style="color:#887;font-size:10px;max-width:300px;overflow:hidden">'+fn.substring(0,40)+'</span><span style="color:#fa0;font-size:9px">'+inf.pct+'%</span><span style="color:#665;font-size:9px">'+inf.speed.toFixed(1)+'MB/s</span></div><div class="dl-bar" style="color:#fa0">'+bar2+'</div></div>';
                }
            }
        }
        if(tn>0 && dn==tn){
            dlHtml+='<div style="text-align:right;margin-top:4px"><button class="dismiss-all" onclick="dismissAll()">✅ 全部清除</button></div>';
        }
        dlHtml+='</div>';
    } else {
        dlHtml='<div class="card" style="border-color:#222;opacity:.4"><div class="card-title" style="color:#334">📥 无下载任务</div></div>';
    }
    $("downloads").innerHTML=dlHtml;
    // Files
    let fs=d.files||[];
    $("files").innerHTML=fs.length?'<div class="card"><div class="card-title">📦 最新输出 ('+fs.length+')</div>'+fs.map(function(f){return '<div style="font-size:10px;color:#445">📹 '+f+'</div>';}).join("")+'</div>':"";
}
</script></body></html>'''

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/v1/metrics":
            d = _cache.get("data") or collect()
            self._json(d)
        elif p == "/health":
            d = _cache.get("data", {})
            dv = d.get("devices", {}) if d else {}
            self._json({"status": "ok", "uptime": int(time.time() - START_TIME), "devices_online": sum(1 for v in dv.values() if v.get("online"))})
        elif p == "/metrics":
            self._metrics()
        elif p == "/stream":
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream")
            self.send_header("Cache-Control","no-cache")
            self.send_header("Connection","keep-alive")
            self.send_header("Access-Control-Allow-Origin","*")
            self.end_headers()
            self.wfile.write("retry: 3000\n\n".encode())
            self.wfile.flush()
            _ssubcribers.append(self)
            try:
                while True:
                    data = _cache.get("data")
                    if data:
                        self.wfile.write(f"data: {json.dumps(data, default=str)}\n\n".encode())
                        self.wfile.flush()
                    time.sleep(2)
            except: pass
            finally:
                if self in _ssubcribers: _ssubcribers.remove(self)
        else:
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode())

    def do_POST(self):
        if self.path == "/api/v1/clear-downloads":
            cl = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(cl).decode() if cl else "{}"
            try:
                req = json.loads(body)
                keys = req.get("keys", [])
                with _lock:
                    for k in keys:
                        _hidden_downloads.add(k)
                self._json({"status": "ok", "hidden": len(keys)})
            except:
                self._json({"status": "error"})
        elif self.path == "/api/v1/clear-all-downloads":
            with _lock:
                _hidden_downloads.clear()
            self._json({"status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _metrics(self):
        d = _cache.get("data") or {}
        devs = d.get("devices", {})
        ts_ms = int(time.time() * 1000)
        lines = []
        lines.append("# HELP k38_up Device online status (1=up 0=down)")
        lines.append("# TYPE k38_up gauge")
        lines.append("# HELP k38_gpu_util_percent GPU utilization percentage")
        lines.append("# TYPE k38_gpu_util_percent gauge")
        lines.append("# HELP k38_gpu_mem_util_percent GPU memory utilization percentage")
        lines.append("# TYPE k38_gpu_mem_util_percent gauge")
        lines.append("# HELP k38_gpu_temp_celsius GPU temperature in Celsius")
        lines.append("# TYPE k38_gpu_temp_celsius gauge")
        lines.append("# HELP k38_gpu_power_watts GPU power draw in Watts")
        lines.append("# TYPE k38_gpu_power_watts gauge")
        lines.append("# HELP k38_gpu_clock_mhz GPU SM clock in MHz")
        lines.append("# TYPE k38_gpu_clock_mhz gauge")
        lines.append("# HELP k38_system_memory_used_gb System memory used GB")
        lines.append("# TYPE k38_system_memory_used_gb gauge")
        lines.append("# HELP k38_link200_latency_ms 200G interconnect latency ms")
        lines.append("# TYPE k38_link200_latency_ms gauge")
        lines.append("# HELP k38_collect_ts_ms Last collection timestamp ms")
        lines.append("# TYPE k38_collect_ts_ms gauge")
        dev_labels = {"mac": ("十六万", "mac"), "d1": ("大傻", "dgx1"), "d2": ("二傻", "dgx2")}
        for dev_id, dev in devs.items():
            name, role = dev_labels.get(dev_id, (dev_id, dev_id))
            lbl = f'device="{dev_id}",name="{name}",role="{role}"'
            up = 1 if dev.get("online") else 0
            lines.append(f"k38_up{{{lbl}}} {up} {ts_ms}")
            if dev.get("online"):
                for k, mk in [("gpu","k38_gpu_util_percent"),("gmem","k38_gpu_mem_util_percent"),("temp","k38_gpu_temp_celsius"),("power","k38_gpu_power_watts"),("clock","k38_gpu_clock_mhz")]:
                    try: lines.append(f"{mk}{{{lbl}}} {float(dev.get(k,0))} {ts_ms}")
                    except: pass
        lk = d.get("link200", {})
        if lk.get("up") and lk.get("latency"):
            lines.append(f"k38_link200_latency_ms{{link=\"200g\"}} {lk['latency']} {ts_ms}")
        lines.append(f"k38_collect_ts_ms {{}} {ts_ms} {ts_ms}")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(("\n".join(lines) + "\n").encode())

    def log_message(self,*a): pass

_cache = {"data": None, "ts": time.time()}
START_TIME = time.time()

def loop():
    global _cache
    while True:
        try:
            d = collect()
            _cache = {"data": d, "ts": time.time()}
        except:
            pass
        time.sleep(2)

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    os.system("touch /tmp/k38_mon_watch")
    print("K38 Command Center v0.5.0 :8899")
    print("  /          → Dashboard")
    print("  /api/v1/metrics → JSON API")
    print("  /health    → Health check")
    ThreadingHTTPServer(("0.0.0.0", 8899), H).serve_forever()
