"""Read this machine's hardware stats with the standard library (+ nvidia-smi if present).
Used by `agentctl.py host report --auto`. Missing values are simply left out."""
import os
import platform
import shutil
import socket
import subprocess


def _nvidia():
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,temperature.gpu,power.draw",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10, check=True).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    gpus = [[x.strip() for x in line.split(",")] for line in out.strip().splitlines() if line.strip()]
    if not gpus:
        return {}

    def num(v):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return {
        "gpu": " + ".join(g[0] for g in gpus),
        "vram_used_gb": round(sum(num(g[1]) for g in gpus) / 1024, 1),
        "vram_total_gb": round(sum(num(g[2]) for g in gpus) / 1024, 1),
        "temp_c": max(num(g[3]) for g in gpus),
        "power_w": round(sum(num(g[4]) for g in gpus)),
    }


def _memory():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) / 1024 / 1024  # kB -> GB
        total = info["MemTotal"]
        return {"ram_total_gb": round(total, 1), "ram_used_gb": round(total - info.get("MemAvailable", 0), 1)}
    except (OSError, KeyError, ValueError):
        pass
    if platform.system() == "Darwin":
        try:
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout) / 1024 ** 3
            return {"ram_total_gb": round(total, 1)}
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
    return {}


def collect():
    stats = {**_memory(), **_nvidia()}
    try:
        stats["cpu_pct"] = round(min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100))
    except (OSError, AttributeError):
        pass
    return stats


def hostname():
    return socket.gethostname()
