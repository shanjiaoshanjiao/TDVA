import asyncio
import json
from pathlib import Path

from core.xml_runtime import (
    XMLGenerationRequest,
    prepare_xml_workspace,
)

from core.xml_result import (
    EntityRecord,
    XMLGenerationResult,
)

from core.global_id_registry import GlobalIdRegistry

from core.package_runtime import (
    PackageGenerationRequest,
    validate_package,
    write_package_report,
)

def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    Path(path).write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

async def fake_generate_units(
    request: XMLGenerationRequest,
    registry: GlobalIdRegistry,
) -> XMLGenerationResult:
    """
    模拟 units.xml：定义一个 aircraft entity。
    """

    paths = prepare_xml_workspace(request)

    result = XMLGenerationResult(
        xml_id=request.xml_id,
        output_xml_path=paths.output_xml_path,
        chunk_mapping_path=paths.chunk_mapping_path,
    )

    unit_id = "hsfw-dataaircraft-000000001"

    registry.ensure(
        biz_id=unit_id,
        entity_type="dataaircraft",
        display_name="F-16CJ型战斗机 #1",
        owner_xml="units",
        source_json_path="$.ActiveUnits.Aircraft[0]",
    )

    registry.save()

    xml_text = f"""<?xml version="1.0" encoding="utf-8"?>
<Units xml_id="units">
    <ActiveUnits>
        <Aircraft>
            <GUID>{unit_id}</GUID>
            <Name>F-16CJ型战斗机 #1</Name>
            <Lon>16.96</Lon>
            <Lat>52.33</Lat>
            <Side>蓝方</Side>
        </Aircraft>
    </ActiveUnits>
</Units>
"""

    Path(paths.output_xml_path).write_text(
        xml_text,
        encoding="utf-8",
    )

    write_json(
        paths.chunk_mapping_path,
        [
            {
                "chunk_id": "units:Aircraft:0",
                "xml_id": "units",
                "node_id": "Aircraft",
                "parent_node_id": "ActiveUnits",
                "list_index": 0,
                "full_json_path": "$.ActiveUnits.Aircraft[0]",
                "node_type": "entity_def",
                "entity_type": "dataaircraft",
                "biz_id": unit_id,
                "display_name": "F-16CJ型战斗机 #1",
                "status": "success",
                "references": [],
            }
        ],
    )

    result.defined_entities[unit_id] = EntityRecord(
        biz_id=unit_id,
        entity_type="dataaircraft",
        display_name="F-16CJ型战斗机 #1",
        owner_xml="units",
        source_json_path="$.ActiveUnits.Aircraft[0]",
        source_chunk_id="units:Aircraft:0",
    )

    result.total_chunk_count = 1
    result.success_chunk_count = 1
    result.fallback_chunk_count = 0
    result.failed_chunk_count = 0
    result.success = True

    return result

async def fake_generate_missions(
    request: XMLGenerationRequest,
    registry: GlobalIdRegistry,
) -> XMLGenerationResult:
    """
    模拟 missions.xml：定义一个 mission entity，并引用 units.xml 里的 aircraft id。
    """

    paths = prepare_xml_workspace(request)

    result = XMLGenerationResult(
        xml_id=request.xml_id,
        output_xml_path=paths.output_xml_path,
        chunk_mapping_path=paths.chunk_mapping_path,
    )

    mission_id = "hsfw-mission-000000001"
    assigned_unit_id = "hsfw-dataaircraft-000000001"

    registry.ensure(
        biz_id=mission_id,
        entity_type="data-mission",
        display_name="掩护运输船队",
        owner_xml="missions",
        source_json_path="$.Missions.Mission[0]",
    )

    registry.save()

    xml_text = f"""<?xml version="1.0" encoding="utf-8"?>
<Missions xml_id="missions">
    <Mission>
        <GUID>{mission_id}</GUID>
        <Name>掩护运输船队</Name>
        <AssignedUnitGUID>{assigned_unit_id}</AssignedUnitGUID>
    </Mission>
</Missions>
"""

    Path(paths.output_xml_path).write_text(
        xml_text,
        encoding="utf-8",
    )

    write_json(
        paths.chunk_mapping_path,
        [
            {
                "chunk_id": "missions:Mission:0",
                "xml_id": "missions",
                "node_id": "Mission",
                "parent_node_id": "Missions",
                "list_index": 0,
                "full_json_path": "$.Missions.Mission[0]",
                "node_type": "entity_def",
                "entity_type": "data-mission",
                "biz_id": mission_id,
                "display_name": "掩护运输船队",
                "status": "success",
                "references": [],
            },
            {
                "chunk_id": "missions:AssignedUnitGUID:0",
                "xml_id": "missions",
                "node_id": "AssignedUnitGUID",
                "parent_node_id": "Mission",
                "list_index": 0,
                "full_json_path": "$.Missions.Mission[0].AssignedUnitGUID",
                "node_type": "entity_ref",
                "entity_type": "dataaircraft",
                "biz_id": None,
                "display_name": "",
                "status": "success",
                "references": [
                    {
                        "role": "assigned_unit",
                        "biz_id": assigned_unit_id,
                        "entity_type": "dataaircraft",
                        "owner_xml": "units",
                    }
                ],
            }
        ],
    )

    result.defined_entities[mission_id] = EntityRecord(
        biz_id=mission_id,
        entity_type="data-mission",
        display_name="掩护运输船队",
        owner_xml="missions",
        source_json_path="$.Missions.Mission[0]",
        source_chunk_id="missions:Mission:0",
    )

    result.referenced_entities[assigned_unit_id] = [
        EntityRecord(
            biz_id=assigned_unit_id,
            entity_type="dataaircraft",
            display_name="F-16CJ型战斗机 #1",
            owner_xml="units",
            source_json_path="$.Missions.Mission[0].AssignedUnitGUID",
            source_chunk_id="missions:AssignedUnitGUID:0",
        )
    ]

    result.total_chunk_count = 2
    result.success_chunk_count = 2
    result.fallback_chunk_count = 0
    result.failed_chunk_count = 0
    result.success = True

    return result

async def main():
    # 清理旧 demo 输出，避免历史文件干扰
    workspace_root = "output/stage3_two_xml_package_demo"

    if Path(workspace_root).exists():
        import shutil
        shutil.rmtree(workspace_root)

    package_request = PackageGenerationRequest(
        package_id="demo_two_xml",
        workspace_root=workspace_root,
        platform_id="demo_platform",
        scenario="",
    )

    package_request.ensure_common_dirs()

    registry_path = package_request.resolve_registry_path()
    reference_registry_path = package_request.resolve_reference_registry_path()

    registry = GlobalIdRegistry(registry_path)

    units_request = XMLGenerationRequest(
        xml_id="units",
        platform_id="demo_platform",

        # 注意：这里建议用 xml_template_path
        # 如果你的 request 字段名是 xml_output_path，需要确认语义
        xml_template_path="platforms/demo/templates/units.xml",
        json_input_path="platforms/demo/inputs/units.json",

        workspace_root=workspace_root,
        global_id_registry_path=registry_path,
        reference_registry_path=reference_registry_path,
    )

    missions_request = XMLGenerationRequest(
        xml_id="missions",
        platform_id="demo_platform",
        xml_template_path="platforms/demo/templates/missions.xml",
        json_input_path="platforms/demo/inputs/missions.json",

        workspace_root=workspace_root,
        global_id_registry_path=registry_path,
        reference_registry_path=reference_registry_path,
    )

    # 必须先 units，后 missions
    # 因为 missions 引用 units 定义的 aircraft id
    units_result = await fake_generate_units(units_request, registry)
    missions_result = await fake_generate_missions(missions_request, registry)

    report = validate_package(
        package_id=package_request.package_id,
        results=[units_result, missions_result],
        registry=registry,
    )

    report_path = write_package_report(
        package_request=package_request,
        report=report,
    )

    print("package report:", report_path)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())