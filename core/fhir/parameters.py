"""Typed FHIR operation parameters from query strings and Parameters resources."""
import re
from collections import namedtuple

from core.fhir import outcomes
from core.fhir.constants import CONTROL_PARAMETERS


def parameter_value(parameter):
    """
    A Parameters.parameter's value: its value[x], resource, or parts (as FhirParameters). R4 allows it one of them, so
    a parameter with more is invalid; a primitive's _value[x] (its id and extensions) is that same value[x].
    """
    given = sorted({key.lstrip('_') for key in parameter if key.lstrip('_').startswith('value')} |
                   ({'resource', 'part'} & parameter.keys()))
    if len(given) > 1:
        raise outcomes.InvalidError(
            f"The parameter '{parameter.get('name')}' has more than one value: {', '.join(given)}.",
            expression=parameter.get('name'))
    for key, value in parameter.items():
        if key.startswith('value'):
            return value
    if 'resource' in parameter:
        return parameter['resource']
    if 'part' in parameter:
        return FhirParameters.from_resource({'parameter': parameter['part']})
    return None


class Coding(namedtuple('Coding', 'system code version display parameter')):
    """A code as an operation is given it. `parameter` is the Coding parameter it came in, None for separate ones."""
    PATHS = {None: '', 'coding': 'Coding.', 'codeableConcept': 'CodeableConcept.coding[0].'}

    def path(self, part):
        """The FHIRPath of a part of the code in an issue: the parameter, or the part of the Coding it came in."""
        return self.PATHS[self.parameter] + part

    def require(self, part, message):
        """The part an operation needs, or a 400 naming the input it's missing from (`part`, or the Coding)."""
        value = getattr(self, part)
        if not value:
            raise outcomes.InvalidError(message, expression=self.parameter or part)
        return value


def coding_of(value, name):
    """
    A Coding input as a Coding: a dict, or in a GET a system|code token, split at its first | as a token search value
    is. Its parts must be text; an absent or empty part is None.
    """
    if isinstance(value, str):
        system, _, code = value.partition('|') if '|' in value else ('', '', value)
        value = {'system': system, 'code': code}
    parts = {key: value.get(key) for key in ('system', 'code', 'version', 'display')} if isinstance(value, dict) else {}
    if not any(parts.values()) or not all(part is None or isinstance(part, str) for part in parts.values()):
        raise outcomes.InvalidError(f"'{name}' must be a Coding.", expression=name)
    return Coding(*(part or None for part in parts.values()), parameter=name)


class FhirParameters:
    """
    An operation's input, read the same way from a GET query string and from a POSTed Parameters resource (FHIR JSON,
    FHIR XML or a form). Names can repeat. Values keep their FHIR JSON form: a string, boolean or number, a dict for
    a Coding, CodeableConcept or resource, and FhirParameters for parts. On a POST, the body's parameters come first,
    then the query string's. Protocol parameters such as _format are left out. Each typed accessor reads the first
    value of a name: None when it's absent or empty, a 400 naming the input when it isn't of the type.
    """

    def __init__(self, items=()):
        self.items = list(items)

    @classmethod
    def from_resource(cls, resource):
        return cls((item.get('name'), parameter_value(item)) for item in resource.get('parameter') or [])

    @classmethod
    def from_request(cls, request):
        items = cls.body_items(request.data) if request.method in ('POST', 'PUT') else []
        return cls(items + cls.query_items(request.query_params))

    @staticmethod
    def query_items(query):
        return [(name, value) for name, values in query.lists() if name not in CONTROL_PARAMETERS for value in values]

    @classmethod
    def body_items(cls, data):
        if not data:
            return []
        if hasattr(data, 'lists'):  # a form
            return cls.query_items(data)
        if isinstance(data, dict) and data.get('resourceType') == 'Parameters':
            return cls.from_resource(data).items
        raise outcomes.InvalidError('The request body must be a Parameters resource.')

    def get(self, name, default=None):
        return next((value for key, value in self.items if key == name), default)

    def getlist(self, name):
        return [value for key, value in self.items if key == name]

    def __contains__(self, name):
        return any(key == name for key, _ in self.items)

    def text(self, name):
        """A text input: a string, code or uri."""
        value = self.get(name)
        if value is None or value == '':
            return None
        if not isinstance(value, str):
            raise outcomes.InvalidError(f"'{name}' must be text.", expression=name)
        return value

    def required_text(self, name, message):
        """A text input the operation needs: absent or empty, a 400 with `message`."""
        value = self.text(name)
        if value is None:
            raise outcomes.InvalidError(message, expression=name)
        return value

    def boolean(self, name):
        """A boolean input: a boolean, or true or false as text, in any case and without padding."""
        value = self.get(name)
        if value is None or value == '':
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ('true', 'false'):
            return value.lower() == 'true'
        raise outcomes.InvalidError(f"'{name}' must be true or false, got '{value}'.", expression=name)

    def whole_number(self, name):
        """A whole-number input: an integer, or up to nine digits as text, without padding."""
        value = self.get(name)
        if value is None or value == '':
            return None
        if isinstance(value, str) and re.fullmatch(r'[0-9]{1,9}', value):
            return int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        raise outcomes.InvalidError(f"'{name}' must be a whole number, got '{value}'.", expression=name)

    def coding(self, name):
        """A Coding input (coding_of)."""
        return coding_of(self.get(name), name)

    def codeable_concept_coding(self, name):
        """
        The one coding of a CodeableConcept input. Several are a 400 not-supported: each is sent on its own, so none
        is left unread.
        """
        concept = self.get(name)
        codings = concept.get('coding') if isinstance(concept, dict) else None
        if not isinstance(codings, list) or not codings:
            raise outcomes.InvalidError(f"'{name}' must hold a coding.", expression=name)
        if len(codings) > 1:
            raise outcomes.NotSupportedError(
                f"A {name} is supported with one coding: send each coding on its own.", expression=name)
        return coding_of(codings[0], name)
