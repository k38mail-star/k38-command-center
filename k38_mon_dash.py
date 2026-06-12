#!/usr/bin/env python3
"""K38 Command Center v0.5.0 — 全设备监控 · Prometheus /metrics · 多设备 · 网络 · 告警"""
import subprocess, os, re, time, threading, json, signal
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
from datetime import datetime
from collections import deque

PASS = "169401"
E2 = "jager-dgx-2@192.168.3.45"
HOME = os.path.expanduser("~")
D1 = "jager-dgx@192.168.3.55"
D1_SSH = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {D1}"
SSH_BASE = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {E2}"
M38 = "jagerm3uitra@192.168.3.29"
M4 = "jagerstudiom4max@192.168.3.46"
M38_SSH = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {M38}"
M4_SSH = f"sshpass -p {PASS} ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no {M4}"

HISTORY = 150  # 5min @ 2s
history = {k: deque(maxlen=HISTORY) for k in [
    "mac_cpu","mac_mem","d1_gpu","d1_mem","d1_temp","d2_gpu","d2_mem","d2_temp","m38_cpu","m38_mem","m4_cpu","m4_mem"
]}
_ssubcribers = []  # SSE 客户端列表
_lock = threading.Lock()
_dl_bytes_prev = 0
_dl_bytes_ts = 0

def s(c, to=4):
    """安全 shell 执行，超时自动杀孤儿进程"""
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

# ═══════ 采集 ═══════
def collect():
    data = {"ts": time.time(), "devices": {}}

    # ── 十六万 Mac (本机采集) ──
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
        # 磁盘
        disk_raw = s("df -m /", to=2)
        dl = disk_raw.strip().split("\n")[-1].split()
        dsk_pct = dl[4] if len(dl) > 4 else "?"
        dsk_used = f"{int(dl[2])//1024}G" if len(dl) > 2 else "?"
        dsk_total = f"{int(dl[1])//1024}G" if len(dl) > 1 else "?"
        # GPU 信息
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

    # ── 大傻 DGX1 ──
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

    # ── 二傻 DGX2 ──
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
    except Exception as e:
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
    except Exception as e:
        data["devices"]["m4"] = {"online": False}

    # ── 200G ──
    try:
        po = s(f"""{D1_SSH} 'ping -c1 -W1 192.168.100.102 2>/dev/null | grep time=' """, to=3)
        m = re.search(r"time=([\d.]+)", po)
        data["link200"] = {"latency": float(m.group(1)) if m else None, "up": bool(m)}
    except: data["link200"] = {"latency": None, "up": False}

    # ── 下载（用实际字节数计算真实速度）──
    global _dl_bytes_prev, _dl_bytes_ts
    dls = {}
    total_bytes = 0
    for src, tag in [(s(f"""{D1_SSH} 'cat /tmp/k38_wan_dl.log' """, to=2),"Wan2.1"),
                     (s(f"""{D1_SSH} 'cat /tmp/k38_t5_download.log' """, to=2),"T5")]:
        for line in src.split("\n"):
            if "Downloading" in line and "%" in line:
                m_fn=re.search(r'\[([^\]]+)\]',line); m_pct=re.search(r'(\d+)%',line)
                m_spd=re.search(r'([\d.]+)\s*(MB/s|KB/s)',line)
                m_sz=re.search(r'([\d.]+)([GMK])/([\d.]+)([GMK])',line)
                if m_fn and m_pct:
                    fn=m_fn.group(1)
                    if not any(fn.endswith(e) for e in ('.safetensors','.pth')): continue
                    pct=int(m_pct.group(1));
                    spd=float(m_spd.group(1)) if m_spd else 0
                    spd_mb=spd/1024 if m_spd and m_spd.group(2)=="KB/s" else spd
                    # 解析已下载字节
                    if m_sz:
                        cur=float(m_sz.group(1))
                        cur_b=cur*(1024**3) if m_sz.group(2)=="G" else cur*(1024**2) if m_sz.group(2)=="M" else cur*1024
                        total_bytes += cur_b
                    dls[fn]={"pct":pct,"speed":spd_mb,"done":pct>=100,"tag":tag}
            elif "DONE" in line:
                m_fn=re.search(r'DONE:\s*(\S+)',line)
                if m_fn:
                    fn=os.path.basename(m_fn.group(1))
                    if any(fn.endswith(e) for e in ('.safetensors','.pth')):
                        dls[fn]={"pct":100,"speed":0,"done":True,"tag":tag}
    # 基于字节差计算真实速度
    now = time.time()
    if total_bytes > 0 and _dl_bytes_prev > 0:
        delta_bytes = total_bytes - _dl_bytes_prev
        delta_sec = now - _dl_bytes_ts if _dl_bytes_ts else 2
        if delta_sec > 0:
            real_speed = delta_bytes / delta_sec / 1024**2  # MB/s
        else:
            real_speed = sum(d["speed"] for d in dls.values() if not d["done"])
    else:
        real_speed = 0
    _dl_bytes_prev = total_bytes
    _dl_bytes_ts = now
    data["downloads"] = dls
    data["dl_real_speed"] = real_speed  # MB/s 真实速度

    # ── 推理 ──
    # ── 公网ping（全设备，跳过离线）──
    pub = {}; pub_all = {}
    ping_src = [("大傻",D1_SSH,"ping"),("二傻",SSH_BASE,"ping"),("三万八",M38_SSH,"curl"),("小四",M4_SSH,"curl")]
    for tag, host in [("baidu_ms","baidu.com"),("ytb_ms","youtube.com"),("github_ms","github.com"),("google_ms","google.com"),("yahoo_hk_ms","yahoo.com.hk")]:
        best = None; details = {}
        for sname, sp, meth in ping_src:
            dk = {"大傻":"d1","二傻":"d2","三万八":"m38","小四":"m4"}.get(sname)
            if dk and not data.get("devices",{}).get(dk,{}).get("online",False):
                continue
            try:
                if meth == "ping":
                    o = s(f"{sp} 'ping -c1 -W2 {host} 2>/dev/null | grep time=' ", to=3)
                    m = re.search(r"time=([\d.]+)", o)
                    v = float(m.group(1)) if m else None
                else:
                    o = s(f"{sp} 'curl -o /dev/null -s -w \"%{time_total}\" https://{host} --connect-timeout 3 --max-time 4' ", to=5)
                    v = float(o.strip()) if o.strip() else None
                    if v is not None: v = round(v * 1000, 1)
                if v is not None:
                    details[sname] = v
                    if best is None or v < best: best = v
            except:
                pass
        if best is not None: pub[tag] = best
        if details: pub_all[tag] = details
    data["public_ping"] = pub
    data["public_ping_all"] = pub_all

    # ── 推理任务（全设备，跳过离线+超时保护）──
    inference_tasks = {"十六万":[]}
    def _inf_one(sp, known_containers):
        tasks = []
        if known_containers:
            out = s(f"{sp} 'docker ps --format \"{{{{.Names}}}}|{{{{.Status}}}}\" 2>/dev/null' ", to=2)
            if out:
                for line in out.strip().split("\n"):
                    parts = line.split("|")
                    n = (parts[0] or "").strip()
                    if n in known_containers:
                        st = (parts[1] or "").strip().split()[0] if len(parts) > 1 else ""
                        tasks.append({"type":"container","name":n,"status":st})
        return tasks
    for dn,sp,kn in [["大傻",D1_SSH,None],["二傻",SSH_BASE,["echo2"]],["三万八",M38_SSH,None],["小四",M4_SSH,None]]:
        dk = {"大傻":"d1","二傻":"d2","三万八":"m38","小四":"m4"}[dn]
        if not data.get("devices",{}).get(dk,{}).get("online",False):
            inference_tasks[dn] = []
        else:
            res = []
            def _r():
                try:
                    t = _inf_one(sp, kn)
                    res[:] = t if t else []
                except:
                    res[:] = []
            th = threading.Thread(target=_r, daemon=True)
            th.start()
            th.join(timeout=4)
            inference_tasks[dn] = res[:] if not th.is_alive() else []
    data["inference_tasks"] = inference_tasks

    # ── 输出文件 ──
    fl = s(f"""{D1_SSH} 'find /home/jager-dgx/k38_output -name "*.mp4" -newer /tmp/k38_mon_watch -type f 2>/dev/null | head -6' """, to=3).strip()
    data["files"] = [os.path.basename(f) for f in fl.split("\n")] if fl else []

    # ── 告警 ──
    alerts = []
    for dev_id, dev in data["devices"].items():
        if dev.get("online"):
            try:
                t = float(dev.get("temp", 0))
                g = float(dev.get("gpu", 0))
                if t > 85: alerts.append({"level":"critical","dev":dev_id,"msg":f"GPU温度 {t}°C"})
                elif t > 75: alerts.append({"level":"warning","dev":dev_id,"msg":f"GPU温度 {t}°C"})
            except: pass
    if data["link200"].get("latency"):
        if data["link200"]["latency"] > 10: alerts.append({"level":"warning","dev":"link200","msg":"200G延迟升高"})
    elif not data["link200"].get("up"): alerts.append({"level":"critical","dev":"link200","msg":"200G断开"})
    data["alerts"] = alerts

    return data

# ═══════ HTML ═══════
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
.inf-row{display:flex;align-items:center;gap:6px;padding:2px 4px;font-size:10px;border-bottom:1px solid #111}
.inf-row:last-child{border-bottom:none}
.inf-dev{width:50px;color:#667;flex-shrink:0}
.inf-type{font-size:10px;width:18px;text-align:center}
.inf-name{flex:1;color:#aab}
.inf-status{color:#667;font-size:9px}
.inf-idle{color:#334;font-size:10px}
.alert{font-size:10px;padding:3px 8px;border-radius:4px;font-weight:bold}
.alert.warning{background:#330;color:#ff0;border:1px solid #660}
.alert.critical{background:#300;color:#f44;border:1px solid #600}
.dl-row{display:flex;justify-content:space-between;align-items:center;padding:2px 0;font-size:10px}
.dl-bar{font-size:9px;font-family:monospace}
.badge{display:inline-block;padding:1px 5px;border-radius:3px;font-size:9px;font-weight:bold}
.badge-up{background:#020;color:#0f0;border:1px solid #060}
.badge-down{background:#200;color:#f44;border:1px solid #400}
.status-grid{display:grid;grid-template-columns:1fr 1fr;gap:3px;font-size:10px;margin-top:4px}
.meta{text-align:center;color:#223;font-size:10px;margin-top:10px}
.section{font-size:10px;color:#334;text-transform:uppercase;letter-spacing:3px;padding:8px 0 4px;border-bottom:1px solid #1a1a3a;margin-bottom:6px}
.scanline{position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:2;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,.03) 2px,rgba(0,0,0,.03) 4px)}
</style></head><body>
<canvas id="bg"></canvas><div class="scanline"></div>
<div id="app">
<div class="header"><h1>K38 COMMAND CENTER</h1><div class="sub">人机共生 · 全设备监控 · <span id="clock">--:--:--</span></div></div>
<div class="topbar">
<span id="conn-status"><span class="indicator live"></span> SSE实时</span>
<span id="alert-count" style="color:#334"></span>
<span>v4 industrial</span>
</div>
<div id="alerts"></div>
<div class="grid" id="devices"></div>
<div id="link200"></div>
<div id="inference"></div>
<div id="downloads"></div>
<div id="files"></div>
<div id="network"></div>
<div class="meta">K38 Command Center v0.5.0 · polling · /api/v1/metrics · /metrics · /health</div>
</div>
<script>
const $=id=>document.getElementById(id);
// BG
const cv=document.getElementById("bg"),c=cv.getContext("2d");
let pts=[];
function rs(){cv.width=window.innerWidth;cv.height=window.innerHeight}rs();window.addEventListener("resize",rs);
for(let i=0;i<80;i++)pts.push({x:Math.random()*cv.width,y:Math.random()*cv.height,vx:(Math.random()-.5)*.4,vy:(Math.random()-.5)*.4,r:Math.random()*1.5+.5});
(function d(){c.clearRect(0,0,cv.width,cv.height);c.strokeStyle="#0ff1";c.lineWidth=.5;
for(let i=0;i<pts.length;i++)for(let j=i+1;j<pts.length;j++){let p=pts[i],q=pts[j],dx=p.x-q.x,dy=p.y-q.y;if(dx*dx+dy*dy<1e4){c.beginPath();c.moveTo(p.x,p.y);c.lineTo(q.x,q.y);c.stroke()}}
for(let p of pts){p.x+=p.vx;p.y+=p.vy;if(p.x<0||p.x>cv.width)p.vx*=-1;if(p.y<0||p.y>cv.height)p.vy*=-1;c.fillStyle="#0ff";c.beginPath();c.arc(p.x,p.y,p.r,0,6.28);c.fill()}requestAnimationFrame(d)})();

// Polling (no SSE, avoids single-thread block)
async function poll(){
    try{
        let r=await fetch("/api/v1/metrics");
        let d=await r.json();
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
    return `<svg width="${sz}" height="${sz}" viewBox="0 0 ${sz} ${sz}"><circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#111" stroke-width="${sw}"/>
<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-dasharray="${dash} ${circ-dash}" stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})" style="filter:drop-shadow(0 0 4px ${color})"/>
<text x="${cx}" y="${cy-4}" text-anchor="middle" fill="#eee" font-size="${sz*.2}px" font-family="monospace" font-weight="bold">${Math.round(pct)}%</text>
<text x="${cx}" y="${cy+11}" text-anchor="middle" fill="#556" font-size="${sz*.12}px">${label}</text></svg>`;
}
function sln(vals,color,w=140,h=22){
    if(vals.length<2)return `<svg width="${w}" height="${h}"></svg>`;
    let mn=Math.min(...vals),mx=Math.max(...vals);if(mn===mx){mn-=1;mx+=1}
    let rng=mx-mn,pts=[],n=vals.length;
    vals.forEach((v,i)=>{let x=i/(n-1)*(w-4)+2,y=h-2-(v-mn)/rng*(h-4);pts.push(`${x.toFixed(1)},${y.toFixed(1)}`)});
    return `<svg width="${w}" height="${h}"><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.5" opacity=".7"/></svg>`;
}

function devcard(id,name,ip,spec,color,dev){
    if(!dev.online)return `<div class="card" style="border-top:2px solid ${color}"><div class="card-title" style="color:${color}">${name}</div><div style="color:#f44;font-size:12px">OFFLINE</div></div>`;
    let gpu=parseFloat(dev.gpu)||0,gmem=parseFloat(dev.gmem)||0,mut=dev.mem_t=="[N/A]"||dev.mem_t=="0"?131072:parseFloat(dev.mem_t)||131072;
    let tc=gtemp(dev.temp),po=parseFloat(dev.power)||0;
    let clk="";if(dev.clock&&dev.clock!="[N/A]")clk=` · ⏱ ${parseFloat(dev.clock).toFixed(0)}MHz`;
    return `<div class="card" style="border-top:2px solid ${color}">
<div class="card-title" style="color:${color}">${name} <span style="color:#556;font-size:10px;font-weight:normal">${spec} · ${ip}</span></div>
<div class="row"><div class="gauge">${sg("g_"+id,gpu,100,"GPU",color,64)}<div class="gauge-label">GPU</div></div>
<div class="gauge">${sg("m_"+id,gmem/mut*100,100,"VRAM",color,64)}<div class="gauge-label">VRAM</div></div>
<div class="gauge">${sg("t_"+id,parseFloat(dev.temp)||0,100,"TEMP",tc,64)}<div class="gauge-label">TEMP</div></div>
<div style="flex:1"><div class="metric">GPU: <b>${dev.gpu}%</b> · 显存 <b>${dev.mem_u}/${dev.mem_t}</b>MB</div>
<div class="metric">🌡 ${dev.temp}°C · ⚡ ${dev.power}W${clk}</div>
<div class="metric">系统: ${dev.sys_used||'?'}/${dev.sys_total||'?'} · ${dev.load||''} · ${dev.uptime||''}</div></div></div>
<div class="row" style="margin-top:4px;gap:6px">${sln(window["hist_"+id+"_gpu"]||[],color,130,18)}${sln(window["hist_"+id+"_temp"]||[],"#f80",130,18)}</div></div>`;
}

function macOnlyCard(id,name,ip,spec,color,dev){
    if(!dev.online)return'<div class="card" style="border-top:2px solid '+color+'"><div class="card-title" style="color:'+color+'">'+name+'</div><div style="color:#f44;font-size:12px">OFFLINE</div></div>';
    var m=window["hist_"+id+"_cpu"]||[];m.push(dev.cpu||0);if(m.length>150)m=m.slice(-150);window["hist_"+id+"_cpu"]=m;
    return'<div class="card" style="border-top:2px solid '+color+'"><div class="card-title" style="color:'+color+'">'+name+' <span style="color:#556;font-size:10px;font-weight:normal">'+spec+' &middot; '+ip+'</span></div><div class="row">'+sg("g_"+id,dev.cpu||0,100,"CPU",color,64)+'<div><div class="metric">CPU: <b>'+(dev.cpu||0).toFixed(0)+'%</b></div><div class="metric">内存: <b>'+(dev.mem_used||0).toFixed(0)+'</b>/'+(dev.mem_total||128).toFixed(0)+'GB</div></div></div><div style="margin-top:4px">'+sln(m,color,340,22)+'</div></div>';
}
    $("clock").textContent=new Date(d.ts*1000).toLocaleTimeString("zh-CN",{hour12:false});
    // Alerts
    let ahtml="";
    if(d.alerts&&d.alerts.length>0){
        ahtml='<div class="alert-bar">'+d.alerts.map(a=>`<span class="alert ${a.level}">${a.level=="critical"?"🚨":"⚠"} ${a.dev}: ${a.msg}</span>`).join("")+'</div>';
        $("alert-count").innerHTML=`<span style="color:${d.alerts.some(a=>a.level=="critical")?'#f44':'#ff0'}">${d.alerts.length}告警</span>`;
    }else{$("alert-count").textContent="✓ 正常"}
    $("alerts").innerHTML=ahtml;
    // Devices
    let mc=d.devices.mac||{},d1=d.devices.d1||{},d2=d.devices.d2||{};
    let mc_cpu=[];try{mc_cpu=JSON.parse($("hist_mac_cpu")?.value||"[]")}catch(e){}
    mc_cpu.push(mc.cpu||0);if(mc_cpu.length>150)mc_cpu=mc_cpu.slice(-150);
    // Update window histories
    window.hist_mac_cpu=mc_cpu;
    if(mc.online){
        window.hist_d1_gpu=window.hist_d1_gpu||[];window.hist_d1_gpu.push(parseFloat(d1.gpu)||0);if(window.hist_d1_gpu.length>150)window.hist_d1_gpu=window.hist_d1_gpu.slice(-150);
        window.hist_d1_temp=window.hist_d1_temp||[];window.hist_d1_temp.push(parseFloat(d1.temp)||0);if(window.hist_d1_temp.length>150)window.hist_d1_temp=window.hist_d1_temp.slice(-150);
        window.hist_d2_gpu=window.hist_d2_gpu||[];window.hist_d2_gpu.push(parseFloat(d2.gpu)||0);if(window.hist_d2_gpu.length>150)window.hist_d2_gpu=window.hist_d2_gpu.slice(-150);
        window.hist_d2_temp=window.hist_d2_temp||[];window.hist_d2_temp.push(parseFloat(d2.temp)||0);if(window.hist_d2_temp.length>150)window.hist_d2_temp=window.hist_d2_temp.slice(-150);
    }
    let mc_disk=mc.disk_pct||"?";
    let dm='<div class="grid">';
    dm+=`<div class="card" style="border-top:2px solid #0ff"><div class="card-title" style="color:#0ff">🖥 十六万 <span style="color:#556;font-size:10px;font-weight:normal">M3 Ultra 512GB · 192.168.3.47</span></div><div class="row">${sg("mc_cpu",mc.cpu||0,100,"CPU","#0ff",64)}<div><div class="metric">CPU: <b>${(mc.cpu||0).toFixed(0)}%</b></div><div class="metric">内存: <b>${(mc.mem_used||0).toFixed(0)}</b>/${(mc.mem_total||512).toFixed(0)}GB</div><div class="metric">磁盘: ${mc_disk} (${mc.disk_used||"?"}/${mc.disk_total||"?"})</div><div class="metric" style="font-size:9px;color:#445">${mc.gpu_info||"M3 Ultra"}</div></div></div><div style="margin-top:4px">${sln(mc_cpu,"#0ff",340,22)}</div></div>`;
    dm+=devcard("d1","🔥 大傻","192.168.3.55","DGX Spark", "#f0f", d1);
    dm+=devcard("d2","💧 二傻","192.168.3.45","DGX Spark", "#0fa", d2);
    dm+=macOnlyCard("m38","三万八","192.168.3.29","M3 Ultra 96GB","#f80",d.devices.m38||{});
    dm+=macOnlyCard("m4","小四","192.168.3.46","M4 Max 128GB","#07f",d.devices.m4||{});
    dm+="</div>";$("devices").innerHTML=dm;

    // 200G
    let lk=d.link200||{};
    let lkc=lk.up?((lk.latency||999)<1?"#0f0":"#ff0"):"#f44";
    let lkt=lk.up?`${lk.latency.toFixed(2)}ms`:"DOWN";
    $("link200").innerHTML=`<div class="card" style="text-align:center;padding:6px"><span style="font-weight:bold;color:${lkc}">⚡ 200G直连: ${lkt}</span></div>`;

    // Network — 公网ping 矩阵
    var pp=d.public_ping||{},ppa=d.public_ping_all||{};
    var tags=["baidu_ms","ytb_ms","github_ms","google_ms","yahoo_hk_ms"];
    var tagNames={"baidu_ms":"百度","ytb_ms":"YouTube","github_ms":"GitHub","google_ms":"Google","yahoo_hk_ms":"雅虎"};
    var devOrderNW=["大傻","二傻","三万八","小四"];
    var nwExpanded=window.nwExpanded||false;
    function toggleNW(){window.nwExpanded=!window.nwExpanded;render(d)}
    var nwHtml='<div class="card" style="border-color:#3a3a2a"><div class="card-title" style="color:#aa0">🌐 NETWORK LINKS</div><div class="row" style="flex-wrap:wrap;gap:4px;font-size:10px">';
    for(var i=0;i<tags.length;i++){
        var t=tags[i];
        if(pp[t]!==undefined){
            var clr=pp[t]<10?'#0f0':pp[t]<100?'#ff0':'#f80';
            nwHtml+='<span style="color:'+clr+';cursor:pointer" onclick="toggleNW()">'+tagNames[t]+' '+pp[t].toFixed(0)+'ms</span>';
            if(i<tags.length-1)nwHtml+=' | ';
        }
    }
    nwHtml+='</div>';
    if(window.nwExpanded){
        nwHtml+='<div style="margin-top:4px;font-size:9px">';
        for(var i=0;i<tags.length;i++){
            var t=tags[i],det=ppa[t]||{};
            for(var j=0;j<devOrderNW.length;j++){
                var dn=devOrderNW[j],v=det[dn];
                if(v!==undefined)nwHtml+='<div style="display:flex;padding:1px 4px"><span style="width:50px;color:#667">'+dn+'</span><span style="width:80px;color:#556">'+tagNames[t]+'</span><span style="color:'+(v<10?'#0f0':v<100?'#ff0':'#f80')+'">'+v.toFixed(0)+'ms</span></div>';
            }
        }
        nwHtml+='</div>';
    }
    nwHtml+='</div>';
    $("network").innerHTML=nwHtml;

    // Inference — 精简化任务卡
    var it=d.inference_tasks||{};
    function inferCard(devName,tasks){
        if(!tasks||tasks.length===0)
            return'<div class="inf-row"><span class="inf-dev">'+devName+'</span><span class="inf-idle">⏸ 空闲</span></div>';
        var h='';
        for(var i=0;i<tasks.length;i++){
            var t=tasks[i];
            var clr=t.type==='container'?'#0f8':'#ff0';
            var nm=t.name||'';
            var st=t.status||'';
            var statusDot=st==='Up'?'🟢':st.indexOf('Exited')>=0?'🔴':'🟡';
            h+='<div class="inf-row"><span class="inf-dev">'+devName+'</span><span class="inf-type" style="color:'+clr+'">'+(t.type==='container'?'📦':'⚙')+'</span><span class="inf-name">'+nm+'</span><span class="inf-status">'+(statusDot||'')+' '+st+'</span></div>';
        }
        return h;
    }
    var infHtml='<div class="card" style="border-color:#3a2a0a"><div class="card-title" style="color:#fa0">🎯 INFERENCE</div>';
    var devOrder=['二傻','大傻','十六万','三万八','小四'];
    for(var i=0;i<devOrder.length;i++){
        infHtml+=inferCard(devOrder[i],it[devOrder[i]]);
    }
    infHtml+='</div>';
    $("inference").innerHTML=infHtml;

    // Downloads
    let dls=d.downloads||{};
    if(Object.keys(dls).length){
        let tp=0,dn=0,tn=Object.keys(dls).length,ts=0;
        for(let k in dls){tp+=dls[k].pct;if(dls[k].done)dn++;if(!dls[k].done)ts+=dls[k].speed}
        tp/=Math.max(tn,1);let tbw=Math.floor(tp/4),tbar="█".repeat(tbw)+"░".repeat(25-tbw);
        let real_spd=d.dl_real_speed||0;
        let spd_text=real_spd>0?`📡真实 ${real_spd.toFixed(1)}MB/s`:`∑${ts.toFixed(1)}MB/s`;
        let eta="";if(real_spd>0&&tp<100){let etm=54*(100-tp)/100*1024/(real_spd*60);eta=etm>=60?`⏳${(etm/60).toFixed(1)}h`:`⏳${etm.toFixed(0)}min`}
        let dr='<div class="card" style="border-color:#f80"><div class="card-title" style="color:#f80">📥 模型下载</div>';
        dr+=`<div style="margin-bottom:6px;padding-bottom:6px;border-bottom:1px solid #222"><span style="font-size:12px;color:#0ff;font-weight:bold">总计 ${tp.toFixed(0)}%</span><span style="font-size:10px;color:#556;margin-left:8px">${dn}/${tn} · ${spd_text}</span><span style="font-size:10px;color:#fa0;margin-left:8px">${eta}</span><div style="color:#0ff;font-size:10px;margin-top:2px">${tbar}</div></div>`;
        let items=Object.entries(dls).sort((a,b)=>a[1].done-b[1].done||a[1].pct-b[1].pct);
        for(let[fn,inf]of items){
            if(inf.done)dr+=`<div style="font-size:10px;color:#464">✅ [${inf.tag}] ${fn.substring(0,48)}</div>`;
            else{let bw2=Math.floor(inf.pct/5),bar2="█".repeat(bw2)+"░".repeat(20-bw2);
                dr+=`<div style="margin:3px 0"><div class="dl-row"><span style="color:#887;font-size:10px;max-width:300px;overflow:hidden">${fn.substring(0,40)}</span><span style="color:#fa0;font-size:9px">${inf.pct}%</span><span style="color:#665;font-size:9px">${inf.speed.toFixed(1)}MB/s</span></div><div class="dl-bar" style="color:#fa0">${bar2}</div></div>`}
        }
        dr+='</div>';$("downloads").innerHTML=dr;
    }else $("downloads").innerHTML="";

    // Files
    let fs=d.files||[];
    $("files").innerHTML=fs.length?`<div class="card"><div class="card-title">📦 最新输出 (${fs.length})</div>${fs.map(f=>`<div style="font-size:10px;color:#445">📹 ${f}</div>`).join("")}</div>`:"";
}
</script></body></html>'''

# ═══════ HTTP ═══════
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/v1/metrics":
            d = _cache.get("data") if _cache else None
            if d is None:
                d = _cache.get("_bgdata", {}) if _cache else {}
                if not d:
                    d = {"ts":time.time(),"devices":{}}
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
            self.wfile.write(f"retry: 3000\n\n".encode())
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

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str, indent=2).encode())

    def _metrics(self):
        """Prometheus text 格式 /metrics 端点"""
        d = _cache.get("data") or {}
        devs = d.get("devices", {})
        ts_ms = int(time.time() * 1000)
        lines = []
        # HELP/TYPE headers
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
                try: lines.append(f"k38_gpu_util_percent{{{lbl}}} {float(dev.get('gpu',0))} {ts_ms}")
                except: pass
                try: lines.append(f"k38_gpu_mem_util_percent{{{lbl}}} {float(dev.get('gmem',0))} {ts_ms}")
                except: pass
                try: lines.append(f"k38_gpu_temp_celsius{{{lbl}}} {float(dev.get('temp',0))} {ts_ms}")
                except: pass
                try: lines.append(f"k38_gpu_power_watts{{{lbl}}} {float(dev.get('power',0))} {ts_ms}")
                except: pass
                try: lines.append(f"k38_gpu_clock_mhz{{{lbl}}} {float(dev.get('clock',0))} {ts_ms}")
                except: pass
        # 200G link
        lk = d.get("link200", {})
        if lk.get("up"):
            lines.append(f"k38_link200_latency_ms{{link=\"200g\"}} {lk.get('latency',0)} {ts_ms}")
        lines.append(f"k38_collect_ts_ms {{}} {ts_ms} {ts_ms}")
        
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(("\n".join(lines) + "\n").encode())

    def log_message(self,*a): pass

# ═══════ 采集线程 ═══════
_cache = {"data": None, "ts": time.time()}
START_TIME = time.time()

def loop():
    global _cache
    # 第一次同步采集确保立即有数据
    try:
        _cache = {"data": collect(), "ts": time.time(), "_bgdata": {}}
    except Exception as e:
        print(f"INIT COLLECT FAIL: {e}")
        _cache = {"data": {"ts": time.time(), "devices": {}}, "ts": time.time(), "_bgdata": {}}
    import sys; sys.stdout.flush()
    while True:
        try:
            d = collect()
            _cache = {"data": d, "ts": time.time(), "_bgdata": d}
        except:
            pass
        time.sleep(2)

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    os.system("touch /tmp/k38_mon_watch")
    print("K38 Command Center v4 industrial :8899")
    print("  /          → Dashboard (SSE real-time)")
    print("  /api/v1/metrics → JSON API")
    print("  /health    → Health check")
    ThreadingHTTPServer(("0.0.0.0", 8899), H).serve_forever()
