import asyncio
import json

from core.global_id_registry import GlobalIdRegistry
from core.package_runtime import (
    PackageGenerationRequest,
    validate_package,
    write_package_report,
)
from core.xml_runtime import (
    XMLGenerationRequest,
    prepare_xml_workspace,
)

from generate_xml import XMLGenerator

async def run_one_document(
    package_request,
    document_request,
    registry,
):
    paths = prepare_xml_workspace(document_request)

    generator = XMLGenerator(
        xml_id=document_request.xml_id,
        global_id_registry=registry,

        chunk_mapping_path=paths.chunk_mapping_path,
        entity_map_file=paths.entity_map_path,
        json_chunk_path=paths.json_chunk_path,
        xml_chunk_path=paths.xml_chunk_path,
        prompt_path=paths.prompt_path,
        ex_info_path=paths.ex_info_path,
        final_xml_path=paths.output_xml_path,
    )

    return await generator.generate(
        package_request.scenario
    )

async def main():
    workspace_root = "output/real_package_run_001"

    package_request = PackageGenerationRequest(
        package_id="target_platform_package",
        workspace_root=workspace_root,
        platform_id="target_platform",
        scenario="",
    )

    package_request.ensure_common_dirs()

    registry = GlobalIdRegistry(
        package_request.resolve_registry_path()
    )

    units_request = XMLGenerationRequest(
        xml_id="units",
        platform_id="target_platform",
        xml_template_path=(
            "platforms/target_platform/templates/units.xml"
        ),
        json_input_path=(
            "platforms/target_platform/inputs/units.json"
        ),
        workspace_root=workspace_root,
        global_id_registry_path=(
            package_request.resolve_registry_path()
        ),
        reference_registry_path=(
            package_request.resolve_reference_registry_path()
        ),
    )

    missions_request = XMLGenerationRequest(
        xml_id="missions",
        platform_id="target_platform",
        xml_template_path=(
            "platforms/target_platform/templates/missions.xml"
        ),
        json_input_path=(
            "platforms/target_platform/inputs/missions.json"
        ),
        workspace_root=workspace_root,
        global_id_registry_path=(
            package_request.resolve_registry_path()
        ),
        reference_registry_path=(
            package_request.resolve_reference_registry_path()
        ),
    )

    results = []

    # 当前先串行，保证依赖定义顺序明确。
    for request in [
        units_request,
        missions_request,
    ]:
        result = await run_one_document(
            package_request=package_request,
            document_request=request,
            registry=registry,
        )

        results.append(result)

        print(
            f"{result.xml_id}: "
            f"success={result.success}, "
            f"defined={len(result.defined_entities)}, "
            f"referenced={len(result.referenced_entities)}"
        )

    report = validate_package(
        package_id=package_request.package_id,
        results=results,
        registry=registry,
    )

    report_path = write_package_report(
        package_request=package_request,
        report=report,
    )

    print("package_report:", report_path)
    print(
        json.dumps(
            report.to_dict(),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )

if __name__ == "__main__":
    asyncio.run(main())