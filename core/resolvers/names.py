"""
``names`` resolver: the display name of a code of a Source (version), including post-coordinated codes.

1. Exact first: the whole code is looked up as a concept of the Source (version) and its own name is used.
2. Composition, only when the code is not a concept and the Source declares ``resolvers.names``: the code is split by
   the configured ``replace`` patterns and ``literals``. Each code becomes the name of its concept, each pattern its
   ``substitution`` (where ``{{name}}`` stands for the name of the code or literal right after the pattern) and each
   literal its own text without the delimiters. All or nothing: a missing concept or name gives no name, never a
   partial one.

The resolver knows no terminology; each use case is configuration only, e.g.:
- ICD-11 ``ND13.2&XJ44E/ND13.3&XJ44E``: ``&`` -> `` [{{name}}]``, ``/`` -> `` / ``;
- SNOMED CT ``71388002:{260686004=129304002}``: ``:`` -> `` with ``, ``{`` and ``}`` -> ````, ``=`` -> ``: ``;
- LOINC ``104765-3:{Specimen}=LP7681-2``: literal ``{`` ... ``}``, ``:`` -> ``, ``, ``=`` -> ``: ``.

The result is a computed presentation value: nothing is created or saved. See DOCS_RESOLVERS.md.
"""
from functools import cached_property

from core.resolvers.core import Resolver, check_keys, check_list, check_string

NAME_PLACEHOLDER = '{{name}}'

# Server-side safety limits. The Source configuration can never raise them.
MAX_EXPRESSION_LENGTH = 2048
MAX_EXPRESSION_COMPONENTS = 64

# Limits of the configuration schema.
MAX_REPLACE_RULES = 16
MAX_LITERALS = 4
MAX_PATTERN_LENGTH = 8
MAX_SUBSTITUTION_LENGTH = 32

TOKEN_CODE = 'code'
TOKEN_LITERAL = 'literal'
TOKEN_PATTERN = 'pattern'


def is_lookup_code(code):
    """Only non-blank strings within the expression limit are looked up."""
    return isinstance(code, str) and bool(code.strip()) and len(code) <= MAX_EXPRESSION_LENGTH


class NamesResolver(Resolver):
    """Display names of codes, composed from the names of their parts when the Source configures it."""
    key = 'names'

    @classmethod
    def validate(cls, config, path):
        check_keys(config, path, {'replace', 'literals'}, {'replace'})
        check_list(config['replace'], f'{path}.replace', {'pattern', 'substitution'}, 1, MAX_REPLACE_RULES)
        patterns = set()
        for index, rule in enumerate(config['replace']):
            rule_path = f'{path}.replace[{index}]'
            check_string(rule['pattern'], f'{rule_path}.pattern', 1, MAX_PATTERN_LENGTH, allow_whitespace=False)
            check_string(rule['substitution'], f'{rule_path}.substitution', 0, MAX_SUBSTITUTION_LENGTH)
            if rule['substitution'].count(NAME_PLACEHOLDER) > 1:
                raise ValueError(f'{rule_path}.substitution may use {NAME_PLACEHOLDER} only once')
            if rule['pattern'] in patterns:
                raise ValueError(f'{rule_path}.pattern is repeated')
            patterns.add(rule['pattern'])
        literals = config.get('literals', [])
        check_list(literals, f'{path}.literals', {'start', 'end'}, 0, MAX_LITERALS)
        for index, literal in enumerate(literals):
            for key in ('start', 'end'):
                key_path = f'{path}.literals[{index}].{key}'
                check_string(literal[key], key_path, 1, MAX_PATTERN_LENGTH, allow_whitespace=False)
                if literal[key] in patterns:
                    raise ValueError(f'{key_path} is also a replace pattern')

    @cached_property
    def substitutions(self):
        """``{pattern: substitution}`` of the configuration."""
        return {rule['pattern']: rule['substitution'] for rule in self.config['replace']}

    def tokenize(self, expression):
        """
        ``[(TOKEN_CODE | TOKEN_LITERAL | TOKEN_PATTERN, text), ...]``, or None when a literal is not closed. At each
        position a literal start wins, then the longest pattern; a code is the text in between. Codes and literals
        lose their surrounding whitespace; nothing is reordered or case-changed.
        """
        patterns = sorted(self.substitutions, key=len, reverse=True)
        literals = self.config.get('literals', [])
        tokens, buffer, position = [], [], 0

        def flush():
            code = ''.join(buffer).strip()
            if code:
                tokens.append((TOKEN_CODE, code))
            buffer.clear()

        while position < len(expression):
            literal = next((item for item in literals if expression.startswith(item['start'], position)), None)
            pattern = None if literal else next(
                (item for item in patterns if expression.startswith(item, position)), None)
            if literal:
                flush()
                end = expression.find(literal['end'], position + len(literal['start']))
                if end < 0:
                    return None
                tokens.append((TOKEN_LITERAL, expression[position + len(literal['start']):end].strip()))
                position = end + len(literal['end'])
            elif pattern:
                flush()
                tokens.append((TOKEN_PATTERN, pattern))
                position += len(pattern)
            else:
                buffer.append(expression[position])
                position += 1
        flush()
        return tokens

    def parse(self, expression):
        """
        The tokens of a code that can be composed, or None. It must be within the limits, hold a pattern or a literal,
        and not start or end with a pattern whose substitution has text. Structural only: no clinical validation.
        """
        if len(expression) > MAX_EXPRESSION_LENGTH:
            return None
        tokens = self.tokenize(expression)
        if not tokens or all(kind == TOKEN_CODE for kind, _ in tokens):
            return None
        if sum(kind != TOKEN_PATTERN for kind, _ in tokens) > MAX_EXPRESSION_COMPONENTS:
            return None
        for kind, text in (tokens[0], tokens[-1]):
            if kind == TOKEN_PATTERN and self.substitutions[text]:
                return None
        return tokens

    def render(self, tokens, names):
        """
        Joins the name: a code becomes ``names[code]``, a literal its text, a pattern its substitution, and
        ``{{name}}`` takes the name of the token right after the pattern. None (all or nothing) when a code or literal
        has no name or ``{{name}}`` is not followed by a code or literal.
        """
        def value(token):
            kind, text = token
            return text if kind == TOKEN_LITERAL else names.get(text) if kind == TOKEN_CODE else None

        parts, position = [], 0
        while position < len(tokens):
            kind, text = tokens[position]
            if kind == TOKEN_PATTERN:
                substitution = self.substitutions[text]
                if NAME_PLACEHOLDER in substitution:
                    name = value(tokens[position + 1]) if position + 1 < len(tokens) else None
                    if not name:
                        return None
                    parts.append(substitution.replace(NAME_PLACEHOLDER, name))
                    position += 2
                    continue
                parts.append(substitution)
            else:
                name = value(tokens[position])
                if not name:
                    return None
                parts.append(name)
            position += 1
        return ''.join(parts)

    def part_names(self, tokens):
        """Names of the codes of the tokens whose concept can be a part: active, not retired and named."""
        names = {}
        for kind, text in tokens:
            if kind == TOKEN_CODE:
                concept = self.find_concept(text)
                if concept and not concept['retired'] and concept['name']:
                    names[text] = concept['name']
        return names

    def resolve(self, codes):
        """
        One name (or None) per logical code (never percent-encoded), in the same order and multiplicity. A user
        without view access to the Source (version) gets no name and no concept is queried.
        """
        if not self.has_view_access():
            return [None] * len(codes)

        distinct = {code for code in codes if is_lookup_code(code)}  # a repeated code is processed once
        self.load_concepts(distinct)
        names, to_compose = {}, {}
        for code in distinct:
            concept = self.find_concept(code)
            if concept:  # exact first; a concept without a name is not recomposed
                names[code] = concept['name'] or None
            elif self.config:
                tokens = self.parse(code)
                if tokens:
                    to_compose[code] = tokens

        self.load_concepts({text for tokens in to_compose.values() for kind, text in tokens if kind == TOKEN_CODE})
        for code, tokens in to_compose.items():
            names[code] = self.render(tokens, self.part_names(tokens))
        return [names.get(code) if is_lookup_code(code) else None for code in codes]
