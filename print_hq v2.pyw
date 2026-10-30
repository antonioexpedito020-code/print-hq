"""
Print HQ - captura de tela em alta qualidade (resolução nativa, sem perda).

Instalação (uma vez):
    pip install mss pillow keyboard pywin32

Uso:
    python print_hq.pyw        (ou dê dois cliques no arquivo)

Atalhos globais:
    Ctrl+Alt+S  -> tela inteira
    Ctrl+Alt+A  -> selecionar área
"""
import ctypes
import io
import queue
import re
import subprocess
import sys
import time
import tkinter as tk
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import mss
from PIL import Image, ImageEnhance, ImageTk

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
    import win32clipboard  # copiar para a área de transferência (opcional)
except Exception:
    win32clipboard = None


VERSION = "1.1.0"
# Troque SEU_USUARIO pelo seu nome de usuário do GitHub:
UPDATE_URL = "https://raw.githubusercontent.com/SEU_USUARIO/print-hq/main/print_hq.pyw"


def version_tuple(text):
    return tuple(int(x) for x in text.split("."))


def fetch_update():
    req = urllib.request.Request(UPDATE_URL, headers={"User-Agent": "PrintHQ"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8")


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
    out = io.BytesIO()
    img.convert("RGB").save(out, "BMP")
    data = out.getvalue()[14:]  # remove o cabeçalho do arquivo BMP
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()
    return True


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Print HQ")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.folder = tk.StringVar(value=str(Path.home() / "Pictures" / "Prints"))
        self.fmt = tk.StringVar(value="PNG")
        self.delay = tk.IntVar(value=0)
        self.clip = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Pronto.")
        self.hotkey_queue = queue.Queue()

        pad = {"padx": 10, "pady": 5}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack()

        ttk.Button(frm, text="📷  Tela inteira", command=lambda: self.capture("full")).grid(
            row=0, column=0, sticky="ew", **pad)
        ttk.Button(frm, text="✂️  Selecionar área", command=lambda: self.capture("region")).grid(
            row=0, column=1, sticky="ew", **pad)

        ttk.Label(frm, text="Formato:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.fmt, state="readonly",
                     values=["PNG", "JPEG (q100)", "WebP (lossless)"], width=18
                     ).grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(frm, text="Atraso (segundos):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Spinbox(frm, from_=0, to=30, textvariable=self.delay, width=6
                    ).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(frm, text="Salvar em:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Button(frm, text="Alterar pasta...", command=self.pick_folder).grid(
            row=3, column=1, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.folder, foreground="gray", wraplength=300
                  ).grid(row=4, column=0, columnspan=2, sticky="w", padx=10)

        ttk.Checkbutton(frm, text="Copiar também para a área de transferência",
                        variable=self.clip).grid(row=5, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(frm, text=f"🔄  Verificar atualização (v{VERSION})", command=self.check_update
                   ).grid(row=6, column=0, columnspan=2, sticky="ew", **pad)
        ttk.Label(frm, textvariable=self.status, wraplength=300
                  ).grid(row=7, column=0, columnspan=2, sticky="w", **pad)

        if keyboard:
            try:
                keyboard.add_hotkey("ctrl+alt+s", lambda: self.hotkey_queue.put("full"))
                keyboard.add_hotkey("ctrl+alt+a", lambda: self.hotkey_queue.put("region"))
                self.status.set("Pronto. Atalhos: Ctrl+Alt+S (tela) / Ctrl+Alt+A (área)")
            except Exception:
                pass
        self.root.after(100, self.poll_hotkeys)

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
        try:
            delay = max(0, int(self.delay.get()))
        except Exception:
            delay = 0

        self.root.withdraw()
        self.root.update()
        time.sleep(0.4 + delay)  # tempo para a janela sumir + atraso escolhido

        try:
            img, left, top = grab_screen()
            if mode == "region":
                box = self.select_region(img, left, top)
                if box is None:
                    self.status.set("Seleção cancelada.")
                    return
                img = img.crop(box)

            path = save_image(img, Path(self.folder.get()), self.fmt.get())
            msg = f"Salvo: {path.name}  ({img.width}x{img.height})"
            if self.clip.get():
                msg += "  | copiado" if copy_to_clipboard(img) else "  | (instale pywin32 p/ copiar)"
            self.status.set(msg)
        except Exception as ex:
            self.status.set(f"Erro: {ex}")
        finally:
            self.root.deiconify()
            self.root.attributes("-topmost", True)

    def check_update(self):
        if "antonioexpedito020-code" in UPDATE_URL:
            messagebox.showinfo("Print HQ", "Configure o UPDATE_URL no início do arquivo primeiro.")
            return
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
