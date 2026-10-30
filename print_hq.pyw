"""
Print HQ v2.0 - prints com editor embutido + gravação de tela leve (via FFmpeg).

Instalação (uma vez):
    py -m pip install mss pillow keyboard pywin32 soundcard numpy
(soundcard/numpy só são necessários para gravar áudio. O FFmpeg o app baixa sozinho.)
"""
import contextlib
import copy
import ctypes
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
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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

VERSION = "2.0.0"
UPDATE_URL = "https://raw.githubusercontent.com/antonioexpedito020-code/print-hq/main/print_hq.pyw"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
CONFIG = Path.home() / ".print_hq.json"
APP_DIR = Path.home() / ".print_hq_data"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

BG, PANEL, FG, MUTED = "#1e1f22", "#2b2d31", "#e6e6e6", "#9aa0a6"
ACCENT, RED = "#4f8cff", "#e5484d"

HK_NAMES = {"region": "Capturar área", "full": "Capturar tela inteira",
            "rec": "Iniciar / parar gravação", "pause": "Pausar / retomar gravação"}
HK_DEFAULT = {"region": "ctrl+alt+a", "full": "ctrl+alt+s", "rec": "ctrl+alt+r", "pause": "ctrl+alt+p"}

RES_MAP = {"Original": 0, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480}
QUALITY = ["Leve (arquivo pequeno)", "Equilibrada", "Alta qualidade"]
QV = {"libx264": [30, 25, 21], "h264_nvenc": [33, 28, 23], "h264_amf": [33, 28, 23], "h264_qsv": [33, 28, 23]}
ENC_LABEL = {"h264_nvenc": "placa NVIDIA (NVENC) - leve para o PC", "h264_amf": "placa AMD (AMF) - leve para o PC",
             "h264_qsv": "Intel (QuickSync) - leve para o PC", "libx264": "processador (x264)"}


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


def exclude_from_capture(win):
    """Faz a janela não aparecer em gravações/prints de tela (Windows 10 2004+)."""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2)
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)
    except Exception:
        pass


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


def copy_to_clipboard(img):
    if win32clipboard is None:
        return False
    rgb = img.convert("RGB")
    bmp = io.BytesIO()
    rgb.save(bmp, "BMP")
    dib = bmp.getvalue()[14:]
    png = io.BytesIO()
    rgb.save(png, "PNG")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib)
        try:  # formato PNG: melhor compatibilidade com Discord, navegadores etc.
            win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat("PNG"), png.getvalue())
        except Exception:
            pass
    finally:
        win32clipboard.CloseClipboard()
    return True


# ====================================================== anotações (desenho em PIL)
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


def get_font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def render(img, shapes):
    """Aplica as anotações na imagem em resolução total, com bordas suavizadas."""
    if not shapes:
        return img
    w, h = img.size
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
        elif t == "line":
            d.line([(x0, y0), (x1, y1)], fill=col, width=wd)
        elif t == "arrow":
            base, pts = arrow_head(s["x0"], s["y0"], s["x1"], s["y1"], s["width"])
            d.line([(x0, y0), (base[0] * k, base[1] * k)], fill=col, width=wd)
            d.polygon([(px * k, py * k) for px, py in pts], fill=col)
        elif t == "text":
            d.text((x0, y0), s["text"], fill=col, font=get_font(text_size(s["width"]) * k))
    if k > 1:
        over = over.resize((w, h), Image.LANCZOS)
    out = img.convert("RGBA")
    out.alpha_composite(over)
    return out.convert("RGB")


# ================================================================ FFmpeg
def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = APP_DIR / "ffmpeg.exe"
    return str(local) if local.exists() else None


def detect_encoder(ffmpeg):
    """Testa de verdade qual codificador por placa de vídeo funciona neste PC."""
    for enc in ("h264_nvenc", "h264_amf", "h264_qsv"):
        try:
            r = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=1280x720:r=30", "-frames:v", "3", "-vf", "format=nv12",
                 "-c:v", enc, "-f", "null", "-"],
                capture_output=True, timeout=20, creationflags=NOWIN)
            if r.returncode == 0:
                return enc
        except Exception:
            pass
    return "libx264"


def enc_args(enc, qi, fps):
    q, g = QV[enc][qi], str(fps * 2)
    if enc == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(q), "-b:v", "0",
                "-profile:v", "high", "-g", g]
    if enc == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", str(q),
                "-qp_p", str(q + 2), "-g", g]
    if enc == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", str(q), "-g", g]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(q), "-profile:v", "high", "-g", g]


def video_filter(backend, src_h, height, pix):
    f = ["hwdownload", "format=bgra"] if backend == "ddagrab" else []
    scale = f"scale=-2:{height}" if height and height < src_h else "scale=iw:ih"
    f.append(scale + ":flags=bicubic:out_color_matrix=bt709:out_range=tv")
    f.append(f"format={pix}")
    return ",".join(f)


def build_cmd(ff, o, out, backend, audio):
    fps, mon = o["fps"], o["mon"]
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-nostats"]
    if backend == "ddagrab":
        cmd += ["-f", "lavfi", "-i", f"ddagrab=output_idx={o['out_idx']}:framerate={fps}:draw_mouse=1"]
    else:
        cmd += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", "1",
                "-offset_x", str(mon["left"]), "-offset_y", str(mon["top"]),
                "-video_size", f"{mon['width']}x{mon['height']}", "-i", "desktop"]
    if audio:
        cmd += ["-thread_queue_size", "1024", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", "pipe:0"]
    pix = "yuv420p" if o["encoder"] == "libx264" else "nv12"
    cmd += ["-vf", video_filter(backend, mon["height"], o["height"], pix)]
    cmd += enc_args(o["encoder"], o["quality"], fps)
    cmd += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-shortest"]
    cmd.append(str(out))
    return cmd


def list_audio():
    """Lista microfones e saídas de som (para captar o áudio do computador via loopback)."""
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

    def stop(self):
        self.stop_ev.set()

    def run(self):
        try:
            try:
                import pythoncom
                pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
            except Exception:
                pass
            if self.offset_ms > 0:
                self.stdin.write(b"\x00" * (int(self.RATE * self.offset_ms / 1000) * 8))
            self._record()
        except Exception as ex:
            self.error = str(ex)
            self._silence()  # mantém o vídeo gravando mesmo se o áudio falhar
        finally:
            try:
                self.stdin.close()
            except Exception:
                pass

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
            while not self.stop_ev.is_set():
                mix = None
                for r in recs:
                    d = r.record(numframes=self.BLOCK)
                    if d.ndim == 1:
                        d = d[:, None]
                    if d.shape[1] == 1:
                        d = np.repeat(d, 2, axis=1)
                    mix = d if mix is None else mix + d
                self.stdin.write(np.clip(mix, -1, 1).astype("<f4").tobytes())
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
    """Grava em segmentos (permite pausar/retomar) e junta tudo no final."""

    def __init__(self, app):
        self.app = app
        self.state = "idle"  # idle | recording | paused | finalizing
        self.proc = self.feeder = self.logf = None
        self.segments, self.acc, self.seg_t0 = [], 0.0, 0.0
        self.opts, self.tmp, self.backend, self.retried = None, None, "ddagrab", False

    def elapsed(self):
        return self.acc + (time.time() - self.seg_t0 if self.state == "recording" else 0)

    def size(self):
        total = 0
        for s in self.segments:
            try:
                total += s.stat().st_size
            except OSError:
                pass
        return total

    def start(self, opts):
        self.opts, self.backend, self.retried = opts, "ddagrab", False
        self.segments, self.acc = [], 0.0
        self.tmp = APP_DIR / "tmp" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._launch()
        self.state = "recording"
        self.app.root.after(2200, self._health)

    def _launch(self):
        o, n = self.opts, len(self.segments)
        seg = self.tmp / f"seg{n:03d}.mkv"
        self.segments.append(seg)
        audio = bool(o["mic"] or o["spk"])
        cmd = build_cmd(o["ffmpeg"], o, seg, self.backend, audio)
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
        for p in sorted(self.tmp.glob("log*.txt")):
            try:
                txt += p.read_text(errors="ignore")[-600:]
            except Exception:
                pass
        return txt.strip()[-600:]

    def _health(self):
        """Se o FFmpeg morreu logo no início, tenta o método alternativo (gdigrab)."""
        if self.state != "recording" or self.proc is None or self.proc.poll() is None:
            return
        err = self.tail_logs()
        if self.backend == "ddagrab" and not self.retried:
            self.retried, self.backend = True, "gdigrab"
            self._kill()
            self.segments.pop()
            self._launch()
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
        if len(segs) == 1:
            cmd = [ff, "-y", "-loglevel", "error", "-i", str(segs[0]), "-c", "copy",
                   "-movflags", "+faststart", str(out)]
        else:
            lst = self.tmp / "list.txt"
            lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
            cmd = [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                   "-c", "copy", "-movflags", "+faststart", str(out)]
        r = subprocess.run(cmd, capture_output=True, creationflags=NOWIN)
        if r.returncode != 0 or not Path(out).exists():
            raise RuntimeError("Falha ao finalizar o vídeo: " + r.stderr.decode(errors="ignore")[-400:])
        shutil.rmtree(self.tmp, ignore_errors=True)


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
    st.configure("Rec.TButton", background=RED, foreground="white", font=("Segoe UI", 11, "bold"),
                 padding=(12, 9))
    st.map("Rec.TButton", background=[("active", "#ff6166"), ("disabled", "#5a2a2c")])
    st.configure("TCheckbutton", background=BG, foreground=FG)
    st.map("TCheckbutton", background=[("active", BG)])
    st.configure("TCombobox", arrowsize=14, padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", PANEL), ("disabled", "#232427")],
           foreground=[("readonly", FG), ("disabled", "#666")],
           selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])
    st.configure("TSpinbox", arrowsize=12, padding=3)
    st.configure("TNotebook", background=BG, borderwidth=0)
    st.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(16, 8), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", FG)])
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


# ===================================== captura de área com editor embutido
TOOLS = [("select", "↖", "v", "Selecionar / mover (V)"), ("arrow", "➚", "a", "Seta (A)"),
         ("rect", "▭", "r", "Retângulo (R)"), ("ellipse", "◯", "o", "Elipse (O)"),
         ("line", "╱", "l", "Linha (L)"), ("text", "T", "t", "Texto (T)")]
COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#000000", "#ffffff"]


class SnapOverlay:
    """Tela congelada: você seleciona a área e as ferramentas aparecem logo abaixo dela."""

    def __init__(self, app, img, left, top, prev, full=False):
        self.app, self.img, self.prev = app, img, prev
        self.W, self.H = img.size
        self.phase, self.box = "select", None
        self.shapes, self.undo = [], []
        self.sel, self.uid, self.drag = None, 0, None
        self.color, self.width, self.syncing = COLORS[0], 4, False
        self.entry = self.entry_win = self.entry_pos = None
        self.sel_start = None

        w = self.win = tk.Toplevel(app.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.geometry(f"{self.W}x{self.H}{left:+d}{top:+d}")
        c = self.canvas = tk.Canvas(w, width=self.W, height=self.H, highlightthickness=0,
                                    cursor="crosshair", bg="black")
        c.pack()
        self.bg = ImageTk.PhotoImage(ImageEnhance.Brightness(img).enhance(0.45))
        c.create_image(0, 0, anchor="nw", image=self.bg)
        self.hint = c.create_text(self.W // 2, 40, fill="white", font=("Segoe UI", 14, "bold"),
                                  text="Arraste para selecionar a área  •  Esc cancela")
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        w.bind("<Key>", self.on_key)
        w.bind("<Escape>", self.cancel)
        w.bind("<Return>", lambda e: None if self.typing() else self.finish(True, False))
        w.bind("<Control-s>", lambda e: None if self.typing() else self.finish(False, True))
        w.bind("<Control-z>", lambda e: None if self.typing() else self.do_undo())
        w.bind("<Delete>", lambda e: None if self.typing() else self.delete_sel())
        exclude_from_capture(w)
        w.focus_force()
        c.focus_set()
        if full:
            self.enter_edit((0, 0, self.W, self.H))

    def typing(self):
        return self.entry is not None

    # ---------- fase 1: escolher a área
    def on_press(self, e):
        if self.phase == "edit":
            return self.edit_press(e)
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
        if self.app.use_editor.get():
            self.enter_edit((x1, y1, x2, y2))
        else:
            self.close()
            self.app.finish_snap(self.prev, self.img.crop((x1, y1, x2, y2)), True, False)

    # ---------- fase 2: anotar
    def enter_edit(self, box):
        c = self.canvas
        c.delete("sel")
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
        self.tool = tk.StringVar(value="arrow")
        kw = dict(bg=PANEL, fg="white", activebackground="#3a3d44", activeforeground="white",
                  relief="flat", bd=0, padx=8, pady=4, takefocus=0)
        for name, label, _, tip in TOOLS:
            r = tk.Radiobutton(bar, text=label, variable=self.tool, value=name, indicatoron=0,
                               selectcolor=ACCENT, font=("Segoe UI", 12), **kw)
            r.pack(side="left", padx=1, pady=4)
            Tip(r, tip)
        tk.Frame(bar, bg="#555", width=1).pack(side="left", fill="y", padx=6, pady=6)
        for col in COLORS:
            sw = tk.Button(bar, bg=col, width=2, relief="solid", bd=1, takefocus=0,
                           command=lambda col=col: self.set_color(col))
            sw.pack(side="left", padx=2)
        self.slider = tk.Scale(bar, from_=1, to=16, orient="horizontal", length=80, showvalue=False,
                               bg=PANEL, troughcolor="#444", highlightthickness=0, takefocus=0,
                               command=self.on_width)
        self.syncing = True
        self.slider.set(self.width)
        self.syncing = False
        self.slider.bind("<ButtonPress-1>", lambda e: self.snap() if self.sel else None)
        self.slider.pack(side="left", padx=(8, 4))
        Tip(self.slider, "Espessura")
        tk.Frame(bar, bg="#555", width=1).pack(side="left", fill="y", padx=6, pady=6)
        for text, cmd, tip in (("↶", self.do_undo, "Desfazer (Ctrl+Z)"), ("🗑", self.delete_sel, "Apagar seleção (Del)")):
            b = tk.Button(bar, text=text, command=cmd, font=("Segoe UI", 11), **kw)
            b.pack(side="left", padx=1)
            Tip(b, tip)
        tk.Frame(bar, bg="#555", width=1).pack(side="left", fill="y", padx=6, pady=6)
        for text, cmd, tip, bgc in (("✖", self.cancel, "Cancelar (Esc)", "#5a2a2c"),
                                    ("📋 Copiar", lambda: self.finish(True, False), "Copiar (Enter)", ACCENT),
                                    ("💾 Salvar", lambda: self.finish(False, True), "Salvar (Ctrl+S)", PANEL)):
            k2 = dict(kw)
            k2["bg"] = bgc
            b = tk.Button(bar, text=text, command=cmd, font=("Segoe UI", 10, "bold"), **k2)
            b.pack(side="left", padx=2)
            Tip(b, tip)
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
        if x1 - x0 >= self.W - 2:  # tela inteira: centraliza
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
        for name, _, key, _ in TOOLS:
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
        elif t == "line":
            c.create_line(x0, y0, x1, y1, fill=col, width=w, capstyle="round", tags=tags)
        elif t == "arrow":
            base, pts = arrow_head(s["x0"], s["y0"], s["x1"], s["y1"], s["width"])
            c.create_line(x0, y0, base[0] + ox, base[1] + oy, fill=col, width=w, capstyle="round", tags=tags)
            c.create_polygon([(px + ox, py + oy) for px, py in pts], fill=col, outline=col, tags=tags)
        elif t == "text":
            c.create_text(x0, y0, anchor="nw", text=s["text"], fill=col, tags=tags,
                          font=("Segoe UI", -text_size(s["width"]), "bold"))

    def handle_points(self, s):
        if s["type"] in ("rect", "ellipse"):
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
        if s["type"] == "text":
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
    def finish(self, do_copy, do_save):
        if self.phase != "edit":
            return
        self.commit_text()
        out = render(self.crop, self.shapes)
        self.close()
        self.app.finish_snap(self.prev, out, do_copy, do_save)

    def cancel(self, e=None):
        self.close()
        self.app.cancel_snap(self.prev)

    def close(self):
        with contextlib.suppress(tk.TclError):
            self.win.destroy()


# ======================================================================= app
class App:
    def __init__(self):
        cfg = self.cfg = load_cfg()
        self.root = tk.Tk()
        self.root.title("Print HQ")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        setup_style(self.root)

        self.ui_queue = queue.Queue()
        self.busy = False
        self.rec = Recorder(self)
        self.ffmpeg, self.encoder, self.minibar = None, None, None
        self.ticking = False
        self.hotkeys = {**HK_DEFAULT, **cfg.get("hotkeys", {})}

        V = lambda key, default: tk.StringVar(value=cfg.get(key, default))
        self.folder = V("folder", str(Path.home() / "Pictures" / "Prints"))
        self.fmt = V("fmt", "PNG")
        self.use_editor = tk.BooleanVar(value=cfg.get("editor", True))
        self.delay = tk.IntVar(value=0)
        self.vfolder = V("vfolder", str(Path.home() / "Videos" / "Gravações"))
        self.rec_mon = V("mon", "")
        self.rec_res = V("res", "1080p")
        self.rec_fps = V("fps", "60")
        self.rec_quality = V("quality", QUALITY[0])
        self.rec_mic = V("mic", "Nenhum")
        self.rec_sys = V("sys", "")
        self.status = tk.StringVar(value="Pronto.")
        self.time_var = tk.StringVar(value="00:00:00")
        self.size_var = tk.StringVar(value="0.0 MB")
        self.enc_var = tk.StringVar(value="")
        for v in (self.folder, self.fmt, self.use_editor, self.vfolder, self.rec_mon, self.rec_res,
                  self.rec_fps, self.rec_quality, self.rec_mic, self.rec_sys):
            v.trace_add("write", lambda *a: self.save_cfg())

        nb = ttk.Notebook(self.root)
        nb.pack(padx=10, pady=(10, 4), fill="both")
        f1, f2, f3 = (ttk.Frame(nb, padding=14) for _ in range(3))
        nb.add(f1, text="📷  Print")
        nb.add(f2, text="🎥  Gravação")
        nb.add(f3, text="⌨  Atalhos")
        self.build_print_tab(f1)
        self.build_rec_tab(f2)
        self.build_hotkeys_tab(f3)

        foot = ttk.Frame(self.root, padding=(14, 0, 14, 10))
        foot.pack(fill="x")
        ttk.Label(foot, textvariable=self.status, style="Muted.TLabel", wraplength=380).pack(anchor="w")
        ttk.Button(foot, text=f"🔄  Verificar atualização (v{VERSION})",
                   command=self.check_update).pack(anchor="w", pady=(6, 0))

        self.register_hotkeys()
        self.refresh_ffmpeg_state()
        self.root.after(50, self.pump)
        self.root.after(400, lambda: exclude_from_capture(self.root))

    # ------------------------------------------------------------ abas
    def build_print_tab(self, f):
        f.columnconfigure((0, 1), weight=1)
        ttk.Button(f, text="✂  Capturar área", style="Accent.TButton",
                   command=lambda: self.capture("region")).grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=4)
        ttk.Button(f, text="🖥  Tela inteira",
                   command=lambda: self.capture("full")).grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=4)
        ttk.Checkbutton(f, text="Editar na hora (setas, formas, texto)", variable=self.use_editor
                        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 4))
        ttk.Label(f, text="Formato ao salvar", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Combobox(f, textvariable=self.fmt, state="readonly", values=["PNG", "JPEG (q100)", "WebP (lossless)"]
                     ).grid(row=3, column=0, sticky="ew", padx=(0, 4))
        ttk.Label(f, text="Atraso (segundos)", style="Muted.TLabel").grid(row=2, column=1, sticky="w", pady=(8, 0), padx=(4, 0))
        ttk.Spinbox(f, from_=0, to=30, textvariable=self.delay, width=6).grid(row=3, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(f, text="Pasta dos prints", style="Muted.TLabel").grid(row=4, column=0, sticky="w", pady=(10, 0))
        ttk.Button(f, text="Alterar…", command=lambda: self.pick_folder(self.folder)
                   ).grid(row=5, column=0, sticky="ew", padx=(0, 4))
        ttk.Label(f, textvariable=self.folder, style="Muted.TLabel", wraplength=380
                  ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def build_rec_tab(self, f):
        f.columnconfigure((0, 1), weight=1)
        self.mon_map = {}
        for i, m in enumerate(list_monitors()):
            self.mon_map[f"Monitor {i + 1}  ({m['width']}x{m['height']})"] = (i, m)
        mons = list(self.mon_map) or ["(nenhum monitor encontrado)"]
        if self.rec_mon.get() not in mons:
            self.rec_mon.set(mons[0])

        def combo(label, var, values, r, c, span=1, state="readonly"):
            ttk.Label(f, text=label, style="Muted.TLabel").grid(row=r, column=c, sticky="w", pady=(6, 0))
            cb = ttk.Combobox(f, textvariable=var, values=values, state=state)
            cb.grid(row=r + 1, column=c, columnspan=span, sticky="ew", padx=(0 if c == 0 else 4, 4 if c == 0 and span == 1 else 0))
            return cb

        combo("Tela", self.rec_mon, mons, 0, 0, span=2)
        combo("Resolução", self.rec_res, list(RES_MAP), 2, 0)
        combo("FPS", self.rec_fps, ["24", "30", "60"], 2, 1)
        combo("Qualidade / tamanho do arquivo", self.rec_quality, QUALITY, 4, 0, span=2)

        mics, spks, default, err = list_audio()
        self.audio_err = err
        self.mic_cb = combo("Microfone", self.rec_mic, ["Nenhum"] + mics, 6, 0)
        self.sys_cb = combo("Áudio do computador", self.rec_sys, ["Nenhum"] + spks, 6, 1)
        if self.rec_mic.get() not in ["Nenhum"] + mics:
            self.rec_mic.set("Nenhum")
        if self.rec_sys.get() not in ["Nenhum"] + spks:
            self.rec_sys.set(default or "Nenhum")
        if err:
            self.mic_cb.configure(state="disabled")
            self.sys_cb.configure(state="disabled")
            ttk.Label(f, text="Áudio indisponível. Instale: py -m pip install soundcard numpy",
                      style="Muted.TLabel", wraplength=380).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(f, text="Pasta dos vídeos", style="Muted.TLabel").grid(row=9, column=0, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Alterar…", command=lambda: self.pick_folder(self.vfolder)
                   ).grid(row=10, column=0, sticky="ew", padx=(0, 4))
        ttk.Label(f, textvariable=self.enc_var, style="Muted.TLabel", wraplength=380
                  ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.dl_btn = ttk.Button(f, text="⬇  Baixar FFmpeg (necessário, ~90 MB)", command=self.download_ffmpeg)
        self.dl_btn.grid(row=12, column=0, columnspan=2, sticky="ew", pady=4)

        ttk.Separator(f).grid(row=13, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(f, textvariable=self.time_var, style="Big.TLabel").grid(row=14, column=0, sticky="w")
        ttk.Label(f, textvariable=self.size_var, style="Head.TLabel").grid(row=14, column=1, sticky="e")
        self.rec_btn = ttk.Button(f, text="●  Gravar", style="Rec.TButton", command=self.toggle_record)
        self.rec_btn.grid(row=15, column=0, sticky="ew", padx=(0, 4), pady=(8, 0))
        self.pause_btn = ttk.Button(f, text="⏸  Pausar", command=self.toggle_pause, state="disabled")
        self.pause_btn.grid(row=15, column=1, sticky="ew", padx=(4, 0), pady=(8, 0))

    def build_hotkeys_tab(self, f):
        self.hk_vars = {}
        f.columnconfigure(0, weight=1)
        for i, (k, label) in enumerate(HK_NAMES.items()):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=8)
            v = tk.StringVar(value=self.hotkeys[k])
            self.hk_vars[k] = v
            ttk.Label(f, textvariable=v, style="Key.TLabel").grid(row=i, column=1, padx=10)
            ttk.Button(f, text="Alterar", command=lambda k=k: self.change_hotkey(k)).grid(row=i, column=2)
        ttk.Button(f, text="Restaurar padrão", command=self.reset_hotkeys
                   ).grid(row=len(HK_NAMES), column=0, columnspan=3, sticky="w", pady=(14, 0))
        if keyboard is None:
            ttk.Label(f, text="Instale 'keyboard' para usar atalhos globais.", style="Muted.TLabel"
                      ).grid(row=len(HK_NAMES) + 1, column=0, columnspan=3, sticky="w", pady=6)

    # ------------------------------------------------------------ geral
    def save_cfg(self):
        try:
            data = {"folder": self.folder.get(), "fmt": self.fmt.get(), "editor": self.use_editor.get(),
                    "vfolder": self.vfolder.get(), "mon": self.rec_mon.get(), "res": self.rec_res.get(),
                    "fps": self.rec_fps.get(), "quality": self.rec_quality.get(), "mic": self.rec_mic.get(),
                    "sys": self.rec_sys.get(), "hotkeys": self.hotkeys,
                    "audio_offset_ms": self.cfg.get("audio_offset_ms", 0)}
            CONFIG.write_text(json.dumps(data), encoding="utf-8")
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
        if k in ("region", "full"):
            self.capture(k)
        elif k == "rec":
            self.toggle_record()
        elif k == "pause":
            self.toggle_pause()

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
            SnapOverlay(self, img, left, top, prev, full=(mode == "full"))
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
            self.restore(prev)

    def cancel_snap(self, prev):
        self.status.set("Captura cancelada.")
        self.restore(prev)

    def finish_snap(self, prev, img, do_copy, do_save):
        self.restore(prev)
        self.deliver(img, do_copy, do_save)

    def deliver(self, img, do_copy, do_save):
        parts, path = [], None
        try:
            if do_copy:
                parts.append("copiado" if copy_to_clipboard(img) else "NÃO copiado (instale pywin32)")
            if do_save:
                path = save_image(img, Path(self.folder.get()), self.fmt.get())
                parts.append(f"salvo ({path.name})")
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
            return
        text = "Print " + " e ".join(parts)
        self.status.set(f"{text} - {img.width}x{img.height}")
        if path:
            buttons = [("📂 Abrir pasta", lambda: self.open_folder(path))]
        else:
            buttons = [("💾 Salvar também", lambda: self.deliver(img, False, True))]
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
        enc = detect_encoder(self.ffmpeg)
        self.ui_queue.put(lambda: self.set_encoder(enc))

    def set_encoder(self, enc):
        self.encoder = enc
        self.enc_var.set(f"Codificador: {ENC_LABEL[enc]}")

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

    # ------------------------------------------------------------ gravação
    def update_rec_ui(self):
        st = self.rec.state
        self.rec_btn.configure(text="■  Parar" if st in ("recording", "paused") else "●  Gravar",
                               state="disabled" if st == "finalizing" else "normal")
        self.pause_btn.configure(text="▶  Retomar" if st == "paused" else "⏸  Pausar",
                                 state="normal" if st in ("recording", "paused") else "disabled")
        if self.minibar:
            self.mb_pause.configure(text="▶" if st == "paused" else "⏸")

    def toggle_record(self):
        if self.rec.state == "idle":
            self.start_record()
        elif self.rec.state in ("recording", "paused"):
            self.stop_record()

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
        if not self.ffmpeg:
            self.status.set("Baixe o FFmpeg na aba Gravação para poder gravar.")
            return
        idx, mon = self.mon_map.get(self.rec_mon.get(), (0, None))
        if mon is None:
            self.status.set("Nenhum monitor encontrado.")
            return
        use_audio = not self.audio_err
        mic = self.rec_mic.get() if use_audio and self.rec_mic.get() != "Nenhum" else ""
        spk = self.rec_sys.get() if use_audio and self.rec_sys.get() not in ("", "Nenhum") else ""
        opts = {"ffmpeg": self.ffmpeg, "mon": mon, "out_idx": idx, "fps": int(self.rec_fps.get()),
                "height": RES_MAP.get(self.rec_res.get(), 0), "quality": QUALITY.index(self.rec_quality.get()),
                "encoder": self.encoder or "libx264", "mic": mic, "spk": spk,
                "offset_ms": int(self.cfg.get("audio_offset_ms", 0))}
        try:
            Path(self.vfolder.get()).mkdir(parents=True, exist_ok=True)
            self.rec.start(opts)
        except Exception as ex:
            self.status.set(f"Não consegui iniciar a gravação: {ex}")
            self.rec.state = "idle"
            return
        self.status.set("Gravando…  (use o atalho ou a barrinha no topo da tela)")
        self.update_rec_ui()
        self.show_minibar()
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
                self.ui_queue.put(lambda: self.rec_done(out, dur, None))
            except Exception as ex:
                self.ui_queue.put(lambda ex=ex: self.rec_done(None, dur, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def rec_done(self, out, dur, err):
        self.rec.state = "idle"
        self.close_minibar()
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.update_rec_ui()
        if err:
            self.status.set("Erro ao gravar.")
            messagebox.showerror("Print HQ", err[-700:])
            return
        size = fmt_size(out.stat().st_size)
        self.status.set(f"Vídeo salvo: {out.name} ({fmt_time(dur)}, {size})")
        show_toast(self.root, None, f"Gravação salva\n{fmt_time(dur)}  •  {size}",
                   [("▶ Abrir", lambda: os.startfile(str(out))),
                    ("📂 Pasta", lambda: self.open_folder(out))], seconds=9)

    def rec_failed(self, err):
        if self.rec.state in ("idle", "finalizing"):
            return
        self.rec._kill()
        self.rec.state = "idle"
        self.close_minibar()
        self.root.deiconify()
        self.update_rec_ui()
        self.status.set("A gravação falhou.")
        messagebox.showerror("Print HQ", "O FFmpeg não conseguiu gravar.\n\n" + (err or "sem detalhes"))

    def tick(self):
        r = self.rec
        if r.state in ("idle", "finalizing"):
            self.ticking = False
            return
        if r.state == "recording" and r.proc is not None and r.proc.poll() is not None \
                and time.time() - r.seg_t0 > 3:
            self.ticking = False
            self.rec_failed(r.tail_logs())
            return
        t, s = fmt_time(r.elapsed()), fmt_size(r.size())
        self.time_var.set(t)
        self.size_var.set(s)
        if self.minibar:
            self.mb_time.set(f"{t}   {s}")
            self.mb_dot.configure(fg=RED if (r.state == "recording" and int(time.time()) % 2 == 0) else "#7a2a2c")
        self.root.after(500, self.tick)

    def show_minibar(self):
        mb = self.minibar = tk.Toplevel(self.root)
        mb.overrideredirect(True)
        mb.attributes("-topmost", True)
        mb.configure(bg=PANEL, highlightbackground="#555", highlightthickness=1)
        self.mb_time = tk.StringVar(value="00:00:00   0.0 MB")
        self.mb_dot = tk.Label(mb, text="●", fg=RED, bg=PANEL, font=("Segoe UI", 12))
        self.mb_dot.pack(side="left", padx=(10, 4))
        tk.Label(mb, textvariable=self.mb_time, fg="white", bg=PANEL,
                 font=("Consolas", 11, "bold")).pack(side="left", padx=6)
        kw = dict(bg=PANEL, fg="white", activebackground="#3a3d44", activeforeground="white",
                  relief="flat", bd=0, padx=8, pady=3, font=("Segoe UI", 11), takefocus=0)
        self.mb_pause = tk.Button(mb, text="⏸", command=self.toggle_pause, **kw)
        self.mb_pause.pack(side="left", pady=4)
        tk.Button(mb, text="■", command=self.toggle_record, **kw).pack(side="left", padx=(0, 6), pady=4)
        mb.update_idletasks()
        mb.geometry(f"+{(self.root.winfo_screenwidth() - mb.winfo_reqwidth()) // 2}+8")
        exclude_from_capture(mb)

    def close_minibar(self):
        if self.minibar:
            with contextlib.suppress(tk.TclError):
                self.minibar.destroy()
            self.minibar = None

    # ------------------------------------------------------------ atualização
    def check_update(self):
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
            self.root.destroy()
            sys.exit(0)
        except SystemExit:
            raise
        except Exception as ex:
            self.status.set(f"Não consegui atualizar: {ex}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
