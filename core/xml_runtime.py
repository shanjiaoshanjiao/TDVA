from dataclasses import dataclass
from pathlib import Path
from typing import Optional

@dataclass
class XMLGenerationRequest:
    """
    描述一次单 XML 文档生成任务。
    """

    xml_id: str
    platform_id: str

    xml_template_path: str
    json_input_path: str

    workspace_root: str

    output_xml_path: Optional[str] = None
    graph_config_path: Optional[str] = None

    global_id_registry_path: Optional[str] = None
    reference_registry_path: Optional[str] = None

@dataclass
class XMLWorkspacePaths:
    """
    当前 XML 文档独立使用的目录和文件路径。
    """

    xml_id: str
    workspace_path: str

    json_chunk_path: str
    xml_chunk_path: str
    prompt_path: str

    chunk_mapping_path: str
    entity_map_path: str
    ex_info_path: str

    final_xml_dir: str
    output_xml_path: str
    global_id_registry_path: str

def prepare_xml_workspace(
    request: XMLGenerationRequest,
) -> XMLWorkspacePaths:
    """
    为一个 XML 文档创建独立工作目录。

    目录结构：

    output/run_001/
      common/
        entity_registry.json
        reference_registry.json

      xmls/
        units/
          json_chunks/
          xml_chunks/
          prompt/
          chunk_mapping.json
          entity_data_map.json
          ex_info.txt
          units.xml
    """

    workspace_path = (
        Path(request.workspace_root)
        / "xmls"
        / request.xml_id
    )

    json_chunk_path = workspace_path / "json_chunks"
    xml_chunk_path = workspace_path / "xml_chunks"
    prompt_path = workspace_path / "prompt"

    final_xml_dir = workspace_path

    if request.output_xml_path:
        output_xml_path = Path(request.output_xml_path)
    else:
        output_xml_path = final_xml_dir / f"{request.xml_id}.xml"

    # 创建当前文档的目录
    json_chunk_path.mkdir(parents=True, exist_ok=True)
    xml_chunk_path.mkdir(parents=True, exist_ok=True)
    prompt_path.mkdir(parents=True, exist_ok=True)
    output_xml_path.parent.mkdir(parents=True, exist_ok=True)

    # 创建包级 common 目录
    if request.global_id_registry_path:
        Path(request.global_id_registry_path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    if request.reference_registry_path:
        Path(request.reference_registry_path).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    return XMLWorkspacePaths(
        xml_id=request.xml_id,
        global_id_registry_path=request.global_id_registry_path,
        workspace_path=str(workspace_path),

        json_chunk_path=str(json_chunk_path),
        xml_chunk_path=str(xml_chunk_path),
        prompt_path=str(prompt_path),

        chunk_mapping_path=str(
            workspace_path / "chunk_mapping.json"
        ),
        entity_map_path=str(
            workspace_path / "entity_data_map.json"
        ),
        ex_info_path=str(
            workspace_path / "ex_info.txt"
        ),

        final_xml_dir=str(final_xml_dir),
        output_xml_path=str(output_xml_path),
    )

def make_xml_chunk_id(
    xml_id: str,
    node_id: str,
    list_index: Optional[int] = None,
) -> str:
    """
    为微分块生成带文档命名空间的 ID。

    示例：

        units:Aircraft:0
        missions:Mission:0
    """

    safe_xml_id = str(xml_id).replace(":", "_")
    safe_node_id = str(node_id).replace(":", "_")

    if list_index is None:
        return f"{safe_xml_id}:{safe_node_id}"

    return f"{safe_xml_id}:{safe_node_id}:{list_index}"