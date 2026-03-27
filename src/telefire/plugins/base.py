import inspect
from functools import wraps
from telefire.utils import camel_to_snake


class CommandRegistry:
    def __init__(self):
        self._commands = {}
        self._sources = {}

    def register(self, name, command, *, source):
        existing_source = self._sources.get(name)
        if existing_source is not None and existing_source != source:
            raise ValueError(
                f"Command '{name}' is already registered by {existing_source}; "
                f"cannot replace it with {source}"
            )

        self._commands[name] = command
        self._sources[name] = source
        return command

    def register_callable(self, func, *, name=None):
        command_name = name or func.__name__
        source = f"{func.__module__}.{func.__qualname__}"
        return self.register(command_name, func, source=source)

    def register_class(self, cls):
        command_name = (
            cls.command_name if hasattr(cls, "command_name") else camel_to_snake(cls.__name__)
        )
        source = f"{cls.__module__}.{cls.__qualname__}"
        return self.register(command_name, _build_command_wrapper(cls), source=source)

    def as_fire_commands(self):
        return dict(self._commands)


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


def _build_command_wrapper(cls):
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

    command_name = cls.command_name if hasattr(cls, "command_name") else camel_to_snake(cls.__name__)
    command.__name__ = command_name
    command.__qualname__ = command.__name__
    command.__doc__ = inspect.getdoc(cls.__call__) or inspect.getdoc(cls)
    command.__signature__ = signature
    return command


class PluginMount(type):
    def __init__(cls, name, bases, attrs):
        super(PluginMount, cls).__init__(name, bases, attrs)
        command_registry.register_class(cls)
