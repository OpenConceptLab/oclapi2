from __future__ import annotations

from typing import Annotated, List, Optional, Union

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class ConceptNameType:
    name: str = strawberry.field(description="Localized text of the concept name.")
    locale: str = strawberry.field(description="Locale/ISO code for the name.")
    type: Optional[str] = strawberry.field(description="Name type (e.g. FULLY_SPECIFIED, SHORT).")
    preferred: bool = strawberry.field(description="Indicates whether this name is preferred for its locale.")
    retired: bool = strawberry.field(description="Indicates whether this name is retired.")


@strawberry.type
class ExternalSourceType:
    """GraphQL metadata for a source referenced by an outbound mapping."""

    uri: Optional[str] = strawberry.field(description="URI identifying the target source.")
    name: Optional[str] = strawberry.field(description="Human-readable name for the target source.")


@strawberry.type
class MappingType:
    map_type: str = strawberry.field(
        name="mapType",
        description="Mapping type (e.g. SAME-AS, NARROWER-THAN).",
    )
    to_source: Optional[ExternalSourceType] = strawberry.field(
        name="toSource",
        description="Metadata about the source/collection the mapping points to.",
    )
    to_code: Optional[str] = strawberry.field(
        name="toCode",
        description="Identifier of the target concept in the mapped source.",
    )
    to_concept_name: Optional[str] = strawberry.field(
        name="toConceptName",
        description="Display name of the target concept when available.",
    )
    sort_weight: Optional[float] = strawberry.field(
        name="sortWeight",
        description="Numeric weight used to order mappings within the same mapping type.",
    )
    comment: Optional[str] = strawberry.field(description="Optional notes attached to the mapping.")


@strawberry.type
class NumericDatatypeDetails:
    units: Optional[str] = strawberry.field(description="Units associated with numeric values.")
    low_absolute: Optional[float] = strawberry.field(
        name="lowAbsolute",
        description="Absolute minimum allowed numeric value.",
    )
    high_absolute: Optional[float] = strawberry.field(
        name="highAbsolute",
        description="Absolute maximum allowed numeric value.",
    )
    low_normal: Optional[float] = strawberry.field(
        name="lowNormal",
        description="Lower bound for the normal range.",
    )
    high_normal: Optional[float] = strawberry.field(
        name="highNormal",
        description="Upper bound for the normal range.",
    )
    low_critical: Optional[float] = strawberry.field(
        name="lowCritical",
        description="Lower bound considered clinically critical.",
    )
    high_critical: Optional[float] = strawberry.field(
        name="highCritical",
        description="Upper bound considered clinically critical.",
    )


@strawberry.type
class CodedDatatypeDetails:
    allow_multiple: Optional[bool] = strawberry.field(
        name="allowMultiple",
        description="Indicates if multiple coded answers can be selected.",
    )


@strawberry.type
class TextDatatypeDetails:
    text_format: Optional[str] = strawberry.field(
        name="textFormat",
        description="Optional format hint for text responses (e.g., paragraph, markdown).",
    )


DatatypeDetails = Annotated[
    Union[NumericDatatypeDetails, CodedDatatypeDetails, TextDatatypeDetails],
    strawberry.union("DatatypeDetails"),
]


@strawberry.type
class DatatypeType:
    name: Optional[str] = strawberry.field(
        description="Datatype associated with the concept (e.g., Text, Numeric)."
    )
    details: Optional[DatatypeDetails] = strawberry.field(
        description="Additional metadata specific to the concept's datatype (when available).",
    )


@strawberry.type
class MetadataType:
    is_set: Optional[bool] = strawberry.field(
        name="isSet",
        description="True when the concept represents a set."
    )
    is_retired: Optional[bool] = strawberry.field(
        name="isRetired",
        description="Retirement status of the concept."
    )
    created_by: Optional[str] = strawberry.field(
        name="createdBy",
        description="Username of the user who created the concept.",
    )
    created_at: Optional[str] = strawberry.field(
        name="createdAt",
        description="Timestamp when the concept was created.",
    )
    updated_by: Optional[str] = strawberry.field(
        name="updatedBy",
        description="Username of the user who last updated the concept.",
    )
    updated_at: Optional[str] = strawberry.field(
        name="updatedAt",
        description="Timestamp of the most recent concept update.",
    )


@strawberry.type
class ConceptType:
    id: strawberry.ID = strawberry.field(
        name="id",
        description="OCL concept record identifier (the database primary key).",
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
    description: Optional[str] = strawberry.field(
        description="Primary description for the concept, resolved from available locales."
    )
    concept_class: Optional[str] = strawberry.field(
        name="conceptClass",
        description="Concept class label recorded on the concept.",
    )
    datatype: Optional[DatatypeType] = strawberry.field(
        description="Datatype information for the concept, including optional details."
    )
    metadata: MetadataType = strawberry.field(
        description="Operational metadata such as status and audit fields."
    )
    extras: JSON = strawberry.field(description="Additional custom metadata attached to the concept.")


@strawberry.type(description="Counts of active, non-retired records in the selected source version.")
class SourceSummaryType:
    """Minimal version-scoped repository summary."""

    active_concepts: int = strawberry.field(description="Number of active, non-retired concepts.", default=0)
    mappings: int = strawberry.field(description="Number of active, non-retired mappings.", default=0)


@strawberry.type(description="Source metadata and statistics for one OCL repository version.")
class SourceType:
    """Selectable source metadata with explicitly requested aggregates."""

    name: Optional[str] = strawberry.field(description="Human-readable source name.", default=None)
    description: Optional[str] = strawberry.field(description="Source description.", default=None)
    canonical_url: Optional[str] = strawberry.field(description="Canonical source URL.", default=None)
    uri: Optional[str] = strawberry.field(
        description="OCL relative URI, e.g. /orgs/CIEL/sources/CIEL/; includes the version for releases.",
        default=None,
    )
    map_types: List[str] = strawberry.field(
        description="Distinct map types used by active, non-retired mappings in this version.", default_factory=list,
    )
    external_sources: List[ExternalSourceType] = strawberry.field(
        description="Visible external target sources referenced by active, non-retired outbound mappings.",
        default_factory=list,
    )
    classes: List[str] = strawberry.field(
        description="Distinct concept classes used by active, non-retired concepts in this version.",
        default_factory=list,
    )
    datatypes: List[str] = strawberry.field(
        description="Distinct datatypes used by active, non-retired concepts in this version.", default_factory=list,
    )
    summary: SourceSummaryType = strawberry.field(
        description="Counts computed only for the selected summary fields.", default_factory=SourceSummaryType,
    )
