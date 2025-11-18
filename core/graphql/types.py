from __future__ import annotations

from typing import List, Optional

import strawberry


@strawberry.type
class ConceptNameType:
    name: str = strawberry.field(description="Localized text of the concept name.")
    locale: str = strawberry.field(description="Locale/ISO code for the name.")
    type: Optional[str] = strawberry.field(description="Name type (e.g. FULLY_SPECIFIED, SHORT).")
    preferred: bool = strawberry.field(description="Indicates whether this name is preferred for its locale.")


@strawberry.type
class ToSourceType:
    url: Optional[str] = strawberry.field(description="URL pointing to the target source.")
    name: Optional[str] = strawberry.field(description="Human-readable name for the target source.")


@strawberry.type
class MappingType:
    map_type: str = strawberry.field(
        name="mapType",
        description="Mapping type (e.g. SAME-AS, NARROWER-THAN).",
    )
    to_source: Optional[ToSourceType] = strawberry.field(
        name="toSource",
        description="Metadata about the source/collection the mapping points to.",
    )
    to_code: Optional[str] = strawberry.field(
        name="toCode",
        description="Identifier of the target concept in the mapped source.",
    )
    comment: Optional[str] = strawberry.field(description="Optional notes attached to the mapping.")


@strawberry.type
class ConceptType:
    id: strawberry.ID = strawberry.field(
        name="id",
        description="CIEL concept identifier (mirrors the numeric ID used internally).",
    )
    external_id: Optional[str] = strawberry.field(
        name="externalId",
        description="External identifier stored in OCL (exposed as the concept UUID in some clients).",
    )
    concept_id: str = strawberry.field(
        name="conceptId",
        description="Concept mnemonic (human-readable identifier within the source).",
    )
    display: Optional[str] = strawberry.field(
        description="Preferred display name resolved from the concept locales."
    )
    names: List[ConceptNameType] = strawberry.field(
        description="All localized names configured for the concept."
    )
    mappings: List[MappingType] = strawberry.field(
        description="Mappings originating from this concept."
    )
