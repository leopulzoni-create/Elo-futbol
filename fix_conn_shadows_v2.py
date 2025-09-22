# fix_conn_shadows_v2.py
# Corrige funciones locales que sombrean al adaptador central de DB.
# - def get_connection() / _conn() / get_conn() / _get_conn()
# - Normaliza "return get_connection())" -> "return get_connection()"
# Hace backup en _backup_fix_conn_YYYYMMDD_HHMM/
from __future__ import annotations
import re
from pathlib import Path
from datetime import datetime

EXCLUDE_FILES = {
    "db.py",
    "fix_conn_shadows_v2.py",
    "refactor_db_adapter.py",
    "refactor_db_adapter_v2.py",
    "__init__.py",
}
EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", ".env"}

# Patrón: def <name>() :  +  bloque indentado
NAMES = r"(get_connection|_conn|get_conn|_get_conn)"
DEF_RE = re.compile(
    rf"(^|\n)def\s+(?P<name>{NAMES})\s*\(\s*\)\s*:\s*\n(?P<body>(?:[ \t]+.*\n)+)",
    re.M,
)

# Si ya está parcheado, no tocamos
ALREADY_OK_RE = re.compile(
    r"def\s+(?:get_connection|_conn|get_conn|_get_conn)\s*\(\s*\)\s*:\s*\n[ \t]*from\s+db\s+import\s+get_connection\s+as\s+_gc\s*\n[ \t]*return\s+_gc\(\)\s*\n",
    re.M,
)

# Arreglo de paréntesis extra
EXTRA_PAREN_RE = re.compile(r"return\s+get_connection\(\)\)")

STUB_FMT = (
    "def {name}():\n"
    "    from db import get_connection as _gc\n"
    "    return _gc()\n"
)

def patch_text(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    # 1) Normalizar errores de paréntesis de refactor previo
    if EXTRA_PAREN_RE.search(text):
        text = EXTRA_PAREN_RE.sub("return get_connection()", text)
        changes.append("Fix: 'return get_connection())' -> 'return get_connection()'")

    # 2) Reemplazar TODAS las defs locales por un puente a db.get_connection()
    out = text
    idx = 0
    while True:
        m = DEF_RE.search(out, idx)
        if not m:
            break
        name = m.group("name")
        start = m.start()
        end = m.end()

        # Si ese bloque ya está correcto, lo saltamos
        if ALREADY_OK_RE.search(out[m.start(): m.start()+200]):
            idx = end
            continue

        stub = STUB_FMT.format(name=name)
        out = out[:m.start()] + ("\n" if m.group(1) == "" else m.group(1)) + stub + out[end:]
        changes.append(f"Patch: def {name}() -> puente a db.get_connection()")
        idx = (m.start() + len(stub) + 1)

    return out, changes

def main():
    root = Path(".").resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    backup_dir = root / f"_backup_fix_conn_{timestamp}"
    touched = 0
    total_changes = 0

    for p in root.rglob("*.py"):
        if p.name in EXCLUDE_FILES:
            continue
        if any(d in p.parts for d in EXCLUDE_DIRS):
            continue

        src = p.read_text(encoding="utf-8", errors="ignore")
        new_src, changes = patch_text(src)
        if changes:
            backup_dir.mkdir(exist_ok=True)
            (backup_dir / p.name).write_text(src, encoding="utf-8")
            p.write_text(new_src, encoding="utf-8")
            print(f"\n=== {p} ===")
            for c in changes:
                print(" -", c)
            touched += 1
            total_changes += len(changes)

    print("\nResumen:")
    print(f"  Archivos modificados: {touched}")
    print(f"  Cambios aplicados:    {total_changes}")
    print(f"  Backup:               {backup_dir if touched else 'n/a'}")

if __name__ == "__main__":
    main()
