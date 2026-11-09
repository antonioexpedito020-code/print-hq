"""Print HQ 1.2.0 — capturas, edição e gravação MP4 para Windows."""
import datetime as dt
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from editor import ImageEditor
import updater

VERSION = '1.2.0'
DATA = Path.home() / '.print_hq_novo'
CONFIG = DATA / 'config.json'
LOG = DATA / 'print_hq.log'
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
BG, PANEL, TEXT, MUTED, BLUE = '#141922', '#202938', '#f4f7fc', '#aab9ce', '#4d93ff'


def log(message):
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open('a', encoding='utf-8') as file:
        file.write(f'{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}\n')


def defaults():
    return {'pictures': str(Path.home() / 'Pictures' / 'Print HQ'),
            'videos': str(Path.home() / 'Videos' / 'Print HQ'),
            'fps': '30', 'quality': 'Alta', 'encoder': 'Automático',
            'preset': 'Qualidade', 'discord_mb': '25'}


def load():
    cfg = defaults()
    try:
        saved = json.loads(CONFIG.read_text(encoding='utf-8'))
        cfg.update({k: v for k, v in saved.items() if k in cfg})
    except (OSError, ValueError, TypeError):
        pass
    return cfg


def find_ffmpeg():
    for candidate in (Path(__file__).resolve().parent / 'ffmpeg.exe',
                      Path.home() / '.print_hq_data' / 'ffmpeg.exe'):
        if candidate.is_file():
            return str(candidate)
    return shutil.which('ffmpeg')


def capture():
    import mss
    from PIL import Image
    with mss.mss() as sct:
        screen = sct.monitors[1]
        shot = sct.grab(screen)
        return Image.frombytes('RGB', shot.size, shot.bgra, 'raw', 'BGRX')


class RegionSelector(tk.Toplevel):
    def __init__(self, parent, image, callback):
        super().__init__(parent)
        from PIL import ImageTk
        self.image = image
        self.callback = callback
        self.start = None
        self.rect = None
        self.overrideredirect(True)
        self.attributes('-topmost', True)
        self.geometry(f'{image.width}x{image.height}+0+0')
        self.canvas = tk.Canvas(self, width=image.width, height=image.height,
                                cursor='crosshair', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw')
        self.canvas.create_rectangle(0, 0, image.width, image.height,
                                     fill='#101722', stipple='gray50', outline='')
        self.canvas.create_text(22, 22, anchor='nw', text='Arraste para selecionar  •  Esc cancela',
                                fill='white', font=('Segoe UI', 14, 'bold'))
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.drag)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.bind('<Escape>', lambda e: (self.destroy(), self.master.deiconify()))
        self.focus_force()

    def press(self, event):
        self.start = (event.x, event.y)

    def drag(self, event):
        if self.start is None:
            return
        self.canvas.delete('selection')
        self.canvas.create_rectangle(*self.start, event.x, event.y,
                                     outline='#55a9ff', width=3, tags='selection')

    def release(self, event):
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        x0, x1 = sorted((max(0, min(self.image.width, x0)), max(0, min(self.image.width, x1))))
        y0, y1 = sorted((max(0, min(self.image.height, y0)), max(0, min(self.image.height, y1))))
        self.destroy()
        self.master.deiconify()
        if x1 - x0 >= 5 and y1 - y0 >= 5:
            self.callback(self.image.crop((x0, y0, x1, y1)))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'Print HQ {VERSION}')
        self.geometry('930x720')
        self.minsize(780, 640)
        self.configure(bg=BG)
        self.cfg = load()
        self.events = queue.Queue()
        self.proc = None
        self.recording = False
        self.output = None
        self.started = None
        self.last_image = None
        self.last_video = None
        self.compressing = False
        self.release = None
        self.updating = False
        self._ui()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(180, self.poll)
        self.bind('<Control-Shift-s>', lambda e: self.screenshot(region=True))
        self.bind('<Control-Shift-p>', lambda e: self.screenshot(region=False))
        log(f'Início Print HQ {VERSION}, Python {sys.version.split()[0]}')
        self.after(1200, self.check_updates)

    def button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=BLUE if primary else PANEL,
                         fg='white', activebackground='#3275d6', activeforeground='white',
                         relief='flat', padx=15, pady=11, font=('Segoe UI', 10, 'bold'),
                         cursor='hand2')

    def label(self, parent, text, size=10, bold=False, color=TEXT):
        return tk.Label(parent, text=text, bg=BG, fg=color,
                        font=('Segoe UI', size, 'bold' if bold else 'normal'))

    def _ui(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill='x', padx=26, pady=(22, 6))
        self.label(top, 'PRINT HQ', 25, True).pack(side='left')
        self.label(top, '  1.2.0  •  Captura, edição e vídeo', 10, color=MUTED).pack(side='left', pady=12)
        self.button(top, 'Verificar atualizações', self.check_updates).pack(side='right')
        self.label(self, 'CAPTURAS DE TELA', 12, True).pack(anchor='w', padx=26, pady=(15, 8))
        actions = tk.Frame(self, bg=BG)
        actions.pack(fill='x', padx=26)
        self.button(actions, '✂  Selecionar área', lambda: self.screenshot(True), True).pack(side='left', padx=(0, 9))
        self.button(actions, '▣  Tela inteira', lambda: self.screenshot(False)).pack(side='left', padx=(0, 9))
        self.button(actions, '✎  Editar último print', self.edit_last).pack(side='left', padx=(0, 9))
        self.button(actions, '▤  Abrir prints', lambda: self.open_dir(self.pictures.get())).pack(side='left')
        self.label(self, 'Editor: setas, formas, caneta, texto, desfoque, recorte, desfazer, copiar e salvar.',
                   color=MUTED).pack(anchor='w', padx=26, pady=(9, 15))
        line = tk.Frame(self, bg='#334056', height=1)
        line.pack(fill='x', padx=26)
        self.label(self, 'GRAVAÇÃO DE VÍDEO', 12, True).pack(anchor='w', padx=26, pady=(18, 8))
        options = tk.Frame(self, bg=BG)
        options.pack(fill='x', padx=26)
        self.fps = tk.StringVar(value=self.cfg['fps'])
        self.quality = tk.StringVar(value=self.cfg['quality'])
        self.encoder = tk.StringVar(value=self.cfg['encoder'])
        self.preset = tk.StringVar(value=self.cfg['preset'])
        self.discord_mb = tk.StringVar(value=self.cfg['discord_mb'])
        for name, var, values in [
            ('FPS', self.fps, ('30', '60')),
            ('Qualidade', self.quality, ('Equilibrada', 'Alta', 'Máxima')),
            ('Codificador', self.encoder, ('Automático', 'AMD AMF', 'Processador x264')),
            ('Perfil', self.preset, ('Qualidade', 'Discord compacto')),
        ]:
            col = tk.Frame(options, bg=BG)
            col.pack(side='left', fill='x', expand=True, padx=(0, 10))
            self.label(col, name, color=MUTED).pack(anchor='w')
            cb = ttk.Combobox(col, textvariable=var, values=values, state='readonly', width=19)
            cb.pack(fill='x', pady=5)
            cb.bind('<<ComboboxSelected>>', lambda e: self.persist())
        discord = tk.Frame(self, bg=BG)
        discord.pack(fill='x', padx=26, pady=(5, 10))
        self.label(discord, 'Limite desejado no Discord (MB):', color=MUTED).pack(side='left')
        ttk.Combobox(discord, textvariable=self.discord_mb, values=('10', '25', '50', '100'),
                     state='readonly', width=7).pack(side='left', padx=8)
        self.label(discord, 'No perfil compacto, o vídeo é comprimido após a gravação.',
                   color=MUTED).pack(side='left', padx=10)
        controls = tk.Frame(self, bg=BG)
        controls.pack(fill='x', padx=26, pady=(4, 10))
        self.record_btn = self.button(controls, '●  Iniciar gravação', self.toggle, True)
        self.record_btn.pack(side='left', padx=(0, 10))
        self.button(controls, '▶  Abrir último vídeo', self.open_last_video).pack(side='left', padx=(0, 10))
        self.button(controls, '▤  Abrir vídeos', lambda: self.open_dir(self.videos.get())).pack(side='left')
        self.label(self, 'Gravação de tela inteira • MP4 H.264 • sem áudio nesta versão.',
                   color=MUTED).pack(anchor='w', padx=26, pady=(0, 13))
        line = tk.Frame(self, bg='#334056', height=1)
        line.pack(fill='x', padx=26)
        self.label(self, 'PASTAS E FERRAMENTAS', 12, True).pack(anchor='w', padx=26, pady=(15, 7))
        self.pictures = tk.StringVar(value=self.cfg['pictures'])
        self.videos = tk.StringVar(value=self.cfg['videos'])
        self.folder('Pasta dos prints', self.pictures)
        self.folder('Pasta dos vídeos', self.videos)
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill='x', padx=26, pady=10)
        self.button(footer, 'Diagnósticos', lambda: self.open_dir(str(DATA))).pack(side='left')
        self.button(footer, 'Histórico de capturas', self.history).pack(side='left', padx=8)
        self.label(footer, 'Atalhos na janela: Ctrl+Shift+S (área) • Ctrl+Shift+P (tela)',
                   color=MUTED).pack(side='left', padx=16)
        self.update_info = tk.StringVar(value='Atualizações: verificando…')
        tk.Label(self, textvariable=self.update_info, bg=BG, fg=MUTED, anchor='w', padx=26).pack(fill='x')
        self.status = tk.StringVar(value='Pronto para capturar.')
        tk.Label(self, textvariable=self.status, bg=PANEL, fg=TEXT, anchor='w',
                 padx=20, pady=12, wraplength=850).pack(side='bottom', fill='x')

    def folder(self, caption, variable):
        row = tk.Frame(self, bg=BG)
        row.pack(fill='x', padx=26, pady=3)
        self.label(row, caption, color=MUTED).pack(side='left', padx=(0, 12))
        tk.Entry(row, textvariable=variable, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                 relief='flat').pack(side='left', fill='x', expand=True, ipady=6)
        self.button(row, 'Escolher', lambda: self.pick_folder(variable)).pack(side='left', padx=(8, 0))

    def pick_folder(self, variable):
        selected = filedialog.askdirectory(initialdir=variable.get() if Path(variable.get()).is_dir() else str(Path.home()))
        if selected:
            variable.set(selected)
            self.persist()

    def persist(self):
        DATA.mkdir(parents=True, exist_ok=True)
        data = {'pictures': self.pictures.get(), 'videos': self.videos.get(),
                'fps': self.fps.get(), 'quality': self.quality.get(),
                'encoder': self.encoder.get(), 'preset': self.preset.get(),
                'discord_mb': self.discord_mb.get()}
        temp = CONFIG.with_suffix('.tmp')
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        temp.replace(CONFIG)

    def open_dir(self, path):
        folder = Path(path).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(str(folder))
        else:
            messagebox.showinfo('Pasta', str(folder))

    def screenshot(self, region=False):
        try:
            self.persist()
            self.withdraw()
            self.update_idletasks()
            # Aguarda o gerenciador de janelas ocultar a janela antes da captura.
            self.after(250, lambda: self._capture_hidden(region))
        except Exception as exc:
            self.deiconify()
            self.error('Falha na captura', exc)

    def _capture_hidden(self, region):
        try:
            image = capture()
            if region:
                RegionSelector(self, image, self._on_image)
            else:
                self._on_image(image)
        except Exception as exc:
            self.error('Falha na captura', exc)
        finally:
            if not region:
                self.deiconify()

    def _on_image(self, image):
        try:
            folder = Path(self.pictures.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'print_{dt.datetime.now():%Y%m%d_%H%M%S_%f}.png'
            image.save(path)
            self.last_image = path
            self.deiconify()
            self.status.set(f'Print salvo: {path.name}. Editor aberto para você editar ou copiar.')
            log(f'Print: {path}')
            ImageEditor(self, image, path, on_saved=self._on_saved)
        except Exception as exc:
            self.error('Falha ao salvar print', exc)

    def _on_saved(self, path):
        self.last_image = Path(path)
        self.status.set(f'Imagem editada salva: {path}')

    def edit_last(self):
        if not self.last_image or not self.last_image.is_file():
            folder = Path(self.pictures.get())
            candidates = sorted(folder.glob('*.png'), key=lambda p: p.stat().st_mtime, reverse=True) if folder.is_dir() else []
            if not candidates:
                messagebox.showinfo('Editor', 'Tire um print primeiro para editar.')
                return
            self.last_image = candidates[0]
        from PIL import Image
        with Image.open(self.last_image) as img:
            ImageEditor(self, img.copy(), self.last_image, on_saved=self._on_saved)

    def choose_encoder(self, ff):
        p = subprocess.run([ff, '-hide_banner', '-encoders'], capture_output=True,
                           text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        available = p.stdout
        if p.returncode:
            raise RuntimeError(p.stderr[-500:])
        requested = self.encoder.get()
        if requested == 'Processador x264':
            return 'libx264'
        if requested == 'AMD AMF' and 'h264_amf' not in available:
            raise RuntimeError('O FFmpeg instalado não tem codificador AMD AMF.')
        return 'h264_amf' if requested == 'AMD AMF' or (requested == 'Automático' and 'h264_amf' in available) else 'libx264'

    def command(self, ff, output, encoder):
        fps = self.fps.get()
        quality = self.quality.get()
        cmd = [ff, '-hide_banner', '-loglevel', 'warning', '-y',
               '-f', 'gdigrab', '-framerate', fps, '-draw_mouse', '1', '-i', 'desktop',
               '-an', '-vf', 'format=nv12' if encoder == 'h264_amf' else 'format=yuv420p']
        if encoder == 'h264_amf':
            rate = {'Equilibrada': 8000, 'Alta': 14000, 'Máxima': 22000}[quality]
            cmd += ['-c:v', 'h264_amf', '-quality', 'balanced', '-rc', 'vbr_peak',
                    '-b:v', f'{rate}k', '-maxrate', f'{int(rate * 1.5)}k']
        else:
            crf = {'Equilibrada': '25', 'Alta': '22', 'Máxima': '19'}[quality]
            cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', crf]
        return cmd + ['-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)]

    def toggle(self):
        self.stop() if self.recording else self.start()

    def start(self):
        try:
            self.persist()
            ff = find_ffmpeg()
            if not ff:
                raise RuntimeError('FFmpeg não encontrado. Instale FFmpeg no PATH ou coloque ffmpeg.exe nesta pasta.')
            folder = Path(self.videos.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / f'gravacao_{dt.datetime.now():%Y%m%d_%H%M%S}.mp4'
            encoder = self.choose_encoder(ff)
            cmd = self.command(ff, output, encoder)
            log('FFmpeg: ' + subprocess.list2cmdline(cmd))
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            self.proc, self.output, self.started = proc, output, dt.datetime.now()
            self.recording = True
            self.record_btn.config(text='■  Parar gravação')
            self.status.set(f'Gravando em {self.fps.get()} FPS com {encoder}...')
            threading.Thread(target=self.watch, args=(proc,), daemon=True).start()
        except Exception as exc:
            self.error('Não foi possível gravar', exc)

    def watch(self, proc):
        lines = []
        try:
            for line in iter(proc.stderr.readline, b''):
                msg = line.decode('utf-8', 'replace').strip()
                if msg:
                    lines.append(msg)
                    lines = lines[-25:]
                    log('FFmpeg: ' + msg)
            code = proc.wait()
            self.events.put(('finished', proc, code, '\n'.join(lines[-10:])))
        except Exception as exc:
            self.events.put(('finished', proc, -1, str(exc)))

    def stop(self):
        if not self.recording or not self.proc:
            return
        self.recording = False
        self.record_btn.config(state='disabled', text='Finalizando...')
        self.status.set('Finalizando vídeo MP4...')
        proc = self.proc
        def worker():
            try:
                if proc.poll() is None:
                    proc.stdin.write(b'q\n')
                    proc.stdin.flush()
                    proc.wait(timeout=20)
            except (OSError, BrokenPipeError):
                pass
            except subprocess.TimeoutExpired:
                proc.kill()
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, proc, code, details = self.events.get_nowait()
                if kind == 'finished' and proc is self.proc:
                    self.proc = None
                    unexpected = self.recording
                    self.recording = False
                    self.record_btn.config(state='normal', text='●  Iniciar gravação')
                    if code == 0 and self.output and self.output.is_file() and self.output.stat().st_size > 1024:
                        self.last_video = self.output
                        self.status.set(f'Vídeo salvo: {self.output.name} ({self.output.stat().st_size / 1048576:.1f} MB)')
                        if self.preset.get() == 'Discord compacto':
                            seconds = max(1, (dt.datetime.now() - self.started).total_seconds())
                            self.compress_discord(self.output, seconds)
                    else:
                        log(f'Falha FFmpeg {code}: {details}')
                        self.status.set('Falha na gravação. Consulte os diagnósticos.')
                        if unexpected:
                            messagebox.showerror('Gravação interrompida', details[-700:] or f'Código {code}')
                elif kind == 'update_check':
                    self.updating = False
                    if code == 0:
                        self.release = details
                        self.update_info.set('Nova versão ' + details['version'] + ' disponível!' if details else 'Print HQ atualizado • nenhuma versão mais recente publicada.')
                        if details and messagebox.askyesno('Atualização disponível', 'Print HQ ' + details['version'] + ' disponível no GitHub.\n\nBaixar e instalar agora?'):
                            self.install_update()
                    else:
                        self.update_info.set('Não foi possível consultar atualizações. O programa continua funcionando.')
                        log('Atualização: ' + str(details))
                elif kind == 'update_stage':
                    self.updating = False
                    if code == 0:
                        if messagebox.askyesno('Atualização pronta', 'Download validado. Fechar o Print HQ para instalar e reiniciar?'):
                            try:
                                updater.launch_install(Path(__file__).resolve().parent)
                                self.destroy()
                            except Exception as exc:
                                self.error('Falha ao iniciar atualização', exc)
                    else:
                        self.update_info.set('Falha no download: atualização não instalada.')
                        self.error('Atualização não instalada', details)
                elif kind == 'compressed':
                    self.compressing = False
                    target, success, detail = proc, code, details
                    if success == 0 and target.is_file() and target.stat().st_size > 1024:
                        self.last_video = target
                        size = target.stat().st_size / 1048576
                        self.status.set(f'Vídeo para Discord pronto: {target.name} ({size:.1f} MB)')
                        log(f'Vídeo Discord: {target} ({size:.1f} MB)')
                        if size > int(self.discord_mb.get()):
                            self.status.set(f'Vídeo criado ({size:.1f} MB), mas ultrapassou o limite desejado. Tente um limite menor.')
                    else:
                        self.status.set('Vídeo original salvo, mas falhou a compressão para Discord.')
                        log(f'Falha compressão Discord: {detail}')
        except queue.Empty:
            pass
        self.after(180, self.poll)

    def compress_discord(self, source, seconds):
        ff = find_ffmpeg()
        limit_mb = int(self.discord_mb.get())
        target = source.with_name(source.stem + '_discord.mp4')
        # Reservamos 8% de margem; sem áudio nesta versão.
        bitrate = max(120, int(limit_mb * 8 * 1024 * 0.92 / seconds))
        cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
               '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-b:v', f'{bitrate}k',
               '-maxrate', f'{bitrate}k', '-bufsize', f'{bitrate * 2}k',
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(target)]
        self.compressing = True
        self.status.set('Criando versão compacta para Discord... Não feche o programa.')
        def worker():
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, errors='replace',
                                   creationflags=CREATE_NO_WINDOW)
                self.events.put(('compressed', target, p.returncode, p.stderr[-500:]))
            except Exception as exc:
                self.events.put(('compressed', target, -1, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def check_updates(self):
        if self.updating or self.recording:
            return
        self.updating = True
        self.update_info.set('Consultando versões no GitHub…')
        def worker():
            try:
                release = updater.latest(VERSION)
                self.events.put(('update_check', None, 0, release))
            except Exception as exc:
                self.events.put(('update_check', None, 1, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def install_update(self):
        if not self.release or self.updating or self.recording or self.compressing:
            return
        self.updating = True
        self.update_info.set('Baixando e validando a atualização…')
        def worker():
            try:
                updater.stage(self.release, Path(__file__).resolve().parent)
                self.events.put(('update_stage', None, 0, 'ok'))
            except Exception as exc:
                self.events.put(('update_stage', None, 1, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def history(self):
        folder = Path(self.pictures.get()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        win = tk.Toplevel(self)
        win.title('Print HQ • Histórico de prints')
        win.geometry('720x520')
        win.configure(bg=BG)
        tk.Label(win, text='HISTÓRICO DE CAPTURAS', bg=BG, fg=TEXT,
                 font=('Segoe UI', 16, 'bold')).pack(anchor='w', padx=18, pady=15)
        items = tk.Listbox(win, bg=PANEL, fg=TEXT, selectbackground=BLUE,
                           borderwidth=0, font=('Segoe UI', 11))
        items.pack(fill='both', expand=True, padx=18, pady=8)
        paths = sorted(folder.glob('*.png'), key=lambda x: x.stat().st_mtime, reverse=True)[:200]
        for path in paths:
            items.insert('end', f'{path.name}    •    {path.stat().st_size / 1024:.0f} KB')
        def chosen():
            sel = items.curselection()
            return paths[sel[0]] if sel else None
        def edit():
            path = chosen()
            if path:
                from PIL import Image
                with Image.open(path) as image:
                    ImageEditor(self, image.copy(), path, on_saved=self._on_saved)
        def open_file():
            path = chosen()
            if path and sys.platform == 'win32':
                os.startfile(str(path))
        bar = tk.Frame(win, bg=BG)
        bar.pack(fill='x', padx=18, pady=12)
        self.button(bar, 'Editar selecionado', edit, True).pack(side='left', padx=(0, 8))
        self.button(bar, 'Abrir imagem', open_file).pack(side='left', padx=(0, 8))
        self.button(bar, 'Abrir pasta', lambda: self.open_dir(str(folder))).pack(side='left')
        items.bind('<Double-Button-1>', lambda e: edit())

    def open_last_video(self):
        if self.last_video and self.last_video.is_file():
            if sys.platform == 'win32':
                os.startfile(str(self.last_video))
        else:
            self.open_dir(self.videos.get())

    def error(self, title, exc):
        log(f'{title}: {exc!r}')
        self.status.set(f'{title}: {exc}')
        messagebox.showerror(title, f'{exc}\n\nDiagnóstico: {LOG}')

    def close(self):
        if self.proc is not None or self.compressing or self.updating:
            messagebox.showwarning('Processamento em andamento', 'Espere a gravação terminar antes de fechar.')
            return
        self.persist()
        self.destroy()


if __name__ == '__main__':
    App().mainloop()
