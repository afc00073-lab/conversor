"""Punto de entrada de Conversor Pro."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys

# --- PARCHE PARA WINDOWS: PROHIBIR VENTANAS SECUNDARIAS --------------------
if os.name == "nt":
    _original_popen = subprocess.Popen

    class NoWindowPopen(_original_popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            if "startupinfo" not in kwargs:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = startupinfo
            super().__init__(*args, **kwargs)

    subprocess.Popen = NoWindowPopen
# ---------------------------------------------------------------------------


def _diagnose() -> int:
    from conversor.ocr import detect_tools

    status = detect_tools()
    print("Conversor Pro · diagnóstico")
    print("-" * 40)
    print(f"OCRmyPDF:    {status.ocrmypdf or 'no instalado'}")
    print(f"Tesseract:   {status.tesseract or 'no encontrado'}")
    print(f"Versión:     {status.tesseract_version or '—'}")
    print(f"Ghostscript: {status.ghostscript or 'no encontrado'}")
    print(f"pytesseract: {'sí' if status.pytesseract else 'no'}")
    print(f"Idiomas:     {', '.join(status.languages or []) or '—'}")
    print(f"Resumen:     {status.summary}")
    return 0


def main() -> int:
    if "--diagnose" in sys.argv or "--diagnostico" in sys.argv:
        return _diagnose()

    try:
        import customtkinter as ctk
    except ImportError:
        print(
            "Falta CustomTkinter. Instala las dependencias con:\n"
            "    pip install -r requirements.txt"
        )
        return 1

    ctk.set_default_color_theme("blue")
    from conversor.ui.app import run

    run()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
