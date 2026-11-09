"""Instala uma atualização já validada após o fechamento do Print HQ."""
import os
from pathlib import Path
import subprocess
import sys
import time

FILES = ('app_core.py', 'editor.py', 'updater.py', 'requirements.txt', 'print_hq.pyw', 'update_worker.py')


def main():
    pid, root = int(sys.argv[1]), Path(sys.argv[2]).resolve()
    stage = root / '.update_pending'
    backup = root / '.update_backup'
    # Espera o programa antigo liberar os arquivos no Windows.
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            try:
                if kernel.WaitForSingleObject(handle, 30000) == 0x00000102:
                    return 1
            finally:
                kernel.CloseHandle(handle)
    else:
        time.sleep(1)
    backup.mkdir(exist_ok=True)
    replaced = []
    try:
        for name in FILES:
            if not (stage / name).is_file():
                raise RuntimeError(f'Arquivo ausente: {name}')
        for name in FILES:
            dest = root / name
            if dest.exists():
                (backup / name).write_bytes(dest.read_bytes())
            os.replace(stage / name, dest)
            replaced.append(name)
    except Exception as exc:
        for name in reversed(replaced):
            saved = backup / name
            if saved.exists():
                os.replace(saved, root / name)
        (root / 'update_error.txt').write_text(str(exc), encoding='utf-8')
        return 2
    subprocess.Popen([sys.executable, str(root / 'print_hq.pyw', 'update_worker.py')], cwd=str(root),
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
