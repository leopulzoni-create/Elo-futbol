# refactor_db_adapter.py
from __future__ import annotations
import argparse, re, sys
from pathlib import Path
from datetime import datetime
import shutil

# --- Config ---
INCLUDE_EXT = {".py"}
EXCLUDE_FILES = {"db.py", "refactor_db_adapter.py", "__init__.py"}
EXCLUDE_DIRS = {".git", ".venv", "venv", ".env", "__pycache__"}

# Regex que capturan conexiones directas a sqlite3
PAT_WITH = re.compile(
    r"(with\s+)sqlite3\.connect\([^)]*\)\s+(as\s+)([A-Za-z_]\w*)(\s*:)", re.MULTILINE
)
PAT_ASSIGN = re.compile(
    r"(^|\s)([A-Za-z_]\w*)\s*=\s*sqlite3\.connect\([^)]*\)", re.MULTILINE
)
PAT_RETURN = re.compile(
    r"(return\s+)sqlite3\.connect\([^)]*\)", re.MULTILINE
)
# Funciones típicas que devuelven conexiones
PAT_FUNC_CONN = re.compile(
    r"(def\s+)(get_conn|_get_conn|_conn)\s*\([^)]*\)\s*:\s*(?:#.*\n|\n)+", re.MULTILINE
)

def add_import_get_connection(text: str) -> tuple[str, bool]:
    if "from db import get_connection" in text:
        return text, False
    # Insertar después del bloque de imports iniciales (o al principio si no hay)
    lines = text.splitlines()
    insert_at = 0
    # saltar shebang / encoding / docstring simple al inicio
    while insert_at < len(lines) and (
        lines[insert_at].startswith("#!") or
        lines[insert_at].startswith("# -*-") or
        lines[insert_at].strip().startswith('"""') or
        lines[insert_at].strip().startswith("'''")
    ):
        # si es docstring de apertura, buscar su cierre
        if lines[insert_at].strip().startswith(('"""',"'''")):
            quote = lines[insert_at].strip()[:3]
            insert_at += 1
            while insert_at < len(lines) and quote not in lines[insert_at]:
                insert_at += 1
            if insert_at < len(lines):
                insert_at += 1
        else:
            insert_at += 1
    # avanzar sobre imports existentes
    while insert_at < len(lines) and (
        lines[insert_at].startswith("import ") or lines[insert_at].startswith("from ")
    ):
        insert_at += 1
    lines.insert(insert_at, "from db import get_connection")
    return "\n".join(lines), True

def apply_replacements(text: str, file_path: Path) -> tuple[str, list[str]]:
    changes = []

    # 1) with sqlite3.connect(...) as X:  -> with get_connection() as X:
    def repl_with(m):
        changes.append("with sqlite3.connect(...) → with get_connection()")
        return f"{m.group(1)}get_connection(){m.group(2)}{m.group(3)}{m.group(4)}"
    text_new = PAT_WITH.sub(repl_with, text)

    # 2) var = sqlite3.connect(...) -> var = get_connection()
    def repl_assign(m):
        prefix, var = m.group(1), m.group(2)
        changes.append(f"{var} = sqlite3.connect(...) → {var} = get_connection()")
        return f"{prefix}{var} = get_connection()"
    text_new2 = PAT_ASSIGN.sub(repl_assign, text_new)

    # 3) return sqlite3.connect(...) -> return get_connection()
    def repl_return(m):
        changes.append("return sqlite3.connect(...) → return get_connection()")
        return f"{m.group(1)}get_connection()"
    text_new3 = PAT_RETURN.sub(repl_return, text_new2)

    # 4) Si detectamos funciones con nombre típico de conexión, nos aseguramos de que usen get_connection()
    #    (no forzamos si ya quedó bien por los pasos anteriores)
    if PAT_FUNC_CONN.search(text_new3) and "get_connection()" not in text_new3:
        # como salvaguarda mínima, no hacemos magia aquí; los pasos previos suelen cubrirlo

        pass

    # 5) Importar from db import get_connection si hicimos algún cambio que lo requiera
    if changes:
        text_new4, added = add_import_get_connection(text_new3)
        if added:
            changes.append("Insertado: from db import get_connection")
        return text_new4, changes
    else:
        return text, changes

def process_file(path: Path, write: bool, backup_dir: Path) -> list[str]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    new_src, changes = apply_replacements(src, path)
    if changes and write:
        # backup
        rel = path.name
        (backup_dir / rel).write_text(src, encoding="utf-8")
        # write
        path.write_text(new_src, encoding="utf-8")
    return changes

def main():
    ap = argparse.ArgumentParser(description="Refactor sqlite3.connect → get_connection() (Turso adapter).")
    ap.add_argument("--write", action="store_true", help="Aplicar cambios (por defecto solo muestra).")
    ap.add_argument("--root", default=".", help="Carpeta raíz del proyecto (default: .)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    backup_dir = root / f"_backup_refactor_{datetime.now().strftime('%Y%m%d_%H%M')}"
    if args.write:
        backup_dir.mkdir(exist_ok=True)

    total_changes = 0
    touched = 0

    for p in root.rglob("*.py"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if p.name in EXCLUDE_FILES:
            continue
        changes = process_file(p, args.write, backup_dir)
        if changes:
            touched += 1
            total_changes += len(changes)
            print(f"\n=== {p} ===")
            for c in changes:
                print(" -", c)

    print("\nResumen:")
    print(f"  Archivos modificados: {touched}")
    print(f"  Cambios aplicados:    {total_changes}")
    if args.write:
        print(f"  Backup guardado en:   {backup_dir}")
    else:
        print("  *Modo prueba* (no se escribió nada). Ejecuta con --write para aplicar.")

if __name__ == "__main__":
    sys.exit(main())

