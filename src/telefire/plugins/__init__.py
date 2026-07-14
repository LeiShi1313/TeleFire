import importlib
import pkgutil


_EXCLUDED_MODULES = {"base", "test"}
_LOADED = False


def load_plugins() -> None:
    global _LOADED
    if _LOADED:
        return

    for _, module_name, _ in pkgutil.iter_modules(__path__):
        if module_name.startswith("_") or module_name in _EXCLUDED_MODULES:
            continue
        # Names come only from modules installed inside this package directory.
        # nosemgrep: python.lang.security.audit.non-literal-import.non-literal-import
        importlib.import_module(f"{__name__}.{module_name}")

    _LOADED = True


__all__ = ["load_plugins"]
