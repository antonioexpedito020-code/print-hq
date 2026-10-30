"""
Print HQ v1.2 - captura de tela em alta qualidade com editor de anotações.

Instalação (uma vez):
    py -m pip install mss pillow keyboard pywin32

Atalhos globais:
    Ctrl+Alt+A  -> selecionar área
    Ctrl+Alt+S  -> tela inteira

No editor:
    Enter = copiar | Ctrl+S = salvar | Esc = descartar | Ctrl+Z = desfazer | Del = apagar
    V Selecionar | A Seta | R Retângulo | O Elipse | L Linha | T Texto
"""
import copy
import ctypes
import io
import json
import math
import queue
import re
import subprocess
import sys
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

import mss
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageTk

# IMPORTANTE: precisa vir antes de criar a janela Tk.
# Sem isso, telas com escala (125%, 150%...) geram prints borrados ou cortados.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

try:
    import keyboard  # atalhos globais (opcional)
except Exception:
    keyboard = None

try:
    import win32clipboard  # área de transferência (opcional, mas recomendado)
except Exception:
    win32clipboard = None

VERSION = "1.2.0"
UPDATE_URL = "https://raw.githubusercontent.com/antonioexpedito020-code/print-hq/main/print_hq.pyw"
CONFIG = Path.home() / ".print_hq.json"


# ----------------------------------------------------------------- utilidades
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


def grab_screen():
    """Captura todos os monitores em resolução nativa."""
    with mss.mss() as sct:
        mon = sct.monitors[0]  # 0 = área virtual com todos os monitores
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    return img, mon["left"], mon["top"]


def save_image(img, folder, fmt):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("print_%Y-%m-%d_%H-%M-%S")
    if fmt == "PNG":
        path = folder / f"{stamp}.png"
        img.save(path, "PNG", optimize=True)
    elif fmt == "JPEG (q100)":
        path = folder / f"{stamp}.jpg"
        img.save(path, "JPEG", quality=100, subsampling=0)
    else:  # WebP lossless
        path = folder / f"{stamp}.webp"
        img.save(path, "WEBP", lossless=True, quality=100)
    return path


def copy_to_clipboard(img):
    if win32clipboard is None:
        return False
    rgb = img.convert("RGB")
    bmp = io.BytesIO()
    rgb.save(bmp, "BMP")
    dib = bmp.getvalue()[14:]  # remove o cabeçalho do arquivo BMP
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


# ------------------------------------------------- desenho das anotações (PIL)
def arrow_head(x0, y0, x1, y1, width):
    """Retorna (base da cabeça, triângulo da cabeça) em coordenadas da imagem."""
    ang = math.atan2(y1 - y0, x1 - x0)
    length = math.hypot(x1 - x0, y1 - y0)
    hl = min(max(14, width * 4.5), length * 0.6)
    hw = hl * 0.45
    bx, by = x1 - hl * math.cos(ang), y1 - hl * math.sin(ang)
    px, py = -math.sin(ang), math.cos(ang)
    pts = [(x1, y1), (bx + px * hw, by + py * hw), (bx - px * hw, by - py * hw)]
    return (bx, by), pts


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
    """Aplica as anotações na imagem em resolução total (com suavização)."""
    if not shapes:
        return img
    w, h = img.size
    k = 2 if w * h <= 12_000_000 else 1  # supersampling p/ bordas suaves
    over = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    for s in shapes:
        col, t = s["color"], s["type"]
        wd = max(1, int(round(s["width"] * k)))
        x0, y0, x1, y1 = (s[n] * k for n in ("x0", "y0", "x1", "y1"))
        if t in ("rect", "ellipse"):
            p = wd / 2
            box = [min(x0, x1) - p, min(y0, y1) - p, max(x0, x1) + p, max(y0, y1) + p]
            if t == "rect":
                d.rectangle(box, outline=col, width=wd)
            else:
                d.ellipse(box, outline=col, width=wd)
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


# ------------------------------------------------------------ notificação
def show_toast(root, img, text, buttons=(), seconds=5):
    """Pequena notificação no canto da tela, com miniatura e botões."""
    bg = "#1f1f1f"
    t = tk.Toplevel(root)
    t.overrideredirect(True)
    t.attributes("-topmost", True)
    t.configure(bg=bg, highlightbackground="#666", highlightthickness=1)

    thumb = img.copy()
    thumb.thumbnail((180, 110))
    ph = ImageTk.PhotoImage(thumb)
    lbl = tk.Label(t, image=ph, bg=bg)
    lbl.image = ph
    lbl.grid(row=0, column=0, padx=10, pady=10)

    right = tk.Frame(t, bg=bg)
    right.grid(row=0, column=1, padx=(0, 12), pady=10, sticky="n")
    tk.Label(right, text=text, fg="white", bg=bg, font=("Segoe UI", 10, "bold"),
             wraplength=240, justify="left").pack(anchor="w")
    bf = tk.Frame(right, bg=bg)
    bf.pack(anchor="w", pady=(8, 0))
    for label, cb in buttons:
        tk.Button(bf, text=label, command=lambda cb=cb: (t.destroy(), cb())
                  ).pack(side="left", padx=(0, 6))
    tk.Button(bf, text="✕", command=t.destroy).pack(side="left")

    t.update_idletasks()
    x = root.winfo_screenwidth() - t.winfo_reqwidth() - 20
    y = root.winfo_screenheight() - t.winfo_reqheight() - 70
    t.geometry(f"+{x}+{y}")

    def close():
        try:
            t.destroy()
        except tk.TclError:
            pass
    t.after(int(seconds * 1000), close)


# -------------------------------------------------------------- editor
TOOLS = [("select", "↖ Selecionar", "v"), ("arrow", "➚ Seta", "a"), ("rect", "▭ Retângulo", "r"),
         ("ellipse", "◯ Elipse", "o"), ("line", "╱ Linha", "l"), ("text", "T Texto", "t")]
COLORS = ["#ff3b30", "#ffcc00", "#34c759", "#0a84ff", "#000000", "#ffffff"]


class Editor:
    def __init__(self, app, img, prev_state):
        self.app, self.img, self.prev = app, img, prev_state
        self.shapes, self.undo = [], []
        self.sel, self.uid, self.drag = None, 0, None
        self.color, self.width, self.syncing = COLORS[0], 4, False

        top = self.top = tk.Toplevel(app.root)
        top.title("Print HQ - Editar print")
        top.protocol("WM_DELETE_WINDOW", self.discard)
        iw, ih = img.size
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        self.scale = min(1.0, sw * 0.9 / iw, sh * 0.72 / ih)
        cw, ch = max(1, int(iw * self.scale)), max(1, int(ih * self.scale))
        shown = img.resize((cw, ch), Image.LANCZOS) if self.scale < 1 else img
        self.photo = ImageTk.PhotoImage(shown)

        # barra 1: ferramentas
        bar1 = tk.Frame(top)
        bar1.pack(fill="x", padx=6, pady=(6, 2))
        self.tool = tk.StringVar(value="arrow")
        for name, label, key in TOOLS:
            tk.Radiobutton(bar1, text=f"{label} ({key.upper()})", variable=self.tool, value=name,
                           indicatoron=0, padx=8, pady=3, selectcolor="#cfe3ff"
                           ).pack(side="left", padx=2)

        # barra 2: estilo
        bar2 = tk.Frame(top)
        bar2.pack(fill="x", padx=6, pady=2)
        for c in COLORS:
            tk.Button(bar2, bg=c, width=2, relief="solid", command=lambda c=c: self.set_color(c)
                      ).pack(side="left", padx=2)
        ttk.Button(bar2, text="Mais cores…", command=self.pick_color).pack(side="left", padx=6)
        tk.Label(bar2, text="Espessura:").pack(side="left", padx=(10, 2))
        self.slider = tk.Scale(bar2, from_=1, to=20, orient="horizontal", length=140,
                               showvalue=True, command=self.on_width)
        self.slider.set(self.width)
        self.slider.bind("<ButtonPress-1>", lambda e: self.snap() if self.sel else None)
        self.slider.pack(side="left")
        ttk.Button(bar2, text="↶ Desfazer", command=self.do_undo).pack(side="left", padx=(12, 2))
        ttk.Button(bar2, text="🗑 Apagar seleção", command=self.delete_sel).pack(side="left", padx=2)

        # área da imagem
        self.canvas = tk.Canvas(top, width=cw, height=ch, highlightthickness=0,
                                cursor="crosshair", bg="#222")
        self.canvas.pack(padx=6, pady=4)
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo, tags="bg")
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        # barra de baixo: ações finais
        bar3 = tk.Frame(top)
        bar3.pack(fill="x", padx=6, pady=(2, 8))
        tk.Label(bar3, text=f"{iw}x{ih}px", fg="gray").pack(side="left")
        ttk.Button(bar3, text="🗑 Descartar (Esc)", command=self.discard).pack(side="right", padx=2)
        ttk.Button(bar3, text="Copiar e salvar", command=lambda: self.done(True, True)
                   ).pack(side="right", padx=2)
        ttk.Button(bar3, text="💾 Salvar (Ctrl+S)", command=lambda: self.done(False, True)
                   ).pack(side="right", padx=2)
        ttk.Button(bar3, text="📋 Copiar (Enter)", command=lambda: self.done(True, False)
                   ).pack(side="right", padx=2)

        top.bind("<Key>", self.on_key)
        top.bind("<Return>", lambda e: self.done(True, False))
        top.bind("<Control-s>", lambda e: self.done(False, True))
        top.bind("<Control-c>", lambda e: self.done(True, False))
        top.bind("<Control-z>", self.do_undo)
        top.bind("<Delete>", self.delete_sel)
        top.bind("<BackSpace>", self.delete_sel)
        top.bind("<Escape>", self.discard)

        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        top.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        top.attributes("-topmost", True)
        top.after(400, lambda: top.attributes("-topmost", False))
        top.after(50, top.focus_force)

    # ---- estado
    def snap(self):
        self.undo.append(copy.deepcopy(self.shapes))
        self.undo = self.undo[-50:]

    def do_undo(self, e=None):
        if self.undo:
            self.shapes = self.undo.pop()
            self.sel = None
            self.redraw()

    def get(self, uid):
        return next((s for s in self.shapes if s["uid"] == uid), None) if uid else None

    def new_shape(self, kind, x, y):
        self.uid += 1
        return {"uid": self.uid, "type": kind, "x0": x, "y0": y, "x1": x, "y1": y,
                "color": self.color, "width": self.width}

    def clamp(self, x, y):
        return (min(max(x, 0), self.img.width), min(max(y, 0), self.img.height))

    def on_key(self, e):
        for name, _, key in TOOLS:
            if e.char and e.char.lower() == key:
                self.tool.set(name)

    def delete_sel(self, e=None):
        s = self.get(self.sel)
        if s:
            self.snap()
            self.shapes.remove(s)
            self.sel = None
            self.redraw()

    # ---- estilo
    def set_color(self, col):
        self.color = col
        s = self.get(self.sel)
        if s:
            self.snap()
            s["color"] = col
            self.redraw()

    def pick_color(self):
        c = colorchooser.askcolor(color=self.color, parent=self.top)[1]
        if c:
            self.set_color(c)

    def on_width(self, v):
        if self.syncing:
            return
        self.width = int(float(v))
        s = self.get(self.sel)
        if s and s["width"] != self.width:
            s["width"] = self.width
            self.redraw()

    # ---- desenho na tela
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
        k, c = self.scale, self.canvas
        x0, y0, x1, y1 = (s[n] * k for n in ("x0", "y0", "x1", "y1"))
        w, col, t = max(1, s["width"] * k), s["color"], s["type"]
        tags = ("shape", f"s{s['uid']}")
        if t == "rect":
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=w, tags=tags)
        elif t == "ellipse":
            c.create_oval(x0, y0, x1, y1, outline=col, width=w, tags=tags)
        elif t == "line":
            c.create_line(x0, y0, x1, y1, fill=col, width=w, capstyle="round", tags=tags)
        elif t == "arrow":
            base, pts = arrow_head(s["x0"], s["y0"], s["x1"], s["y1"], s["width"])
            c.create_line(x0, y0, base[0] * k, base[1] * k, fill=col, width=w,
                          capstyle="round", tags=tags)
            c.create_polygon([(px * k, py * k) for px, py in pts], fill=col, outline=col, tags=tags)
        elif t == "text":
            c.create_text(x0, y0, anchor="nw", text=s["text"], fill=col, tags=tags,
                          font=("Segoe UI", -max(6, int(text_size(s["width"]) * k)), "bold"))

    def handle_points(self, s):
        if s["type"] in ("rect", "ellipse"):
            return [(s["x0"], s["y0"]), (s["x1"], s["y1"]), (s["x0"], s["y1"]), (s["x1"], s["y0"])]
        if s["type"] in ("line", "arrow"):
            return [(s["x0"], s["y0"]), (s["x1"], s["y1"])]
        return []

    def draw_handles(self, s):
        k, c = self.scale, self.canvas
        for i, (x, y) in enumerate(self.handle_points(s)):
            c.create_rectangle(x * k - 5, y * k - 5, x * k + 5, y * k + 5, fill="white",
                               outline="#0a84ff", width=2, tags=("handle", f"h{i}"))
        if s["type"] == "text":
            bb = c.bbox(f"s{s['uid']}")
            if bb:
                c.create_rectangle(bb[0] - 3, bb[1] - 3, bb[2] + 3, bb[3] + 3, outline="#0a84ff",
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

    # ---- mouse
    def on_press(self, e):
        k = self.scale
        ix, iy = self.clamp(e.x / k, e.y / k)
        tool = self.tool.get()
        self.drag = None

        h = self.hit_handle(e.x, e.y) if self.sel else None
        if h is not None:  # arrastar um ponto de redimensionamento (vale p/ qualquer ferramenta)
            self.drag = {"mode": "resize", "h": h, "pushed": False}
            return

        if tool == "select":
            uid = self.hit_shape(e.x, e.y)
            self.sel = uid
            if uid:
                s = self.get(uid)
                self.syncing = True
                self.slider.set(s["width"])
                self.syncing = False
                self.width = s["width"]
                self.drag = {"mode": "move", "last": (e.x / k, e.y / k), "pushed": False}
            self.redraw()
        elif tool == "text":
            txt = simpledialog.askstring("Texto", "Digite o texto:", parent=self.top)
            if txt:
                self.snap()
                s = self.new_shape("text", ix, iy)
                s["text"] = txt
                self.shapes.append(s)
                self.sel = s["uid"]
                self.redraw()
        else:
            self.snap()
            s = self.new_shape(tool, ix, iy)
            self.shapes.append(s)
            self.sel = s["uid"]
            self.drag = {"mode": "draw"}
            self.redraw()

    def on_drag(self, e):
        d, s, k = self.drag, self.get(self.sel), self.scale
        if not d or not s:
            return
        ix, iy = self.clamp(e.x / k, e.y / k)
        if d["mode"] == "draw":
            s["x1"], s["y1"] = ix, iy
        elif d["mode"] == "resize":
            if not d["pushed"]:
                self.snap()
                d["pushed"] = True
            fx, fy = [("x0", "y0"), ("x1", "y1"), ("x0", "y1"), ("x1", "y0")][d["h"]]
            s[fx], s[fy] = ix, iy
        elif d["mode"] == "move":
            if not d["pushed"]:
                self.snap()
                d["pushed"] = True
            rx, ry = e.x / k, e.y / k
            dx, dy = rx - d["last"][0], ry - d["last"][1]
            for a in ("x0", "x1"):
                s[a] += dx
            for b in ("y0", "y1"):
                s[b] += dy
            d["last"] = (rx, ry)
        self.redraw()

    def on_release(self, e):
        d, self.drag = self.drag, None
        if d and d["mode"] == "draw":
            s = self.get(self.sel)
            if s and math.hypot(s["x1"] - s["x0"], s["y1"] - s["y0"]) < 4:  # clique sem arrastar
                self.shapes.remove(s)
                self.sel = None
                if self.undo:
                    self.undo.pop()
            self.redraw()

    # ---- finalizar
    def done(self, do_copy, do_save):
        out = render(self.img, self.shapes)
        self.close()
        self.app.deliver(out, do_copy, do_save)

    def discard(self, e=None):
        if self.shapes and not messagebox.askyesno(
                "Print HQ", "Descartar este print e as anotações?", parent=self.top):
            return
        self.close()
        self.app.status.set("Print descartado.")

    def close(self):
        self.top.destroy()
        self.app.restore(self.prev)


# ---------------------------------------------------------------- app
class App:
    def __init__(self):
        cfg = load_cfg()
        self.root = tk.Tk()
        self.root.title("Print HQ")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.folder = tk.StringVar(value=cfg.get("folder", str(Path.home() / "Pictures" / "Prints")))
        self.fmt = tk.StringVar(value=cfg.get("fmt", "PNG"))
        self.use_editor = tk.BooleanVar(value=cfg.get("editor", True))
        self.delay = tk.IntVar(value=0)
        self.status = tk.StringVar(value="Pronto.")
        self.hotkey_queue = queue.Queue()
        self.busy = False
        for v in (self.folder, self.fmt, self.use_editor):
            v.trace_add("write", lambda *a: self.save_cfg())

        pad = {"padx": 10, "pady": 5}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack()

        ttk.Button(frm, text="📷  Tela inteira", command=lambda: self.capture("full")).grid(
            row=0, column=0, sticky="ew", **pad)
        ttk.Button(frm, text="✂️  Selecionar área", command=lambda: self.capture("region")).grid(
            row=0, column=1, sticky="ew", **pad)

        ttk.Checkbutton(frm, text="Abrir editor (setas, formas, texto) após capturar",
                        variable=self.use_editor).grid(row=1, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(frm, text="Formato ao salvar:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.fmt, state="readonly",
                     values=["PNG", "JPEG (q100)", "WebP (lossless)"], width=18
                     ).grid(row=2, column=1, sticky="ew", **pad)

        ttk.Label(frm, text="Atraso (segundos):").grid(row=3, column=0, sticky="w", **pad)
        ttk.Spinbox(frm, from_=0, to=30, textvariable=self.delay, width=6
                    ).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(frm, text="Pasta para salvar:").grid(row=4, column=0, sticky="w", **pad)
        ttk.Button(frm, text="Alterar pasta...", command=self.pick_folder).grid(
            row=4, column=1, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.folder, foreground="gray", wraplength=320
                  ).grid(row=5, column=0, columnspan=2, sticky="w", padx=10)

        ttk.Button(frm, text=f"🔄  Verificar atualização (v{VERSION})", command=self.check_update
                   ).grid(row=6, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.status, wraplength=320
                  ).grid(row=7, column=0, columnspan=2, sticky="w", **pad)

        if keyboard:
            try:
                keyboard.add_hotkey("ctrl+alt+s", lambda: self.hotkey_queue.put("full"))
                keyboard.add_hotkey("ctrl+alt+a", lambda: self.hotkey_queue.put("region"))
                self.status.set("Pronto. Atalhos: Ctrl+Alt+A (área) / Ctrl+Alt+S (tela inteira)")
            except Exception:
                pass
        self.root.after(100, self.poll_hotkeys)

    def save_cfg(self):
        try:
            CONFIG.write_text(json.dumps({"folder": self.folder.get(), "fmt": self.fmt.get(),
                                          "editor": self.use_editor.get()}), encoding="utf-8")
        except Exception:
            pass

    def poll_hotkeys(self):
        try:
            while True:
                self.capture(self.hotkey_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll_hotkeys)

    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder.get())
        if d:
            self.folder.set(d)

    def restore(self, prev):
        """Devolve a janela principal ao estado em que estava antes da captura."""
        self.busy = False
        if prev == "iconic":
            self.root.iconify()
        elif prev == "normal":
            self.root.deiconify()
            self.root.attributes("-topmost", True)

    def select_region(self, img, left, top):
        """Mostra a tela congelada escurecida e deixa arrastar para escolher a área."""
        w, h = img.size
        sel = tk.Toplevel(self.root)
        sel.overrideredirect(True)
        sel.attributes("-topmost", True)
        sel.geometry(f"{w}x{h}{left:+d}{top:+d}")

        bg = ImageTk.PhotoImage(ImageEnhance.Brightness(img).enhance(0.45))
        canvas = tk.Canvas(sel, width=w, height=h, highlightthickness=0, cursor="crosshair")
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=bg)
        canvas.image = bg  # evita o garbage collector
        canvas.create_text(w // 2, 40, fill="white", font=("Segoe UI", 14, "bold"),
                           text="Arraste para selecionar a área  •  Esc para cancelar")

        st = {"x0": 0, "y0": 0, "rect": None, "label": None, "result": None}

        def press(e):
            st["x0"], st["y0"] = e.x, e.y
            st["rect"] = canvas.create_rectangle(e.x, e.y, e.x, e.y, outline="#00aaff", width=2)
            st["label"] = canvas.create_text(e.x + 8, e.y - 12, anchor="w",
                                             fill="white", font=("Segoe UI", 10, "bold"))

        def drag(e):
            canvas.coords(st["rect"], st["x0"], st["y0"], e.x, e.y)
            canvas.coords(st["label"], max(e.x, st["x0"]) + 8, min(e.y, st["y0"]) - 12)
            canvas.itemconfig(st["label"],
                              text=f"{abs(e.x - st['x0'])} x {abs(e.y - st['y0'])}")

        def release(e):
            x1, x2 = sorted((st["x0"], e.x))
            y1, y2 = sorted((st["y0"], e.y))
            if x2 - x1 > 4 and y2 - y1 > 4:
                st["result"] = (x1, y1, x2, y2)
            sel.destroy()

        canvas.bind("<ButtonPress-1>", press)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)
        sel.bind("<Escape>", lambda e: sel.destroy())
        sel.focus_force()
        sel.grab_set()
        self.root.wait_window(sel)
        return st["result"]

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
        time.sleep(0.4 + delay)  # tempo para a janela sumir + atraso escolhido
        try:
            img, left, top = grab_screen()
            if mode == "region":
                box = self.select_region(img, left, top)
                if box is None:
                    self.status.set("Captura cancelada.")
                    self.restore(prev)
                    return
                img = img.crop(box)
            if self.use_editor.get():
                Editor(self, img, prev)  # o editor chama restore() ao fechar
            else:
                self.restore(prev)
                self.deliver(img, True, False)
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
            self.restore(prev)

    def open_editor(self, img):
        if self.busy:
            return
        self.busy = True
        try:
            Editor(self, img, self.root.state())
        except Exception as ex:
            self.busy = False
            self.status.set(f"Erro: {ex}")

    def deliver(self, img, do_copy, do_save):
        """Copia e/ou salva o print e mostra a notificação."""
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

        buttons = []
        if path:
            buttons.append(("📂 Abrir pasta", lambda: self.open_folder(path)))
        else:
            buttons.append(("💾 Salvar", lambda: self.deliver(img, False, True)))
            if not self.use_editor.get():
                buttons.append(("✏️ Editar", lambda: self.open_editor(img)))
        show_toast(self.root, img, text, buttons)

    def open_folder(self, path):
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception:
            pass

    def check_update(self):
        try:
            code = fetch_update()
            m = re.search(r'^VERSION\s*=\s*"([\d.]+)"', code, re.M)
            if not m:
                raise ValueError("arquivo remoto sem VERSION")
            compile(code, "update", "exec")  # garante que não está com erro de sintaxe
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
