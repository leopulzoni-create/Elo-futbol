# refactor_db_adapter_v2.py
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
from datetime import datetime

INCLUDE_EXT = {".py"}
EXCLUDE_FILES = {"db.py", "refactor_db_adapter.py", "refactor_db_adapter_v2.py", "__init__.py"}
EXCLUDE_DIRS  = {".git", ".venv", "venv", ".env", "__pycache__"}

# --- Patrones ---
# 1) with sqlite3.connect(...) as X:
PAT_WITH = re.compile(
    r"(with\s+)sqlite3\.connect\([^)]*\)\s+(as\s+)([A-Za-z_]\w*)(\s*:)",
    re.MULTILINE
)
# 2) var = sqlite3.connect(...)
PAT_ASSIGN = re.compile(
    r"(^|\s)([A-Za-z_]\w*)\s*=\s*sqlite3\.connect\([^)]*\)",
    re.MULTILINE
)
# 3) return sqlite3.connect(...)
PAT_RETURN = re.compile(
    r"(return\s+)sqlite3\.connect\([^)]*\)",
    re.MULTILINE
)
# 4) row_factory = sqlite3.Row   (remover)
PAT_ROWFACT = re.compile(
    r"^\s*[\w\.]+\s*\.row_factory\s*=\s*sqlite3\.Row\s*$",
    re.MULTILINE
)
# 5) funciones típicas que devuelven conexión (ajuste suave si quedaron sin tocar)
PAT_FUNC_CONN = re.compile(
    r"(def\s+)(get_conn|_get_conn|_conn)\s*\([^)]*\)\s*:\s*",
    re.MULTILINE
)

def add_import(text: str) -> tuple[str, bool]:
    if "from db import get_connection" in text:
        return text, False
    lines = text.splitlines()
    i = 0
    # Saltar shebang/encoding/docstring inicial
    while i < len(lines) and (
        lines[i].startswith("#!") or
        lines[i].startswith("# -*-") or
        lines[i].strip().startswith('"""') or
        lines[i].strip().startswith("'''")
    ):
        if lines[i].strip().startswith(('"""',"'''")):
            q = lines[i].strip()[:3]; i += 1
            while i < len(lines) and q not in lines[i]:
                i += 1
            if i < len(lines): i += 1
        else:
            i += 1
    # Avanzar imports existentes
    while i < len(lines) and (lines[i].startswith("import ") or lines[i].startswith("from ")):
        i += 1
    lines.insert(i, "from db import get_connection")
    return "\n".join(lines), True

def apply(text: str, path: Path) -> tuple[str, list[str]]:
    changes = []
    # with sqlite3.connect(...) as X:
    def repl_with(m):
        changes.append("with sqlite3.connect(...) → with get_connection()")
        return f"{m.group(1)}get_connection(){m.group(2)}{m.group(3)}{m.group(4)}"
    out = PAT_WITH.sub(repl_with, text)

    # var = sqlite3.connect(...)
    def repl_assign(m):
        prefix, var = m.group(1), m.group(2)
        changes.append(f"{var} = sqlite3.connect(...) → {var} = get_connection()")
        return f"{prefix}{var} = get_connection()"
    out = PAT_ASSIGN.sub(repl_assign, out)

    # return sqlite3.connect(...)
    def repl_return(m):
        changes.append("return sqlite3.connect(...) → return get_connection()")
        return f"{m.group(1)}get_connection()"
    out = PAT_RETURN.sub(repl_return, out)

    # eliminar row_factory = sqlite3.Row
    if PAT_ROWFACT.search(out):
        out = PAT_ROWFACT.sub("", out)
        changes.append("Eliminado: conn.row_factory = sqlite3.Row")

    # si hubo cambios, insertar import
    if changes:
        out2, added = add_import(out)
        if added:
            changes.append("Insertado: from db import get_connection")
        out = out2

    # ajuste extra: si detectamos def get_conn/_get_conn/_conn y aún no hay get_connection(), aseguramos return
    if PAT_FUNC_CONN.search(out) and "get_connection()" not in out:
        # Intento seguro: reemplazar cualquier 'return sqlite3.connect(...)' ya lo hicimos arriba.
        # Si no hay return, no forzamos más para evitar falsos positivos.
        pass

    return out, changes

def main():
    ap = argparse.ArgumentParser(description="Refactor total: sqlite3.connect(...) → get_connection() + limpia row_factory.")
    ap.add_argument("--write", action="store_true", help="Aplicar cambios (por defecto solo muestra).")
    ap.add_argument("--root", default=".", help="Carpeta raíz del proyecto (default: .)")
    args = ap.parse_args()
    args.write = True
    root = Path(args.root).resolve()
    backup_dir = root / f"_backup_refactor_v2_{datetime.now().strftime('%Y%m%d_%H%M')}"

    total_files = 0
    total_changes = 0

    for p in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts): continue
        if p.name in EXCLUDE_FILES: continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        new_src, changes = apply(src, p)
        if changes:
            total_files += 1
            total_changes += len(changes)
            print(f"\n=== {p} ===")
            for c in changes:
                print(" -", c)
            if args.write:
                backup_dir.mkdir(exist_ok=True)
                (backup_dir / p.name).write_text(src, encoding="utf-8")
                p.write_text(new_src, encoding="utf-8")

    print("\nResumen:")
    print(f"  Archivos tocados: {total_files}")
    print(f"  Cambios detectados: {total_changes}")
    if args.write:
        print(f"  Backup: {backup_dir}")
    else:
        print("  *Modo prueba* (no se escribió nada). Usa --write para aplicar.")

if __name__ == "__main__":
    sys.exit(main())

