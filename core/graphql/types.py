from __future__ import annotations

from typing import List, Optional

import strawberry


@strawberry.type
class ConceptNameType:
    name: str
    locale: str
    type: Optional[str]
    preferred: bool


@strawberry.type
class ToSourceType:
    url: Optional[str]
    name: Optional[str]


@strawberry.type
class MappingType:
    map_type: str = strawberry.field(name="mapType")
    to_source: Optional[ToSourceType] = strawberry.field(name="toSource")
    to_code: Optional[str] = strawberry.field(name="toCode")
    comment: Optional[str]


@strawberry.type
class ConceptType:
    concept_id: str = strawberry.field(name="conceptId")
    display: Optional[str]
    names: List[ConceptNameType]
    mappings: List[MappingType]
