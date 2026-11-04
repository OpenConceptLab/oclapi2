import inspect

from django.conf import settings
from django.core.checks import Error, register
from django.utils.module_loading import import_string

from core.capabilities.policy import CapabilityPolicy


def takes(function, *args, **kwargs):
    try:
        inspect.signature(function).bind(*args, **kwargs)
    except TypeError:
        return False
    return True


def get_arguments(interface):
    """What a call of this CapabilityPolicy method passes: required arguments by position, the others by name."""
    args, kwargs = [], {}
    for name, param in list(inspect.signature(interface).parameters.items())[1:]:  # after `self`
        if param.kind == param.VAR_KEYWORD:
            kwargs['any_other_keyword'] = None
        elif param.default is param.empty:
            args.append(None)
        else:
            kwargs[name] = None
    return args, kwargs


def get_misfits(policy):
    """What can't take the arguments it's called with. Reads signatures only: a policy's __init__ doesn't run here."""
    bare = object.__new__(policy)
    misfits = [] if takes(policy) else ['__init__']
    for name, interface in inspect.getmembers(CapabilityPolicy, inspect.isfunction):
        args, kwargs = get_arguments(interface)
        if not takes(getattr(bare, name), *args, **kwargs):
            misfits.append(name)
    return misfits


@register()
def check_capability_policy(**_):
    """At start-up (`migrate` runs the checks): a policy that can't be used would fail every use of a capability."""
    path = settings.CAPABILITY_POLICY
    try:
        policy = import_string(path)
    except Exception as ex:  # a module that imports but fails (a RuntimeError from a model, say) counts too
        return [Error(f'CAPABILITY_POLICY {path!r} does not import: {ex}', id='capabilities.E001')]
    if not (isinstance(policy, type) and issubclass(policy, CapabilityPolicy)):
        return [Error(f'CAPABILITY_POLICY {path!r} is not a subclass of CapabilityPolicy', id='capabilities.E002')]
    if inspect.isabstract(policy):
        missing = ', '.join(sorted(policy.__abstractmethods__))
        return [Error(f'CAPABILITY_POLICY {path!r} does not implement {missing}', id='capabilities.E003')]
    misfits = get_misfits(policy)
    if misfits:
        return [Error(
            f'CAPABILITY_POLICY {path!r} can\'t take the arguments of {", ".join(misfits)}', id='capabilities.E004'
        )]
    return []
