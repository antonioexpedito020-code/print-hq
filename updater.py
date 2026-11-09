"""Atualização por releases GitHub, com verificação SHA256 e instalação reversível.

O aplicativo não executa código remoto: só aceita arquivos do próprio pacote.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile

REPO = 'antonioexpedito020-code/print-hq'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
FILES = ('app_core.py', 'editor.py', 'updater.py', 'requirements.txt', 'print_hq.pyw', 'update_worker.py')
MAX_ZIP = 12 * 1024 * 1024


def version_tuple(version):
    parts = version.lstrip('v').split('.')
    return tuple(int(x) for x in parts[:3])


def request(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'PrintHQ-Updater/1.2', 'Accept': 'application/vnd.github+json'})
    return urllib.request.urlopen(req, timeout=20)


def latest(current):
    try:
        with request(API) as response:
            info = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None  # Nenhuma versão publicada ainda
        raise
    version = str(info['tag_name']).lstrip('v')
    if version_tuple(version) <= version_tuple(current):
        return None
    assets = {a['name']: a for a in info.get('assets', [])}
    asset = assets.get(f'PrintHQ_{version}.zip')
    checksum = assets.get(f'PrintHQ_{version}.sha256')
    if not asset or not checksum:
        raise RuntimeError('A versão publicada não contém ZIP e SHA256 oficiais.')
    return {'version': version, 'zip': asset['browser_download_url'],
            'checksum': checksum['browser_download_url']}


def stage(release, root):
    """Baixa e valida o pacote antes de substituir qualquer arquivo."""
    with request(release['checksum']) as response:
        expected = response.read(1024).decode('ascii').strip().split()[0].lower()
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
        raise RuntimeError('Checksum da versão inválido.')
    with request(release['zip']) as response:
        data = response.read(MAX_ZIP + 1)
    if len(data) > MAX_ZIP:
        raise RuntimeError('Pacote de atualização maior que o permitido.')
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError('Verificação SHA256 falhou. Nenhum arquivo foi alterado.')
    staging = root / '.update_pending'
    staging.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = {}
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            name = Path(entry.filename).name
            if name in FILES:
                if name in entries:
                    raise RuntimeError('Pacote contém arquivos duplicados.')
                entries[name] = entry
        if set(entries) != set(FILES):
            raise RuntimeError('Pacote incompleto; atualização cancelada.')
        for name, entry in entries.items():
            (staging / name).write_bytes(archive.read(entry))
    return staging


def launch_install(root):
    """Inicia um instalador separado; o programa deve fechar em seguida."""
    worker = root / 'update_worker.py'
    if not worker.is_file():
        raise RuntimeError('Instalador de atualizações não encontrado.')
    subprocess.Popen([sys.executable, str(worker), str(os.getpid()), str(root)],
                     cwd=str(root), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                     close_fds=True)
