"""
Print HQ v4.4
  Prints com editor embutido (setas, formas, texto, censura, marca-texto, numeração, OCR, fixar na tela)
  Gravação leve (FFmpeg) de monitor, janela ou área + replay dos últimos segundos
  Ferramentas: reduzir p/ Discord, cortar, GIF  |  Histórico  |  Ícone na bandeja  |  Atalhos editáveis

Instalação (uma vez):
    py -m pip install mss pillow keyboard pywin32 soundcard numpy pystray
"""
import contextlib
import hashlib
import copy
import ctypes
import platform
import ctypes.wintypes
import io
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import mss
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

# IMPORTANTE: antes de criar qualquer janela. Evita prints borrados em telas com escala (125%, 150%...).
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

try:
    import keyboard  # atalhos globais
except Exception:
    keyboard = None
try:
    import win32clipboard  # área de transferência
except Exception:
    win32clipboard = None
try:
    import winreg  # iniciar com o Windows
except Exception:
    winreg = None

VERSION = "4.4.0"
UPDATE_URL = "https://raw.githubusercontent.com/antonioexpedito020-code/print-hq/main/print_hq.pyw"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
CONFIG = Path.home() / ".print_hq.json"
APP_DIR = Path.home() / ".print_hq_data"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
FROZEN = getattr(sys, "frozen", False)

BG, PANEL, FG, MUTED = "#1e1f22", "#2b2d31", "#e6e6e6", "#9aa0a6"
ACCENT, RED = "#4f8cff", "#e5484d"

HK_NAMES = {"region": "Capturar área", "region_quick": "Capturar área e copiar direto",
            "window": "Capturar janela", "full": "Capturar tela inteira",
            "rec": "Iniciar / parar gravação", "pause": "Pausar / retomar gravação",
            "replay_save": "Salvar replay agora", "replay_toggle": "Ligar / desligar replay"}
HK_DEFAULT = {"region": "ctrl+alt+a", "region_quick": "ctrl+alt+q", "window": "ctrl+alt+w", "full": "ctrl+alt+s", "rec": "ctrl+alt+r",
              "pause": "ctrl+alt+p", "replay_save": "ctrl+alt+d", "replay_toggle": "ctrl+alt+y"}

RES_MAP = {"Original": 0, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}
QUALITY = ["Leve (arquivo pequeno)", "Equilibrada", "Alta qualidade", "Máxima"]
QV = {"libx264": [29, 25, 22, 18], "h264_nvenc": [31, 26, 22, 18], "h264_amf": [31, 26, 22, 18],
      "h264_qsv": [31, 26, 22, 18]}
PROFILES = {  # (resolução, fps, qualidade)
    "Mais leve para o PC - 1080p 30 fps": ("1080p", "30", QUALITY[1]),
    "Equilibrado - 1080p 60 fps": ("1080p", "60", QUALITY[1]),
    "Jogo - 1080p 60 fps, alta qualidade": ("1080p", "60", QUALITY[2]),
    "Tutorial - 1080p 30 fps, alta qualidade": ("1080p", "30", QUALITY[2]),
    "Discord leve - 720p 30 fps": ("720p", "30", QUALITY[0]),
    "Baixo consumo - 720p 30 fps": ("720p", "30", QUALITY[1]),
    "Máxima - tela original 60 fps": ("Original", "60", QUALITY[3]),
}
ENC_LABEL = {"h264_nvenc": "placa NVIDIA (NVENC) - leve para o PC", "h264_amf": "placa AMD (AMF) - leve para o PC",
             "h264_qsv": "Intel (QuickSync) - leve para o PC", "libx264": "processador (x264)"}
WIN_LABEL, AREA_LABEL = "Uma janela específica", "Uma área da tela"
STAGE_NAMES = {("ddagrab", True): "captura direta pela placa de vídeo",
               ("ddagrab", False): "captura compatível (passa pelo processador)",
               ("gdigrab", False): "captura alternativa (gdigrab)"}
CAP_MODES = ["Automático (recomendado)", "Compatível (usa mais o processador)"]
ENC_MODES = ["Automático (leve para o PC, otimiza depois)", "Processador (x264)", "Placa de vídeo"]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ================================================================= utilidades
def version_tuple(text):
    return tuple(int(x) for x in text.split("."))


def fetch_update():
    req = urllib.request.Request(UPDATE_URL, headers={"User-Agent": "PrintHQ"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8")


def load_cfg():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def fmt_time(sec):
    sec = int(sec)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def fmt_size(n):
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.1f} MB"


_api = {}


def win_api():
    if not _api:
        u, d = ctypes.windll.user32, ctypes.windll.dwmapi
        cp = ctypes.c_void_p
        u.IsWindowVisible.argtypes = [cp]
        u.IsIconic.argtypes = [cp]
        u.GetWindowTextLengthW.argtypes = [cp]
        u.GetWindowTextW.argtypes = [cp, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowRect.argtypes = [cp, ctypes.POINTER(ctypes.wintypes.RECT)]
        u.GetAncestor.argtypes = [cp, ctypes.c_uint]
        u.GetAncestor.restype = cp
        u.SetWindowDisplayAffinity.argtypes = [cp, ctypes.c_uint]
        d.DwmGetWindowAttribute.argtypes = [cp, ctypes.c_uint, cp, ctypes.c_uint]
        _api["u"], _api["d"] = u, d
    return _api["u"], _api["d"]


def own_hwnd(win):
    if sys.platform != "win32":
        return None
    try:
        win.update_idletasks()
        return win_api()[0].GetAncestor(win.winfo_id(), 2)
    except Exception:
        return None


def exclude_from_capture(win):
    """Faz a janela não aparecer em gravações/prints de tela (Windows 10 2004+)."""
    try:
        h = own_hwnd(win)
        if h:
            win_api()[0].SetWindowDisplayAffinity(h, 0x11)
    except Exception:
        pass


def list_windows(skip=()):
    """Janelas visíveis: [(hwnd, título, (x0, y0, x1, y1))], da mais à frente para a mais ao fundo."""
    if sys.platform != "win32":
        return []
    u, d = win_api()
    res = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        try:
            if hwnd in skip or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            cloaked = ctypes.c_int(0)
            d.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
            if cloaked.value:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == "Program Manager":
                return True
            r = ctypes.wintypes.RECT()
            if d.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:
                u.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left >= 60 and r.bottom - r.top >= 40:
                res.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        except Exception:
            pass
        return True
    u.EnumWindows.argtypes = [proc, ctypes.c_void_p]
    u.EnumWindows(proc(cb), 0)
    return res


def grab_screen():
    """Captura todos os monitores em resolução nativa."""
    with mss.mss() as sct:
        mon = sct.monitors[0]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return img, mon["left"], mon["top"]


def list_monitors():
    try:
        with mss.mss() as sct:
            return list(sct.monitors[1:])
    except Exception:
        return []


def save_image(img, folder, fmt):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("print_%Y-%m-%d_%H-%M-%S")
    if fmt == "PNG":
        path = folder / f"{stamp}.png"
        img.save(path, "PNG", optimize=True)
    elif fmt == "JPEG (q100)":
        path = folder / f"{stamp}.jpg"
        img.save(path, "JPEG", quality=100, subsampling=0)
    else:
        path = folder / f"{stamp}.webp"
        img.save(path, "WEBP", lossless=True, quality=100)
    return path


def open_path(p):
    try:
        open_path(p)
    except Exception:
        with contextlib.suppress(Exception):
            subprocess.Popen(["xdg-open", str(p)])


def _open_clipboard():
    for _ in range(8):  # outro programa pode estar usando a área de transferência por um instante
        try:
            win32clipboard.OpenClipboard()
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("A área de transferência está ocupada por outro programa. Tente de novo.")


def copy_to_clipboard(img):
    if win32clipboard is None:
        return False
    rgb = img.convert("RGB")
    bmp = io.BytesIO()
    rgb.save(bmp, "BMP")
    dib = bmp.getvalue()[14:]
    png = io.BytesIO()
    rgb.save(png, "PNG")
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        with contextlib.suppress(Exception):  # formato PNG: melhor compatibilidade com Discord/navegadores
            win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("PNG"), png.getvalue())
    finally:
        win32clipboard.CloseClipboard()
    return True


def copy_text(txt):
    if win32clipboard is None:
        return False
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, txt)
    finally:
        win32clipboard.CloseClipboard()
    return True


OCR_PS = r'''param([string]$Path)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Foundation.IAsyncOperation`1, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.RandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Text
'''


def ocr_image(img):
    """Lê o texto da imagem com o OCR que já vem no Windows 10/11."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    ps, png = APP_DIR / "ocr.ps1", APP_DIR / "ocr_tmp.png"
    ps.write_text(OCR_PS, encoding="utf-8-sig")
    big = img if img.width >= 400 else img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    big.save(png)
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps),
                        "-Path", str(png)], capture_output=True, timeout=60, creationflags=NOWIN)
    out = r.stdout.decode("utf-8", errors="ignore").strip()
    if not out and r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="ignore")[-300:] or "o OCR do Windows falhou")
    return out


def make_icon(size=64):
    S = size * 4
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((S * .04, S * .04, S * .96, S * .96), radius=S * .22, fill=ACCENT)
    d.rounded_rectangle((S * .2, S * .34, S * .8, S * .74), radius=S * .08, fill="white")
    d.rectangle((S * .38, S * .26, S * .62, S * .36), fill="white")
    d.ellipse((S * .36, S * .4, S * .64, S * .68), fill=ACCENT)
    d.ellipse((S * .43, S * .47, S * .57, S * .61), fill="white")
    return im.resize((size, size), Image.LANCZOS)


def autostart_cmd():
    if FROZEN:
        return f'"{sys.executable}" --tray'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return f'"{pyw if pyw.exists() else exe}" "{Path(__file__).resolve()}" --tray'


def get_autostart():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "PrintHQ")
        return True
    except OSError:
        return False


def set_autostart(on):
    if winreg is None:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "PrintHQ", 0, winreg.REG_SZ, autostart_cmd())
        else:
            with contextlib.suppress(OSError):
                winreg.DeleteValue(k, "PrintHQ")


# ====================================================== anotações (desenho em PIL)
PIXEL_TYPES = ("rect", "ellipse", "blur", "mark")


def arrow_head(x0, y0, x1, y1, width):
    ang = math.atan2(y1 - y0, x1 - x0)
    length = math.hypot(x1 - x0, y1 - y0)
    hl = min(max(14, width * 4.5), length * 0.6)
    hw = hl * 0.45
    bx, by = x1 - hl * math.cos(ang), y1 - hl * math.sin(ang)
    px, py = -math.sin(ang), math.cos(ang)
    return (bx, by), [(x1, y1), (bx + px * hw, by + py * hw), (bx - px * hw, by - py * hw)]


def text_size(width):
    return 10 + int(width) * 4


def num_radius(width):
    return 9 + int(width) * 2


def get_font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def is_light(col):
    c = col.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return (r * 299 + g * 587 + b * 114) / 1000 > 150


def render(img, shapes):
    """Aplica as anotações na imagem em resolução total, com bordas suavizadas."""
    if not shapes:
        return img
    base = img.convert("RGB").copy()
    for s in shapes:  # censura primeiro: o que for desenhado por cima fica nítido
        if s["type"] == "blur":
            x0, x1 = sorted((int(s["x0"]), int(s["x1"])))
            y0, y1 = sorted((int(s["y0"]), int(s["y1"])))
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(base.width, x1), min(base.height, y1)
            if x1 - x0 > 1 and y1 - y0 > 1:
                reg = base.crop((x0, y0, x1, y1))
                block = 4 + int(s["width"]) * 2
                small = reg.resize((max(1, (x1 - x0) // block), max(1, (y1 - y0) // block)), Image.BILINEAR)
                base.paste(small.resize(reg.size, Image.NEAREST), (x0, y0))
    w, h = base.size
    k = 2 if w * h <= 12_000_000 else 1
    over = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for s in shapes:
        col, t = s["color"], s["type"]
        wd = max(1, int(round(s["width"] * k)))
        x0, y0, x1, y1 = (s[n] * k for n in ("x0", "y0", "x1", "y1"))
        if t in ("rect", "ellipse"):
            p = wd / 2
            box = [min(x0, x1) - p, min(y0, y1) - p, max(x0, x1) + p, max(y0, y1) + p]
            (d.rectangle if t == "rect" else d.ellipse)(box, outline=col, width=wd)
        elif t == "mark":
            c = col.lstrip("#")
            rgba = (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 100)
            d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=rgba)
        elif t == "line":
            d.line([(x0, y0), (x1, y1)], fill=col, width=wd)
        elif t == "arrow":
            bs, pts = arrow_head(s["x0"], s["y0"], s["x1"], s["y1"], s["width"])
            d.line([(x0, y0), (bs[0] * k, bs[1] * k)], fill=col, width=wd)
            d.polygon([(px * k, py * k) for px, py in pts], fill=col)
        elif t == "text":
            d.text((x0, y0), s["text"], fill=col, font=get_font(text_size(s["width"]) * k))
        elif t == "num":
            r = num_radius(s["width"]) * k
            d.ellipse((x0 - r, y0 - r, x0 + r, y0 + r), fill=col)
            d.text((x0, y0), str(s["n"]), fill="#111111" if is_light(col) else "#ffffff",
                   font=get_font(int(r * 1.25)), anchor="mm")
    if k > 1:
        over = over.resize((w, h), Image.LANCZOS)
    out = base.convert("RGBA")
    out.alpha_composite(over)
    return out.convert("RGB")


def tool_icon(name, size=22, color="#ffffff"):
    S = size * 6
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw, p = max(2, S // 9), S // 7
    if name == "select":
        pts = [(.22, .10), (.22, .84), (.40, .67), (.54, .93), (.66, .87), (.52, .61), (.76, .61)]
        d.polygon([(x * S, y * S) for x, y in pts], fill=color)
    elif name == "arrow":
        bs, hd = arrow_head(.16 * S, .84 * S, .84 * S, .16 * S, S * .10)
        d.line([(.16 * S, .84 * S), bs], fill=color, width=lw)
        d.polygon(hd, fill=color)
    elif name == "rect":
        d.rectangle((p, p * 1.6, S - p, S - p * 1.6), outline=color, width=lw)
    elif name == "ellipse":
        d.ellipse((p, p, S - p, S - p), outline=color, width=lw)
    elif name == "line":
        d.line([(p, S - p), (S - p, p)], fill=color, width=lw)
    elif name == "text":
        d.text((S / 2, S / 2), "T", fill=color, font=get_font(int(S * .85)), anchor="mm")
    elif name == "blur":
        c = S // 4
        for i in range(4):
            for j in range(4):
                if (i + j) % 2 == 0:
                    d.rectangle((p // 2 + i * c * .86, p // 2 + j * c * .86,
                                 p // 2 + (i + 1) * c * .86 - 2, p // 2 + (j + 1) * c * .86 - 2), fill=color)
    elif name == "mark":
        d.rectangle((p // 2, S * .32, S - p // 2, S * .68), fill="#ffcc00")
    elif name == "num":
        d.ellipse((p // 2, p // 2, S - p // 2, S - p // 2), fill=color)
        d.text((S / 2, S / 2), "1", fill="#1e1f22", font=get_font(int(S * .6)), anchor="mm")
    return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))


# ================================================================ FFmpeg
def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = APP_DIR / "ffmpeg.exe"
    return str(local) if local.exists() else None


BASE_CAP = [8000, 14000, 22000, 36000]  # kbps no limite, em 1080p a 60 fps (por qualidade)


def bitrate_cap(qi, w, h, fps):
    """Teto de bitrate: garante que o arquivo nunca explode, mesmo em cenas muito rápidas."""
    k = BASE_CAP[qi] * (w * h * fps) / (1920 * 1080 * 60)
    return int(min(80000, max(1500, k)))


def out_dims(o):
    kind, mon = o["kind"], o["mon"]
    if kind == "area":
        w, h = o["area"][2], o["area"][3]
    elif kind == "window":
        w, h = 1920, 1080
    else:
        w, h = mon["width"], mon["height"]
    if o["height"] and o["height"] < h:
        w, h = int(w * o["height"] / h) // 2 * 2, o["height"]
    return w, h


def enc_args(enc, qi, fps, cap=None, rich=True):
    # quadro completo (keyframe) só a cada ~8 s: em tela parada isso economiza muito. O replay força os seus próprios.
    q, g = QV[enc][qi], str(fps * 8)
    cap = cap or 20000
    rate = ["-maxrate", f"{cap}k", "-bufsize", f"{cap * 2}k"]
    if enc == "h264_nvenc":
        a = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(q), "-b:v", "0",
             "-profile:v", "high", "-g", g]
        if rich:  # qualidade extra: ajuste adaptativo, olhar à frente e B-frames
            a += ["-tune", "hq", "-spatial-aq", "1", "-aq-strength", "8", "-rc-lookahead", "16", "-bf", "2"] + rate
        return a
    if enc == "h264_amf":
        if rich:  # bitrate-alvo com pico: bem mais eficiente que CQP nesse codificador
            return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "vbr_peak", "-b:v", f"{int(cap * 0.7)}k",
                    "-maxrate", f"{cap}k", "-g", g]
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2), "-g", g]
    if enc == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium" if rich else "veryfast", "-global_quality", str(q), "-g", g]
    # ao vivo o importante é não travar o jogo; o tamanho final fica por conta da otimização depois da gravação
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(q), "-profile:v", "high", "-g", g] + rate


def _run_test(cmd, timeout):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NOWIN).returncode == 0
    except Exception:
        return False


def detect_hw(ff):
    """Testa de verdade o que este PC suporta e guarda o resultado (e o erro) de cada teste."""
    base = [ff, "-hide_banner", "-loglevel", "error"]
    log = []

    def run(name, cmd, timeout):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NOWIN)
            ok = r.returncode == 0
            log.append((name, ok, r.stderr.decode(errors="ignore").strip()[-500:]))
            return ok
        except Exception as ex:
            log.append((name, False, str(ex)))
            return False

    def src_test(enc, rich):
        return base + ["-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30", "-frames:v", "10",
                       "-vf", "format=nv12"] + enc_args(enc, 1, 30, 8000, rich) + ["-f", "null", "-"]

    res = {"encoder": "libx264", "rich": True, "gpu": False, "gpu_vf": None, "log": log}
    for enc in ("h264_nvenc", "h264_amf", "h264_qsv"):
        if not run(f"{enc} (básico)", src_test(enc, False), 30):
            continue
        rich = run(f"{enc} (avançado)", src_test(enc, True), 30)
        res.update(encoder=enc, rich=rich)
        if enc in ("h264_nvenc", "h264_amf"):
            for name, vf in (("direta", None), ("direta + scale_d3d11", "scale_d3d11=format=nv12")):
                cmd = base + ["-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=30", "-frames:v", "12"]
                cmd += (["-vf", vf] if vf else []) + enc_args(enc, 1, 30, 8000, rich) + ["-f", "null", "-"]
                if run(f"captura {name} com {enc}", cmd, 40):
                    res.update(gpu=True, gpu_vf=vf)
                    break
        break
    run("captura ddagrab pelo processador (x264)", base + ["-f", "lavfi", "-i", "ddagrab=output_idx=0:framerate=30",
                                                             "-frames:v", "12", "-vf", "hwdownload,format=bgra,format=yuv420p",
                                                             "-c:v", "libx264", "-preset", "ultrafast", "-f", "null", "-"], 40)
    return res


def video_filter(backend, height, pix, flags="fast_bilinear"):
    """Só reduz (nunca amplia) e garante largura/altura pares."""
    f = ["hwdownload", "format=bgra"] if backend == "ddagrab" else []
    if height:
        scale = f"scale=-2:trunc(min(ih\\,{height})/2)*2"
    else:
        scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    f.append(scale + f":flags={flags}:out_color_matrix=bt709:out_range=tv")
    f.append(f"format={pix}")
    return ",".join(f)


def build_cmd(ff, o, out, backend, audio, replay_wrap=None, gpu=False):
    """gpu=True: a imagem vai direto da captura para o codificador da placa, sem passar pelo processador."""
    fps, mon, kind = o["fps"], o["mon"], o["kind"]
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-nostats"]
    if kind == "window":
        cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1", "-i", "title=" + o["title"]]
    elif backend == "ddagrab":
        src = f"ddagrab=output_idx={o['out_idx']}:framerate={fps}:draw_mouse=1"
        if kind == "area":
            ax, ay, aw, ah = o["area"]
            src += f":video_size={aw}x{ah}:offset_x={ax - mon['left']}:offset_y={ay - mon['top']}"
        cmd += ["-f", "lavfi", "-i", src]
    else:
        if kind == "area":
            ax, ay, aw, ah = o["area"]
        else:
            ax, ay, aw, ah = mon["left"], mon["top"], mon["width"], mon["height"]
        cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1", "-offset_x", str(ax),
                "-offset_y", str(ay), "-video_size", f"{aw}x{ah}", "-i", "desktop"]
    if audio:
        cmd += ["-thread_queue_size", "1024", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", "pipe:0"]
    if gpu and backend == "ddagrab":
        if o.get("gpu_vf"):
            cmd += ["-vf", o["gpu_vf"]]
    else:
        pix = "yuv420p" if o["encoder"] == "libx264" else "nv12"
        cmd += ["-vf", video_filter("gdigrab" if kind == "window" else backend, o["height"], pix)]
    ow, oh = out_dims(o)
    cmd += enc_args(o["encoder"], o["quality"], fps, bitrate_cap(o["quality"], ow, oh, fps), o.get("rich", True))
    cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", f"{[96, 128, 160, 192][o['quality']]}k", "-ar", "48000", "-shortest"]
    if replay_wrap:  # replay: anel de pedaços de 2 s que se sobrescrevem
        cmd += ["-force_key_frames", "expr:gte(t,n_forced*2)", "-f", "segment", "-segment_time", "2",
                "-segment_wrap", str(replay_wrap), "-segment_format", "mpegts", "-reset_timestamps", "1"]
    cmd.append(str(out))
    return cmd


def list_audio():
    """Lista microfones e saídas de som (o áudio do computador é captado por loopback)."""
    try:
        import soundcard as sc
        mics = [m.name for m in sc.all_microphones(include_loopback=False)]
        spks = [s.name for s in sc.all_speakers()]
        try:
            default = sc.default_speaker().name
        except Exception:
            default = spks[0] if spks else None
        return mics, spks, default, None
    except Exception as ex:
        return [], [], None, str(ex)


class AudioFeeder(threading.Thread):
    """Lê microfone e/ou áudio do computador, mistura e envia para o FFmpeg."""
    RATE, BLOCK = 48000, 960  # 20 ms por bloco

    def __init__(self, stdin, mic, spk, offset_ms=0):
        super().__init__(daemon=True)
        self.stdin, self.mic, self.spk, self.offset_ms = stdin, mic, spk, offset_ms
        self.stop_ev = threading.Event()
        self.error = None
        self.t0, self.written = None, 0

    def stop(self):
        self.stop_ev.set()

    def _pad_to_clock(self, final=False):
        """Se o PC engasgou e perdeu áudio, completa com silêncio: o som continua do tamanho do vídeo."""
        if self.t0 is None:
            return
        want = int((time.time() - self.t0) * self.RATE) - self.written
        if want > self.RATE * (0.0 if final else 0.15):
            self.stdin.write(b"\x00" * (want * 8))  # estéreo float32 = 8 bytes por amostra
            self.written += want

    def run(self):
        try:
            with contextlib.suppress(Exception):
                import pythoncom
                pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            self.t0 = time.time()
            if self.offset_ms > 0:
                n = int(self.RATE * self.offset_ms / 1000)
                self.stdin.write(b"\x00" * (n * 8))
                self.written += n
            self._record()
            self._pad_to_clock(final=True)
        except Exception as ex:
            self.error = str(ex)
            self._silence()  # mantém o vídeo gravando mesmo se o áudio falhar
        finally:
            with contextlib.suppress(Exception):
                self.stdin.close()

    def _record(self):
        import numpy as np
        import soundcard as sc
        with contextlib.ExitStack() as stack:
            recs = []
            if self.spk:
                dev = sc.get_microphone(id=self.spk, include_loopback=True)
                recs.append(stack.enter_context(
                    dev.recorder(samplerate=self.RATE, channels=2, blocksize=self.BLOCK * 4)))
            if self.mic:
                dev = sc.get_microphone(id=self.mic)
                ch = max(1, min(2, getattr(dev, "channels", 2)))
                recs.append(stack.enter_context(
                    dev.recorder(samplerate=self.RATE, channels=ch, blocksize=self.BLOCK * 4)))
            skip = int(-self.offset_ms / 20) if self.offset_ms < 0 else 0
            while not self.stop_ev.is_set():
                mix = None
                for r in recs:
                    d = r.record(numframes=self.BLOCK)
                    if d.ndim == 1:
                        d = d[:, None]
                    if d.shape[1] == 1:
                        d = np.repeat(d, 2, axis=1)
                    mix = d if mix is None else mix + d
                if skip > 0:
                    skip -= 1
                    self.t0 += self.BLOCK / self.RATE  # som descartado de propósito (ajuste negativo)
                    continue
                self.stdin.write(np.clip(mix, -1, 1).astype("<f4").tobytes())
                self.written += self.BLOCK
                self._pad_to_clock()
                self.stdin.flush()

    def _silence(self):
        z, t0, n = b"\x00" * (self.BLOCK * 8), time.time(), 0
        while not self.stop_ev.is_set():
            try:
                self.stdin.write(z)
                self.stdin.flush()
            except Exception:
                return
            n += 1
            wait = t0 + n * 0.02 - time.time()
            if wait > 0:
                time.sleep(wait)


class Recorder:
    """Grava em segmentos (permite pausar/retomar) ou mantém o replay em anel."""

    def __init__(self, app):
        self.app = app
        self.state = "idle"  # idle | recording | paused | finalizing | replay
        self.mode = "record"
        self.proc = self.feeder = self.logf = None
        self.segments, self.acc, self.seg_t0 = [], 0.0, 0.0
        self.opts, self.tmp = None, None
        self.stages, self.stage_i = [], 0
        self.last_cmd, self.last_stage = [], None
        self.replay_secs = 30

    def elapsed(self):
        return self.acc + (time.time() - self.seg_t0 if self.state == "recording" else 0)

    def size(self):
        total = 0
        for s in self.segments:
            with contextlib.suppress(OSError):
                total += s.stat().st_size
        return total

    def _stages(self, opts):
        if opts["kind"] == "window":
            return [("gdigrab", False)]
        st = ([("ddagrab", True)] if opts.get("gpu") else []) + [("ddagrab", False), ("gdigrab", False)]
        pref = opts.get("stage_pref")
        if pref and tuple(pref) in st:  # etapa que o "Testar desempenho" mostrou que acompanha o FPS
            st = st[st.index(tuple(pref)):]
        return st

    def start(self, opts):
        self.mode, self.opts = "record", opts
        self.stages, self.stage_i = self._stages(opts), 0
        self.segments, self.acc = [], 0.0
        self.tmp = APP_DIR / "tmp" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._launch()
        self.state = "recording"
        self.app.root.after(2200, self._health)

    def start_replay(self, opts, secs):
        self.mode, self.opts, self.replay_secs = "replay", opts, secs
        self.stages, self.stage_i = self._stages(opts), 0
        self.segments = []
        self.tmp = APP_DIR / "replay"
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._launch()
        self.state = "replay"
        self.app.root.after(2200, self._health)

    def _launch(self):
        o, n = self.opts, len(self.segments)
        backend, gpu = self.stages[self.stage_i]
        audio = bool(o["mic"] or o["spk"])
        if self.mode == "replay":
            need = int(self.replay_secs / 2) + 2
            cmd = build_cmd(o["ffmpeg"], o, self.tmp / "rp%03d.ts", backend, audio, replay_wrap=need + 3, gpu=gpu)
        else:
            seg = self.tmp / f"seg{n:03d}.mkv"
            self.segments.append(seg)
            cmd = build_cmd(o["ffmpeg"], o, seg, backend, audio, gpu=gpu)
        self.last_cmd, self.last_stage = cmd, (backend, gpu)
        self.logf = open(self.tmp / f"log{n}.txt", "wb")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=self.logf, creationflags=NOWIN)
        self.feeder = None
        if audio:
            self.feeder = AudioFeeder(self.proc.stdin, o["mic"], o["spk"], o.get("offset_ms", 0))
            self.feeder.start()
        self.seg_t0 = time.time()

    def tail_logs(self):
        txt = ""
        if self.tmp:
            for p in sorted(self.tmp.glob("log*.txt")):
                with contextlib.suppress(Exception):
                    txt += p.read_text(errors="ignore")[-600:]
        return txt.strip()[-600:]

    def _health(self):
        """Se o FFmpeg morreu logo no início, desce para o próximo modo de captura."""
        if self.state not in ("recording", "replay") or self.proc is None or self.proc.poll() is None:
            return
        err = self.tail_logs()
        if self.stage_i + 1 < len(self.stages):
            self.stage_i += 1
            self._kill()
            if self.mode == "record" and self.segments:
                self.segments.pop()
            self._launch()
            self.app.ui_queue.put(lambda: self.app.status.set("Usando o modo de captura compatível…"))
            self.app.root.after(2200, self._health)
        else:
            self.app.ui_queue.put(lambda: self.app.rec_failed(err))

    def _kill(self):
        if self.feeder:
            self.feeder.stop()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.logf.close()
        self.proc = self.feeder = None

    def _end_segment(self):
        p = self.proc
        if p is None:
            return
        if self.feeder:
            self.feeder.stop()
            self.feeder.join(timeout=5)
        else:
            with contextlib.suppress(Exception):
                p.stdin.write(b"q")
                p.stdin.flush()
                p.stdin.close()
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        with contextlib.suppress(Exception):
            self.logf.close()
        self.proc = self.feeder = None

    def pause(self):
        if self.state != "recording":
            return
        self.acc += time.time() - self.seg_t0
        self.state = "paused"
        self._end_segment()

    def resume(self):
        if self.state != "paused":
            return
        self._launch()
        self.state = "recording"

    def begin_stop(self):
        was = self.state
        if was == "recording":
            self.acc += time.time() - self.seg_t0
        self.state = "finalizing"
        return was

    def finalize(self, was, out):
        if was == "recording":
            self._end_segment()
        ff = self.opts["ffmpeg"]
        segs = [s for s in self.segments if s.exists() and s.stat().st_size > 2048]
        if not segs:
            raise RuntimeError("Nenhum vídeo foi gravado. " + self.tail_logs())
        self._concat(ff, segs, out)
        with contextlib.suppress(Exception):  # fica disponível para o Diagnóstico
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "ultima_gravacao.txt").write_text(
                f"ETAPA: {self.last_stage}\nCOMANDO:\n{' '.join(map(str, self.last_cmd))}\n\nLOG DO FFMPEG:\n{self.tail_logs()}\n",
                encoding="utf-8")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _concat(self, ff, files, out):
        if len(files) == 1:
            cmd = [ff, "-y", "-loglevel", "error", "-i", str(files[0]), "-c", "copy",
                   "-movflags", "+faststart", str(out)]
        else:
            lst = files[0].parent / "list.txt"
            lst.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in files), encoding="utf-8")
            cmd = [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                   "-c", "copy", "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, capture_output=True, creationflags=NOWIN)
        if r.returncode != 0 or not Path(out).exists():
            raise RuntimeError("Falha ao finalizar o vídeo: " + r.stderr.decode(errors="ignore")[-400:])

    def stop_replay(self):
        self._end_segment()
        self.state = "idle"
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save_replay(self, out):
        """Junta os últimos segundos do anel em um MP4."""
        files = sorted(self.tmp.glob("rp*.ts"), key=lambda p: p.stat().st_mtime)
        files = [f for f in files if f.stat().st_size > 2048][-(int(self.replay_secs / 2) + 1):]
        if not files:
            raise RuntimeError("O replay ainda não tem nada gravado. Aguarde alguns segundos.")
        cp = self.tmp / "save"
        shutil.rmtree(cp, ignore_errors=True)
        cp.mkdir()
        copies = []
        for f in files:  # copia antes: o anel continua sobrescrevendo os arquivos
            shutil.copy2(f, cp / f.name)
            copies.append(cp / f.name)
        try:
            self._concat(self.opts["ffmpeg"], copies, out)
        finally:
            shutil.rmtree(cp, ignore_errors=True)


# ------------------------------------------------------------- ferramentas de vídeo
def probe_duration(ff, path):
    r = subprocess.run([ff, "-hide_banner", "-i", str(path)], capture_output=True, creationflags=NOWIN)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode(errors="ignore"))
    if not m:
        raise RuntimeError("Não consegui ler a duração do vídeo.")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def parse_time(s):
    s = s.strip()
    if not s:
        return None
    t = 0.0
    for part in s.replace(",", ".").split(":"):
        t = t * 60 + float(part)
    return t


def discord_cmd(ff, src, out, dur, mb):
    total = mb * 1024 * 1024 * 8 * 0.92  # 8% de folga
    vk = int((total / dur - 96000) / 1000)
    if vk < 80:
        raise RuntimeError("O vídeo é longo demais para esse limite. Corte-o antes ou aumente o limite.")
    h = 1080 if vk >= 2500 else 720 if vk >= 1200 else 480 if vk >= 500 else 360
    vf = f"scale=-2:trunc(min(ih\\,{h})/2)*2" + (",fps=30" if vk < 4000 else "")
    return [ff, "-y", "-i", str(src), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-b:v", f"{vk}k",
            "-maxrate", f"{int(vk * 1.15)}k", "-bufsize", f"{vk * 2}k", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)]


def trim_cmd(ff, src, out, start, span, precise, enc, qi, rich=True):
    cmd = [ff, "-y", "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{span:.3f}"]
    if precise:
        cmd += ["-vf", "format=yuv420p" if enc == "libx264" else "format=nv12"]
        cmd += enc_args(enc, qi, 60, rich=rich) + ["-c:a", "aac", "-b:a", "160k"]
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    return cmd + ["-movflags", "+faststart", str(out)]


def gif_cmd(ff, src, out, start, span, width, fps):
    vf = (f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
          "[s0]palettegen[p];[s1][p]paletteuse")
    return [ff, "-y", "-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", str(src), "-vf", vf, "-loop", "0", str(out)]


def optimize_cmd(ff, src, out, qi, height=0):
    crf = [31, 27, 24, 20][qi]  # um pouco acima do da gravação: aqui o objetivo é encolher
    cmd = [ff, "-y", "-i", str(src)]
    if height:  # reduz a resolução com calma (alta qualidade), já que o jogo não está mais rodando
        cmd += ["-vf", f"scale=-2:trunc(min(ih\\,{height})/2)*2:flags=lanczos"]
    return cmd + ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-g", "600", "-pix_fmt", "yuv420p",
                  "-c:a", "copy", "-movflags", "+faststart", str(out)]


def count_frames(ff, path):
    """Quantos quadros de vídeo o arquivo tem de verdade (mede se a gravação acompanhou o FPS)."""
    try:
        r = subprocess.run([ff, "-hide_banner", "-an", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
                            "-f", "null", "-"], capture_output=True, timeout=180,
                           creationflags=NOWIN | (0x4000 if sys.platform == "win32" else 0))
        m = re.findall(r"frame=\s*(\d+)", r.stderr.decode(errors="ignore"))
        return int(m[-1]) if m else 0
    except Exception:
        return 0


def run_job(cmd, total, on_progress, low=False):
    full = cmd[:1] + ["-progress", "pipe:1", "-nostats", "-loglevel", "error"] + cmd[1:]
    p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         creationflags=NOWIN | (0x4000 if low and sys.platform == "win32" else 0))
    errs = []
    for line in p.stdout:
        s = line.decode(errors="ignore").strip()
        m = re.match(r"out_time_(?:us|ms)=(\d+)", s)
        if m and total:
            on_progress(min(100, int(m[1]) / 1e6 / total * 100))
        elif s and "=" not in s:
            errs.append(s)
    return p.wait(), "\n".join(errs)


# ============================================================== interface
def setup_style(root):
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=("Segoe UI", 10),
                 bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, focuscolor=BG)
    st.configure("TFrame", background=BG)
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Muted.TLabel", foreground=MUTED)
    st.configure("Head.TLabel", font=("Segoe UI", 10, "bold"), foreground=ACCENT)
    st.configure("Big.TLabel", font=("Segoe UI", 26, "bold"))
    st.configure("Key.TLabel", background=PANEL, padding=(10, 4), font=("Consolas", 10, "bold"))
    st.configure("TButton", background=PANEL, foreground=FG, padding=(10, 6), borderwidth=0)
    st.map("TButton", background=[("active", "#3a3d44"), ("disabled", "#232427")],
           foreground=[("disabled", "#666")])
    st.configure("Accent.TButton", background=ACCENT, foreground="white", font=("Segoe UI", 10, "bold"))
    st.map("Accent.TButton", background=[("active", "#7aa5ff"), ("disabled", "#2e3a5c")])
    st.configure("Rec.TButton", background=RED, foreground="white", font=("Segoe UI", 11, "bold"), padding=(12, 9))
    st.map("Rec.TButton", background=[("active", "#ff6166"), ("disabled", "#5a2a2c")])
    st.configure("TCheckbutton", background=BG, foreground=FG)
    st.map("TCheckbutton", background=[("active", BG)])
    st.configure("TCombobox", arrowsize=14, padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", "#222428"), ("disabled", "#232427")],
           foreground=[("readonly", FG), ("disabled", "#666")],
           selectbackground=[("readonly", "#222428")], selectforeground=[("readonly", FG)])
    st.configure("TEntry", fieldbackground="#222428", foreground=FG, insertcolor=FG, padding=4)
    st.configure("TSpinbox", fieldbackground="#222428", foreground=FG, insertcolor=FG, arrowsize=12, padding=3)
    st.configure("Vertical.TScrollbar", background="#383b42", troughcolor=BG, arrowcolor=FG, bordercolor=BG,
                 lightcolor="#383b42", darkcolor="#383b42")
    st.map("Vertical.TScrollbar", background=[("active", "#454951")])
    st.configure("TSpinbox", arrowsize=12, padding=3)
    st.configure("TNotebook", background=BG, borderwidth=0)
    st.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(12, 8), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", FG)])
    st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, borderwidth=0)
    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)


class Tip:
    def __init__(self, w, text):
        self.w, self.text, self.tw, self.job = w, text, None, None
        w.bind("<Enter>", self.enter, add="+")
        w.bind("<Leave>", self.leave, add="+")
        w.bind("<ButtonPress>", self.leave, add="+")

    def enter(self, e):
        self.job = self.w.after(450, self.show)

    def show(self):
        x, y = self.w.winfo_rootx(), self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tw = tw = tk.Toplevel(self.w)
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        tw.geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, bg="#111", fg="white", padx=6, pady=2, font=("Segoe UI", 9)).pack()

    def leave(self, e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tw:
            self.tw.destroy()
            self.tw = None


def show_toast(root, img, text, buttons=(), seconds=5):
    """Notificação no canto da tela, com miniatura (opcional) e botões."""
    bg = "#1f1f1f"
    t = tk.Toplevel(root)
    t.overrideredirect(True)
    t.attributes("-topmost", True)
    t.configure(bg=bg, highlightbackground="#666", highlightthickness=1)
    col = 0
    if img is not None:
        thumb = img.copy()
        thumb.thumbnail((180, 110))
        ph = ImageTk.PhotoImage(thumb)
        lbl = tk.Label(t, image=ph, bg=bg)
        lbl.image = ph
        lbl.grid(row=0, column=0, padx=10, pady=10)
        col = 1
    right = tk.Frame(t, bg=bg)
    right.grid(row=0, column=col, padx=(10 if col == 0 else 0, 12), pady=10, sticky="n")
    tk.Label(right, text=text, fg="white", bg=bg, font=("Segoe UI", 10, "bold"),
             wraplength=260, justify="left").pack(anchor="w")
    bf = tk.Frame(right, bg=bg)
    bf.pack(anchor="w", pady=(8, 0))
    for label, cb in buttons:
        tk.Button(bf, text=label, command=lambda cb=cb: (t.destroy(), cb())).pack(side="left", padx=(0, 6))
    tk.Button(bf, text="✕", command=t.destroy).pack(side="left")
    t.update_idletasks()
    x = root.winfo_screenwidth() - t.winfo_reqwidth() - 20
    y = root.winfo_screenheight() - t.winfo_reqheight() - 70
    t.geometry(f"+{x}+{y}")
    exclude_from_capture(t)

    def close():
        with contextlib.suppress(tk.TclError):
            t.destroy()
    t.after(int(seconds * 1000), close)


class PinWindow:
    """Print flutuante sempre à frente: arraste para mover, roda do mouse muda o tamanho, botão direito fecha."""

    def __init__(self, app, img):
        self.orig, self.scale = img, 1.0
        sw, sh = app.root.winfo_screenwidth(), app.root.winfo_screenheight()
        self.scale = min(1.0, sw * 0.8 / img.width, sh * 0.8 / img.height)
        w = self.win = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        self.label = tk.Label(w, bd=0, highlightthickness=2, highlightbackground=ACCENT)
        self.label.pack()
        self.show()
        w.geometry(f"+{(sw - w.winfo_reqwidth()) // 2}+{(sh - w.winfo_reqheight()) // 2}")
        self.drag = (0, 0)
        for wid in (w, self.label):
            wid.bind("<ButtonPress-1>", self.press)
            wid.bind("<B1-Motion>", self.move)
            wid.bind("<Button-3>", lambda e: w.destroy())
            wid.bind("<MouseWheel>", self.wheel)

    def show(self):
        w, h = max(40, int(self.orig.width * self.scale)), max(40, int(self.orig.height * self.scale))
        self.photo = ImageTk.PhotoImage(self.orig.resize((w, h), Image.LANCZOS))
        self.label.configure(image=self.photo)

    def press(self, e):
        self.drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def move(self, e):
        self.win.geometry(f"+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}")

    def wheel(self, e):
        self.scale = min(3.0, max(0.1, self.scale * (1.1 if e.delta > 0 else 0.9)))
        self.show()


# ===================================== captura com editor embutido
TOOLS = [("select", "v", "Selecionar / mover (V)"), ("arrow", "a", "Seta (A)"), ("rect", "r", "Retângulo (R)"),
         ("ellipse", "o", "Elipse (O)"), ("line", "l", "Linha (L)"), ("text", "t", "Texto (T)"),
         ("blur", "b", "Censurar - pixeliza a área (B)"), ("mark", "m", "Marca-texto (M)"),
         ("num", "n", "Numeração 1, 2, 3 (N)")]
COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#000000", "#ffffff"]


class SnapOverlay:
    """Tela congelada. mode: region | full | window | pick (só escolhe a área, p/ gravação)."""

    def __init__(self, app, img, left, top, prev, mode="region", on_pick=None, quick=False):
        self.app, self.img, self.prev, self.mode, self.on_pick = app, img, prev, mode, on_pick
        self.left, self.top = left, top
        self.editor = (not quick) and app.use_editor.get()
        self.W, self.H = img.size
        self.phase, self.box = "select", None
        self.shapes, self.undo = [], []
        self.sel, self.uid, self.drag = None, 0, None
        self.color, self.width, self.syncing = COLORS[0], 4, False
        self.entry = self.entry_win = self.entry_pos = None
        self.sel_start, self.hover, self.armed, self.wins = None, None, False, []

        w = self.win = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.geometry(f"{self.W}x{self.H}{left:+d}{top:+d}")
        c = self.canvas = tk.Canvas(w, width=self.W, height=self.H, highlightthickness=0,
                                    cursor="crosshair", bg="black")
        c.pack()
        self.bg = ImageTk.PhotoImage(ImageEnhance.Brightness(img).enhance(0.45))
        c.create_image(0, 0, anchor="nw", image=self.bg)
        msg = ("Clique na janela que deseja capturar  •  Esc cancela" if mode == "window" else
               "Arraste para selecionar a área  •  Esc cancela")
        self.hint = c.create_text(self.W // 2, 40, fill="white", font=("Segoe UI", 14, "bold"), text=msg)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Motion>", self.on_motion)
        w.bind("<Key>", self.on_key)
        w.bind("<Escape>", self.cancel)
        w.bind("<Return>", lambda e: None if self.typing() else self.finish(True, False))
        w.bind("<Control-c>", lambda e: None if self.typing() else self.finish(True, False))
        w.bind("<Control-s>", lambda e: None if self.typing() else self.finish(False, True))
        w.bind("<Control-z>", lambda e: None if self.typing() else self.do_undo())
        w.bind("<Delete>", lambda e: None if self.typing() else self.delete_sel())
        exclude_from_capture(w)
        w.focus_force()
        c.focus_set()
        if mode == "window":
            mine = own_hwnd(w)
            for _, title, (x0, y0, x1, y1) in list_windows(skip={mine}):
                self.wins.append((title, (x0 - left, y0 - top, x1 - left, y1 - top)))
        if mode == "full":
            self.enter_edit((0, 0, self.W, self.H))

    def typing(self):
        return self.entry is not None

    # ---------- fase 1: escolher a área / janela
    def on_motion(self, e):
        if self.mode != "window" or self.phase != "select":
            return
        hit = next(((t, r) for t, r in self.wins if r[0] <= e.x <= r[2] and r[1] <= e.y <= r[3]), None)
        if hit == self.hover:
            return
        self.hover = hit
        self.canvas.delete("hl")
        if hit:
            t, r = hit
            self.canvas.create_rectangle(*r, outline=ACCENT, width=3, tags="hl")
            self.canvas.create_text(r[0] + 8, max(12, r[1] - 12), anchor="w", text=t[:60], fill="white",
                                    font=("Segoe UI", 10, "bold"), tags="hl")

    def on_press(self, e):
        if self.phase == "edit":
            return self.edit_press(e)
        if self.mode == "window":
            if self.hover:
                r = self.hover[1]
                self.after_select((max(0, r[0]), max(0, r[1]), min(self.W, r[2]), min(self.H, r[3])))
            return
        self.sel_start = (e.x, e.y)
        self.canvas.delete("sel")
        self.canvas.create_rectangle(e.x, e.y, e.x, e.y, outline=ACCENT, width=2, tags=("sel", "selrect"))
        self.canvas.create_text(e.x + 8, e.y - 12, anchor="w", fill="white", tags=("sel", "sellabel"),
                                font=("Segoe UI", 10, "bold"))

    def on_drag(self, e):
        if self.phase == "edit":
            return self.edit_drag(e)
        if not self.sel_start:
            return
        x0, y0 = self.sel_start
        self.canvas.coords("selrect", x0, y0, e.x, e.y)
        self.canvas.coords("sellabel", max(e.x, x0) + 8, min(e.y, y0) - 12)
        self.canvas.itemconfig("sellabel", text=f"{abs(e.x - x0)} x {abs(e.y - y0)}")

    def on_release(self, e):
        if self.phase == "edit":
            return self.edit_release(e)
        if not self.sel_start:
            return
        x0, y0 = self.sel_start
        self.sel_start = None
        x1, x2 = sorted((max(0, min(x0, e.x)), max(0, max(x0, e.x))))
        y1, y2 = sorted((max(0, min(y0, e.y)), max(0, max(y0, e.y))))
        x2, y2 = min(x2, self.W), min(y2, self.H)
        if x2 - x1 < 5 or y2 - y1 < 5:
            self.canvas.delete("sel")
            return
        self.after_select((x1, y1, x2, y2))

    def after_select(self, box):
        if self.mode == "pick":
            absbox = (box[0] + self.left, box[1] + self.top, box[2] + self.left, box[3] + self.top)
            self.close()
            self.app.restore(self.prev)
            self.on_pick(absbox)
        elif self.editor:
            self.enter_edit(box)
        else:
            self.close()
            self.app.finish_snap(self.prev, self.img.crop(box), True, False)

    # ---------- fase 2: anotar
    def enter_edit(self, box):
        c = self.canvas
        c.delete("sel")
        c.delete("hl")
        c.delete(self.hint)
        self.phase, self.box = "edit", box
        self.ox, self.oy = box[0], box[1]
        self.bw, self.bh = box[2] - box[0], box[3] - box[1]
        self.crop = self.img.crop(box)
        self.bright = ImageTk.PhotoImage(self.crop)
        c.create_image(self.ox, self.oy, anchor="nw", image=self.bright, tags="bg")
        c.create_rectangle(self.ox, self.oy, self.ox + self.bw, self.oy + self.bh,
                           outline=ACCENT, width=2, tags="border")
        self.build_toolbar()

    def build_toolbar(self):
        bar = tk.Frame(self.canvas, bg=PANEL, highlightbackground="#555", highlightthickness=1)
        row1 = tk.Frame(bar, bg=PANEL)
        row1.pack(fill="x", padx=4, pady=(4, 0))
        row2 = tk.Frame(bar, bg=PANEL)
        row2.pack(fill="x", padx=4, pady=(2, 4))
        self.tool = tk.StringVar(value="arrow")
        self.icons = {}
        kw = dict(bg=PANEL, fg="white", activebackground="#3a3d44", activeforeground="white",
                  relief="flat", bd=0, padx=8, pady=4, takefocus=0)
        for name, _, tip in TOOLS:
            self.icons[name] = tool_icon(name)
            r = tk.Radiobutton(row1, image=self.icons[name], variable=self.tool, value=name, indicatoron=0,
                               selectcolor=ACCENT, **kw)
            r.pack(side="left", padx=1)
            Tip(r, tip)
        tk.Frame(row1, bg="#555", width=1).pack(side="left", fill="y", padx=6, pady=4)
        for col in COLORS:
            tk.Button(row1, bg=col, width=2, relief="solid", bd=1, takefocus=0,
                      command=lambda col=col: self.set_color(col)).pack(side="left", padx=2)
        self.slider = tk.Scale(row1, from_=1, to=16, orient="horizontal", length=80, showvalue=False,
                               bg=PANEL, troughcolor="#444", highlightthickness=0, takefocus=0,
                               command=self.on_width)
        self.syncing = True
        self.slider.set(self.width)
        self.syncing = False
        self.slider.bind("<ButtonPress-1>", lambda e: self.snap() if self.sel else None)
        self.slider.pack(side="left", padx=(8, 4))
        Tip(self.slider, "Espessura / tamanho")

        def btn(text, cmd, tip, bgc=PANEL, bold=False):
            k2 = dict(kw)
            k2["bg"] = bgc
            b = tk.Button(row2, text=text, command=cmd, font=("Segoe UI", 10, "bold" if bold else "normal"), **k2)
            b.pack(side="left", padx=2)
            Tip(b, tip)
        btn("Desfazer", self.do_undo, "Ctrl+Z")
        btn("Apagar", self.delete_sel, "Apaga a forma selecionada (Del)")
        btn("Fixar na tela", lambda: self.finish(False, False, pin=True), "Deixa o print flutuando sobre as janelas")
        btn("Ler texto (OCR)", lambda: self.finish(False, False, ocr=True), "Copia o texto que aparece na imagem")
        tk.Frame(row2, bg=PANEL).pack(side="left", expand=True, fill="x")
        btn("Cancelar", self.cancel, "Esc", "#5a2a2c")
        btn("Salvar", lambda: self.finish(False, True), "Ctrl+S")
        btn("Copiar", lambda: self.finish(True, False), "Ctrl+C ou Enter", ACCENT, True)

        bar.update_idletasks()
        bw, bh = bar.winfo_reqwidth(), bar.winfo_reqheight()
        x0, y0, x1, y1 = self.box
        x = min(max(4, x0), max(4, self.W - bw - 4))
        if y1 + 8 + bh <= self.H:
            y = y1 + 8
        elif y0 - 8 - bh >= 0:
            y = y0 - 8 - bh
        else:
            y = max(4, y1 - bh - 40)
        if x1 - x0 >= self.W - 2:
            x = max(4, (self.W - bw) // 2)
        self.canvas.create_window(x, y, window=bar, anchor="nw", tags="toolbar")

    # ---------- estado / estilo
    def snap(self):
        self.undo.append(copy.deepcopy(self.shapes))
        self.undo = self.undo[-50:]

    def do_undo(self, e=None):
        if self.phase == "edit" and self.undo:
            self.shapes = self.undo.pop()
            self.sel = None
            self.redraw()

    def get(self, uid):
        return next((s for s in self.shapes if s["uid"] == uid), None) if uid else None

    def new_shape(self, kind, x, y):
        self.uid += 1
        return {"uid": self.uid, "type": kind, "x0": x, "y0": y, "x1": x, "y1": y,
                "color": self.color, "width": self.width}

    def lclamp(self, x, y):
        return min(max(x, 0), self.bw), min(max(y, 0), self.bh)

    def on_key(self, e):
        if self.phase != "edit" or self.typing():
            return
        for name, key, _ in TOOLS:
            if e.char and e.char.lower() == key:
                self.tool.set(name)

    def delete_sel(self, e=None):
        s = self.get(self.sel)
        if s:
            self.snap()
            self.shapes.remove(s)
            self.sel = None
            self.redraw()

    def set_color(self, col):
        self.color = col
        s = self.get(self.sel)
        if s:
            self.snap()
            s["color"] = col
            self.redraw()

    def on_width(self, v):
        if self.syncing:
            return
        self.width = int(float(v))
        s = self.get(self.sel)
        if s and s["width"] != self.width:
            s["width"] = self.width
            self.redraw()

    # ---------- desenho na tela
    def redraw(self):
        c = self.canvas
        c.delete("shape")
        c.delete("handle")
        for s in self.shapes:
            self.draw_shape(s)
        s = self.get(self.sel)
        if s:
            self.draw_handles(s)

    def draw_shape(self, s):
        c, ox, oy = self.canvas, self.ox, self.oy
        x0, y0, x1, y1 = s["x0"] + ox, s["y0"] + oy, s["x1"] + ox, s["y1"] + oy
        w, col, t = max(1, s["width"]), s["color"], s["type"]
        tags = ("shape", f"s{s['uid']}")
        if t == "rect":
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=w, tags=tags)
        elif t == "ellipse":
            c.create_oval(x0, y0, x1, y1, outline=col, width=w, tags=tags)
        elif t == "blur":
            c.create_rectangle(x0, y0, x1, y1, fill="#000000", stipple="gray50", outline=ACCENT,
                               dash=(4, 3), tags=tags)
        elif t == "mark":
            c.create_rectangle(x0, y0, x1, y1, fill=col, stipple="gray50", outline="", tags=tags)
        elif t == "line":
            c.create_line(x0, y0, x1, y1, fill=col, width=w, capstyle="round", tags=tags)
        elif t == "arrow":
            bs, pts = arrow_head(s["x0"], s["y0"], s["x1"], s["y1"], s["width"])
            c.create_line(x0, y0, bs[0] + ox, bs[1] + oy, fill=col, width=w, capstyle="round", tags=tags)
            c.create_polygon([(px + ox, py + oy) for px, py in pts], fill=col, outline=col, tags=tags)
        elif t == "text":
            c.create_text(x0, y0, anchor="nw", text=s["text"], fill=col, tags=tags,
                          font=("Segoe UI", -text_size(s["width"]), "bold"))
        elif t == "num":
            r = num_radius(s["width"])
            c.create_oval(x0 - r, y0 - r, x0 + r, y0 + r, fill=col, outline="", tags=tags)
            c.create_text(x0, y0, text=str(s["n"]), fill="#111111" if is_light(col) else "#ffffff",
                          font=("Segoe UI", -int(r * 1.25), "bold"), tags=tags)

    def handle_points(self, s):
        if s["type"] in PIXEL_TYPES:
            return [(s["x0"], s["y0"]), (s["x1"], s["y1"]), (s["x0"], s["y1"]), (s["x1"], s["y0"])]
        if s["type"] in ("line", "arrow"):
            return [(s["x0"], s["y0"]), (s["x1"], s["y1"])]
        return []

    def draw_handles(self, s):
        c = self.canvas
        for i, (x, y) in enumerate(self.handle_points(s)):
            x, y = x + self.ox, y + self.oy
            c.create_rectangle(x - 5, y - 5, x + 5, y + 5, fill="white", outline=ACCENT, width=2,
                               tags=("handle", f"h{i}"))
        if s["type"] in ("text", "num"):
            bb = c.bbox(f"s{s['uid']}")
            if bb:
                c.create_rectangle(bb[0] - 3, bb[1] - 3, bb[2] + 3, bb[3] + 3, outline=ACCENT,
                                   dash=(3, 3), tags="handle")

    def hit_handle(self, x, y):
        for it in reversed(self.canvas.find_overlapping(x - 6, y - 6, x + 6, y + 6)):
            for t in self.canvas.gettags(it):
                if len(t) == 2 and t[0] == "h" and t[1].isdigit():
                    return int(t[1])
        return None

    def hit_shape(self, x, y):
        for it in reversed(self.canvas.find_overlapping(x - 5, y - 5, x + 5, y + 5)):
            tags = self.canvas.gettags(it)
            if "shape" in tags:
                for t in tags:
                    if t.startswith("s") and t[1:].isdigit():
                        return int(t[1:])
        return None

    # ---------- mouse (edição)
    def edit_press(self, e):
        if self.entry:
            self.commit_text()
        lx, ly = e.x - self.ox, e.y - self.oy
        self.drag = None
        h = self.hit_handle(e.x, e.y) if self.sel else None
        if h is not None:
            self.drag = {"mode": "resize", "h": h, "pushed": False}
            return
        if not (0 <= lx <= self.bw and 0 <= ly <= self.bh):
            return
        tool = self.tool.get()
        if tool == "select":
            uid = self.hit_shape(e.x, e.y)
            self.sel = uid
            if uid:
                s = self.get(uid)
                self.syncing = True
                self.slider.set(s["width"])
                self.syncing = False
                self.width = s["width"]
                self.drag = {"mode": "move", "last": (e.x, e.y), "pushed": False}
            self.redraw()
        elif tool == "text":
            self.start_text(lx, ly)
        elif tool == "num":
            self.snap()
            s = self.new_shape("num", lx, ly)
            s["n"] = max([q.get("n", 0) for q in self.shapes if q["type"] == "num"], default=0) + 1
            self.shapes.append(s)
            self.sel = s["uid"]
            self.redraw()
        else:
            self.snap()
            s = self.new_shape(tool, lx, ly)
            self.shapes.append(s)
            self.sel = s["uid"]
            self.drag = {"mode": "draw"}
            self.redraw()

    def edit_drag(self, e):
        d, s = self.drag, self.get(self.sel)
        if not d or not s:
            return
        lx, ly = self.lclamp(e.x - self.ox, e.y - self.oy)
        if d["mode"] == "draw":
            s["x1"], s["y1"] = lx, ly
        elif d["mode"] == "resize":
            if not d["pushed"]:
                self.snap()
                d["pushed"] = True
            fx, fy = [("x0", "y0"), ("x1", "y1"), ("x0", "y1"), ("x1", "y0")][d["h"]]
            s[fx], s[fy] = lx, ly
        elif d["mode"] == "move":
            if not d["pushed"]:
                self.snap()
                d["pushed"] = True
            dx, dy = e.x - d["last"][0], e.y - d["last"][1]
            s["x0"] += dx
            s["x1"] += dx
            s["y0"] += dy
            s["y1"] += dy
            d["last"] = (e.x, e.y)
        self.redraw()

    def edit_release(self, e):
        d, self.drag = self.drag, None
        if d and d["mode"] == "draw":
            s = self.get(self.sel)
            if s and math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) < 4:
                self.shapes.remove(s)
                self.sel = None
                if self.undo:
                    self.undo.pop()
            self.redraw()

    # ---------- texto direto na imagem
    def start_text(self, lx, ly):
        size = text_size(self.width)
        dark = self.color.lower() in ("#ffffff", "#ffcc00")
        e = tk.Entry(self.canvas, font=("Segoe UI", -size, "bold"), fg=self.color, width=16,
                     bg="#222222" if dark else "#ffffff", insertbackground=self.color, relief="flat")
        self.entry, self.entry_pos = e, (lx, ly)
        self.entry_win = self.canvas.create_window(lx + self.ox, ly + self.oy, window=e, anchor="nw")
        e.bind("<Return>", lambda ev: (self.commit_text(), "break")[1])
        e.bind("<Escape>", lambda ev: (self.cancel_text(), "break")[1])
        e.bind("<FocusOut>", lambda ev: self.commit_text())
        e.focus_set()

    def cancel_text(self):
        if self.entry:
            e, self.entry = self.entry, None
            self.canvas.delete(self.entry_win)
            e.destroy()
            self.canvas.focus_set()

    def commit_text(self):
        if not self.entry:
            return
        txt, (lx, ly) = self.entry.get().strip(), self.entry_pos
        self.cancel_text()
        if txt:
            self.snap()
            s = self.new_shape("text", lx, ly)
            s["text"] = txt
            self.shapes.append(s)
            self.sel = s["uid"]
            self.redraw()

    # ---------- finalizar
    def finish(self, do_copy, do_save, pin=False, ocr=False):
        if self.phase != "edit":
            return
        self.commit_text()
        out = render(self.crop, self.shapes)
        self.close()
        if pin:
            self.app.restore(self.prev)
            PinWindow(self.app, out)
            self.app.status.set("Print fixado na tela (botão direito fecha).")
        elif ocr:
            self.app.finish_ocr(self.prev, out)
        else:
            self.app.finish_snap(self.prev, out, do_copy, do_save)

    def cancel(self, e=None):
        if self.phase == "edit" and self.shapes and not self.armed:  # protege contra Esc sem querer
            self.armed = True
            self.canvas.create_text(self.W // 2, 40, tags="warn", fill="white", font=("Segoe UI", 14, "bold"),
                                    text="Há anotações. Pressione Esc de novo para descartar.")
            self.win.after(3000, self.disarm)
            return
        self.close()
        self.app.cancel_snap(self.prev)

    def disarm(self):
        self.armed = False
        with contextlib.suppress(tk.TclError):
            self.canvas.delete("warn")

    def close(self):
        with contextlib.suppress(tk.TclError):
            self.win.destroy()


# ----------------------------------------------------------- widgets modernos
UI = 1.0  # fator de escala da tela (definido ao abrir a janela)
SIDE, BTN, BTN_H, FIELD = "#17181a", "#383b42", "#454951", "#222428"


def px(n):
    return max(1, int(round(n * UI)))


def rrect(c, x0, y0, x1, y1, r, **kw):
    r = max(1, min(int(r), int((x1 - x0) // 2), int((y1 - y0) // 2)))
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
           x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


class RoundBtn(tk.Canvas):
    """Botão arredondado. kind: normal | accent | danger | ghost."""
    KINDS = {"normal": (BTN, BTN_H, FG), "accent": (ACCENT, "#6b9bff", "white"),
             "danger": (RED, "#ff6166", "white"), "ghost": (None, "#33363c", FG)}

    def __init__(self, parent, text, command=None, kind="normal", bg=None, font=("Segoe UI", 10, "bold"),
                 padx=16, pady=8, image=None):
        self.text, self.command, self.kind, self.st, self.image = text, command, kind, "normal", image
        self.fnt = tkfont.Font(font=font)
        self.padx, self.pady, self.hover = px(padx), px(pady), False
        try:
            bgc = bg or parent["bg"]
        except Exception:
            bgc = BG
        w, h = self._measure()
        super().__init__(parent, width=w, height=h, bg=bgc, highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.draw()

    def _measure(self):
        lines = self.text.split("\n")
        w = self.fnt.measure(max(lines, key=len)) + self.padx * 2
        h = self.fnt.metrics("linespace") * len(lines) + self.pady * 2
        if self.image is not None:
            w = max(w, self.image.width() + self.padx * 2)
            h += self.image.height() + px(4)
        return w, h

    def _set_hover(self, v):
        self.hover = v
        self.draw()

    def _click(self, e):
        if self.st == "disabled" or not self.command:
            return
        if 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 5:
            w, h = int(float(self.cget("width"))), int(float(self.cget("height")))
        fill, hov, fg = self.KINDS[self.kind]
        if self.st == "disabled":
            fill, fg = ("#34373c" if fill else None), "#6b6f76"
        elif self.hover:
            fill = hov
        if fill:
            rrect(self, 1, 1, w - 1, h - 1, px(10), fill=fill, outline="")
        ty = h / 2
        if self.image is not None:
            ih = self.image.height()
            total = ih + px(4) + self.fnt.metrics("linespace") * (self.text.count("\n") + 1)
            top = (h - total) / 2
            self.create_image(w / 2, top + ih / 2, image=self.image)
            ty = top + ih + px(4) + (total - ih - px(4)) / 2
        self.create_text(w / 2, ty, text=self.text, fill=fg, font=self.fnt, justify="center")

    def configure(self, cnf=None, **kw):
        changed = False
        for k, attr in (("text", "text"), ("command", "command"), ("kind", "kind"), ("state", "st")):
            if k in kw:
                setattr(self, attr, kw.pop(k))
                changed = True
        if "text" in (cnf or {}) or changed:
            w, _ = self._measure()
            if w > int(float(self.cget("width"))):
                super().configure(width=w)
        if kw or cnf:
            super().configure(cnf, **kw)
        self.draw()
    config = configure


class Switch(tk.Canvas):
    """Interruptor ligado/desligado ligado a uma BooleanVar."""

    def __init__(self, parent, variable, command=None, bg=PANEL):
        super().__init__(parent, width=px(46), height=px(24), bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.command = variable, command
        self.bind("<Button-1>", self.toggle)
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def toggle(self, e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def draw(self):
        self.delete("all")
        w, h = int(float(self.cget("width"))), int(float(self.cget("height")))
        on = bool(self.var.get())
        rrect(self, 1, 1, w - 1, h - 1, h // 2, fill=ACCENT if on else "#4a4e56", outline="")
        r = h // 2 - 3
        cx = w - h // 2 if on else h // 2
        self.create_oval(cx - r, h // 2 - r, cx + r, h // 2 + r, fill="white", outline="")


class Segmented(tk.Frame):
    """Abas em forma de pílulas."""

    def __init__(self, parent, options, command, bg=BG):
        super().__init__(parent, bg=bg)
        self.command, self.labels, self.base = command, {}, bg
        for key, text in options:
            lab = tk.Label(self, text=text, bg=bg, fg=MUTED, font=("Segoe UI", 10, "bold"),
                           padx=px(12), pady=px(6), cursor="hand2")
            lab.pack(side="left", padx=(0, px(2)))
            lab.bind("<Button-1>", lambda e, k=key: self.select(k))
            self.labels[key] = lab

    def select(self, key):
        for k, lab in self.labels.items():
            lab.configure(bg=PANEL if k == key else self.base, fg="white" if k == key else MUTED)
        self.command(key)


def nav_icon(name, color, size=22):
    S = size * 6
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = max(3, S // 12)
    if name == "home":
        d.line([(.10 * S, .50 * S), (.50 * S, .14 * S), (.90 * S, .50 * S)], fill=color, width=lw, joint="curve")
        d.rectangle((.2 * S, .46 * S, .8 * S, .88 * S), outline=color, width=lw)
        d.line([(.5 * S, .88 * S), (.5 * S, .62 * S)], fill=color, width=lw)
    elif name == "tools":
        for y, kx in ((.26, .68), (.5, .32), (.74, .62)):
            d.line([(.12 * S, y * S), (.88 * S, y * S)], fill=color, width=lw)
            r = .1 * S
            d.ellipse((kx * S - r, y * S - r, kx * S + r, y * S + r), fill=color)
    elif name == "hist":
        d.ellipse((.12 * S, .12 * S, .88 * S, .88 * S), outline=color, width=lw)
        d.line([(.5 * S, .5 * S), (.5 * S, .26 * S)], fill=color, width=lw)
        d.line([(.5 * S, .5 * S), (.68 * S, .58 * S)], fill=color, width=lw)
    else:
        for k in range(8):
            a = math.pi / 4 * k
            c, s = math.cos(a), math.sin(a)
            d.line([((.5 + .30 * c) * S, (.5 + .30 * s) * S), ((.5 + .44 * c) * S, (.5 + .44 * s) * S)],
                   fill=color, width=int(lw * 1.6))
        d.ellipse((.22 * S, .22 * S, .78 * S, .78 * S), outline=color, width=lw)
        d.ellipse((.40 * S, .40 * S, .60 * S, .60 * S), outline=color, width=lw)
    return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))


def cap_icon(name, size=30, color="#ffffff"):
    S = size * 6
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = max(3, S // 11)
    if name in ("region", "quick"):
        L, m = .28 * S, .12 * S
        for x, y, dx, dy in ((m, m, 1, 1), (S - m, m, -1, 1), (m, S - m, 1, -1), (S - m, S - m, -1, -1)):
            d.line([(x + dx * L, y), (x, y), (x, y + dy * L)], fill=color, width=lw, joint="curve")
        if name == "quick":
            d.ellipse((.4 * S, .4 * S, .6 * S, .6 * S), fill=color)
    elif name == "window":
        d.rectangle((.1 * S, .18 * S, .9 * S, .82 * S), outline=color, width=lw)
        d.line([(.1 * S, .36 * S), (.9 * S, .36 * S)], fill=color, width=lw)
    else:
        d.rectangle((.08 * S, .14 * S, .92 * S, .68 * S), outline=color, width=lw)
        d.line([(.5 * S, .68 * S), (.5 * S, .84 * S)], fill=color, width=lw)
        d.line([(.3 * S, .86 * S), (.7 * S, .86 * S)], fill=color, width=lw)
    return ImageTk.PhotoImage(im.resize((size, size), Image.LANCZOS))


IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def make_thumb(path, ff, size):
    """Miniatura para o histórico (imagem direta; vídeo via um quadro extraído pelo FFmpeg)."""
    path = Path(path)
    try:
        if path.suffix.lower() in IMG_EXT:
            im = Image.open(path).convert("RGB")
        else:
            if not ff:
                return None
            th = APP_DIR / "thumbs"
            th.mkdir(parents=True, exist_ok=True)
            out = th / f"{hashlib.md5(str(path).encode()).hexdigest()[:12]}_{int(path.stat().st_mtime)}.png"
            if not out.exists():
                for ss in ("1", "0"):
                    subprocess.run([ff, "-y", "-loglevel", "error", "-ss", ss, "-i", str(path), "-frames:v", "1",
                                    "-vf", "scale=240:-2", str(out)], capture_output=True, timeout=25,
                                   creationflags=NOWIN)
                    if out.exists():
                        break
            im = Image.open(out).convert("RGB")
        im.thumbnail(size)
        bgimg = Image.new("RGB", size, "#111111")
        bgimg.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
        return bgimg
    except Exception:
        return None


class Tray:
    def __init__(self, app):
        import pystray
        q = app.ui_queue.put

        def item(text, fn, **kw):
            return pystray.MenuItem(text, lambda icon, it: q(fn), **kw)
        menu = pystray.Menu(
            item("Abrir Print HQ", app.show_main, default=True),
            pystray.Menu.SEPARATOR,
            item("Capturar área", lambda: app.capture("region")),
            item("Capturar janela", lambda: app.capture("window")),
            item("Tela inteira", lambda: app.capture("full")),
            pystray.Menu.SEPARATOR,
            item("Gravar / parar", app.toggle_record),
            item("Salvar replay", app.save_replay),
            pystray.Menu.SEPARATOR,
            item("Sair", app.quit_app))
        self.icon = pystray.Icon("PrintHQ", make_icon(64), "Print HQ", menu)
        self.icon.run_detached()

    def stop(self):
        with contextlib.suppress(Exception):
            self.icon.stop()


# ======================================================================= app
SNAP_MODES = ["Abrir o editor (anotar antes de copiar)", "Copiar direto ao soltar o mouse"]


class App:
    def __init__(self):
        global UI
        cfg = self.cfg = load_cfg()
        self.root = tk.Tk()
        self.root.title("Print HQ")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.report_callback_exception = self.on_ui_error
        UI = max(1.0, self.root.winfo_fpixels("1i") / 96)
        setup_style(self.root)
        with contextlib.suppress(Exception):
            self.app_icon = ImageTk.PhotoImage(make_icon(64))
            self.root.iconphoto(True, self.app_icon)

        self.ui_queue = queue.Queue()
        self.busy, self.job_running, self._applying = False, False, False
        self.rec = Recorder(self)
        self.ffmpeg, self.encoder, self.minibar, self.area = None, None, None, None
        self.ticking, self.tray, self.hist_imgs = False, None, {}
        self.hotkeys = {**HK_DEFAULT, **cfg.get("hotkeys", {})}
        self.hk_vars = {k: tk.StringVar(value=v) for k, v in self.hotkeys.items()}
        self.history = [h for h in cfg.get("history", []) if Path(h[0]).exists()][:60]

        V = lambda key, default: tk.StringVar(value=cfg.get(key, default))
        self.folder = V("folder", str(Path.home() / "Pictures" / "Prints"))
        self.fmt = V("fmt", "PNG")
        self.use_editor = tk.BooleanVar(value=cfg.get("editor", True))
        self.snap_mode = tk.StringVar(value=SNAP_MODES[0 if self.use_editor.get() else 1])
        self.delay = tk.IntVar(value=0)
        self.vfolder = V("vfolder", str(Path.home() / "Videos" / "Gravações"))
        self.rec_target = V("target", "")
        self.rec_window = tk.StringVar(value="")
        self.rec_res = V("res", "1080p")
        self.rec_fps = V("fps", "60")
        self.rec_quality = V("quality", QUALITY[1])
        if cfg.get("v") != 4:  # versões antigas gravavam em "Leve": sobe para "Equilibrada"
            self.rec_quality.set(QUALITY[1])
        self.rec_mic = V("mic", "Nenhum")
        self.rec_sys = V("sys", "")
        self.profile = tk.StringVar(value="Personalizado")
        self.sync_profile()
        self.audio_offset = tk.IntVar(value=int(cfg.get("audio_offset_ms", 0)))
        self.replay_len = V("replay_len", "30 s")
        self.cap_mode = V("cap_mode", CAP_MODES[0])
        self.enc_mode = V("enc_mode", ENC_MODES[0])
        if cfg.get("v") != 6 or self.enc_mode.get() not in ENC_MODES:
            self.enc_mode.set(ENC_MODES[0])  # versões anteriores escolhiam o processador e travavam o jogo
        self.auto_opt = tk.BooleanVar(value=cfg.get("auto_opt", True) if cfg.get("v") == 6 else True)
        self.post_height = 0
        self.est_var = tk.StringVar(value="")
        self.hw, self.hw_ready, self._hk_last = {"encoder": "libx264", "rich": True, "gpu": False}, False, {}
        self.replay_var = tk.BooleanVar(value=False)
        self.replay_text = tk.StringVar(value="Desligado")
        self.close_tray = tk.BooleanVar(value=cfg.get("close_tray", True))
        self.autostart = tk.BooleanVar(value=get_autostart())
        self.precise = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Pronto.")
        self._st_job = None
        self.status.trace_add("write", self._status_changed)
        self.time_var = tk.StringVar(value="00:00:00")
        self.size_var = tk.StringVar(value="0.0 MB")
        self.enc_var = tk.StringVar(value="")
        self.area_var = tk.StringVar(value="Nenhuma área escolhida.")
        self.tool_file = tk.StringVar()
        self.t_start, self.t_end = tk.StringVar(), tk.StringVar()
        self.dc_limit = tk.StringVar(value="10 MB")
        self.gif_w, self.gif_fps = tk.StringVar(value="480"), tk.StringVar(value="15")

        self.mon_list = list_monitors()
        self.mon_map = {f"Monitor {i + 1}  ({m['width']}x{m['height']})": (i, m) for i, m in enumerate(self.mon_list)}
        self.targets = list(self.mon_map) + [WIN_LABEL, AREA_LABEL]
        self.screen_h = max((m["height"] for m in self.mon_list), default=1080)
        self.res_values = [k for k, v in RES_MAP.items() if v == 0 or v <= self.screen_h]
        if self.rec_res.get() not in self.res_values:
            self.rec_res.set("1080p" if "1080p" in self.res_values else "Original")
        self.sync_profile()
        if self.rec_target.get() not in self.targets:
            self.rec_target.set(self.targets[0])
        mics, spks, default, err = list_audio()
        self.audio_err, self.mics, self.spks = err, mics, spks
        if self.rec_mic.get() not in ["Nenhum"] + mics:
            self.rec_mic.set("Nenhum")
        if self.rec_sys.get() not in ["Nenhum"] + spks:
            self.rec_sys.set(default or "Nenhum")

        for v in (self.folder, self.fmt, self.use_editor, self.vfolder, self.rec_target, self.rec_res, self.rec_fps,
                  self.rec_quality, self.rec_mic, self.rec_sys, self.replay_len, self.close_tray, self.audio_offset, self.cap_mode, self.auto_opt, self.enc_mode):
            v.trace_add("write", lambda *a: self.save_cfg())
        for v in (self.rec_res, self.rec_fps, self.rec_quality):
            v.trace_add("write", self.mark_custom)
        for v in (self.rec_res, self.rec_fps, self.rec_quality, self.rec_target):
            v.trace_add("write", self.update_estimate)
        self.cap_mode.trace_add("write", self.refresh_enc_label)
        self.enc_mode.trace_add("write", self.refresh_enc_label)

        self.cap_icons = {n: cap_icon(n) for n in ("region", "window", "full", "quick")}
        self.nav_icons = {n: (nav_icon(n, MUTED), nav_icon(n, "#ffffff"))
                          for n in ("home", "tools", "hist", "cfg")}

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        side = tk.Frame(outer, bg=SIDE)
        side.pack(side="left", fill="y")
        tk.Label(side, text="Print HQ", bg=SIDE, fg="white", font=("Segoe UI", 15, "bold")
                 ).pack(padx=px(18), pady=(px(18), px(16)), anchor="w")
        self.nav = {}
        for key, label in (("home", "Início"), ("tools", "Ferramentas"), ("hist", "Histórico"),
                           ("cfg", "Configurações")):
            self.make_nav(side, key, label)
        tk.Label(side, text=f"v{VERSION}", bg=SIDE, fg="#555", font=("Segoe UI", 8)
                 ).pack(side="bottom", pady=px(10))

        main = tk.Frame(outer, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=px(18), pady=px(14))
        host = tk.Frame(main, bg=BG)
        host.pack(fill="both", expand=True)
        host.grid_rowconfigure(0, weight=1)
        host.grid_columnconfigure(0, weight=1)
        self.pages = {k: tk.Frame(host, bg=BG) for k in ("home", "tools", "hist", "cfg")}
        for pg in self.pages.values():
            pg.grid(row=0, column=0, sticky="nsew")
        tk.Label(main, textvariable=self.status, bg=BG, fg=MUTED, anchor="nw", justify="left", height=2,
                 wraplength=px(560), font=("Segoe UI", 9)).pack(fill="x", pady=(px(8), 0))

        self.build_home(self.pages["home"])
        self.build_tools(self.pages["tools"])
        self.build_history(self.pages["hist"])
        self.build_settings(self.pages["cfg"])
        self.update_estimate()
        self.show_page("home")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            self.tray = Tray(self)
        except Exception:
            self.tray = None
        if self.tray and "--tray" in sys.argv:
            self.root.withdraw()
        self.register_hotkeys()
        self.refresh_ffmpeg_state()
        self.root.after(50, self.pump)
        self.root.after(400, lambda: exclude_from_capture(self.root))

    def _status_changed(self, *a):
        if self._st_job:
            self.root.after_cancel(self._st_job)
            self._st_job = None
        if self.status.get() != "Pronto.":
            self._st_job = self.root.after(45000, lambda: self.status.set("Pronto."))

    def on_ui_error(self, exc, val, tb):
        import traceback
        txt = "".join(traceback.format_exception(exc, val, tb))
        with contextlib.suppress(Exception):
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "error.log").write_text(txt, encoding="utf-8")
        self.status.set(f"Erro: {val}")

    # ------------------------------------------------------------ navegação
    def make_nav(self, side, key, label):
        row = tk.Frame(side, bg=SIDE, cursor="hand2")
        row.pack(fill="x", pady=1)
        bar = tk.Frame(row, bg=SIDE, width=px(4))
        bar.pack(side="left", fill="y")
        ic = tk.Label(row, bg=SIDE, image=self.nav_icons[key][0])
        ic.pack(side="left", padx=(px(14), px(8)), pady=px(10))
        tx = tk.Label(row, text=label, bg=SIDE, fg=MUTED, font=("Segoe UI", 11), anchor="w", width=13)
        tx.pack(side="left", padx=(0, px(14)))
        for w in (row, bar, ic, tx):
            w.bind("<Button-1>", lambda e, k=key: self.show_page(k))
        self.nav[key] = (row, bar, ic, tx)

    def show_page(self, key):
        self.pages[key].tkraise()
        for k, (row, bar, ic, tx) in self.nav.items():
            on = k == key
            bgc = "#26282c" if on else SIDE
            row.configure(bg=bgc)
            ic.configure(bg=bgc, image=self.nav_icons[k][1 if on else 0])
            tx.configure(bg=bgc, fg="white" if on else MUTED)
            bar.configure(bg=ACCENT if on else bgc)

    # ------------------------------------------------------------ helpers de interface
    def lbl(self, parent, text, muted=True, bg=PANEL, size=None, bold=False, **kw):
        return tk.Label(parent, text=text, bg=bg, fg=MUTED if muted else FG, anchor="w", justify="left",
                        font=("Segoe UI", size or (9 if muted else 10), "bold" if bold else "normal"), **kw)

    def cmb(self, parent, var, values, width=22, **kw):
        return ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width, **kw)

    def card(self, parent, title=None, pady=(0, 0)):
        outer = tk.Frame(parent, bg=PANEL)
        outer.pack(fill="x", pady=pady)
        inner = tk.Frame(outer, bg=PANEL)
        inner.pack(fill="x", padx=px(14), pady=px(12))
        if title:
            tk.Label(inner, text=title, bg=PANEL, fg="white", font=("Segoe UI", 11, "bold"), anchor="w"
                     ).pack(fill="x", pady=(0, px(6)))
        body = tk.Frame(inner, bg=PANEL)
        body.pack(fill="x")
        return body

    def heading(self, p, title, sub=None):
        tk.Label(p, text=title, bg=BG, fg="white", font=("Segoe UI", 16, "bold"), anchor="w").pack(fill="x")
        if sub:
            tk.Label(p, text=sub, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                     wraplength=px(540)).pack(fill="x", pady=(2, px(8)))

    def form_row(self, parent, r, label, widget):
        self.lbl(parent, label).grid(row=r, column=0, sticky="w", pady=px(5), padx=(0, px(14)))
        widget.grid(row=r, column=1, sticky="w", pady=px(5))

    # ------------------------------------------------------------ INÍCIO
    def build_home(self, p):
        self.heading(p, "O que você quer fazer?",
                     "Os atalhos funcionam mesmo com esta janela fechada (o app fica na bandeja do Windows).")
        row = tk.Frame(p, bg=BG)
        row.pack(fill="x")
        tiles = (("Área", "region", "accent", "region", "region"), ("Janela", "window", "normal", "window", "window"),
                 ("Tela inteira", "full", "normal", "full", "full"),
                 ("Área e copiar", "quick", "normal", "quick", "region_quick"))
        for col, (label, mode, kind, icon, hk) in enumerate(tiles):
            row.columnconfigure(col, weight=1, uniform="tile")
            tile = tk.Frame(row, bg=PANEL)
            tile.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else px(6), 0))
            RoundBtn(tile, label, command=lambda m=mode: self.capture(m), kind=kind, bg=PANEL,
                     image=self.cap_icons[icon], padx=6).pack(fill="x", padx=px(8), pady=(px(8), px(2)))
            tk.Label(tile, textvariable=self.hk_vars[hk], bg=PANEL, fg=MUTED, font=("Consolas", 8)
                     ).pack(pady=(0, px(6)))

        card = self.card(p, "Gravação de vídeo", pady=(px(12), 0))
        card.columnconfigure((0, 1), weight=1)
        self.lbl(card, "O que gravar").grid(row=0, column=0, sticky="w")
        self.lbl(card, "Perfil").grid(row=0, column=1, sticky="w", padx=(px(8), 0))
        self.cmb(card, self.rec_target, self.targets, width=26).grid(row=1, column=0, sticky="ew")
        pc = self.cmb(card, self.profile, list(PROFILES) + ["Personalizado"], width=26)
        pc.grid(row=1, column=1, sticky="ew", padx=(px(8), 0))
        pc.bind("<<ComboboxSelected>>", self.apply_profile)
        self.extra = tk.Frame(card, bg=PANEL)
        self.extra.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(px(6), 0))
        self.extra.columnconfigure(0, weight=1)
        self.win_cb = ttk.Combobox(self.extra, textvariable=self.rec_window, state="readonly",
                                   postcommand=self.refresh_windows)
        self.win_cb.grid(row=0, column=0, sticky="ew")
        self.area_row = tk.Frame(self.extra, bg=PANEL)
        RoundBtn(self.area_row, "Selecionar área…", command=self.pick_area, bg=PANEL, padx=12, pady=5,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(self.area_row, textvariable=self.area_var, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
                 ).pack(side="left", padx=8)
        self.rec_target.trace_add("write", lambda *a: self.on_target_change())
        tk.Label(card, textvariable=self.time_var, bg=PANEL, fg="white", font=("Segoe UI", 28, "bold")
                 ).grid(row=3, column=0, sticky="w", pady=(px(10), 0))
        tk.Label(card, textvariable=self.size_var, bg=PANEL, fg=ACCENT, font=("Segoe UI", 12, "bold")
                 ).grid(row=3, column=1, sticky="e", pady=(px(10), 0))
        self.rec_btn = RoundBtn(card, "●  Gravar", command=self.toggle_record, kind="danger", bg=PANEL,
                                font=("Segoe UI", 11, "bold"), pady=10)
        self.rec_btn.grid(row=4, column=0, sticky="ew", pady=(px(6), 0))
        self.pause_btn = RoundBtn(card, "Pausar", command=self.toggle_pause, bg=PANEL, pady=10)
        self.pause_btn.grid(row=4, column=1, sticky="ew", padx=(px(8), 0), pady=(px(6), 0))
        self.pause_btn.configure(state="disabled")
        RoundBtn(card, "Testar desempenho (10 s)", command=self.perf_test, kind="ghost", bg=PANEL,
                 font=("Segoe UI", 9), padx=10, pady=4).grid(row=5, column=0, columnspan=2, sticky="w",
                                                              pady=(px(6), 0))
        self.on_target_change()

        card2 = self.card(p, "Replay (guarda só os últimos segundos)", pady=(px(10), 0))
        r2 = tk.Frame(card2, bg=PANEL)
        r2.pack(fill="x")
        Switch(r2, self.replay_var, command=self.toggle_replay, bg=PANEL).pack(side="left")
        tk.Label(r2, textvariable=self.replay_text, bg=PANEL, fg=FG, font=("Segoe UI", 10), width=10, anchor="w"
                 ).pack(side="left", padx=(px(8), px(8)))
        self.cmb(r2, self.replay_len, ["15 s", "30 s", "60 s", "120 s"], width=7).pack(side="left")
        self.replay_save_btn = RoundBtn(r2, "Salvar agora", command=self.save_replay, bg=PANEL, padx=14, pady=6)
        self.replay_save_btn.pack(side="right")
        self.replay_save_btn.configure(state="disabled")

    def on_target_change(self):
        t = self.rec_target.get()
        self.win_cb.grid_remove()
        self.area_row.grid_remove()
        if t == WIN_LABEL:
            self.win_cb.grid()
            self.refresh_windows()
        elif t == AREA_LABEL:
            self.area_row.grid(row=0, column=0, sticky="w")

    def refresh_windows(self):
        titles = [t for _, t, _ in list_windows(skip={own_hwnd(self.root)})]
        self.win_cb.configure(values=titles)
        if self.rec_window.get() not in titles and titles:
            self.rec_window.set(titles[0])

    def pick_area(self):
        if self.busy:
            return
        self.busy = True
        prev = self.root.state()
        self.root.withdraw()
        self.root.update()
        time.sleep(0.4)
        try:
            img, left, top = grab_screen()
            SnapOverlay(self, img, left, top, prev, mode="pick", on_pick=self.area_picked)
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
            self.restore(prev)

    def area_picked(self, box):
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        for i, m in enumerate(self.mon_list):
            if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                x0, y0 = max(box[0], m["left"]), max(box[1], m["top"])
                x1, y1 = min(box[2], m["left"] + m["width"]), min(box[3], m["top"] + m["height"])
                w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
                if w >= 64 and h >= 64:
                    self.area = ((x0, y0, w, h), i, m)
                    self.area_var.set(f"{w}x{h} no Monitor {i + 1}")
                    return
        self.status.set("Área pequena demais ou fora dos monitores.")

    # ------------------------------------------------------------ perfis
    def sync_profile(self):
        cur = (self.rec_res.get(), self.rec_fps.get(), self.rec_quality.get())
        self.profile.set(next((n for n, v in PROFILES.items() if v == cur), "Personalizado"))

    def apply_profile(self, e=None):
        pf = PROFILES.get(self.profile.get())
        if pf:
            self._applying = True
            self.rec_res.set(pf[0])
            self.rec_fps.set(pf[1])
            self.rec_quality.set(pf[2])
            self._applying = False
            self.status.set(f"Perfil aplicado: {pf[0]}, {pf[1]} fps, {pf[2].split(' (')[0].lower()}.")

    def mark_custom(self, *a):
        if not self._applying:
            self.sync_profile()

    # ------------------------------------------------------------ FERRAMENTAS
    def build_tools(self, p):
        self.heading(p, "Ferramentas de vídeo", "Escolha um vídeo (ou use a última gravação) e aplique uma ação.")
        c1 = self.card(p, "Vídeo")
        r = tk.Frame(c1, bg=PANEL)
        r.pack(fill="x")
        RoundBtn(r, "Escolher…", command=self.pick_tool_file, bg=PANEL, padx=14, pady=6).pack(side="left")
        RoundBtn(r, "Última gravação", command=self.use_last_video, bg=PANEL, padx=14, pady=6
                 ).pack(side="left", padx=px(8))
        tk.Label(c1, textvariable=self.tool_file, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                 wraplength=px(500), justify="left").pack(fill="x", pady=(px(6), 0))

        c2 = self.card(p, "Reduzir para enviar no Discord", pady=(px(8), 0))
        r = tk.Frame(c2, bg=PANEL)
        r.pack(fill="x")
        self.lbl(r, "Limite de tamanho").pack(side="left")
        self.cmb(r, self.dc_limit, ["10 MB", "25 MB", "50 MB", "100 MB"], width=8).pack(side="left", padx=px(10))
        RoundBtn(r, "Reduzir", command=lambda: self.start_job("discord"), kind="accent", bg=PANEL, padx=18, pady=6
                 ).pack(side="right")

        c2b = self.card(p, "Otimizar (menor, quase sem perder qualidade)", pady=(px(8), 0))
        r = tk.Frame(c2b, bg=PANEL)
        r.pack(fill="x")
        self.lbl(r, "Recodifica com mais cuidado; o arquivo costuma encolher bastante.").pack(side="left")
        RoundBtn(r, "Otimizar", command=lambda: self.optimize(self.tool_file.get(), False), bg=PANEL, padx=18,
                 pady=6).pack(side="right")

        c3 = self.card(p, "Cortar e exportar GIF", pady=(px(8), 0))
        c3.columnconfigure((1, 3), weight=1)
        self.lbl(c3, "Início").grid(row=0, column=0, sticky="w")
        ttk.Entry(c3, textvariable=self.t_start, width=9).grid(row=0, column=1, sticky="w", padx=(px(6), px(14)))
        self.lbl(c3, "Fim").grid(row=0, column=2, sticky="w")
        ttk.Entry(c3, textvariable=self.t_end, width=9).grid(row=0, column=3, sticky="w", padx=(px(6), 0))
        self.lbl(c3, "Formato mm:ss ou segundos. Vazio = vídeo todo.").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, px(6)))
        r = tk.Frame(c3, bg=PANEL)
        r.grid(row=2, column=0, columnspan=4, sticky="ew")
        Switch(r, self.precise, bg=PANEL).pack(side="left")
        self.lbl(r, "Corte preciso (mais lento)", muted=False).pack(side="left", padx=px(8))
        RoundBtn(r, "Cortar", command=lambda: self.start_job("trim"), bg=PANEL, padx=18, pady=6).pack(side="right")
        r = tk.Frame(c3, bg=PANEL)
        r.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(px(8), 0))
        self.lbl(r, "GIF: largura").pack(side="left")
        self.cmb(r, self.gif_w, ["320", "480", "640"], width=5).pack(side="left", padx=px(6))
        self.lbl(r, "fps").pack(side="left")
        self.cmb(r, self.gif_fps, ["10", "15", "20"], width=4).pack(side="left", padx=px(6))
        RoundBtn(r, "Exportar GIF", command=lambda: self.start_job("gif"), bg=PANEL, padx=18, pady=6).pack(side="right")
        self.progress = ttk.Progressbar(p, maximum=100)
        self.progress.pack(fill="x", pady=(px(12), 0))

    # ------------------------------------------------------------ HISTÓRICO
    def build_history(self, p):
        self.heading(p, "Histórico", "Seus últimos prints e vídeos. Clique nos botões de cada item.")
        wrap = tk.Frame(p, bg=BG)
        wrap.pack(fill="both", expand=True)
        self.hist_canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, height=px(400), width=px(540))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.hist_canvas.yview)
        self.hist_canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.hist_canvas.pack(side="left", fill="both", expand=True)
        self.hist_inner = tk.Frame(self.hist_canvas, bg=BG)
        self.hist_win = self.hist_canvas.create_window((0, 0), window=self.hist_inner, anchor="nw")
        self.hist_inner.bind("<Configure>",
                             lambda e: self.hist_canvas.configure(scrollregion=self.hist_canvas.bbox("all")))
        self.hist_canvas.bind("<Configure>", lambda e: self.hist_canvas.itemconfigure(self.hist_win, width=e.width))
        self.hist_canvas.bind("<Enter>", lambda e: self.hist_canvas.bind_all("<MouseWheel>", self._hist_wheel))
        self.hist_canvas.bind("<Leave>", lambda e: self.hist_canvas.unbind_all("<MouseWheel>"))
        self.refresh_history()

    def _hist_wheel(self, e):
        self.hist_canvas.yview_scroll(int(-e.delta / 120), "units")

    # ------------------------------------------------------------ CONFIGURAÇÕES
    def build_settings(self, p):
        self.heading(p, "Configurações")
        seg = Segmented(p, [("prints", "Prints"), ("video", "Vídeo"), ("audio", "Áudio"),
                            ("keys", "Atalhos"), ("gen", "Geral")], self.show_cfg_section)
        seg.pack(anchor="w", pady=(0, px(8)))
        host = tk.Frame(p, bg=BG)
        host.pack(fill="both", expand=True)
        host.grid_rowconfigure(0, weight=1)
        host.grid_columnconfigure(0, weight=1)
        self.cfg_secs = {k: tk.Frame(host, bg=BG) for k in ("prints", "video", "audio", "keys", "gen")}
        for f in self.cfg_secs.values():
            f.grid(row=0, column=0, sticky="nsew")

        c = self.card(self.cfg_secs["prints"])
        sm = self.cmb(c, self.snap_mode, SNAP_MODES, width=38)
        sm.bind("<<ComboboxSelected>>", lambda e: self.use_editor.set(self.snap_mode.get() == SNAP_MODES[0]))
        self.form_row(c, 0, "Ao selecionar uma área", sm)
        self.form_row(c, 1, "Formato ao salvar", self.cmb(c, self.fmt, ["PNG", "JPEG (q100)", "WebP (lossless)"], width=18))
        self.form_row(c, 2, "Atraso antes de capturar (s)", ttk.Spinbox(c, from_=0, to=30, textvariable=self.delay, width=6))
        rf = tk.Frame(c, bg=PANEL)
        RoundBtn(rf, "Alterar pasta…", command=lambda: self.pick_folder(self.folder), bg=PANEL, padx=12, pady=5,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.form_row(c, 3, "Pasta dos prints", rf)
        tk.Label(c, textvariable=self.folder, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w", wraplength=px(480),
                 justify="left").grid(row=4, column=0, columnspan=2, sticky="w")
        self.lbl(c, "Dica: no editor, Ctrl+C copia, Ctrl+S salva e Esc cancela.").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(px(8), 0))

        c = self.card(self.cfg_secs["video"])
        self.form_row(c, 0, "Resolução", self.cmb(c, self.rec_res, self.res_values, width=14))
        self.form_row(c, 1, "FPS", self.cmb(c, self.rec_fps, ["24", "30", "60"], width=14))
        self.form_row(c, 2, "Qualidade", self.cmb(c, self.rec_quality, QUALITY, width=24))
        self.form_row(c, 3, "Codificador", self.cmb(c, self.enc_mode, ENC_MODES, width=34))
        self.form_row(c, 4, "Modo de captura", self.cmb(c, self.cap_mode, CAP_MODES, width=34))
        ro = tk.Frame(c, bg=PANEL)
        Switch(ro, self.auto_opt, bg=PANEL).pack(side="left")
        self.lbl(ro, "Otimizar depois de gravar (arquivo bem menor)", muted=False).pack(side="left", padx=px(8))
        ro.grid(row=5, column=0, columnspan=2, sticky="w", pady=px(5))
        tk.Label(c, textvariable=self.est_var, bg=PANEL, fg=ACCENT, font=("Segoe UI", 9), anchor="w",
                 wraplength=px(480), justify="left").grid(row=6, column=0, columnspan=2, sticky="w", pady=(px(4), 0))
        rf = tk.Frame(c, bg=PANEL)
        RoundBtn(rf, "Alterar pasta…", command=lambda: self.pick_folder(self.vfolder), bg=PANEL, padx=12, pady=5,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.form_row(c, 7, "Pasta dos vídeos", rf)
        tk.Label(c, textvariable=self.vfolder, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), anchor="w", wraplength=px(480),
                 justify="left").grid(row=8, column=0, columnspan=2, sticky="w")
        tk.Label(c, textvariable=self.enc_var, bg=PANEL, fg=FG, font=("Segoe UI", 9), anchor="w", wraplength=px(480),
                 justify="left").grid(row=9, column=0, columnspan=2, sticky="w", pady=(px(8), 0))
        self.dl_btn = RoundBtn(c, "Baixar FFmpeg (necessário, ~90 MB)", command=self.download_ffmpeg, kind="accent",
                               bg=PANEL, padx=14, pady=7)
        self.dl_btn.grid(row=10, column=0, columnspan=2, sticky="w", pady=(px(6), 0))
        self.lbl(c, f"Seu monitor tem {self.screen_h}p: resoluções maiores que isso não melhorariam a imagem.").grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(px(6), 0))

        c = self.card(self.cfg_secs["audio"])
        self.mic_cb = self.cmb(c, self.rec_mic, ["Nenhum"] + self.mics, width=46)
        self.sys_cb = self.cmb(c, self.rec_sys, ["Nenhum"] + self.spks, width=46)
        self.form_row(c, 0, "Microfone", self.mic_cb)
        self.form_row(c, 1, "Áudio do computador", self.sys_cb)
        self.form_row(c, 2, "Ajuste de sincronia (ms)", ttk.Spinbox(c, from_=-500, to=1000, increment=50,
                                                                   textvariable=self.audio_offset, width=8))
        self.lbl(c, "Se o som estiver adiantado em relação ao vídeo, aumente o número.\n"
                    "Se estiver atrasado, use um número negativo.").grid(row=3, column=0, columnspan=2, sticky="w")
        if self.audio_err:
            self.mic_cb.configure(state="disabled")
            self.sys_cb.configure(state="disabled")
            self.lbl(c, "Áudio indisponível. No PowerShell: py -m pip install soundcard numpy", muted=False
                     ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(px(8), 0))

        c = self.card(self.cfg_secs["keys"])
        for i, (k, label) in enumerate(HK_NAMES.items()):
            self.lbl(c, label, muted=False).grid(row=i, column=0, sticky="w", pady=px(4))
            tk.Label(c, textvariable=self.hk_vars[k], bg="#1f2023", fg="white", font=("Consolas", 10, "bold"),
                     padx=px(10), pady=px(3)).grid(row=i, column=1, padx=px(12))
            RoundBtn(c, "Alterar", command=lambda k=k: self.change_hotkey(k), bg=PANEL, padx=12, pady=4,
                     font=("Segoe UI", 9, "bold")).grid(row=i, column=2)
        RoundBtn(c, "Restaurar padrão", command=self.reset_hotkeys, bg=PANEL, padx=12, pady=5,
                 font=("Segoe UI", 9, "bold")).grid(row=len(HK_NAMES), column=0, sticky="w", pady=(px(10), 0))
        if keyboard is None:
            self.lbl(c, "Instale 'keyboard' para usar atalhos globais.").grid(
                row=len(HK_NAMES) + 1, column=0, columnspan=3, sticky="w")

        c = self.card(self.cfg_secs["gen"])
        r = tk.Frame(c, bg=PANEL)
        r.pack(fill="x", pady=px(5))
        Switch(r, self.autostart, command=self.on_autostart, bg=PANEL).pack(side="left")
        self.lbl(r, "Iniciar junto com o Windows (na bandeja)", muted=False).pack(side="left", padx=px(10))
        r = tk.Frame(c, bg=PANEL)
        r.pack(fill="x", pady=px(5))
        Switch(r, self.close_tray, bg=PANEL).pack(side="left")
        self.lbl(r, "Fechar a janela (X) mantém o app na bandeja", muted=False).pack(side="left", padx=px(10))
        RoundBtn(c, f"Verificar atualização (v{VERSION})", command=self.check_update, bg=PANEL, padx=14, pady=7
                 ).pack(anchor="w", pady=(px(10), 0))
        RoundBtn(c, "Diagnóstico (relatório para enviar ao suporte)", command=self.run_diagnostic, bg=PANEL, padx=14,
                 pady=7).pack(anchor="w", pady=(px(6), 0))
        self.show_cfg_section("prints")
        seg.select("prints")

    def show_cfg_section(self, key):
        self.cfg_secs[key].tkraise()

    # ------------------------------------------------------------ geral
    def save_cfg(self):
        try:
            data = {"folder": self.folder.get(), "fmt": self.fmt.get(), "editor": self.use_editor.get(),
                    "vfolder": self.vfolder.get(), "target": self.rec_target.get(), "res": self.rec_res.get(),
                    "fps": self.rec_fps.get(), "quality": self.rec_quality.get(), "mic": self.rec_mic.get(),
                    "sys": self.rec_sys.get(), "hotkeys": self.hotkeys, "replay_len": self.replay_len.get(),
                    "close_tray": self.close_tray.get(), "history": self.history,
                    "audio_offset_ms": self.get_offset(), "v": 6, "cap_mode": self.cap_mode.get(), "enc_mode": self.enc_mode.get(), "stage_pref": self.cfg.get("stage_pref"),
                    "auto_opt": self.auto_opt.get()}
            tmp = CONFIG.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, CONFIG)
        except Exception:
            pass

    def pump(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
        self.root.after(50, self.pump)

    def pick_folder(self, var):
        d = filedialog.askdirectory(initialdir=var.get())
        if d:
            var.set(d)

    def restore(self, prev):
        self.busy = False
        if prev == "iconic":
            self.root.iconify()
        elif prev == "normal":
            self.root.deiconify()
            self.root.attributes("-topmost", True)

    def show_main(self):
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()

    def on_close(self):
        if self.tray and self.close_tray.get():
            self.root.withdraw()
            self.status.set("Print HQ continua na bandeja (ícone perto do relógio).")
        else:
            self.quit_app()

    def quit_app(self):
        if self.rec.state in ("recording", "paused", "replay"):
            if not messagebox.askyesno("Print HQ", "Há uma gravação/replay em andamento. Sair mesmo assim?"):
                return
            self.rec._kill()
        if self.tray:
            self.tray.stop()
        with contextlib.suppress(Exception):
            if keyboard:
                keyboard.clear_all_hotkeys()
        self.root.destroy()
        os._exit(0)

    def on_autostart(self):
        try:
            set_autostart(self.autostart.get())
            self.status.set("Vai iniciar com o Windows." if self.autostart.get() else "Não inicia mais com o Windows.")
        except Exception as ex:
            self.status.set(f"Não consegui alterar: {ex}")

    # ------------------------------------------------------------ histórico
    def add_history(self, path, kind):
        self.history.insert(0, [str(path), kind, datetime.now().strftime("%d/%m %H:%M")])
        self.history = self.history[:60]
        self.save_cfg()
        self.refresh_history()

    def refresh_history(self):
        self.history = [h for h in self.history if Path(h[0]).exists()]
        for w in self.hist_inner.winfo_children():
            w.destroy()
        self.hist_imgs = {}
        if not self.history:
            tk.Label(self.hist_inner, text="Nada por aqui ainda.\nSeus prints e vídeos aparecem nesta lista.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="center").pack(pady=px(40), padx=px(60))
            return
        size = (px(96), px(54))
        self.hist_imgs["blank"] = blank = ImageTk.PhotoImage(Image.new("RGB", size, "#111111"))
        names = {"print": "Print", "video": "Vídeo", "replay": "Replay", "tool": "Arquivo"}
        pending = []
        for i, (path, kind, ts) in enumerate(self.history[:40]):
            row = tk.Frame(self.hist_inner, bg=PANEL)
            row.pack(fill="x", pady=3)
            th = tk.Label(row, image=blank, bg="#111111", bd=0)
            th.pack(side="left", padx=px(8), pady=px(8))
            info = tk.Frame(row, bg=PANEL)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=Path(path).name, bg=PANEL, fg="white", font=("Segoe UI", 10, "bold"),
                     anchor="w").pack(fill="x")
            try:
                fsize = fmt_size(Path(path).stat().st_size)
            except OSError:
                fsize = ""
            tk.Label(info, text=f"{names.get(kind, kind)}  •  {ts}  •  {fsize}", bg=PANEL, fg=MUTED,
                     font=("Segoe UI", 9), anchor="w").pack(fill="x")
            btns = tk.Frame(info, bg=PANEL)
            btns.pack(fill="x", pady=(px(4), 0))
            is_img = Path(path).suffix.lower() in IMG_EXT
            acts = [("Abrir", "open"), ("Pasta", "folder"), ("Copiar", "copy") if is_img else ("p/ Discord", "discord"),
                    ("Remover", "remove")]
            for text, act in acts:
                RoundBtn(btns, text, command=lambda a=act, i=i: self.hist_act(a, i), kind="ghost", bg=PANEL,
                         font=("Segoe UI", 9), padx=8, pady=3).pack(side="left", padx=(0, 2))
            pending.append((th, path))
        threading.Thread(target=self._load_thumbs, args=(pending, size), daemon=True).start()

    def _load_thumbs(self, pending, size):
        for th, path in pending:
            im = make_thumb(path, self.ffmpeg, size)
            if im is not None:
                self.ui_queue.put(lambda th=th, im=im: self._set_thumb(th, im))

    def _set_thumb(self, th, im):
        with contextlib.suppress(tk.TclError):
            ph = ImageTk.PhotoImage(im)
            th.configure(image=ph)
            self.hist_imgs[str(th)] = ph

    def hist_act(self, act, idx):
        if idx >= len(self.history):
            return
        item = self.history[idx]
        path = Path(item[0])
        if act == "open":
            open_path(path)
        elif act == "folder":
            self.open_folder(path)
        elif act == "copy":
            copy_to_clipboard(Image.open(path))
            self.status.set("Imagem copiada.")
        elif act == "discord":
            self.tool_file.set(str(path))
            self.start_job("discord")
        elif act == "remove":
            del self.history[idx]
            self.save_cfg()
            self.refresh_history()

    # ------------------------------------------------------------ atalhos
    def register_hotkeys(self):
        if keyboard is None:
            return
        with contextlib.suppress(Exception):
            keyboard.clear_all_hotkeys()
        bad = []
        for k, hk in self.hotkeys.items():
            try:
                keyboard.add_hotkey(hk, lambda k=k: self.ui_queue.put(lambda: self.on_hotkey(k)))
            except Exception:
                bad.append(HK_NAMES[k])
        if bad:
            self.status.set("Atalho inválido: " + ", ".join(bad))

    def on_hotkey(self, k):
        now = time.time()
        if now - self._hk_last.get(k, 0) < 0.6:  # segurar a tecla não dispara várias vezes
            return
        self._hk_last[k] = now
        if k in ("region", "window", "full"):
            self.capture(k)
        elif k == "region_quick":
            self.capture("quick")
        elif k == "rec":
            self.toggle_record()
        elif k == "pause":
            self.toggle_pause()
        elif k == "replay_save":
            self.save_replay()
        elif k == "replay_toggle":
            self.toggle_replay()

    def change_hotkey(self, k):
        if keyboard is None:
            messagebox.showinfo("Print HQ", "Instale a biblioteca 'keyboard' para usar atalhos.")
            return
        self.status.set(f"Pressione a nova combinação para “{HK_NAMES[k]}”…")
        with contextlib.suppress(Exception):
            keyboard.clear_all_hotkeys()

        def work():
            try:
                combo = keyboard.read_hotkey(suppress=False)
            except Exception:
                combo = None
            self.ui_queue.put(lambda: self.set_hotkey(k, combo))
        threading.Thread(target=work, daemon=True).start()

    def set_hotkey(self, k, combo):
        if combo and combo in [v for kk, v in self.hotkeys.items() if kk != k]:
            self.status.set(f"“{combo}” já está em uso por outro atalho.")
        elif combo:
            self.hotkeys[k] = combo
            self.hk_vars[k].set(combo)
            self.save_cfg()
            self.status.set(f"Atalho definido: {combo}")
        self.register_hotkeys()

    def reset_hotkeys(self):
        self.hotkeys = dict(HK_DEFAULT)
        for k, v in self.hk_vars.items():
            v.set(self.hotkeys[k])
        self.save_cfg()
        self.register_hotkeys()
        self.status.set("Atalhos restaurados.")

    # ------------------------------------------------------------ prints
    def capture(self, mode):
        if self.busy:
            return
        self.busy = True
        try:
            delay = max(0, int(self.delay.get()))
        except Exception:
            delay = 0
        prev = self.root.state()
        self.root.withdraw()
        self.root.update()
        time.sleep(0.4 + delay)
        try:
            img, left, top = grab_screen()
            if mode == "full" and not self.use_editor.get():
                self.finish_snap(prev, img, True, False)
                return
            SnapOverlay(self, img, left, top, prev, mode="region" if mode == "quick" else mode, quick=(mode == "quick"))
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
            self.restore(prev)

    def cancel_snap(self, prev):
        self.status.set("Captura cancelada.")
        self.restore(prev)

    def finish_snap(self, prev, img, do_copy, do_save):
        self.restore(prev)
        self.deliver(img, do_copy, do_save)

    def finish_ocr(self, prev, img):
        self.restore(prev)
        self.status.set("Lendo o texto da imagem…")

        def work():
            try:
                txt = ocr_image(img)
                if not txt:
                    self.ui_queue.put(lambda: self.status.set("Nenhum texto encontrado na imagem."))
                    return
                ok = copy_text(txt)
                msg = ("Texto copiado:\n" if ok else "Texto lido (instale pywin32 para copiar):\n") + \
                    (txt[:140] + ("…" if len(txt) > 140 else ""))
                self.ui_queue.put(lambda: (self.status.set("Texto copiado."), show_toast(self.root, None, msg, seconds=6)))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: self.status.set(f"OCR falhou: {ex}"))
        threading.Thread(target=work, daemon=True).start()

    def deliver(self, img, do_copy, do_save):
        parts, path = [], None
        try:
            if do_copy:
                parts.append("copiado" if copy_to_clipboard(img) else "NÃO copiado (instale pywin32)")
            if do_save:
                path = save_image(img, Path(self.folder.get()), self.fmt.get())
                parts.append(f"salvo ({path.name})")
                self.add_history(path, "print")
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
            return
        text = "Print " + " e ".join(parts)
        self.status.set(f"{text} - {img.width}x{img.height}")
        if path:
            buttons = [("Abrir pasta", lambda: self.open_folder(path))]
        else:
            buttons = [("Salvar também", lambda: self.deliver(img, False, True))]
        show_toast(self.root, img, text, buttons)

    def open_folder(self, path):
        with contextlib.suppress(Exception):
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])

    # ------------------------------------------------------------ FFmpeg
    def refresh_ffmpeg_state(self):
        self.ffmpeg = find_ffmpeg()
        if self.ffmpeg:
            self.dl_btn.grid_remove()
            self.enc_var.set("Codificador: detectando…")
            threading.Thread(target=self._detect, daemon=True).start()
        else:
            self.enc_var.set("FFmpeg não encontrado: ele é necessário para gravar vídeo.")
            self.dl_btn.grid()

    def _detect(self):
        hw = detect_hw(self.ffmpeg)
        self.ui_queue.put(lambda: self.set_hw(hw))

    def set_hw(self, hw):
        self.hw, self.encoder, self.hw_ready = hw, hw["encoder"], True
        self.refresh_enc_label()

    def pick_encoder(self):
        """Automático: usa a placa/vídeo integrado quando existe (não rouba CPU do jogo); o tamanho final
        vem da otimização depois da gravação."""
        hw, mode = self.hw, self.enc_mode.get()
        if hw["encoder"] != "libx264" and mode != ENC_MODES[1]:
            return hw["encoder"], hw["rich"], hw["gpu"]
        return "libx264", True, False

    def refresh_enc_label(self, *a):
        if not self.hw_ready:
            return
        enc, rich, gpu_ok = self.pick_encoder()
        direct = gpu_ok and self.cap_mode.get() == CAP_MODES[0]
        self.enc_var.set(f"Em uso: {ENC_LABEL[enc]}\nDetectado neste PC: {ENC_LABEL[self.hw['encoder']]}\nCaptura: " +
                         ("direta pela placa de vídeo (a mais leve)" if direct else "compatível (passa pelo processador)"))

    def update_estimate(self, *a):
        try:
            tgt = self.rec_target.get()
            if tgt in self.mon_map:
                mon = self.mon_map[tgt][1]
                o = {"kind": "monitor", "mon": mon, "area": None}
            elif tgt == AREA_LABEL and self.area:
                o = {"kind": "area", "mon": self.area[2], "area": self.area[0]}
            else:
                o = {"kind": "window", "mon": None, "area": None}
            o["height"] = RES_MAP.get(self.rec_res.get(), 0)
            fps, qi = int(self.rec_fps.get()), QUALITY.index(self.rec_quality.get())
            w, h = out_dims(o)
            mb = bitrate_cap(qi, w, h, fps) * 60 / 8 / 1024
            self.est_var.set(f"Saída {w}x{h} a {fps} fps: no máximo ~{mb:.0f} MB por minuto "
                             "durante a gravação; ao terminar, a otimização encolhe o arquivo.")
        except Exception:
            self.est_var.set("")

    def download_ffmpeg(self):
        self.dl_btn.configure(state="disabled")
        self.status.set("Baixando FFmpeg…")

        def work():
            try:
                APP_DIR.mkdir(parents=True, exist_ok=True)
                zpath = APP_DIR / "ffmpeg.zip"
                req = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": "PrintHQ"})
                with urllib.request.urlopen(req, timeout=30) as r, open(zpath, "wb") as out:
                    total, done = int(r.headers.get("Content-Length") or 0), 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = done * 100 // total
                            self.ui_queue.put(lambda p=pct: self.status.set(f"Baixando FFmpeg… {p}%"))
                with zipfile.ZipFile(zpath) as z:
                    name = next((n for n in z.namelist() if n.endswith("/bin/ffmpeg.exe")), None)
                    if not name:
                        raise RuntimeError("ffmpeg.exe não encontrado no arquivo baixado")
                    with z.open(name) as src, open(APP_DIR / "ffmpeg.exe", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                zpath.unlink(missing_ok=True)
                self.ui_queue.put(lambda: (self.status.set("FFmpeg instalado."), self.refresh_ffmpeg_state()))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: (self.status.set(f"Falha ao baixar FFmpeg: {ex}"),
                                                 self.dl_btn.configure(state="normal")))
        threading.Thread(target=work, daemon=True).start()

    def get_offset(self):
        try:
            return int(self.audio_offset.get())
        except Exception:
            return 0

    def make_opts(self):
        """Monta as opções de gravação/replay a partir da tela. Retorna None se algo faltar."""
        if not self.ffmpeg:
            self.status.set("Baixe o FFmpeg na aba Gravar para poder gravar.")
            return None
        tgt = self.rec_target.get()
        kind, idx, mon, area, title = "monitor", 0, None, None, ""
        if tgt == WIN_LABEL:
            kind, title = "window", self.rec_window.get()
            if not title:
                self.status.set("Escolha uma janela na aba Gravar.")
                return None
            mon = self.mon_list[0] if self.mon_list else {"left": 0, "top": 0, "width": 1920, "height": 1080}
        elif tgt == AREA_LABEL:
            if not self.area:
                self.status.set("Escolha a área na aba Gravar (Selecionar área…).")
                return None
            kind, (area, idx, mon) = "area", self.area
        else:
            idx, mon = self.mon_map.get(tgt, (0, None))
            if mon is None:
                self.status.set("Nenhum monitor encontrado.")
                return None
        use_audio = not self.audio_err
        mic = self.rec_mic.get() if use_audio and self.rec_mic.get() != "Nenhum" else ""
        spk = self.rec_sys.get() if use_audio and self.rec_sys.get() not in ("", "Nenhum") else ""
        enc, rich, gpu_ok = self.pick_encoder()
        height = RES_MAP.get(self.rec_res.get(), 0)
        src_h = area[3] if kind == "area" else mon["height"]
        gpu = bool(gpu_ok and self.cap_mode.get() == CAP_MODES[0] and kind in ("monitor", "area"))
        post_h = 0
        if gpu and height and height < src_h:  # a captura direta não reduz: grava na resolução da tela e reduz depois
            post_h, height = height, 0
        return {"ffmpeg": self.ffmpeg, "kind": kind, "mon": mon, "out_idx": idx, "area": area, "title": title,
                "fps": int(self.rec_fps.get()), "height": height, "post_height": post_h, "quality": QUALITY.index(self.rec_quality.get()),
                "encoder": enc, "rich": rich, "gpu": gpu, "gpu_vf": self.hw.get("gpu_vf") if enc != "libx264" else None,
                "stage_pref": self.cfg.get("stage_pref"),
                "mic": mic, "spk": spk, "offset_ms": self.get_offset()}

    def disk_ok(self):
        try:
            folder = Path(self.vfolder.get())
            folder.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(folder).free
        except Exception:
            return True
        if free < 300 * 1024 ** 2:
            self.status.set(f"Pouco espaço no disco ({fmt_size(free)} livres). Libere espaço antes de gravar.")
            return False
        if free < 2 * 1024 ** 3:
            self.status.set(f"Atenção: só {fmt_size(free)} livres no disco.")
        return True

    # ------------------------------------------------------------ gravação
    def update_rec_ui(self):
        st = self.rec.state
        busy = st in ("recording", "paused")
        self.rec_btn.configure(text="■  Parar" if busy else "●  Gravar",
                               state="disabled" if st in ("finalizing", "replay") else "normal")
        self.pause_btn.configure(text="Retomar" if st == "paused" else "Pausar", state="normal" if busy else "disabled")
        self.replay_var.set(st == "replay")
        self.replay_text.set("Ligado" if st == "replay" else "Desligado")
        self.replay_save_btn.configure(state="normal" if st == "replay" else "disabled")
        if self.minibar and hasattr(self, "mb_pause"):
            with contextlib.suppress(tk.TclError):
                self.mb_pause.configure(text="Retomar" if st == "paused" else "Pausar")

    def toggle_record(self):
        if self.rec.state == "idle":
            self.start_record()
        elif self.rec.state in ("recording", "paused"):
            self.stop_record()
        elif self.rec.state == "replay":
            self.status.set("Desligue o replay antes de gravar.")

    def toggle_pause(self):
        if self.rec.state == "recording":
            self.rec.pause()
            self.status.set("Gravação pausada.")
        elif self.rec.state == "paused":
            try:
                self.rec.resume()
                self.status.set("Gravando…")
            except Exception as ex:
                self.status.set(f"Erro ao retomar: {ex}")
        self.update_rec_ui()

    def start_record(self):
        opts = self.make_opts()
        if not opts or not self.disk_ok():
            return
        self.post_height = opts["post_height"]
        try:
            Path(self.vfolder.get()).mkdir(parents=True, exist_ok=True)
            self.rec.start(opts)
        except Exception as ex:
            self.status.set(f"Não consegui iniciar a gravação: {ex}")
            self.rec.state = "idle"
            return
        self.status.set("Gravando…  (use o atalho ou a barrinha no topo da tela)")
        self.update_rec_ui()
        self.show_minibar(replay=False)
        self.root.iconify()
        if not self.ticking:
            self.ticking = True
            self.tick()

    def stop_record(self):
        was = self.rec.begin_stop()
        self.status.set("Finalizando vídeo…")
        self.update_rec_ui()
        out = Path(self.vfolder.get()) / datetime.now().strftime("gravacao_%Y-%m-%d_%H-%M-%S.mp4")
        dur = self.rec.elapsed()

        def work():
            try:
                self.rec.finalize(was, out)
                frames = count_frames(self.ffmpeg, out)
                self.ui_queue.put(lambda: self.rec_done(out, dur, None, frames))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: self.rec_done(None, dur, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def rec_done(self, out, dur, err, frames=0):
        self.rec.state = "idle"
        self.close_minibar()
        if self.root.state() == "iconic":
            self.show_main()
        self.update_rec_ui()
        if err:
            self.status.set("Erro ao gravar.")
            messagebox.showerror("Print HQ", err[-700:])
            return
        size = fmt_size(out.stat().st_size)
        kbps = int(out.stat().st_size * 8 / max(1.0, dur) / 1000)
        msg = f"Vídeo salvo: {out.name} ({fmt_time(dur)}, {size}, média de {kbps} kbps)"
        try:
            target = int(self.rec_fps.get())
        except ValueError:
            target = 60
        real = frames / dur if dur > 1 and frames else 0
        if real:
            msg += f" • {real:.0f} fps reais"
            if real < target * 0.9:
                msg += f" (abaixo dos {target} fps pedidos: tente 30 fps)"
        self.status.set(msg)
        self.add_history(out, "video")
        self.tool_file.set(str(out))
        show_toast(self.root, None, f"Gravação salva\n{fmt_time(dur)}  •  {size}",
                   [("Abrir", lambda: open_path(out)), ("Pasta", lambda: self.open_folder(out)),
                    ("p/ Discord", lambda: self.start_job("discord"))], seconds=10)
        if self.ffmpeg and (self.post_height or self.auto_opt.get()):
            self.optimize(out, replace=True, height=self.post_height, force=bool(self.post_height))

    def rec_failed(self, err):
        if self.rec.state in ("idle", "finalizing"):
            return
        self.rec._kill()
        self.rec.state = "idle"
        self.close_minibar()
        self.show_main()
        self.update_rec_ui()
        self.status.set("A gravação falhou.")
        messagebox.showerror("Print HQ", "O FFmpeg não conseguiu gravar.\n\n" + (err or "sem detalhes"))

    def tick(self):
        r = self.rec
        if r.state not in ("recording", "paused"):
            self.ticking = False
            return
        if r.state == "recording" and r.proc is not None and r.proc.poll() is not None \
                and time.time() - r.seg_t0 > 3:
            self.ticking = False
            if r.size() > 2048:
                self.status.set("A gravação parou sozinha. Salvando o que foi gravado…")
                self.stop_record()
            else:
                self.rec_failed(r.tail_logs())
            return
        t, s = fmt_time(r.elapsed()), fmt_size(r.size())
        self.time_var.set(t)
        self.size_var.set(s)
        if self.minibar:
            self.mb_time.set(f"{t}   {s}")
            self.mb_dot.configure(fg=RED if (r.state == "recording" and int(time.time()) % 2 == 0) else "#7a2a2c")
        self.root.after(500, self.tick)

    def show_minibar(self, replay):
        mb = self.minibar = tk.Toplevel(self.root)
        mb.overrideredirect(True)
        mb.attributes("-topmost", True)
        mb.configure(bg=PANEL, highlightbackground="#555", highlightthickness=1)
        self.mb_time = tk.StringVar(value="REPLAY ligado" if replay else "00:00:00   0.0 MB")
        self.mb_dot = tk.Label(mb, text="●", fg=RED, bg=PANEL, font=("Segoe UI", 12))
        self.mb_dot.pack(side="left", padx=(10, 4))
        tk.Label(mb, textvariable=self.mb_time, fg="white", bg=PANEL, font=("Consolas", 11, "bold")).pack(side="left", padx=6)
        kw = dict(bg=PANEL, fg="white", activebackground="#3a3d44", activeforeground="white",
                  relief="flat", bd=0, padx=8, pady=3, font=("Segoe UI", 10), takefocus=0)
        if replay:
            tk.Button(mb, text="Salvar replay", command=self.save_replay, **kw).pack(side="left", pady=4)
            tk.Button(mb, text="Desligar", command=self.toggle_replay, **kw).pack(side="left", padx=(0, 6), pady=4)
        else:
            self.mb_pause = tk.Button(mb, text="Pausar", command=self.toggle_pause, **kw)
            self.mb_pause.pack(side="left", pady=4)
            tk.Button(mb, text="Parar", command=self.toggle_record, **kw).pack(side="left", padx=(0, 6), pady=4)
        mb.update_idletasks()
        mb.geometry(f"+{(self.root.winfo_screenwidth() - mb.winfo_reqwidth()) // 2}+8")
        exclude_from_capture(mb)

    def close_minibar(self):
        if self.minibar:
            with contextlib.suppress(tk.TclError):
                self.minibar.destroy()
            self.minibar = None

    # ------------------------------------------------------------ replay
    def toggle_replay(self):
        try:
            if self.rec.state == "replay":
                self.rec.stop_replay()
                self.close_minibar()
                self.status.set("Replay desligado.")
                return
            if self.rec.state != "idle":
                self.status.set("Pare a gravação antes de ligar o replay.")
                return
            opts = self.make_opts()
            if not opts:
                return
            secs = int(self.replay_len.get().split()[0])
            if not self.disk_ok():
                return
            self.post_height = opts["post_height"]
            try:
                self.rec.start_replay(opts, secs)
            except Exception as ex:
                self.status.set(f"Não consegui ligar o replay: {ex}")
                self.rec.state = "idle"
                return
            self.show_minibar(replay=True)
            self.status.set(f"Replay ligado: guardando os últimos {secs} s. Atalho: {self.hotkeys['replay_save']}")
        finally:
            self.update_rec_ui()

    def save_replay(self):
        if self.rec.state != "replay":
            self.status.set("Ligue o replay primeiro (aba Replay).")
            return
        out = Path(self.vfolder.get()) / datetime.now().strftime("replay_%Y-%m-%d_%H-%M-%S.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        self.status.set("Salvando replay…")

        def work():
            try:
                self.rec.save_replay(out)
                self.ui_queue.put(lambda: self.replay_saved(out, None))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: self.replay_saved(None, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def replay_saved(self, out, err):
        if err:
            self.status.set(f"Replay não salvo: {err[-200:]}")
            return
        self.add_history(out, "replay")
        self.tool_file.set(str(out))
        if self.post_height and self.ffmpeg:
            self.optimize(out, replace=True, height=self.post_height, force=True)
        self.status.set(f"Replay salvo: {out.name} ({fmt_size(out.stat().st_size)})")
        show_toast(self.root, None, f"Replay salvo\n{fmt_size(out.stat().st_size)}",
                   [("Abrir", lambda: open_path(out)), ("Pasta", lambda: self.open_folder(out)),
                    ("p/ Discord", lambda: self.start_job("discord"))], seconds=8)

    # ------------------------------------------------------------ ferramentas de vídeo
    def pick_tool_file(self):
        p = filedialog.askopenfilename(initialdir=self.vfolder.get(),
                                       filetypes=[("Vídeos", "*.mp4 *.mkv *.mov *.webm *.avi"), ("Todos", "*.*")])
        if p:
            self.tool_file.set(p)

    def use_last_video(self):
        vids = [h for h in self.history if h[1] in ("video", "replay")]
        if vids:
            self.tool_file.set(vids[0][0])
        else:
            self.status.set("Ainda não há gravações no histórico.")

    def start_job(self, kind):
        if not self.ffmpeg:
            self.status.set("Baixe o FFmpeg na aba Gravar.")
            return
        src = Path(self.tool_file.get())
        if not src.is_file():
            self.status.set("Escolha um vídeo na aba Ferramentas.")
            return
        if self.job_running:
            self.status.set("Já existe uma tarefa em andamento.")
            return
        try:
            s = parse_time(self.t_start.get()) or 0.0
            e = parse_time(self.t_end.get())
            mb = int(self.dc_limit.get().split()[0])
            gw, gf = int(self.gif_w.get()), int(self.gif_fps.get())
        except ValueError:
            self.status.set("Tempo inválido. Use mm:ss ou segundos.")
            return
        precise, (enc, rich, _), qi = self.precise.get(), self.pick_encoder(), QUALITY.index(self.rec_quality.get())
        self.job_running = True
        self.progress.configure(value=0)
        self.status.set("Processando…")

        def work():
            try:
                ff = self.ffmpeg
                dur = probe_duration(ff, src)
                end = min(e, dur) if e else dur
                span = end - s
                if span <= 0:
                    raise RuntimeError("O fim precisa ser maior que o início.")
                if kind == "discord":
                    out, total = src.with_name(src.stem + "_discord.mp4"), dur
                    cmd = discord_cmd(ff, src, out, dur, mb)
                elif kind == "trim":
                    out, total = src.with_name(src.stem + "_corte.mp4"), span
                    cmd = trim_cmd(ff, src, out, s, span, precise, enc, qi, rich)
                else:
                    span = min(span, 30)
                    out, total = src.with_name(src.stem + ".gif"), span
                    cmd = gif_cmd(ff, src, out, s, span, gw, gf)
                rc, err = run_job(cmd, total, lambda p: self.ui_queue.put(lambda: self.progress.configure(value=p)))
                if rc != 0 or not out.exists():
                    raise RuntimeError(err[-300:] or "o FFmpeg falhou")
                self.ui_queue.put(lambda: self.job_done(out, None))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: self.job_done(None, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def job_done(self, out, err):
        self.job_running = False
        if err:
            self.status.set(f"Falhou: {err}")
            return
        self.progress.configure(value=100)
        self.root.after(4000, lambda: self.progress.configure(value=0))
        size = fmt_size(out.stat().st_size)
        self.status.set(f"Pronto: {out.name} ({size})")
        self.add_history(out, "tool")
        show_toast(self.root, None, f"Pronto!\n{out.name}  •  {size}",
                   [("Abrir", lambda: open_path(out)), ("Pasta", lambda: self.open_folder(out))], seconds=8)

    def run_diagnostic(self):
        if not self.ffmpeg:
            self.status.set("Baixe o FFmpeg primeiro (Configurações > Vídeo).")
            return
        self.status.set("Gerando o diagnóstico (leva uns 20 segundos)…")

        def work():
            try:
                text, hw = self.build_report()
            except Exception as ex:
                text, hw = f"Falha ao gerar o diagnóstico: {ex}", None
            self.ui_queue.put(lambda: self.show_report(text, hw))
        threading.Thread(target=work, daemon=True).start()

    def build_report(self):
        ff = self.ffmpeg

        def out(args):
            try:
                r = subprocess.run([ff, "-hide_banner"] + args, capture_output=True, timeout=30, creationflags=NOWIN)
                return (r.stdout + r.stderr).decode(errors="ignore")
            except Exception as ex:
                return str(ex)
        ver = out(["-version"]).splitlines()[:1]
        L = [f"Print HQ v{VERSION}", f"Windows: {platform.platform()}", f"Python: {sys.version.split()[0]}",
             f"Processador: {platform.processor()}",
             "Tela(s): " + ", ".join(f"{m['width']}x{m['height']}" for m in self.mon_list), "",
             "FFmpeg: " + (ver[0] if ver else "?"),
             "Codificadores H.264: " + ", ".join(sorted(set(re.findall(r"h264_\w+|libx264", out(["-encoders"]))))),
             "Filtros: " + ", ".join(n for n in ("ddagrab", "gfxcapture", "scale_d3d11", "hwdownload")
                                     if re.search(rf"\b{n}\b", out(["-filters"]))), "", "TESTES NESTE PC:"]
        hw = detect_hw(ff)
        for name, ok, err in hw["log"]:
            L.append(f"  [{'OK' if ok else 'FALHOU'}] {name}" + (f"\n      {err}" if err and not ok else ""))
        L += ["", f"Resultado: codificador={hw['encoder']} avançado={hw['rich']} captura direta={hw['gpu']} "
                  f"filtro={hw['gpu_vf']}", "", "AJUSTES:",
              f"  resolução={self.rec_res.get()} fps={self.rec_fps.get()} qualidade={self.rec_quality.get()}",
              f"  codificador={self.enc_mode.get()} | captura={self.cap_mode.get()} | otimizar={self.auto_opt.get()}",
              f"  áudio: mic={self.rec_mic.get()} | pc={self.rec_sys.get()} | sincronia={self.get_offset()} ms",
              f"  etapa preferida: {self.cfg.get('stage_pref')}"]
        last = APP_DIR / "ultima_gravacao.txt"
        if last.exists():
            L += ["", "ÚLTIMA GRAVAÇÃO:", last.read_text(errors="ignore")[-2500:]]
        return "\n".join(L), hw

    def show_report(self, text, hw):
        if hw:
            self.set_hw(hw)
        with contextlib.suppress(Exception):
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "diagnostico.txt").write_text(text, encoding="utf-8")
        self.status.set("Diagnóstico pronto. Clique em “Copiar tudo” e cole na conversa.")
        w = tk.Toplevel(self.root)
        w.title("Print HQ - Diagnóstico")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        txt = tk.Text(w, width=100, height=28, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                      font=("Consolas", 9), wrap="word")
        txt.pack(fill="both", expand=True, padx=px(10), pady=(px(10), 0))
        txt.insert("1.0", text)
        bar = tk.Frame(w, bg=BG)
        bar.pack(fill="x", padx=px(10), pady=px(8))

        def do_copy():
            ok = copy_text(txt.get("1.0", "end").strip())
            self.status.set("Relatório copiado." if ok else "Não consegui copiar: selecione o texto e use Ctrl+C.")
        RoundBtn(bar, "Copiar tudo", command=do_copy, kind="accent", bg=BG, padx=16, pady=7).pack(side="left")
        RoundBtn(bar, "Fechar", command=w.destroy, bg=BG, padx=16, pady=7).pack(side="left", padx=8)

    def perf_test(self):
        if self.rec.state != "idle" or self.job_running:
            self.status.set("Pare a gravação e o replay antes de testar o desempenho.")
            return
        opts = self.make_opts()
        if not opts or not self.disk_ok():
            return
        opts["mic"] = opts["spk"] = ""
        opts["stage_pref"] = None
        target = opts["fps"]
        self.job_running = True
        self.status.set("Teste de desempenho: ABRA O JOGO AGORA. Começa em 6 s e testa cada modo por 10 s.")

        def work():
            results, tmpd = [], APP_DIR / "tmp"
            tmpd.mkdir(parents=True, exist_ok=True)
            time.sleep(6)
            for backend, gpu in Recorder(self)._stages(opts):
                out = tmpd / "perf.mkv"
                cmd = build_cmd(opts["ffmpeg"], opts, out, backend, False, gpu=gpu)
                cmd = cmd[:-1] + ["-t", "10", str(out)]
                try:
                    r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NOWIN)
                    ok, err = r.returncode == 0 and out.exists(), r.stderr.decode(errors="ignore")[-200:]
                except Exception as ex:
                    ok, err = False, str(ex)
                if ok:
                    n = count_frames(opts["ffmpeg"], out)
                    results.append((backend, gpu, n / 10, int(out.stat().st_size * 8 / 10 / 1000), ""))
                else:
                    results.append((backend, gpu, 0, 0, err))
                with contextlib.suppress(Exception):
                    out.unlink()
                if ok and results[-1][2] >= target * 0.9:
                    break
            self.ui_queue.put(lambda: self.perf_done(results, target))
        threading.Thread(target=work, daemon=True).start()

    def perf_done(self, results, target):
        self.job_running = False
        ok = [r for r in results if r[2] > 0]
        lines = [f"• {STAGE_NAMES.get((b, g), b)}: " + (f"{fps:.0f} fps, ~{kb} kbps" if fps else "não funcionou")
                 for b, g, fps, kb, _ in results]
        if not ok:
            self.status.set("O teste falhou em todos os modos. Veja Configurações > Geral > Diagnóstico.")
            return
        good = next((r for r in ok if r[2] >= target * 0.9), None)
        pick = good or max(ok, key=lambda r: r[2])
        self.cfg["stage_pref"] = [pick[0], pick[1]]
        self.save_cfg()
        msg = "Resultado do teste:\n" + "\n".join(lines) + f"\n\nVou usar: {STAGE_NAMES.get((pick[0], pick[1]))}."
        self.status.set(f"Teste concluído: {pick[2]:.0f} fps com {STAGE_NAMES.get((pick[0], pick[1]))}.")
        if not good and target > 30:
            if messagebox.askyesno("Teste de desempenho", msg + f"\n\nNenhum modo chegou a {target} fps "
                                   f"(melhor: {pick[2]:.0f}). Mudar para 30 fps?"):
                self.rec_fps.set("30")
        else:
            messagebox.showinfo("Teste de desempenho", msg)

    def optimize(self, src, replace=False, height=0, force=False):
        """Recodifica com calma (prioridade baixa) para encolher o arquivo sem perda visível."""
        src = Path(src) if src else None
        if not self.ffmpeg:
            self.status.set("Baixe o FFmpeg em Configurações > Vídeo.")
            return
        if not src or not src.is_file():
            self.status.set("Escolha um vídeo na aba Ferramentas.")
            return
        if self.job_running:
            self.status.set("Já existe uma tarefa em andamento.")
            return
        self.job_running = True
        self.progress.configure(value=0)
        qi = QUALITY.index(self.rec_quality.get())
        out = src.with_name(src.stem + "_leve.mp4")
        self.status.set("Otimizando o vídeo em segundo plano (pode usar o PC normalmente)…" if not height else
                        f"Convertendo para {height}p e otimizando (pode usar o PC normalmente)…")

        def work():
            try:
                dur = probe_duration(self.ffmpeg, src)
                rc, err = run_job(optimize_cmd(self.ffmpeg, src, out, qi, height), dur, lambda p: self.ui_queue.put(
                    lambda: (self.progress.configure(value=p), self.status.set(f"Otimizando… {int(p)}%"))), low=True)
                if rc != 0 or not out.exists():
                    raise RuntimeError(err[-300:] or "o FFmpeg falhou")
                before, after = src.stat().st_size, out.stat().st_size
                final = out
                if replace:
                    if force or after < before * 0.95:
                        os.replace(out, src)
                    else:
                        out.unlink(missing_ok=True)
                        after = before
                    final = src
                self.ui_queue.put(lambda: self.opt_done(final, before, after, replace, None))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: self.opt_done(None, 0, 0, replace, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def opt_done(self, final, before, after, replace, err):
        self.job_running = False
        if err:
            self.status.set(f"Não consegui otimizar: {err}")
            return
        self.progress.configure(value=100)
        self.root.after(4000, lambda: self.progress.configure(value=0))
        pct = max(0, round((1 - after / before) * 100)) if before else 0
        msg = f"Otimizado: {fmt_size(before)} → {fmt_size(after)} (-{pct}%)" if after < before else \
            "O vídeo já estava bem compacto; mantive o original."
        self.status.set(msg)
        if not replace:
            self.add_history(final, "tool")
        show_toast(self.root, None, msg, [("Abrir", lambda: open_path(final)), ("Pasta", lambda: self.open_folder(final))],
                   seconds=8)

    # ------------------------------------------------------------ atualização
    def check_update(self):
        if FROZEN:
            self.status.set("Na versão .exe a atualização é manual: baixe o novo print_hq.pyw e rode build_exe.bat.")
            return
        try:
            code = fetch_update()
            m = re.search(r'^VERSION\s*=\s*"([\d.]+)"', code, re.M)
            if not m:
                raise ValueError("arquivo remoto sem VERSION")
            compile(code, "update", "exec")
            if version_tuple(m.group(1)) <= version_tuple(VERSION):
                self.status.set(f"Você já está na versão mais recente (v{VERSION}).")
                return
            if not messagebox.askyesno(
                    "Print HQ", f"Nova versão {m.group(1)} disponível (você tem {VERSION}).\nAtualizar agora?"):
                return
            path = Path(__file__).resolve()
            path.with_suffix(".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            path.write_text(code, encoding="utf-8")
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe")
            subprocess.Popen([str(pyw if pyw.exists() else exe), str(path)])
            if self.tray:
                self.tray.stop()
            self.root.destroy()
            os._exit(0)
        except urllib.error.HTTPError as ex:
            hint = " O arquivo no GitHub precisa se chamar exatamente print_hq.pyw." if ex.code == 404 else ""
            self.status.set(f"Não consegui atualizar (erro {ex.code}).{hint}")
        except Exception as ex:
            self.status.set(f"Não consegui atualizar: {ex}")

    def run(self):
        self.root.mainloop()


_MUTEX = None


def already_running():
    """Evita abrir o app duas vezes (os atalhos disparariam em dobro)."""
    global _MUTEX
    if sys.platform != "win32":
        return False
    try:
        _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "PrintHQ_single_instance")
        return ctypes.windll.kernel32.GetLastError() == 183
    except Exception:
        return False


if __name__ == "__main__":
    if "--make-icon" in sys.argv:  # usado pelo build_exe.bat
        make_icon(256).save("print_hq.ico", sizes=[(256, 256), (64, 64), (32, 32), (16, 16)])
        sys.exit(0)
    if already_running():
        with contextlib.suppress(Exception):
            r0 = tk.Tk()
            r0.withdraw()
            messagebox.showinfo("Print HQ", "O Print HQ já está aberto.\nProcure o ícone dele ao lado do relógio.")
        sys.exit(0)
    try:
        App().run()
    except Exception:  # sem console no pythonw: mostra o erro em uma janela e salva em arquivo
        import traceback
        err = traceback.format_exc()
        with contextlib.suppress(Exception):
            APP_DIR.mkdir(parents=True, exist_ok=True)
            (APP_DIR / "error.log").write_text(err, encoding="utf-8")
        with contextlib.suppress(Exception):
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror("Print HQ - erro ao iniciar", err[-1500:])
        sys.exit(1)
