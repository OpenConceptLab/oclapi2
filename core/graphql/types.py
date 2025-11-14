from __future__ import annotations

from typing import List, Optional

import strawberry


@strawberry.type
class MappingType:
    map_type: str = strawberry.field(name="mapType")
    to_source: Optional[str] = strawberry.field(name="toSource")
    to_code: Optional[str] = strawberry.field(name="toCode")
    comment: Optional[str]


@strawberry.type
class ConceptType:
    concept_id: str = strawberry.field(name="conceptId")
    display: Optional[str]
    mappings: List[MappingType]
