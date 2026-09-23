import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from core.xml_runtime import (
    XMLGenerationRequest,
    prepare_xml_workspace,
)

from core.xml_result import (
    EntityRecord,
    XMLGenerationResult,
)

from core.global_id_registry import GlobalIdRegistry

logger = logging.getLogger(__name__)

class XMLGenerator:
    """
    单个 XML 脚本生成器。

    一个 XMLGenerator 实例只负责生成一个 XML 文件。
    多个 XML 文件之间通过同一个 GlobalIdRegistry 共享实体 ID。
    """

    def __init__(
        self,
        xml_id: str,
        global_id_registry: GlobalIdRegistry,
        *,
        chunk_mapping_path: str,
        entity_map_file: str,
        json_chunk_path: str,
        xml_chunk_path: str,
        final_xml_path: str,
        prompt_path: str,
        ex_info_path: str,
        entity_id_map_file: str | None = None,
    ):
        self.xml_id = xml_id
        self.registry = global_id_registry

        self.chunk_mapping_path = str(chunk_mapping_path)
        self.entity_map_file = str(entity_map_file)
        self.json_chunk_path = str(json_chunk_path)
        self.xml_chunk_path = str(xml_chunk_path)
        self.final_xml_path = str(final_xml_path)
        self.prompt_path = str(prompt_path)
        self.ex_info_file = str(ex_info_path)

        self.entity_id_map_file = (
            str(entity_id_map_file)
            if entity_id_map_file
            else str(
                Path(self.final_xml_path).parent
                / "entity_id_map.json"
            )
        )

        self.id_map: dict[str, Any] = {}
        self._errors: list[str] = []
        self._warnings: list[str] = []

        self._prepare_directories()

    def _prepare_directories(self) -> None:
        """
        创建当前 XML 脚本需要的目录。

        final_xml_path 是文件路径，因此这里只创建它的父目录。
        """

        Path(self.chunk_mapping_path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.entity_map_file).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.json_chunk_path).mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.xml_chunk_path).mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.prompt_path).mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.final_xml_path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(self.ex_info_file).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def _load_chunk_mapping(self) -> dict[str, dict[str, Any]]:
        """
        读取当前 XML 脚本的微分块映射表。

        兼容两种格式：

        1. 字典格式：
           {
               "units:Aircraft:0": {...}
           }

        2. 列表格式：
           [
               {
                   "chunk_id": "units:Aircraft:0",
                   ...
               }
           ]
        """

        mapping_path = Path(self.chunk_mapping_path)

        if not mapping_path.exists():
            self._warnings.append(
                f"chunk mapping 不存在: {mapping_path}"
            )
            return {}

        try:
            data = json.loads(
                mapping_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            self._errors.append(
                f"chunk mapping JSON 无法解析: {exc}"
            )
            return {}

        if isinstance(data, dict):
            normalized: dict[str, dict[str, Any]] = {}

            for chunk_id, item in data.items():
                if not isinstance(item, dict):
                    continue

                item.setdefault("chunk_id", chunk_id)
                item.setdefault("xml_id", self.xml_id)
                normalized[chunk_id] = item

            return normalized

        if isinstance(data, list):
            normalized = {}

            for index, item in enumerate(data):
                if not isinstance(item, dict):
                    continue

                chunk_id = (
                    item.get("chunk_id")
                    or item.get("instance_id")
                    or f"{self.xml_id}:chunk:{index}"
                )

                item.setdefault("chunk_id", chunk_id)
                item.setdefault("xml_id", self.xml_id)
                normalized[chunk_id] = item

            return normalized

        self._warnings.append(
            "chunk mapping 必须是 JSON 对象或数组"
        )
        return {}

    def _count_chunk_states(
        self,
        chunk_mapping: dict[str, dict[str, Any]],
    ) -> tuple[int, int, int]:
        """
        统计成功、fallback、失败微分块数量。

        兼容常见状态字段：
        status / state / success / fallback
        """

        success_count = 0
        fallback_count = 0
        failed_count = 0

        for item in chunk_mapping.values():
            status = str(
                item.get("status")
                or item.get("state")
                or ""
            ).lower()

            if item.get("fallback") is True:
                fallback_count += 1

            if item.get("success") is False:
                failed_count += 1
                continue

            if status in {
                "failed",
                "failure",
                "error",
            }:
                failed_count += 1
                continue

            if status in {
                "fallback",
                "default",
            }:
                fallback_count += 1
                success_count += 1
                continue

            if status in {
                "success",
                "succeeded",
                "ok",
                "generated",
            }:
                success_count += 1
                continue

            # 如果旧映射没有状态字段，默认认为该分块已生成。
            if not status and item.get("content") is not None:
                success_count += 1
                continue

            if not status:
                success_count += 1
                continue

            failed_count += 1

        return success_count, fallback_count, failed_count

    def _collect_defined_entities(
        self,
        chunk_mapping: dict[str, dict[str, Any]],
    ) -> dict[str, EntityRecord]:
        """
        收集当前 XML 脚本定义的实体。
        """

        defined: dict[str, EntityRecord] = {}

        for chunk_id, item in chunk_mapping.items():
            biz_id = (
                item.get("biz_id")
                or item.get("entity_id")
                or item.get("global_id")
            )

            if not biz_id:
                continue

            entity_type = (
                item.get("entity_type")
                or item.get("semantic_type")
                or ""
            )

            display_name = (
                item.get("display_name")
                or item.get("name")
                or ""
            )

            entity = EntityRecord(
                biz_id=str(biz_id),
                entity_type=str(entity_type),
                display_name=str(display_name),
                owner_document=self.xml_id,
                source_json_path=item.get("full_json_path"),
                source_chunk_id=chunk_id,
            )

            defined[str(biz_id)] = entity

            # 写入包级注册表。
            self.registry.ensure(
                biz_id=str(biz_id),
                entity_type=str(entity_type),
                display_name=str(display_name),
                owner_document=self.xml_id,
                source_json_path=item.get("full_json_path") or "",
            )

        return defined

    def _collect_referenced_entities(
        self,
        chunk_mapping: dict[str, dict[str, Any]],
    ) -> dict[str, list[EntityRecord]]:
        """
        收集当前 XML 脚本引用的外部实体。
        """

        references: dict[str, list[EntityRecord]] = {}

        for chunk_id, item in chunk_mapping.items():
            raw_refs = item.get("references", [])

            if not isinstance(raw_refs, list):
                continue

            for ref in raw_refs:
                if not isinstance(ref, dict):
                    continue

                target_id = (
                    ref.get("biz_id")
                    or ref.get("entity_id")
                    or ref.get("global_id")
                )

                if not target_id:
                    continue

                target_id = str(target_id)

                entity = EntityRecord(
                    biz_id=target_id,
                    entity_type=str(
                        ref.get("entity_type")
                        or ref.get("semantic_type")
                        or ""
                    ),
                    display_name=str(
                        ref.get("display_name")
                        or ref.get("name")
                        or ""
                    ),
                    owner_document=ref.get("owner_document"),
                    source_json_path=item.get("full_json_path"),
                    source_chunk_id=chunk_id,
                )

                references.setdefault(target_id, []).append(entity)

        return references

    def _find_unresolved_references(
        self,
        referenced_entities: dict[str, list[EntityRecord]],
    ) -> list[str]:
        """
        检查当前 XML 引用的实体是否已经存在于全局注册表。
        """

        unresolved: list[str] = []

        for biz_id in referenced_entities:
            if not self.registry.contains(biz_id):
                unresolved.append(biz_id)

        return unresolved

    def _get_errors(self) -> list[str]:
        return list(self._errors)

    def _get_warnings(self) -> list[str]:
        return list(self._warnings)

    def _write_ex_info(
        self,
        start_ts: float,
        result: XMLGenerationResult,
    ) -> None:
        elapsed = time.time() - start_ts

        total = result.total_chunk_count
        success_ratio = (
            result.success_chunk_count / total
            if total > 0
            else 0.0
        )

        content = (
            f"xml_id: {self.xml_id}\n"
            f"总耗时: {elapsed:.2f} 秒\n"
            f"总分块数: {total}\n"
            f"成功分块数: {result.success_chunk_count}\n"
            f"fallback 分块数: {result.fallback_chunk_count}\n"
            f"失败分块数: {result.failed_chunk_count}\n"
            f"生成成功比例: {success_ratio:.4f}\n"
        )

        Path(self.ex_info_file).write_text(
            content,
            encoding="utf-8",
        )

    async def generate(
        self,
        scenario: str,
    ) -> XMLGenerationResult:
        """
        生成一个 XML 脚本并返回结构化结果。

        这里的 _generate_as_before() 需要替换成你原来已有的
        XML 图构建、分块、LLM 生成和组装流程。
        """

        start_ts = time.time()

        result = XMLGenerationResult(
            xml_id=self.xml_id,
            output_xml_path=self.final_xml_path,
            chunk_mapping_path=self.chunk_mapping_path,
        )

        try:
            # 1. 执行原有 XML 生成流程。
            final_xml = await self._run_original_generation(scenario)

            if not isinstance(final_xml, str):
                raise TypeError(
                    "_run_original_generation() 必须返回 XML 字符串"
                )

            # 2. 写入最终 XML 文件。
            Path(self.final_xml_path).write_text(
                final_xml,
                encoding="utf-8",
            )

            # 3. 读取分块映射。
            chunk_mapping = self._load_chunk_mapping()

            result.total_chunk_count = len(chunk_mapping)

            (
                result.success_chunk_count,
                result.fallback_chunk_count,
                result.failed_chunk_count,
            ) = self._count_chunk_states(chunk_mapping)

            # 4. 收集本文件定义的实体。
            result.defined_entities = (
                self._collect_defined_entities(chunk_mapping)
            )

            # 5. 收集本文件引用的实体。
            result.referenced_entities = (
                self._collect_referenced_entities(chunk_mapping)
            )

            # 6. 检查未解析引用。
            result.unresolved_refs = (
                self._find_unresolved_references(
                    result.referenced_entities
                )
            )

            if result.unresolved_refs:
                result.warnings.append(
                    "存在尚未在全局注册表中找到的引用: "
                    + ", ".join(result.unresolved_refs)
                )

            result.errors.extend(self._get_errors())
            result.warnings.extend(self._get_warnings())

            result.success = (
                result.failed_chunk_count == 0
                and not result.errors
            )

        except Exception as exc:
            logger.exception(
                "XML 脚本生成失败: %s",
                self.xml_id,
            )

            result.success = False
            result.errors.append(str(exc))

        finally:
            self.registry.save()
            self._write_ex_info(start_ts, result)

        return result

    async def _run_original_generation(
        self,
        scenario: str,
    ) -> str:
        """
        原有生成流程适配入口。

        你把原来 generate() 中这部分逻辑移动到这里：

            1. build graph
            2. chunk XML/JSON
            3. LLM generate
            4. assemble
            5. 返回完整 XML 字符串

        注意：
        这里不能再使用 asyncio.run()，
        因为当前 generate() 本身已经运行在事件循环中。
        """

        generated = self._generate_as_before(scenario)

        if asyncio.iscoroutine(generated):
            generated = await generated

        return generated

    def _generate_as_before(
        self,
        scenario: str,
    ) -> str:
        """
        临时占位方法。

        请将你原来的 XML 生成逻辑放入这里。
        当前方法只是为了让这个简化版接口结构完整。
        """

        raise NotImplementedError(
            "请将原有 XML 生成流程移动到 "
            "XMLGenerator._generate_as_before()"
        )

async def main() -> None:
    output_root = "output/mozi_run_001"

    scenario = ""

    request = XMLGenerationRequest(
        xml_id="mozi_scenario",
        platform_id="mozi",

        # 注意：
        # 这里应该是 XML 模板路径，不是最终输出路径。
        xml_template_path="templates/mozi_scenario.xml",

        json_input_path="input/mozi_scenario.json",
        workspace_root=output_root,

        global_id_registry_path=(
            f"{output_root}/common/entity_registry.json"
        ),
        reference_registry_path=(
            f"{output_root}/common/reference_registry.json"
        ),
    )

    paths = prepare_xml_workspace(request)

    registry = GlobalIdRegistry(
        paths.global_id_registry_path
    )

    generator = XMLGenerator(
        xml_id=request.xml_id,
        global_id_registry=registry,

        chunk_mapping_path=paths.chunk_mapping_path,
        entity_map_file=paths.entity_map_path,
        json_chunk_path=paths.json_chunk_path,
        xml_chunk_path=paths.xml_chunk_path,
        prompt_path=paths.prompt_path,
        ex_info_path=paths.ex_info_path,

        # 这里必须使用最终 XML 文件路径。
        final_xml_path=paths.output_xml_path,
    )

    result = await generator.generate(scenario)

    print("生成完成")
    print(f"xml_id: {result.xml_id}")
    print(f"success: {result.success}")
    print(f"output_xml_path: {result.output_xml_path}")
    print(f"chunk_mapping_path: {result.chunk_mapping_path}")
    print(f"defined_entities: {len(result.defined_entities)}")
    print(
        "referenced_entities: "
        f"{len(result.referenced_entities)}"
    )

    if result.warnings:
        print("warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")

    if result.errors:
        print("errors:")
        for error in result.errors:
            print(f"  - {error}")

if __name__ == "__main__":
    asyncio.run(main())