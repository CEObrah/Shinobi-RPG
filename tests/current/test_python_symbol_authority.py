import builtins
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime" / "shinobi_runtime"
_IMPLICIT_MODULE_GLOBALS = {"__file__", "__name__", "__package__", "__spec__", "__loader__", "__builtins__"}


def test_runtime_referenced_globals_are_defined_or_imported():
    """Catch branch-only NameErrors that bytecode compilation cannot detect."""
    builtin_names = set(dir(builtins))
    unresolved = []
    for path in sorted(RUNTIME.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        table = symtable.symtable(source, str(path), "exec")
        module_names = {symbol.get_name() for symbol in table.get_symbols()}

        def walk(scope):
            for symbol in scope.get_symbols():
                name = symbol.get_name()
                if (
                    symbol.is_referenced()
                    and symbol.is_global()
                    and name not in module_names
                    and name not in builtin_names
                    and name not in _IMPLICIT_MODULE_GLOBALS
                ):
                    unresolved.append((str(path.relative_to(ROOT)), scope.get_lineno(), scope.get_name(), name))
            for child in scope.get_children():
                walk(child)

        walk(table)

    assert unresolved == []
