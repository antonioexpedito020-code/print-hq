"""Inicializador estável do Print HQ. Abra com dois cliques."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app_core import App

if __name__ == '__main__':
    App().mainloop()
