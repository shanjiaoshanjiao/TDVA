from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from core.xml_result import XMLGenerationResult

@dataclass
class PackageGenerationRequest:
    """
    多 XML 文档包的公共配置。

    一个 package 可以包含多个 XML：
        units.xml
        missions.xml
        deployments.xml
        ...

    这里只负责任务组织，不负责 LLM 生成逻辑。
    """

    package_id: str
    workspace_root: str

    platform_id: str = "unknown"

    scenario: str = ""

    global_id_registry_path: Optional[str] = None
    reference_registry_path: Optional[str] = None

    xml_ids: list = field(default_factory=list)

    def common_path(self) -> Path:
        return Path(self.workspace_root) / "common"

    def report_path(self) -> Path:
        return Path(self.workspace_root) / "package_report.json"

    def resolve_registry_path(self) -> str:
        if self.global_id_registry_path:
            return str(self.global_id_registry_path)

        return str(self.common_path() / "entity_registry.json")

    def resolve_reference_registry_path(self) -> str:
        if self.reference_registry_path:
            return str(self.reference_registry_path)

        return str(self.common_path() / "reference_registry.json")

    def ensure_common_dirs(self) -> None:
        self.common_path().mkdir(parents=True, exist_ok=True)

        Path(self.resolve_registry_path()).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.resolve_reference_registry_path()).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.report_path().parent.mkdir(
            parents=True,
            exist_ok=True,
        )

@dataclass
class PackageValidationResult:
    """
    多 XML 文档生成后的跨文件校验结果。
    """

    package_id: str
    success: bool = False

    xml_ids: list = field(default_factory=list)

    xml_results: dict = field(default_factory=dict)

    defined_owner_index: dict = field(default_factory=dict)
    reference_index: dict = field(default_factory=dict)

    unresolved_references: dict = field(default_factory=dict)
    duplicate_definitions: list = field(default_factory=list)

    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "package_id": self.package_id,
            "success": self.success,
            "xml_ids": self.xml_ids,

            "xml_results": {
                xml_id: (
                    asdict(result)
                    if hasattr(result, "__dataclass_fields__")
                    else str(result)
                )
                for xml_id, result in self.xml_results.items()
            },

            "defined_owner_index": self.defined_owner_index,

            "reference_index": {
                key: sorted(list(value))
                for key, value in self.reference_index.items()
            },

            "unresolved_references": self.unresolved_references,
            "duplicate_definitions": self.duplicate_definitions,
            "errors": self.errors,
            "warnings": self.warnings,
        }

def registry_contains(registry, biz_id: str) -> bool:
    """
    兼容不同版本的 GlobalIdRegistry。
    """

    if hasattr(registry, "contains"):
        return bool(registry.contains(biz_id))

    if hasattr(registry, "get"):
        return registry.get(biz_id) is not None

    records = getattr(registry, "records", {})
    return biz_id in records

def get_reference_records(result: XMLGenerationResult):
    """
    从 XMLGenerationResult 中统一提取引用记录。

    兼容两种字段名：
        referenced_entities
        references
    """

    return getattr(result, "referenced_entities", {})

def validate_package(
    package_id: str,
    results: list[XMLGenerationResult],
    registry,
) -> PackageValidationResult:
    """
    多 XML 跨文件校验。

    本阶段只做三件事：

    1. 每个 XML 定义了哪些 biz_id
    2. 每个 XML 引用了哪些 biz_id
    3. 被引用的 biz_id 是否存在于全局 registry 中
    """

    report = PackageValidationResult(
        package_id=package_id,
    )

    defined_owner: dict[str, str] = {}
    references_by_target: dict[str, set] = defaultdict(set)

    results_by_id = {}

    for result in results:
        results_by_id[result.xml_id] = result
        report.xml_ids.append(result.xml_id)

        for error in result.errors:
            report.errors.append(f"{result.xml_id}: {error}")

        for warning in result.warnings:
            report.warnings.append(f"{result.xml_id}: {warning}")

        # 1. 收集本文件定义的实体
        for biz_id, entity in result.defined_entities.items():
            owner = entity.owner_xml or result.xml_id

            old_owner = defined_owner.get(biz_id)

            if old_owner and old_owner != owner:
                report.duplicate_definitions.append(
                    f"{biz_id}: first={old_owner}, duplicate={owner}"
                )
                continue

            defined_owner[biz_id] = owner

        # 2. 收集本文件引用的实体
        referenced_entities = get_reference_records(result)

        for biz_id, refs in referenced_entities.items():
            references_by_target[biz_id].add(result.xml_id)

            source_doc = result.xml_id

            # 如果引用记录里明确写了 owner_xml，可以使用它
            for ref in refs:
                if getattr(ref, "owner_xml", None):
                    source_doc = ref.owner_xml
                    break

            if not registry_contains(registry, biz_id):
                report.unresolved_references.setdefault(
                    source_doc,
                    [],
                )

                if biz_id not in report.unresolved_references[source_doc]:
                    report.unresolved_references[source_doc].append(biz_id)

    report.xml_results = results_by_id
    report.defined_owner_index = defined_owner

    report.reference_index = {
        biz_id: owners
        for biz_id, owners in references_by_target.items()
    }

    # 成功条件：
    # 1. 所有 XML 自身成功
    # 2. 没有失败微分块
    # 3. 没有未解析引用
    # 4. 没有重复定义
    # 5. 没有包级 error
    all_docs_success = all(
        result.success
        and result.failed_chunk_count == 0
        for result in results
    )

    report.success = (
        all_docs_success
        and not report.unresolved_references
        and not report.duplicate_definitions
        and not report.errors
    )

    return report

def write_package_report(
    package_request: PackageGenerationRequest,
    report: PackageValidationResult,
) -> str:
    """
    写 package_report.json
    """

    import json

    output_path = Path(package_request.report_path())
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    return str(output_path)