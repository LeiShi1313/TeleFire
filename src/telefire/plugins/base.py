import inspect
from functools import wraps

from telefire.utils import camel_to_snake


class CommandRegistry:
    def __init__(self):
        self._commands = {}
        self._sources = {}

    def register(self, name, command, *, source, group=None):
        command_path = (*self._normalize_group(group), name)
        existing_source = self._sources.get(command_path)
        if existing_source is not None and existing_source != source:
            raise ValueError(
                f"Command '{self._format_path(command_path)}' is already registered by {existing_source}; "
                f"cannot replace it with {source}"
            )

        node = self._commands
        for segment in command_path[:-1]:
            existing = node.get(segment)
            if existing is None:
                existing = {}
                node[segment] = existing
            elif not isinstance(existing, dict):
                raise ValueError(
                    f"Cannot create command group '{segment}' because a command already "
                    f"exists at '{self._format_path(command_path[:-1])}'"
                )
            node = existing

        existing = node.get(name)
        if isinstance(existing, dict):
            raise ValueError(
                f"Cannot register command '{self._format_path(command_path)}' because that "
                "path is already used as a command group"
            )

        node[name] = command
        self._sources[command_path] = source
        return command

    def register_callable(self, func, *, name=None, group=None):
        command_name = name or func.__name__
        source = f"{func.__module__}.{func.__qualname__}"
        return self.register(command_name, func, source=source, group=group)

    def register_class(self, cls):
        command_group = self._resolve_command_group(cls)
        command_name = self._resolve_command_name(
            cls.command_name if hasattr(cls, "command_name") else camel_to_snake(cls.__name__),
            command_group,
        )
        source = f"{cls.__module__}.{cls.__qualname__}"
        return self.register(
            command_name,
            _build_command_wrapper(cls, command_name),
            source=source,
            group=command_group,
        )

    def as_fire_commands(self):
        return self._copy_tree(self._commands)

    def _resolve_command_group(self, cls):
        return getattr(cls, "command_group", None)

    def _resolve_command_name(self, raw_name, group):
        if not isinstance(raw_name, str):
            return raw_name

        normalized_group = self._normalize_group(group)
        if normalized_group:
            prefix = f"{normalized_group[-1]}_"
            if raw_name.startswith(prefix):
                return raw_name.removeprefix(prefix)

        return raw_name

    def _normalize_group(self, group):
        if group is None:
            return ()
        if isinstance(group, str):
            return tuple(part for part in group.split(".") if part)
        return tuple(str(part) for part in group if part)

    def _format_path(self, path):
        return " ".join(path)

    def _copy_tree(self, node):
        copied = {}
        for key, value in node.items():
            copied[key] = self._copy_tree(value) if isinstance(value, dict) else value
        return copied


command_registry = CommandRegistry()


def _iter_command_parameters(signature):
    for index, parameter in enumerate(signature.parameters.values()):
        if index == 0 and parameter.name == "self":
            continue
        yield parameter


def _build_command_signature(cls):
    call_signature = inspect.signature(cls.__call__)
    init_signature = inspect.signature(cls.__init__)

    call_parameters = list(_iter_command_parameters(call_signature))
    call_parameter_names = {parameter.name for parameter in call_parameters}

    init_parameters = []
    for parameter in _iter_command_parameters(init_signature):
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if parameter.name in call_parameter_names:
            continue
        init_parameters.append(
            parameter.replace(kind=inspect.Parameter.KEYWORD_ONLY)
        )

    return inspect.Signature(
        parameters=[*call_parameters, *init_parameters],
        return_annotation=call_signature.return_annotation,
    )


def _build_command_wrapper(cls, command_name):
    signature = _build_command_signature(cls)
    init_signature = inspect.signature(cls.__init__)
    call_parameters = list(_iter_command_parameters(inspect.signature(cls.__call__)))
    init_parameter_names = {
        parameter.name
        for parameter in _iter_command_parameters(init_signature)
        if parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }

    @wraps(cls.__call__)
    def command(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()

        command_values = dict(bound.arguments)
        init_kwargs = {
            name: command_values.pop(name)
            for name in list(command_values)
            if name in init_parameter_names
        }

        call_args = []
        call_kwargs = {}
        for parameter in call_parameters:
            if parameter.name not in command_values:
                continue
            value = command_values[parameter.name]
            if parameter.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                call_args.append(value)
            elif parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                call_args.extend(value)
            elif parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                call_kwargs[parameter.name] = value
            elif parameter.kind == inspect.Parameter.VAR_KEYWORD:
                call_kwargs.update(value)

        return cls(**init_kwargs)(*call_args, **call_kwargs)

    command.__name__ = command_name
    command.__qualname__ = command.__name__
    command.__doc__ = inspect.getdoc(cls.__call__) or inspect.getdoc(cls)
    command.__signature__ = signature
    return command


class PluginMount(type):
    def __init__(cls, name, bases, attrs):
        super(PluginMount, cls).__init__(name, bases, attrs)
        command_registry.register_class(cls)
