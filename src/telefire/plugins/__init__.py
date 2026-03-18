import sys
import inspect
import importlib
import pkgutil


class Wrapper:
    def __getattr__(self, item):
        return globals().get(item, None)


__all__ = []
for loader, name, is_pkg in pkgutil.walk_packages(__path__, prefix=__name__ + "."):
    try:
        module = importlib.import_module(name)
    except Exception:
        continue
    for attr_name, value in inspect.getmembers(module):
        if attr_name.startswith('__'):
            continue
        globals()[attr_name] = value
        __all__.append(attr_name)
    __all__.append(module)
sys.modules[__name__] = Wrapper()
