import inspect

from django.conf import settings
from django.core.checks import Error, register
from django.utils.module_loading import import_string

from core.capabilities.policy import CapabilityPolicy


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
    return []
