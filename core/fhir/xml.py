"""FHIR JSON and XML conversion through the R4B resource models."""
import json
import logging
import types
import typing
from functools import lru_cache

from fhir.resources.R4B import get_fhir_model_class
from fhir_core.types import FhirBase
from lxml import etree
from pydantic import ValidationError
from rest_framework.exceptions import ParseError
from rest_framework.utils.encoders import JSONEncoder

from core.fhir import outcomes
from core.fhir.constants import (
    EMPTY_VALUES, EXTENSION_ELEMENTS, FHIR_NS, REPLACEMENT_CHARACTER, XHTML_NS, XML_FORBIDDEN_CHARACTERS, XML_NAME)

logger = logging.getLogger('oclapi')


def xml_parser():
    """A parser that never fetches or expands entities: a request body is untrusted."""
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True)


def prune(value):
    """FHIR has no empty values: drop None, '', {} and [] at every depth."""
    if isinstance(value, dict):
        items = ((key, prune(item)) for key, item in value.items())
        return {key: item for key, item in items if item not in EMPTY_VALUES}
    if isinstance(value, (list, tuple)):
        return [item for item in (prune(item) for item in value) if item not in EMPTY_VALUES]
    return value


def from_xml(content):
    """FHIR XML -> FHIR JSON, through the R4B model of the root element. Raises ParseError."""
    try:
        root = etree.fromstring(content, parser=xml_parser())
        name = etree.QName(root)
        if name.namespace != FHIR_NS:
            raise ValueError(f'the root element {root.tag} is not a FHIR resource')
        resource = get_fhir_model_class(name.localname).model_validate_xml(content, xmlparser=xml_parser())
    except Exception as ex:
        message = (str(ex).splitlines() or [type(ex).__name__])[0]
        raise ParseError(f'The body is not a valid FHIR XML resource: {message}') from ex
    return json.loads(resource.model_dump_json())


def json_values(data):
    """The data as the JSON response carries it: DRF's encoder writes datetimes, dates, decimals and UUIDs."""
    return json.loads(json.dumps(data, cls=JSONEncoder))


def xml_safe(value):
    """(value, how many characters were replaced): every character XML 1.0 forbids becomes U+FFFD."""
    if isinstance(value, str):
        return XML_FORBIDDEN_CHARACTERS.subn(REPLACEMENT_CHARACTER, value)
    if isinstance(value, dict):
        items = [(xml_safe(key), xml_safe(item)) for key, item in value.items()]
        return {key: item for (key, _), (item, _) in items}, sum(left + right for (_, left), (_, right) in items)
    if isinstance(value, (list, tuple)):
        items = [xml_safe(item) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    return value, 0


def element_type(annotation):
    """(the R4B model of a field's values, or None for a primitive; whether the field repeats)."""
    repeats = False
    while True:
        origin = typing.get_origin(annotation)
        if origin in (typing.Union, types.UnionType):
            annotation = next(arg for arg in typing.get_args(annotation) if arg is not type(None))
        elif origin is list:
            repeats, annotation = True, typing.get_args(annotation)[0]
        elif origin is typing.Annotated:
            annotation = typing.get_args(annotation)[0]
        else:
            break
    is_model = isinstance(annotation, type) and issubclass(annotation, FhirBase)
    return (annotation.get_model_klass() if is_model else None), repeats


@lru_cache(maxsize=None)
def model_elements(model_class):
    """An R4B model's elements by their JSON name: (field name, the model of its values or None, repeats)."""
    return {field.alias or name: (name, *element_type(field.annotation))
            for name, field in model_class.model_fields.items() if name != 'fhir_comments'}


def resource_model(data):
    """The R4B model of a FHIR JSON resource, or None when it isn't one."""
    if not isinstance(data, dict) or not isinstance(data.get('resourceType'), str):
        return None
    try:
        return get_fhir_model_class(data['resourceType'])
    except ValueError:
        return None


@lru_cache(maxsize=1024)
def log_dropped(model_class, key, reason):
    """What the XML leaves out, each kind logged once per process: the content's problem, not the request's."""
    logger.warning('FHIR XML: %s.%s %s', model_class.__name__, key, reason)


def xhtml_markup(text):
    """A narrative div as XHTML markup: the text itself when it is an XHTML div, else the text inside one."""
    try:
        if etree.fromstring(text, parser=xml_parser()).tag == f'{{{XHTML_NS}}}div':
            return text
    except etree.XMLSyntaxError:
        pass
    return etree.tostring(xhtml_div(text), encoding='unicode')


def conform_value(value_model, value):
    """One value of an element, conformed, or None when it doesn't have the element's shape."""
    if value_model is None:
        return None if isinstance(value, (dict, list)) else value
    if not isinstance(value, dict):
        return None
    model_class = resource_model(value) if 'resourceType' in value else value_model  # a resource names its type
    return conform(model_class, value) if model_class else None


def conform(model_class, data):
    """
    FHIR JSON reduced to what the R4 model defines, at every depth. A key R4 doesn't define (OCL's revisionDate) and
    a value of the wrong shape are left out, each kind logged once: that is the content's problem, not the wire
    format's. The JSON response itself is not changed.
    """
    elements, result = model_elements(model_class), {}
    for key, value in data.items():
        if key == 'resourceType':
            result[key] = value
            continue
        if key not in elements:
            log_dropped(model_class, key, 'is an element R4 does not define, so the XML leaves it out')
            continue
        _, value_model, repeats = elements[key]
        if isinstance(value, list) and not repeats:  # OCL's ConceptMap.identifier is a list; R4 allows one
            if len(value) > 1:
                log_dropped(model_class, key, 'repeats, but not in R4, so the XML keeps only the first')
            value = value[:1]
        items = value if isinstance(value, list) else [value]
        kept = [item for item in (conform_value(value_model, item) for item in items) if item is not None]
        if len(kept) < len(items):
            log_dropped(model_class, key, 'has values of a shape R4 does not allow, so the XML leaves them out')
        if key == 'div' and kept:
            kept = [xhtml_markup(kept[0])]
        if kept:
            result[key] = kept if repeats else kept[0]
    return result


def construct(model_class, data):
    """
    A model instance from conformed FHIR JSON, nested models included, built without validation, so the model
    still writes its elements in FHIR's order. For what the model refuses only on a pattern, such as an OCL id
    with '_' or '@', or a code with a trailing space.
    """
    elements, values = model_elements(model_class), {}
    for key, value in data.items():
        if key == 'resourceType':
            continue
        name, value_model, repeats = elements[key]
        items = [construct_value(value_model, item) for item in (value if repeats else [value])]
        values[name] = items if repeats else items[0]
    return model_class.model_construct(**values)


def construct_value(value_model, value):
    if value_model is None:
        return value if isinstance(value, bool) else str(value)  # the writer takes text, and booleans as they are
    return construct(resource_model(value) or value_model, value)


def to_xml(data):
    """
    FHIR JSON -> FHIR XML, always through the R4B model, so in FHIR's element order. It starts from the values the
    JSON response carries. Keys R4 doesn't define are left out; a resource the model refuses on a pattern is built
    without validation; characters XML 1.0 forbids become U+FFFD. Data that isn't a FHIR resource is answered with
    an OperationOutcome.
    """
    if data is None:
        return b''
    data = json_values(data)
    model_class = resource_model(data)
    if model_class is None:
        data = outcomes.operation_outcome(outcomes.issue('not-supported', 'This response has no FHIR XML form.'))
        model_class = resource_model(data)
    data, replaced = xml_safe(data)
    if replaced:
        logger.warning('FHIR XML: %s character(s) XML cannot hold became U+FFFD in a %s', replaced,
                       data['resourceType'])
    data = prune(conform(model_class, prune(data)))
    try:
        instance = model_class.model_validate(data)
    except ValidationError:
        instance = construct(model_class, data)
    try:
        return instance.model_dump_xml()
    except Exception:
        logger.exception('FHIR XML: the %s model could not write it; written element by element', model_class.__name__)
        root = etree.Element(f"{{{FHIR_NS}}}{data['resourceType']}", nsmap={None: FHIR_NS})
        write_xml_children(root, data, resource=True)
        return etree.tostring(root, xml_declaration=True, encoding='utf-8')


def write_xml_children(element, obj, resource=False, extension=False):
    """The last resort writer: element by element, in the data's own order."""
    for key, value in obj.items():
        if key == 'resourceType' or not XML_NAME.match(key):
            continue
        if not resource and (key == 'id' or (extension and key == 'url')) and not isinstance(value, (dict, list)):
            element.set(key, str(value))  # an element's id and an extension's url are XML attributes
        elif key == 'div' and isinstance(value, str):
            element.append(xhtml_div(value))
        else:
            for item in value if isinstance(value, list) else [value]:
                write_xml_element(element, key, item)


def write_xml_element(parent, name, value):
    child = etree.SubElement(parent, f'{{{FHIR_NS}}}{name}')
    if isinstance(value, dict) and value.get('resourceType'):
        inner = etree.SubElement(child, f"{{{FHIR_NS}}}{value['resourceType']}")  # a resource in a container
        write_xml_children(inner, value, resource=True)
    elif isinstance(value, dict):
        write_xml_children(child, value, extension=name in EXTENSION_ELEMENTS)
    else:
        child.set('value', ('true' if value else 'false') if isinstance(value, bool) else str(value))


def xhtml_div(text):
    try:
        div = etree.fromstring(text, parser=xml_parser())
        if div.tag == f'{{{XHTML_NS}}}div':
            return div
    except etree.XMLSyntaxError:
        pass
    div = etree.Element(f'{{{XHTML_NS}}}div', nsmap={None: XHTML_NS})
    div.text = text
    return div
