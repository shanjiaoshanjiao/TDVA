from dataclasses import dataclass, field
from typing import Optional

@dataclass
class EntityRecord:
    """
    一个实体或被引用实体的记录。
    """

    biz_id: str
    entity_type: str = ""
    display_name: str = ""

    owner_xml: Optional[str] = None
    source_json_path: Optional[str] = None
    source_chunk_id: Optional[str] = None

@dataclass
class XMLGenerationResult:
    """
    单个 XML 文档生成结果。
    """

    xml_id: str
    success: bool = False

    workspace_path: str = ""
    output_xml_path: str = ""
    chunk_mapping_path: str = ""

    total_chunk_count: int = 0
    success_chunk_count: int = 0
    fallback_chunk_count: int = 0
    failed_chunk_count: int = 0

    defined_entities: dict[str, EntityRecord] = field(
        default_factory=dict
    )

    referenced_entities: dict[str, list[EntityRecord]] = field(
        default_factory=dict
    )

    unresolved_refs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_defined_entity(self, entity: EntityRecord) -> None:
        self.defined_entities[entity.biz_id] = entity

    def add_reference(self, entity: EntityRecord) -> None:
        self.referenced_entities.setdefault(
            entity.biz_id,
            [],
        ).append(entity)

    def to_dict(self) -> dict:
        return {
            "xml_id": self.xml_id,
            "success": self.success,
            "workspace_path": self.workspace_path,
            "output_xml_path": self.output_xml_path,
            "chunk_mapping_path": self.chunk_mapping_path,
            "total_chunk_count": self.total_chunk_count,
            "success_chunk_count": self.success_chunk_count,
            "fallback_chunk_count": self.fallback_chunk_count,
            "failed_chunk_count": self.failed_chunk_count,
            "defined_entities": {
                key: vars(value)
                for key, value in self.defined_entities.items()
            },
            "referenced_entities": {
                key: [vars(item) for item in values]
                for key, values in self.referenced_entities.items()
            },
            "unresolved_refs": self.unresolved_refs,
            "warnings": self.warnings,
            "errors": self.errors,
        }