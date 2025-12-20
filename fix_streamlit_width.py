from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Carpetas típicas a ignorar
SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", ".streamlit", ".pytest_cache", "site-packages"}

def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)

def patch_text(s: str) -> str:
    # Reemplazos directos
    s = s.replace("width='stretch'", "width='stretch'")
    s = s.replace("width='content'", "width='content'")
    return s

def main() -> int:
    changed = []
    for p in ROOT.rglob("*.py"):
        if should_skip(p):
            continue
        txt = p.read_text(encoding="utf-8")
        new = patch_text(txt)
        if new != txt:
            p.write_text(new, encoding="utf-8")
            changed.append(p)

    if changed:
        print("Archivos modificados:")
        for p in changed:
            print(" -", p)
    else:
        print("No se encontraron usos de use_container_width en .py")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
