"""Editor de capturas: operações destrutivas com histórico e pré-visualização."""
import ctypes
import io
import math
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from PIL import Image, ImageDraw, ImageFilter, ImageTk

BG = '#141922'
PANEL = '#222a36'
FG = '#f3f6fb'
ACCENT = '#5b9cff'


def copy_image_windows(image):
    """Copia bitmap para a área de transferência usando CF_DIB."""
    if not hasattr(ctypes, 'windll'):  # pragma: no cover - Windows only
        raise RuntimeError('Disponível apenas no Windows')
    output = io.BytesIO()
    image.convert('RGB').save(output, 'BMP')
    data = output.getvalue()[14:]
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    GMEM_MOVEABLE = 0x0002
    CF_DIB = 8
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError('Falha ao alocar memória para clipboard')
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        raise OSError('Falha ao bloquear memória')
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise OSError('Clipboard ocupado por outro aplicativo')
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(handle)
            raise OSError('Falha ao copiar imagem')
    finally:
        user32.CloseClipboard()


class ImageEditor(tk.Toplevel):
    def __init__(self, master, image, default_path, on_saved=None):
        super().__init__(master)
        self.title('Print HQ — Editor de captura')
        self.geometry('1100x760')
        self.minsize(750, 520)
        self.configure(bg=BG)
        self.image = image.convert('RGB').copy()
        self.history = []
        self.future = []
        self.default_path = default_path
        self.on_saved = on_saved
        self.tool = tk.StringVar(value='seta')
        self.color = '#ff5252'
        self.width = tk.IntVar(value=4)
        self.start = None
        self.last = None
        self.preview = None
        self.scale = 1.0
        self.photo = None
        self._ui()
        self.after(100, self.render)
        self.bind('<Control-z>', lambda e: self.undo())
        self.bind('<Control-y>', lambda e: self.redo())
        self.bind('<Control-s>', lambda e: self.save())
        self.bind('<Control-c>', lambda e: self.copy())

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, bg=PANEL, fg=FG,
                         activebackground='#354359', activeforeground='white',
                         relief='flat', padx=9, pady=7, cursor='hand2')

    def _ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill='x', padx=12, pady=10)
        tk.Label(top, text='EDITOR DE CAPTURA', bg=BG, fg=FG,
                 font=('Segoe UI', 15, 'bold')).pack(side='left', padx=(0, 15))
        self._button(top, 'Salvar como…', self.save).pack(side='right', padx=3)
        self._button(top, 'Copiar imagem', self.copy).pack(side='right', padx=3)
        self._button(top, 'Refazer', self.redo).pack(side='right', padx=3)
        self._button(top, 'Desfazer', self.undo).pack(side='right', padx=3)

        tools = tk.Frame(self, bg=PANEL)
        tools.pack(fill='x', padx=12, pady=(0, 8))
        for label, value in [('↗ Seta', 'seta'), ('▭ Retângulo', 'retangulo'),
                             ('◯ Elipse', 'elipse'), ('✎ Caneta', 'caneta'),
                             ('T Texto', 'texto'), ('▧ Desfocar', 'desfocar'),
                             ('✂ Recortar', 'recortar')]:
            tk.Radiobutton(tools, text=label, value=value, variable=self.tool,
                           bg=PANEL, fg=FG, selectcolor='#425674',
                           activebackground=PANEL, activeforeground=FG,
                           indicatoron=False, relief='flat', padx=8, pady=9).pack(side='left', padx=2)
        for label, value in [('🔴', '#ff5252'), ('🟡', '#ffd34e'), ('🔵', '#43a4ff'), ('⚪', '#ffffff')]:
            self._button(tools, label, lambda c=value: setattr(self, 'color', c)).pack(side='left', padx=1)
        tk.Spinbox(tools, from_=1, to=20, width=3, textvariable=self.width,
                   bg='#354359', fg=FG, buttonbackground=PANEL).pack(side='right', padx=8)
        tk.Label(tools, text='Traço', bg=PANEL, fg=FG).pack(side='right')

        wrapper = tk.Frame(self, bg='#0d1118')
        wrapper.pack(fill='both', expand=True, padx=12)
        self.canvas = tk.Canvas(wrapper, bg='#0d1118', highlightthickness=0, cursor='crosshair')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self.render())
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.status = tk.StringVar(value='Arraste sobre a imagem para editar. Ctrl+Z desfaz • Ctrl+S salva.')
        tk.Label(self, textvariable=self.status, bg=BG, fg='#b9c5d5', anchor='w').pack(fill='x', padx=14, pady=8)

    def render(self):
        if not self.winfo_exists():
            return
        cw = max(self.canvas.winfo_width() - 20, 1)
        ch = max(self.canvas.winfo_height() - 20, 1)
        self.scale = min(cw / self.image.width, ch / self.image.height, 1.0)
        w = max(1, round(self.image.width * self.scale))
        h = max(1, round(self.image.height * self.scale))
        display = self.image.resize((w, h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(display)
        self.canvas.delete('all')
        self.offset_x = (self.canvas.winfo_width() - w) // 2
        self.offset_y = (self.canvas.winfo_height() - h) // 2
        self.canvas.create_image(self.offset_x, self.offset_y, image=self.photo, anchor='nw')
        self.status.set(f'{self.image.width} × {self.image.height} px  •  Zoom {self.scale:.0%}  •  Ctrl+Z desfaz')

    def coords(self, event):
        x = round((event.x - self.offset_x) / self.scale)
        y = round((event.y - self.offset_y) / self.scale)
        return (max(0, min(self.image.width - 1, x)),
                max(0, min(self.image.height - 1, y)))

    def press(self, event):
        self.start = self.coords(event)
        self.last = self.start
        self.canvas.delete('preview')
        if self.tool.get() == 'texto':
            text = simpledialog.askstring('Adicionar texto', 'Digite o texto:', parent=self)
            if text:
                self.snapshot()
                d = ImageDraw.Draw(self.image)
                # Fonte escalável do Windows, fallback PIL
                from PIL import ImageFont
                try:
                    font = ImageFont.truetype('arial.ttf', max(14, self.width.get() * 7))
                except OSError:
                    font = ImageFont.load_default()
                d.text(self.start, text, fill=self.color, font=font,
                       stroke_width=1, stroke_fill='#000000')
                self.render()
            self.start = None
        elif self.tool.get() == 'caneta':
            self.snapshot()

    def drag(self, event):
        if self.start is None:
            return
        end = self.coords(event)
        tool = self.tool.get()
        if tool == 'caneta':
            ImageDraw.Draw(self.image).line([self.last, end], fill=self.color,
                                            width=self.width.get(), joint='curve')
            self.last = end
            self.render()
            return
        self.canvas.delete('preview')
        x0, y0 = self.start
        x1, y1 = end
        p = lambda x, y: (self.offset_x + x * self.scale, self.offset_y + y * self.scale)
        a, b = p(x0, y0), p(x1, y1)
        if tool in ('retangulo', 'recortar', 'desfocar'):
            self.canvas.create_rectangle(*a, *b, outline=self.color, width=2, dash=(5, 3), tags='preview')
        elif tool == 'elipse':
            self.canvas.create_oval(*a, *b, outline=self.color, width=2, tags='preview')
        elif tool == 'seta':
            self.canvas.create_line(*a, *b, fill=self.color, width=3, arrow='last', tags='preview')

    def release(self, event):
        if self.start is None:
            return
        tool = self.tool.get()
        end = self.coords(event)
        x0, y0 = self.start
        x1, y1 = end
        self.start = None
        if tool == 'caneta':
            self.render()
            return
        if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
            self.render()
            return
        self.snapshot()
        d = ImageDraw.Draw(self.image)
        bounds = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if tool == 'retangulo':
            d.rectangle(bounds, outline=self.color, width=self.width.get())
        elif tool == 'elipse':
            d.ellipse(bounds, outline=self.color, width=self.width.get())
        elif tool == 'seta':
            d.line([(x0, y0), (x1, y1)], fill=self.color, width=self.width.get())
            angle = math.atan2(y1 - y0, x1 - x0)
            length = max(12, self.width.get() * 5)
            for delta in (-0.55, 0.55):
                end_point = (x1 - length * math.cos(angle + delta),
                             y1 - length * math.sin(angle + delta))
                d.line([(x1, y1), end_point], fill=self.color, width=self.width.get())
        elif tool == 'desfocar':
            x0, y0, x1, y1 = bounds
            patch = self.image.crop(bounds).filter(ImageFilter.GaussianBlur(radius=16))
            self.image.paste(patch, (x0, y0))
        elif tool == 'recortar':
            self.image = self.image.crop(bounds)
        self.render()

    def snapshot(self):
        self.history.append(self.image.copy())
        self.history = self.history[-25:]
        self.future.clear()

    def undo(self):
        if self.history:
            self.future.append(self.image.copy())
            self.image = self.history.pop()
            self.render()

    def redo(self):
        if self.future:
            self.history.append(self.image.copy())
            self.image = self.future.pop()
            self.render()

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, title='Salvar captura',
                    initialdir=str(self.default_path.parent),
                    initialfile=self.default_path.name,
                    defaultextension='.png', filetypes=[('Imagem PNG', '*.png'), ('JPEG', '*.jpg')])
        if not path:
            return
        try:
            self.image.save(path, quality=95)
            self.status.set(f'Imagem salva: {path}')
            if self.on_saved:
                self.on_saved(path)
        except Exception as exc:
            messagebox.showerror('Falha ao salvar', str(exc), parent=self)

    def copy(self):
        try:
            copy_image_windows(self.image)
            self.status.set('Imagem copiada! Cole com Ctrl+V no Discord ou em outro aplicativo.')
        except Exception as exc:
            messagebox.showerror('Falha ao copiar', str(exc), parent=self)
