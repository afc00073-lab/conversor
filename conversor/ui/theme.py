"""Paleta y utilidades visuales de Conversor Pro."""

from __future__ import annotations

import customtkinter as ctk

# Colores con variante (claro, oscuro) para que CustomTkinter los adapte.
BG = ("#f3f5f9", "#0b1220")
SIDEBAR_BG = ("#e9edf5", "#0a101c")
CARD_BG = ("#ffffff", "#111c33")
CARD_BORDER = ("#e2e8f0", "#22304a")
ROW_BG = ("#f8fafc", "#0f1a2e")
ROW_BORDER = ("#e8edf5", "#1b2942")
MUTED = ("#64748b", "#94a3b8")
TEXT = ("#0f172a", "#e2e8f0")
SUBTLE = ("#f1f5f9", "#16223a")
GREEN = ("#059669", "#34d399")
RED = ("#dc2626", "#f87171")
AMBER = ("#d97706", "#fbbf24")
BLUE = ("#2563eb", "#60a5fa")

FONT = "Segoe UI"
MONO = "Cascadia Code"


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def mono_font(size: int = 12) -> ctk.CTkFont:
    return ctk.CTkFont(family=MONO, size=size)


def apply_appearance(mode: str) -> None:
    if mode in {"Light", "Dark", "System"}:
        ctk.set_appearance_mode(mode)
