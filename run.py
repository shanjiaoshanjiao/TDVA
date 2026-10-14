import re
import os
import json
import time
import copy
from dataclasses import dataclass, field
from threading import Lock
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5


import logging 
from pathlib import Path
import pandas as pd
import traceback
from typing import List, Dict, Any, Tuple, Optional, Union, Set

from docx import Document
from markitdown import MarkItDown

import asyncio
import threading
from asyncio import Lock as AsyncLock
import aiohttp
from openai import OpenAI, AsyncOpenAI, APIError, APIConnectionError

from xml.dom import minidom
import xmlschema
import lxml
from lxml import etree
from py2neo import Graph, NodeMatcher

from lxml import etree as ET
from xml.sax.saxutils import escape

import pymysql
from fuzzywuzzy import fuzz

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)



@dataclass
class GlobalIdRegistry:
    """
    全局实体业务 ID 注册表。

    核心约束：
    1. 同一个 (entity_type, entity_path) 始终返回同一个业务 ID；
    2. 不同实体不允许复用同一个业务 ID；
    3. 支持从映射文件恢复历史 ID，以保证多次运行时 ID 稳定；
    4. 使用线程锁保证并发场景下分配 ID 的安全性。

    映射格式：
    {
        "{entity_type}:{entity_path}": "{entity_id}"
    }
    """

    entity_ids: dict[str, str] = field(default_factory=dict)
    used_ids: set[str] = field(default_factory=set)

    # lock 不参与 dataclass 的比较和打印，也不接受外部传入。
    lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )

    @staticmethod
    def build_key(
        entity_path: str,
        entity_type: str,
    ) -> str:
        """
        构造实体映射的唯一键。

        示例：
        entity_type = "Unit"
        entity_path = "/scenario/blue_force[0]"

        返回：
        "Unit:/scenario/blue_force[0]"
        """
        if not entity_type or not isinstance(entity_type, str):
            raise ValueError(
                f"entity_type 必须是非空字符串，当前值: {entity_type!r}"
            )

        if not entity_path or not isinstance(entity_path, str):
            raise ValueError(
                f"entity_path 必须是非空字符串，当前值: {entity_path!r}"
            )

        return f"{entity_type}:{entity_path}"

    def get_or_create(
        self,
        entity_path: str,
        entity_type: str,
    ) -> str:
        """
        获取实体业务 ID；如果该实体尚未注册，则生成新的 UUID。

        同一个 entity_type + entity_path 的组合，无论调用多少次，
        都会得到完全相同的 ID。
        """
        key = self.build_key(
            entity_path=entity_path,
            entity_type=entity_type,
        )

        with self.lock:
            # 已注册：直接返回，确保同一实体 ID 稳定。
            existing_id = self.entity_ids.get(key)
            if existing_id is not None:
                return existing_id

            # 未注册：生成不与现有 ID 冲突的 UUID。
            entity_id = str(uuid4())
            while entity_id in self.used_ids:
                entity_id = str(uuid4())

            self.entity_ids[key] = entity_id
            self.used_ids.add(entity_id)

            return entity_id

    def register_existing(
        self,
        entity_path: str,
        entity_type: str,
        entity_id: str,
    ) -> str:
        """
        注册一个已经存在的实体业务 ID。

        主要用于从 entity_id_map.json 恢复历史映射，
        使得程序重复执行时同一实体仍然保持同一个 ID。

        异常情况：
        - 同一实体已有不同 ID：报错；
        - 不同实体尝试使用同一 ID：报错。
        """
        key = self.build_key(
            entity_path=entity_path,
            entity_type=entity_type,
        )

        if not entity_id or not isinstance(entity_id, str):
            raise ValueError(
                f"entity_id 必须是非空字符串，当前值: {entity_id!r}"
            )

        with self.lock:
            # 场景 1：当前实体已经注册。
            existing_id = self.entity_ids.get(key)
            if existing_id is not None:
                if existing_id != entity_id:
                    raise ValueError(
                        "同一实体不能注册多个不同 ID。"
                        f"实体: {key}；"
                        f"已有 ID: {existing_id}；"
                        f"尝试注册 ID: {entity_id}"
                    )
                return existing_id

            # 场景 2：该 ID 已经属于另一个实体。
            if entity_id in self.used_ids:
                other_key = next(
                    (
                        registered_key
                        for registered_key, registered_id
                        in self.entity_ids.items()
                        if registered_id == entity_id
                    ),
                    None,
                )

                raise ValueError(
                    "不同实体不能复用同一个业务 ID。"
                    f"已占用实体: {other_key}；"
                    f"当前实体: {key}；"
                    f"冲突 ID: {entity_id}"
                )

            # 场景 3：新实体 + 新 ID，正常注册。
            self.entity_ids[key] = entity_id
            self.used_ids.add(entity_id)

            return entity_id

    def get(
        self,
        entity_path: str,
        entity_type: str,
    ) -> str | None:
        """
        查询已存在的实体业务 ID。

        若实体尚未注册，返回 None；
        不会生成新的 UUID。
        """
        key = self.build_key(
            entity_path=entity_path,
            entity_type=entity_type,
        )

        with self.lock:
            return self.entity_ids.get(key)

    def contains(
        self,
        entity_path: str,
        entity_type: str,
    ) -> bool:
        """
        判断某实体是否已经注册。
        """
        return self.get(
            entity_path=entity_path,
            entity_type=entity_type,
        ) is not None

    def to_dict(self) -> dict[str, str]:
        """
        返回可序列化的映射副本。

        返回副本而不是原始 entity_ids，
        防止调用方意外修改注册表内部状态。
        """
        with self.lock:
            return dict(self.entity_ids)

    def save(
        self,
        path: str | Path,
    ) -> None:
        """
        将 ID 映射保存为 JSON 文件。

        使用临时文件 + os.replace 原子替换，避免程序中断时
        直接破坏已有的 entity_id_map.json。
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = path.with_suffix(
            f"{path.suffix}.tmp"
        )

        with self.lock:
            data = dict(self.entity_ids)

        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

        os.replace(temp_path, path)

    @classmethod
    def load(
        cls,
        path: str | Path,
    ) -> "GlobalIdRegistry":
        """
        从 JSON 映射文件恢复全局实体 ID 注册表。

        若文件不存在，则返回一个空注册表。
        """
        path = Path(path)
        registry = cls()

        if not path.exists():
            return registry

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError(
                f"实体 ID 映射文件必须是 JSON 对象: {path}"
            )

        for key, entity_id in data.items():
            try:
                entity_type, entity_path = key.split(":", 1)
            except ValueError as exc:
                raise ValueError(
                    f"非法 ID 映射键，格式应为 "
                    f"'entity_type:entity_path'，实际为: {key!r}"
                ) from exc

            registry.register_existing(
                entity_path=entity_path,
                entity_type=entity_type,
                entity_id=entity_id,
            )

        return registry


@dataclass(frozen=True)
class Workspace:
    prompt_dir: Path
    json_chunk_dir: Path
    xml_chunk_dir: Path
    final_xml_dir: Path
    chunk_mapping_file: Path
    entity_map_file: Path
    extra_info_file: Path


class WorkspaceManager:
    def __init__(
        self,
        prompt_path: str | Path,
        json_chunk_path: str | Path,
        xml_chunk_path: str | Path,
        final_xml_path: str | Path,
        chunk_mapping_path: str | Path,
        entity_map_file: str | Path,
        ex_info_path: str | Path,
    ) -> None:
        self.workspace = Workspace(
            prompt_dir=Path(prompt_path),
            json_chunk_dir=Path(json_chunk_path),
            xml_chunk_dir=Path(xml_chunk_path),
            final_xml_dir=Path(final_xml_path),
            chunk_mapping_file=Path(chunk_mapping_path),
            entity_map_file=Path(entity_map_file),
            extra_info_file=Path(ex_info_path),
        )

    def prepare(self) -> Workspace:
        """创建目录，并返回本次任务的路径集合。"""
        workspace = self.workspace

        directory_paths = (
            workspace.prompt_dir,
            workspace.json_chunk_dir,
            workspace.xml_chunk_dir,
            workspace.final_xml_dir,
        )

        for path in directory_paths:
            path.mkdir(parents=True, exist_ok=True)

        file_paths = (
            workspace.chunk_mapping_file,
            workspace.entity_map_file,
            workspace.extra_info_file,
        )

        for path in file_paths:
            path.parent.mkdir(parents=True, exist_ok=True)

        return workspace


class Neo4jTemplateService:
    """
    Neo4j XML 模板访问服务。

    职责：
    1. 连接 Neo4j；
    2. 创建查询索引；
    3. 查询 XML 模板图谱；
    4. 将图谱数据转换为模板 fragments。
    """
    def __init__(
        self,
        neo4j_uri: str,
        neo4j_auth: tuple[str, str],
    ) -> None:
        self.neo4j_graph = Graph(
            neo4j_uri,
            auth=neo4j_auth,
        )
        self.matcher = NodeMatcher(self.neo4j_graph)

    def create_indexes(self):
        """创建Neo4j 图数据库索引"""
        try:
            self.neo4j_graph.run("CREATE INDEX node_id_index IF NOT EXISTS FOR (n:Node) ON (n.id)")
            self.neo4j_graph.run("CREATE INDEX field_name_index IF NOT EXISTS FOR (f:Field) ON (f.name)")
            logger.info("数据库索引创建/验证完成")
        except Exception:
            logger.exception(
                "创建 Neo4j 索引失败"
            )
            raise

    def generate_fragments(self) -> List[Dict]:
        """基于 Neo4j 模板图谱生成 XML 模板 fragments"""
        fragments: List[Dict] = []
        self.id_map = {}
        
        try:
            # 1. 查询所有直接子节点关系
            children_map = self._load_children_map()

            # 2. 查询根节点
            root_data = self._load_root_data()

            if root_data is None:
                logger.warning(
                    "未查询到根节点，无法生成 XML 片段"
                )
                return []

            root_fragment = self._build_root_fragment(
                root_data=root_data,
                children_map=children_map,
            )
            
            fragments.append(root_fragment)
            self.id_map[
                root_data["node_id"]
            ] = f"fragment_{root_data['node_id']}.xml"

            logger.info(
                "找到根节点: %s (ID: %s)",
                root_data["node_name"],
                root_data["node_id"],
            )

            # 3. 批量查询普通节点及其属性
            node_ids = [
                fragment["node_id"]
                for fragment in fragments
            ]
            node_ids.extend(
                self._get_all_node_ids()
            )

            nodes = self._load_node_data(
                node_ids=node_ids
            )
            
            # 保留原有的 ID 到名称映射逻辑
            id2name = {
                node["node_id"]: node["node_name"]
                for node in nodes
            }
            id2name[
                root_data["node_id"]
            ] = root_data["node_name"]

            # 4. 构建普通节点片段
            for node in nodes:
                fragment = self._build_node_fragment(
                    node=node,
                    root_data=root_data,
                    children_map=children_map,
                    id2name=id2name,
                    existing_fragments=fragments,
                )

                if fragment is None:
                    continue

                fragments.append(fragment)

                node_id = fragment["node_id"]
                self.id_map[node_id] = (
                    f"fragment_{node_id}.xml"
                )

            logger.info(
                "共生成 %d 个基础 XML 片段",
                len(fragments),
            )

            return fragments
            
        except Exception:
            logger.exception(
                "生成片段失败"
            )
            raise


    def _load_children_map(self) -> Dict[int, List[Dict]]:
        """查询并按父节点 ID 分组直接子节点。"""
        children_query = """
            MATCH (parent)-[:CONTAINS]->(child:Node)
            WHERE parent:Node OR parent:Root_Node
            RETURN id(parent) AS parent_id,
                    id(child) AS child_id,
                    child.name AS child_name
        """

        children_data = (
            self.neo4j_graph
            .run(children_query)
            .data()
        )

        children_map: Dict[int, List[Dict]] = {}

        for item in children_data:
            parent_id = item["parent_id"]

            if parent_id not in children_map:
                children_map[parent_id] = []

            children_map[parent_id].append(
                {
                    "child_id": item["child_id"],
                    "child_name": item["child_name"],
                }
            )

        return children_map

    def _load_root_data(self) -> Optional[Dict]:
        """查询根节点及其字段属性。"""
        root_query = """
            MATCH (root:Root_Node)-[:HAS_FIELD]->(field:Field)
            RETURN root.name AS node_name,
                root.description AS node_des,
                id(root) AS node_id,
                COLLECT({
                    name: field.name,
                    des: field.description,
                    note: field.note,
                    example: field.example
                }) AS attributes
        """

        root_result = (
            self.neo4j_graph
            .run(root_query)
            .data()
        )

        if not root_result:
            return None

        return root_result[0]

    def _build_root_fragment(
        self,
        root_data: Dict,
        children_map: Dict[int, List[Dict]],
    ) -> Dict:
        """根据根节点查询结果构造根 fragment。"""
        root_node_id = root_data["node_id"]

        return {
            "node_id": root_node_id,
            "node_name": root_data["node_name"],
            "node_des": root_data["node_des"],
            "parent_node_id": None,
            "parent_node_name": None,
            "node_type": "Root_Node",
            "attributes": root_data["attributes"],
            "children": children_map.get(
                root_node_id,
                [],
            ),
            "is_root": True,
        }

    def _load_node_data(
        self,
        node_ids: List[int],
    ) -> List[Dict]:
        """批量查询普通 Node 节点及其属性。"""
        batch_query = """
            UNWIND $node_ids AS node_id
            MATCH (n:Node) WHERE id(n) = node_id
            OPTIONAL MATCH (parent)-[:CONTAINS]->(n)
            OPTIONAL MATCH (n)-[:HAS_FIELD]->(field:Field)
            RETURN node_id,
                n.name AS node_name,
                n.description AS node_des,
                COLLECT(DISTINCT id(parent)) AS parent_ids,
                COLLECT({
                    name: field.name,
                    des: field.description,
                    note: field.note,
                    example: field.example
                }) AS attributes
        """

        return (
            self.neo4j_graph
            .run(
                batch_query,
                node_ids=node_ids,
            )
            .data()
        )

    def _get_all_node_ids(self) -> List[int]:
        """获取所有Node节点的ID"""
        query = "MATCH (n:Node) RETURN id(n) AS node_id"
        return [record["node_id"] for record in self.neo4j_graph.run(query).data()]
    
    def _build_node_fragment(
        self,
        node: Dict,
        root_data: Dict,
        children_map: Dict[int, List[Dict]],
        id2name: Dict[int, str],
        existing_fragments: List[Dict],
    ) -> Optional[Dict]:
        """根据单个 Neo4j 节点构造普通 fragment。"""
        node_id = node["node_id"]
        root_node_id = root_data["node_id"]

        parent_ids = node.get(
            "parent_ids",
            [],
        )

        # 非根节点没有父节点时跳过
        if (
            node_id != root_node_id
            and len(parent_ids) == 0
        ):
            return None

        parent_node_id = [
            (
                "node_root"
                if parent_id == root_node_id
                else parent_id
            )
            for parent_id in parent_ids
            if parent_id
        ]

        children = children_map.get(
            node_id,
            [],
        )

        # 后续遇到同 ID 的节点时跳过
        if any(
            fragment["node_id"] == node_id
            for fragment in existing_fragments
        ):
            return None

        unique_attributes = (
            self._deduplicate_attributes(
                node.get("attributes", [])
            )
        )

        return {
            "node_id": node_id,
            "node_name": node["node_name"],
            "parent_node_id": parent_node_id,
            "parent_node_name": {
                parent_id: id2name.get(parent_id)
                for parent_id in parent_node_id
            },
            "node_type": "Node",
            "attributes": unique_attributes,
            "children": children,
            "is_root": False,
        }

    def _deduplicate_attributes(
        self,
        attributes: List[Dict],
    ) -> List[Dict]:
        """按字段 name 去重，保留第一次出现的字段。"""
        unique_attributes: List[Dict] = []
        field_names = set()

        for attr in attributes:
            field_name = attr["name"]

            if field_name in field_names:
                continue

            field_names.add(field_name)
            unique_attributes.append(attr)

        return unique_attributes


ENTITY_NODE_TYPES = {
    "Side": "side",
    "Aircraft": "aircraft",
    "Ship": "ship",
    "Submarine": "submarine",
    "Facility": "facility",
    "Satellite": "satellite",
    "Group": "group",
    "Sensor": "sensor",
    "Loadout": "loadout",
    "Mission": "mission",
    "FuelRec": "fuelrec",
    "CD": "cd",
    "Engine": "engine",
    "Mount": "mount",
    "AirFacility":"airFacility",
    "DockFacility":"dockFacility",
    "Magazine":"magazine"
}

CONTAINER_NODE_NAMES = {
    "Scenario",
    "Sides",
    "Aircrafts",
    "Ships",
    "Submarines",
    "Facilitys",
    "Satellites",
    "Groups",
    "Sensors",
    "Loadouts",
    "Missions",
    "Fuels",
    "Magazines"
}

class NestedJsonEntityMatcher:
    """
    从嵌套 JSON 中提取与 XML fragment 匹配的数据。

    该类不负责：
    - 实体 ID 分配
    - entity_data_map 文件保存
    - XML 分块生成
    - LLM 调用
    """
    def normalize_string(self, value: str) -> str:
        """
        字符串归一化：统一大小写 + 处理常见单复数后缀
        解决"Aircraft/Aircrafts"、"Ship/Ships"、"City/Cities"等前后缀差异
        """
        if not isinstance(value, str):
            return ""
  
        return value.lower().strip()

    def _build_matched_entity_data(
        self,
        matched_key: str,
        target_value,
        fragment: Dict,
        current_path: List[
            Union[str, Tuple[str, int]]
        ],
        current_dict_key: Optional[str] = None,
    ) -> List[Dict]:
        """
        构造匹配节点的实体数据。

        该函数不负责递归，只负责：
        - 提取 dict 中的标量字段；
        - 处理 list 中的 dict；
        - 处理 list 中的 str；
        - 构造路径元数据。
        """
        matched_data_list: List[Dict] = []

        def format_path_item(
            path_item: Union[str, Tuple[str, int]]
        ) -> str:
            if isinstance(path_item, tuple):
                return (
                    f"{path_item[0]}[{path_item[1]}]"
                )

            return str(path_item)

        def add_path_metadata(
            entity_data: Dict,
            position: List[
                Union[str, Tuple[str, int]]
            ],
        ) -> None:
            entity_data["_position"] = position

            if len(position) > 1:
                parent_position = position[-2]

                if isinstance(parent_position, tuple):
                    entity_data["_parent_json_key"] = (
                        f"{parent_position[0]}"
                        f"[{parent_position[1]}]"
                    )
                else:
                    entity_data["_parent_json_key"] = (
                        parent_position
                    )
            else:
                entity_data["_parent_json_key"] = None

            entity_data["_full_path_str"] = (
                " -> ".join(
                    format_path_item(item)
                    for item in position
                )
            )

        # target_value 是字典
        if isinstance(target_value, dict):
            entity_data = {
                key: value
                for key, value in target_value.items()
                if not isinstance(value, dict)
                and not isinstance(value, list)
            }

            position = current_path + [matched_key]

            add_path_metadata(
                entity_data=entity_data,
                position=position,
            )

            matched_data_list.append(
                {
                    matched_key: entity_data,
                }
            )

        # target_value 是列表
        elif (
            isinstance(target_value, list)
            and len(target_value) > 0
        ):
            for idx, item in enumerate(target_value):
                position = current_path + [
                    (matched_key, idx)
                ]

                # 列表元素是字典
                if isinstance(item, dict):
                    entity_data = {
                        key: value
                        for key, value in item.items()
                        if not isinstance(value, dict)
                        and not isinstance(value, list)
                    }

                    add_path_metadata(
                        entity_data=entity_data,
                        position=position,
                    )

                    matched_data_list.append(
                        {
                            matched_key: entity_data,
                        }
                    )

                # 列表元素是字符串
                elif isinstance(item, str):
                    entity_data = {
                        "Name": item,
                    }

                    add_path_metadata(
                        entity_data=entity_data,
                        position=position,
                    )

                    matched_data_list.append(
                        {
                            matched_key: entity_data,
                        }
                    )

        return matched_data_list

    def _extract_from_list(
        self,
        nested_data: List,
        fragment: Dict,
        current_path: List[
            Union[str, Tuple[str, int]]
        ],
        parent_key: Optional[str] = None,
        current_dict_key: Optional[str] = None,
        allowed_parent_nodes: Optional[Set[str]] = None,
    ) -> List[Dict]:
        """
        遍历列表，保持原始列表路径逻辑。
        """
        matched_data_list: List[Dict] = []

        if allowed_parent_nodes is None:
            allowed_parent_nodes = set()

        for idx, item in enumerate(nested_data):
            if parent_key is None:
                list_tuple = ("unknown", idx)
            else:
                list_tuple = (parent_key, idx)

            new_path = current_path.copy()
            new_path.append(list_tuple)

            recursive_result = (
                self._extract_nested_entity_data_by_type(
                    nested_data=item,
                    fragment=fragment,
                    current_path=new_path,
                    parent_key=None,
                    current_dict_key=current_dict_key,
                    allowed_parent_nodes=(
                        allowed_parent_nodes
                    ),
                )
            )

            if recursive_result:
                matched_data_list.extend(
                    recursive_result
                )

        return matched_data_list

    def _extract_from_dict(
        self,
        nested_data: Dict,
        fragment: Dict,
        current_path: List[
            Union[str, Tuple[str, int]]
        ],
        parent_key: Optional[str] = None,
        current_dict_key: Optional[str] = None,
        allowed_parent_nodes: Optional[Set[str]] = None,
    ) -> List[Dict]:
        """
        遍历字典，保持原始递归行为：
        1. 查找当前字典中的匹配 key；
        2. 处理匹配节点；
        3. 继续递归当前字典中的所有 value。
        """
        matched_data_list: List[Dict] = []

        if allowed_parent_nodes is None:
            allowed_parent_nodes = set()

        target_node_name = fragment["node_name"]
        target_normalized = self.normalize_string(
            target_node_name
        )

        if not target_normalized:
            return matched_data_list

        matched_key: Optional[str] = None

        # 查找当前字典中与目标节点名称匹配的 key
        for key in nested_data.keys():
            if not isinstance(key, str):
                continue

            key_normalized = self.normalize_string(key)

            if not key_normalized:
                continue

            if key_normalized == target_normalized:
                matched_key = key

        # 处理当前字典中匹配到的节点
        if matched_key is not None:
            current_parent_name = ""

            if current_path:
                parent_path_item = current_path[-1]

                if isinstance(parent_path_item, tuple):
                    current_parent_name = parent_path_item[0]

                elif isinstance(parent_path_item, str):
                    current_parent_name = parent_path_item

            current_parent_normalized = self.normalize_string(
                current_parent_name
            )

            # 父节点限制校验
            if (
                allowed_parent_nodes
                and current_parent_normalized
                not in allowed_parent_nodes
            ):
                logger.info(
                    "节点[%s]父节点[%s]不在允许列表[%s]中，跳过",
                    matched_key,
                    current_parent_name,
                    allowed_parent_nodes,
                )

                # 保持原始逻辑：
                # 父节点不匹配时，继续遍历当前 nested_data 的所有 value，
                # 但不处理当前匹配节点本身，然后直接返回。
                for key, value in nested_data.items():
                    if isinstance(value, dict):
                        recursive_path = (
                            current_path + [key]
                        )
                        recursive_dict_key = key
                    else:
                        recursive_path = (
                            current_path.copy()
                        )
                        recursive_dict_key = (
                            current_dict_key
                        )

                    recursive_result = (
                        self._extract_nested_entity_data_by_type(
                            nested_data=value,
                            fragment=fragment,
                            current_path=recursive_path,
                            parent_key=(
                                key
                                if isinstance(value, list)
                                else None
                            ),
                            current_dict_key=(
                                recursive_dict_key
                            ),
                            allowed_parent_nodes=(
                                allowed_parent_nodes
                            ),
                        )
                    )

                    if recursive_result:
                        matched_data_list.extend(
                            recursive_result
                        )

                return matched_data_list

            # 父节点校验通过，构造当前匹配节点结果
            target_value = nested_data[matched_key]

            matched_result = (
                self._build_matched_entity_data(
                    matched_key=matched_key,
                    target_value=target_value,
                    fragment=fragment,
                    current_path=current_path,
                    current_dict_key=current_dict_key,
                )
            )

            if matched_result:
                matched_data_list.extend(
                    matched_result
                )

            # 注意：
            # 这里不能 continue。
            # 原始逻辑在处理匹配节点后，
            # 仍然会继续递归当前字典的所有 value。

        # 递归遍历当前字典中的所有 value
        for key, value in nested_data.items():
            if isinstance(value, dict):
                recursive_path = current_path + [key]
                recursive_dict_key = key
            else:
                recursive_path = current_path.copy()
                recursive_dict_key = current_dict_key

            recursive_result = (
                self._extract_nested_entity_data_by_type(
                    nested_data=value,
                    fragment=fragment,
                    current_path=recursive_path,
                    parent_key=(
                        key
                        if isinstance(value, list)
                        else None
                    ),
                    current_dict_key=recursive_dict_key,
                    allowed_parent_nodes=(
                        allowed_parent_nodes
                    ),
                )
            )

            if recursive_result:
                matched_data_list.extend(
                    recursive_result
                )

        return matched_data_list
    
    def _extract_nested_entity_data_by_type(
        self,
        nested_data: Union[Dict, List],
        fragment: Dict,
        current_path: List[
            Union[str, Tuple[str, int]]
        ],
        parent_key: Optional[str] = None,
        current_dict_key: Optional[str] = None,
        allowed_parent_nodes: Optional[Set[str]] = None,
    ) -> List[Dict]:
        """
        根据数据类型分发到字典或列表处理函数。
        """
        if allowed_parent_nodes is None:
            allowed_parent_nodes = set()

        if isinstance(nested_data, dict):
            return self._extract_from_dict(
                nested_data=nested_data,
                fragment=fragment,
                current_path=current_path,
                parent_key=parent_key,
                current_dict_key=current_dict_key,
                allowed_parent_nodes=allowed_parent_nodes,
            )

        if isinstance(nested_data, list):
            return self._extract_from_list(
                nested_data=nested_data,
                fragment=fragment,
                current_path=current_path,
                parent_key=parent_key,
                current_dict_key=current_dict_key,
                allowed_parent_nodes=allowed_parent_nodes,
            )

        return []
    
    
    def extract_nested_entity_data(
        self,
        nested_data: Union[Dict, List],
        fragment: Dict,
        current_path: Optional[
            List[Union[str, Tuple[str, int]]]
        ] = None,
        parent_key: Optional[str] = None,
        current_dict_key: Optional[str] = None,
    ) -> List[Dict]:
        """
        对外暴露的嵌套 JSON 提取入口
        """
        if current_path is None:
            current_path = []
        else:
            current_path = current_path.copy()

        parent_node_dict = fragment.get(
            "parent_node_name",
            {},
        )

        allowed_parent_nodes: Set[str] = set()

        if isinstance(parent_node_dict, dict):
            for parent_name in parent_node_dict.values():
                if (
                    isinstance(parent_name, str)
                    and parent_name.strip()
                ):
                    normalized_parent = self.normalize_string(
                        parent_name
                    )

                    if normalized_parent:
                        allowed_parent_nodes.add(
                            normalized_parent
                        )

        return self._extract_nested_entity_data_by_type(
            nested_data=nested_data,
            fragment=fragment,
            current_path=current_path,
            parent_key=parent_key,
            current_dict_key=current_dict_key,
            allowed_parent_nodes=allowed_parent_nodes,
        )

class EntityMappingService:
    """
    负责：

    1. 根据 XML 模板片段从输入 JSON 中提取实体数据；
    2. 根据实体完整路径识别业务实体类型；
    3. 为业务实体预分配全局唯一 ID；
    4. 保存实体数据映射和实体 ID 映射。
    """

    def __init__(
        self,
        entity_id_map_file,
        entity_map_file,
        entity_id_registry=None,
        nested_json_entity_matcher=None,
    ):
        self.entity_id_map_file = Path(
            entity_id_map_file
        )
        self.entity_map_file = Path(
            entity_map_file
        )

        self.nested_json_entity_matcher = (
            nested_json_entity_matcher
            if nested_json_entity_matcher is not None
            else NestedJsonEntityMatcher()
        )

        # 原有 GlobalIdRegistry 初始化逻辑保持不变
        self.entity_id_registry = (
            entity_id_registry
            if entity_id_registry is not None
            else GlobalIdRegistry()
        )


    def _get_node_name_from_path(
        self, 
        entity_path: str
    ) -> str:
        if not entity_path:
            return ""

        node_name = entity_path.rsplit("/", 1)[-1]

        # 例如 Side[0] 转成 Side
        if "[" in node_name:
            node_name = node_name.split("[", 1)[0]

        return node_name

    def _get_entity_type(
        self,
        value: dict,
        entity_path: str = "",
    ):
        if not isinstance(value, dict):
            return None

        node_name = self._get_node_name_from_path(entity_path)
        
        if node_name in CONTAINER_NODE_NAMES:
            return None

        return ENTITY_NODE_TYPES.get(node_name)
    
    def _is_entity_dict(
        self,
        value: dict,
        entity_path: str = "",
    ) -> bool:
        """业务实体ID预分配"""
        return self._get_entity_type(value, entity_path) is not None

    def _set_entity_id(
        self,
        entity: dict,
        entity_id: str,
    ) -> None:
        """
        为业务实体写入系统生成的 ID。

        原始 GUID 是输入 JSON 中已有的业务标识，需要保留。
        ID 是系统为跨分块一致性生成的内部唯一标识，两者含义不同。
        """

        if not isinstance(entity, dict):
            return

        entity["ID"] = entity_id


    def _normalize_full_path(
        self,
        full_path_str,
    ):
        """
        将 _full_path_str 转换为统一的 JSON 路径格式。

        示例：
            Scenario -> ActiveUnits -> Aircraft[0]
        转换为：
            /Scenario/ActiveUnits/Aircraft[0]
        """

        if not isinstance(full_path_str, str):
            return ""

        fragments = [
            fragment.strip()
            for fragment in full_path_str.split("->")
            if fragment.strip()
        ]

        if not fragments:
            return ""

        return "/" + "/".join(fragments)

    def _inject_ids_by_full_path(
        self,
        value,
    ):
        """
        根据实体自身的 _full_path_str，为 ENTITY_NODE_TYPES 中定义的
        业务实体分配唯一 ID。

        普通节点、容器节点以及未配置在 ENTITY_NODE_TYPES 中的节点不分配 ID。
        """

        if isinstance(value, list):
            for item in value:
                self._inject_ids_by_full_path(item)
            return

        if not isinstance(value, dict):
            return

        full_path_str = value.get("_full_path_str")

        if isinstance(full_path_str, str) and full_path_str.strip():
            entity_path = self._normalize_full_path(full_path_str)

            if entity_path:
                # 使用完整路径获取当前节点名称，并通过现有逻辑判断
                # 当前节点是否属于业务实体
                entity_type = self._get_entity_type(
                    value=value,
                    entity_path=entity_path,
                )
              
                # 只有 ENTITY_NODE_TYPES 中配置的业务实体才分配 ID
                if entity_type is not None:
                    entity_id = self.entity_id_registry.get_or_create(
                        entity_path,
                        entity_type,
                    )

                    # 使用统一的 ID 写入逻辑，避免实体中同时存在
                    # ID、GUID、其他主键字段
                    self._set_entity_id(
                        entity=value,
                        entity_id=entity_id,
                    )

        for key, child in value.items():
            if key in {
                "ID",
                "_full_path_str",
                "_position",
                "_parent_json_key",
            }:
                continue

            self._inject_ids_by_full_path(child)


    def _inject_ids_into_entity_data_map(
        self, 
        entity_data_map
    ):
        """
        根据实体内部的 _full_path_str 注入唯一 ID。

        _full_path_str 示例：
            Scenario -> ActiveUnits -> Aircraft[0]

        不再从局部 entity_data 的根节点重新构造路径，
        避免生成 [0]/Aircraft、[0]/Side 等错误短路径。
        """

        for node_id, entity_data in entity_data_map.items():
            self._inject_ids_by_full_path(entity_data)

        return entity_data_map


    def prepare_entity_data_map(
        self,
        json_data: Dict,
        fragments: List[Dict],
    ) -> Dict:
        """
        根据 XML 模板片段，从输入 JSON 中提取实体数据，
        并为实体统一分配业务 ID。
        """
        entity_data_map = {}

        for fragment in fragments:
            node_id = fragment["node_id"]

            entity_data_map[node_id] = (
                self.nested_json_entity_matcher
                .extract_nested_entity_data(
                    nested_data=json_data,
                    fragment=fragment,
                )
            )

        entity_data_map = self._inject_ids_into_entity_data_map(
            entity_data_map
        )

        self.entity_id_registry.save(
            self.entity_id_map_file
        )

        logger.info(
            "实体业务ID预分配完成，共分配 %d 个ID，映射文件：%s",
            len(self.entity_id_registry.entity_ids),
            self.entity_id_map_file,
        )

        Path(self.entity_map_file).write_text(
                json.dumps(entity_data_map, ensure_ascii=False, indent=2),
                encoding="utf-8"
        )
        logger.info(f"实体数据映射已保存至: {self.entity_map_file}")

        return entity_data_map

class ChunkService:
    """
    根据模板 fragments 和 entity_data_map 构建并保存分块。

    负责：
    - 生成 XML 模板分块
    - 生成 JSON 数据分块
    - 处理 List 多实例
    - 生成 chunk_mapping
    - 保存分块文件

    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def sanitize_string(self, value) -> str:
        """生成合法的 XML 节点名。"""
        if self.is_nan(value):
            return "unknown_node"

        sanitized = str(value)
        sanitized = re.sub(r"[\u4e00-\u9fff]+", "", sanitized)
        sanitized = re.sub(r"\s+", "", sanitized)
        sanitized = sanitized.replace("/", "_")
        sanitized = sanitized.replace("+", "_")
        sanitized = sanitized.replace("，", "")
        sanitized = sanitized.replace("：", "")

        # 清除 XML 节点名中不允许的字符
        sanitized = re.sub(r"[^A-Za-z0-9_.-]", "_", sanitized)

        if not sanitized:
            return "unknown_node"

        # XML 节点名必须以字母或下划线开头
        if not re.match(r"^[A-Za-z_]", sanitized):
            sanitized = f"node_{sanitized}"

        return sanitized

    def is_nan(self, value):
        """检查值是否为 NaN/空值
        【修改】新增：支持识别字符串"NaN"、空字符串、空列表等业务空值
        """
        if value is None:
            return True
        # 处理字符串类型的空值
        if isinstance(value, str):
            value_stripped = value.strip().lower()
            return value_stripped == "" or value_stripped == "nan"
        # 处理列表/字典等空容器
        if isinstance(value, (list, dict)):
            return len(value) == 0
        try:
            return value != value  # NaN 不等于自身
        except:
            return False
        
    # 格式化XML（移除编码声明，直接生成带缩进的Unicode字符串）
    def serialize_xml_element(
        self,
        elem: etree._Element, 
        indent: str = "  "
    ) -> str:
        """使用lxml格式化XML（无编码声明，仅返回片段）"""
        # 生成带缩进的字节流，再解码为Unicode（避免编码声明）
        xml_bytes = etree.tostring(
            elem,
            encoding='utf-8',
            pretty_print=True,  # lxml自带缩进
            xml_declaration=False,  # 关键：不生成XML声明
            with_comments=True  # 保留注释节点
        )
        # 解码为字符串，清理空行
        xml_str = xml_bytes.decode('utf-8')
        clean_xml = "\n".join([line for line in xml_str.split("\n") if line.strip()])
        return clean_xml


    def save_chunk_bundle(
        self,
        xml_chunks: dict[str, str],
        json_chunks: dict[str, dict],
        chunk_mapping: dict[str, dict],
    ) -> None:
        """
        将内存中的 XML/JSON 分块和映射表统一写入磁盘。

        工作空间目录由 WorkspaceManager.prepare() 统一创建。
        本方法只负责保存分块文件和映射文件。
        """
        xml_chunk_dir = self.workspace.xml_chunk_dir
        json_chunk_dir = self.workspace.json_chunk_dir
        chunk_mapping_file = self.workspace.chunk_mapping_file

        for instance_node_id, xml_content in xml_chunks.items():
            xml_filepath = (
                xml_chunk_dir
                / f"xml_chunk_{instance_node_id}.xml"
            )

            xml_filepath.write_text(
                xml_content,
                encoding="utf-8",
            )

            # 将实际文件路径补回 mapping。
            # 使用 str，保证后续 json.dumps() 可以序列化。
            if instance_node_id in chunk_mapping:
                chunk_mapping[instance_node_id][
                    "xml_chunk_path"
                ] = str(xml_filepath)

        for instance_node_id, json_content in json_chunks.items():
            json_filepath = (
                json_chunk_dir
                / f"json_chunk_{instance_node_id}.json"
            )

            json_filepath.write_text(
                json.dumps(
                    json_content,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            if instance_node_id in chunk_mapping:
                chunk_mapping[instance_node_id][
                    "json_chunk_path"
                ] = str(json_filepath)

        chunk_mapping_file.write_text(
            json.dumps(
                chunk_mapping,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        logger.info(
            "XML分块已保存: %s 个",
            len(xml_chunks),
        )
        logger.info(
            "JSON分块已保存: %s 个",
            len(json_chunks),
        )
        logger.info(
            "分块映射表已保存至: %s",
            chunk_mapping_file,
        )


    def serialize_fragment_as_xml_chunk(
        self, 
        fragment: Dict, 
        node_id: str, 
        parent_node_ids: list, 
        node_type: str, 
        parent_json_key: str = "unknown", 
        full_path_str: str = "unknown"
    ):
        """生成带锚点属性的XML分块（修复编码声明+序列化冲突）"""
        # 1. 改用lxml.etree创建节点（替换标准库ET）
        node_name = self.sanitize_string(fragment.get("node_name", "unknown"))
        # 关键：用lxml.etree创建Element，而非标准库xml.etree
        node_element = etree.Element(node_name)  # 这里用lxml的etree（代码开头已导入from lxml import etree）
        
        # 2. 嵌入锚点属性（保留原有逻辑，优化parent-id处理）
        node_element.set("xml-id", node_id)
        # 处理parent_node_ids（列表转字符串，避免XML属性语法错误）
        parent_id_str = ",".join(parent_node_ids) if isinstance(parent_node_ids, list) else str(parent_node_ids)
        node_element.set("parent-id", parent_id_str)
        node_element.set("node-type", str(node_type or "unknown"))
        node_element.set("parent-json-key", self.sanitize_string(parent_json_key))
        node_element.set("full-json-path", self.sanitize_string(full_path_str))
        
        # 3. 处理字段属性：生成注释节点 + 字段元素（保留逻辑，改用lxml的Comment）
        attrs = fragment.get("attributes", [])
        for field_attr in attrs:
            field_name = field_attr.get("name", "unknown")
            field_des = field_attr.get("des")
            field_exp = field_attr.get("example")
            field_note = field_attr.get("note")

            if self.is_nan(field_name):
                continue
            field_name = self.sanitize_string(field_name)

            # 拼接注释内容
            comment_parts = []
            if field_des and not self.is_nan(field_des):
                field_des = "".join(field_des.strip().split("\n"))
                comment_parts.append(f"{field_name} 中文描述：{str(field_des)}")
            if field_note and not self.is_nan(field_note):
                field_note = "；".join(field_note.strip().split("\n"))
                comment_parts.append(f"生成约束：{str(field_note)}")
            if field_exp and not self.is_nan(field_exp):
                comment_parts.append(f"举例：{str(field_exp)}")
            
            comment_text = " | ".join(comment_parts) if comment_parts else f"{field_name}：无辅助信息"
            # 关键：用lxml.etree.Comment创建注释节点（而非标准库ET.Comment）
            comment_node = etree.Comment(comment_text)
            node_element.append(comment_node)

            # 创建字段元素
            field_elem = etree.SubElement(node_element, field_name)
        
        # 调用lxml格式化函数
        return self.serialize_xml_element(node_element)



    def generate_aligned_chunks(
        self, 
        fragments: List[Dict], 
        entity_data_map: Dict[str, List[Dict]]  # 新增：节点类型->List元素字典
    ) -> Tuple[Dict, Dict, Dict]:
        """
        根据模板片段和 JSON 实例创建：
        - XML chunks
        - JSON chunks
        - chunk mapping
        """ 
        xml_chunks: dict[str, str] = {}
        json_chunks: dict[str, dict] = {}
        chunk_mapping: dict[str, dict] = {}     # 分块映射表（key: node_id_索引） 

        node_instance_count_map = {}  # 记录每个原始节点生成了几个实例
        # 保证父节点先处理、子节点后处理，避免子节点读取不到父节点实例数
        fragment_map = {
            fragment["node_id"]: fragment
            for fragment in fragments
        }

        def get_node_depth(node_id, visited=None):
            """
            递归计算节点深度：
            根节点/无父节点深度为 0；
            父节点深度 + 1 为当前节点深度。
            """
            if visited is None:
                visited = set()

            # 防止异常的循环父子引用导致无限递归
            if node_id in visited:
                logger.warning(f"检测到循环父子关系: {node_id}")
                return 0

            visited.add(node_id)

            fragment = fragment_map.get(node_id)
            if not fragment:
                return 0

            parent_ids = fragment.get("parent_node_id", []) or []

            valid_parent_ids = [
                pid for pid in parent_ids
                if pid and pid != "node_root" and pid in fragment_map
            ]

            if not valid_parent_ids:
                return 0

            return max(
                get_node_depth(parent_id, visited.copy())
                for parent_id in valid_parent_ids
            ) + 1

        # Python sorted 是稳定排序：同一层节点仍尽量维持原有顺序
        fragments = sorted(
            fragments,
            key=lambda fragment: get_node_depth(fragment["node_id"])
        )

        logger.info(
            "节点处理顺序: %s",
            [
                f"{fragment['node_id']}({fragment.get('node_name', '')})"
                for fragment in fragments
            ]
        )

        for fragment in fragments:
            # 获取当前节点的信息
            node_id = fragment["node_id"]
            node_type = str(fragment.get("node_type") or "unknown")
            node_name = self.sanitize_string(fragment.get("node_name", "unknown"))
            parent_node_id = fragment.get("parent_node_id", [])
            node_name = fragment.get("node_name", node_name)

            # 获取当前节点对应的List元素列表
            matched_entity_list = entity_data_map.get(node_id)

            # 当前节点没有自己的匹配数据时，继承父节点实例数。
            # 例如 Sensor 有 3 个实例，则 Cov / Cov_Ill / Ptc / Ptc-track 也要生成 3 个占位实例。
            if not matched_entity_list:                
                parent_count = 1
                if parent_node_id:
                    valid_parent_ids = [pid for pid in parent_node_id if pid and pid != "node_root"]
                    if valid_parent_ids:
                        parent_count = max(
                            node_instance_count_map.get(pid, 1)
                            for pid in valid_parent_ids
                        )
                    # if node_name in ["Weaps"]:
                    #     print(node_name,  node_instance_count_map, parent_count, parent_node_id)
                matched_entity_list = [{} for _ in range(parent_count)]

            node_instance_count_map[node_id] = len(matched_entity_list)
            logger.info(f"节点{node_id}({node_name})匹配到{len(matched_entity_list)}个List元素")

            for idx, entity_data in enumerate(matched_entity_list):
                instance_node_id = f"node_{node_id}_{idx}"

                if parent_node_id:
                    instance_parent_id = [
                        f"{parent_id}_{idx}" if parent_id != "node_root" else "node_root"
                        for parent_id in parent_node_id
                        if parent_id
                    ]
                else:
                    instance_parent_id = []

                # 1. 生成带锚点属性的XML分块
                # 【新增】传递上层JSON键信息到XML分块
                # matched_entity_list为空时会使用{}作为占位，因此这里不能直接取第一个key
                if entity_data:
                    entity_data_key = next(iter(entity_data), None)
                    entity_value = entity_data.get(entity_data_key, {})

                    if isinstance(entity_value, dict):
                        parent_json_key = entity_value.get("_parent_json_key", "unknown")
                        full_path_str = entity_value.get(
                            "_full_path_str",
                            fragment.get("node_name", "unknown")
                        )
                    else:
                        parent_json_key = "unknown"
                        full_path_str = fragment.get("node_name", "unknown")
                else:
                    # Cov、Cov_Ill等没有匹配数据的节点使用占位信息
                    parent_json_key = "unknown"
                    full_path_str = fragment.get("node_name", "unknown")

                ## 生成XML分块
                xml_chunk = self.serialize_fragment_as_xml_chunk(fragment, instance_node_id, instance_parent_id, node_type, parent_json_key, full_path_str)
    
                # 2. 生成JSON分块（绑定锚点与父子关系+填充当前List元素数据）
                json_chunk = self.generate_json_chunk(
                    fragment=fragment,
                    node_id=instance_node_id,
                    parent_node_id=instance_parent_id,
                    node_type=node_type,
                    entity_data=entity_data,
                    parent_json_key=parent_json_key,
                    full_path_str=full_path_str,
                )

                xml_chunks[instance_node_id] = xml_chunk
                json_chunks[instance_node_id] = json_chunk
                
                # 3. 构建分块映射表条目
                chunk_mapping[instance_node_id] = {
                    "node_id": instance_node_id,
                    "original_node_id": node_id,  # 记录原始节点ID
                    "list_index": idx,  # 记录List元素索引
                    "parent_node_id": instance_parent_id,
                    "node_type": node_type,
                    "node_name": node_name,
                    "json_node_name": full_path_str.split("->")[-1].strip(),
                    "parent_json_key": parent_json_key,  # 【新增】记录原始JSON的上层键
                    "full_json_path": full_path_str,     # 【新增】记录完整的JSON路径
                    "children_node_ids": [f"node_{child['child_id']}_{idx}" for child in fragment.get("children", [])]
                }
                                
        return xml_chunks, json_chunks, chunk_mapping
    

    def generate_json_chunk(
        self,
        fragment: Dict,
        node_id: str,
        parent_node_id: list,
        node_type: str,
        entity_data: Dict = None,
        parent_json_key: str = "unknown",
        full_path_str: str = "unknown",
    ) -> Dict:
        """生成JSON分块（绑定锚点与父子关系+填充List元素数据）"""
        entity_data = entity_data or {}
        try:
            # 仅解析 node_xxx_yyy 格式，提取最后一段作为索引
            id_parts = node_id.split("_")
            if len(id_parts) >=3 and id_parts[-1].isdigit():
                idx = id_parts[-1]
            else:
                idx = 0
        except Exception as e:
            logger.warning(f"解析节点ID {node_id} 索引失败: {e}，使用默认索引0")
            idx = 0
        
        children_node_ids = []
        for child in fragment.get("children", []):
            child_id = child["child_id"]
            children_node_ids.append(f"node_{child_id}_{idx}")
        
        json_chunk = {
            "node_id": node_id,
            "parent_node_id": parent_node_id,
            "node_type": node_type,
            "data": entity_data,
            "parent_json_key": parent_json_key,
            "full_json_path": full_path_str,
            "children_node_ids": children_node_ids
        }
        
        return json_chunk


    def build_and_save_chunks(
        self,
        fragments: list[dict],
        entity_data_map: dict,
    ) -> dict:
        (
            xml_chunks,
            json_chunks,
            chunk_mapping,
        ) = self.generate_aligned_chunks(
            fragments=fragments,
            entity_data_map=entity_data_map,
        )

        xml_ids = set(xml_chunks)
        json_ids = set(json_chunks)
        mapping_ids = set(chunk_mapping)

        if xml_ids != json_ids:
            raise ValueError(
                "XML 分块和 JSON 分块的实例 ID 不一致"
            )

        if xml_ids != mapping_ids:
            raise ValueError(
                "XML 分块和 chunk_mapping 的实例 ID 不一致"
            )

        self.save_chunk_bundle(
            xml_chunks=xml_chunks,
            json_chunks=json_chunks,
            chunk_mapping=chunk_mapping,
        )

        return chunk_mapping

class PromptBuilder:
    """
    构造 XML 微分块生成任务所需的 Prompt。

    负责：
    - 读取 Prompt 模板；
    - 准备 XML 和 JSON 输入；
    - 移除 JSON 内部路径元数据；
    - 渲染最终 Prompt。

    """
    _EXCLUDED_JSON_KEYS = frozenset(
        {
            "_position",
            "_parent_json_key",
            "_full_path_str",
        }
    )

    def __init__(
        self,
        prompt_template_path,
    ):
        self.prompt_template_path = Path(
            prompt_template_path
        )

    def _build_llm_prompt(
        self,
        fragment,
        xml_content,
        json_chunk,
    ):
        if not self.prompt_template_path.exists():
            raise FileNotFoundError(
                f"Prompt 模板不存在: "
                f"{self.prompt_template_path}"
            )

        json_content = {
            k: {
                v_k: v_v
                for v_k, v_v in v.items()
                if v_k not in self._EXCLUDED_JSON_KEYS
            }
            if isinstance(v, dict)
            else v
            for k, v in json_chunk["data"].items()
        }

        json_data = json.dumps(
            json_content,
            ensure_ascii=False,
            indent=2,
        )

        prompt_template = (
            self.prompt_template_path
            .read_text(encoding="utf-8")
        )

        return prompt_template.format(
            node_name=fragment["node_name"],
            node_id=fragment["node_id"],
            xml_content=xml_content,
            json_data=json_data,
        )

    def build_prompt(
        self,
        fragment,
        xml_content,
        json_chunk,
    ):
        return self._build_llm_prompt(
            fragment=fragment,
            xml_content=xml_content,
            json_chunk=json_chunk,
        )

class LLMGenerationService:
    def __init__(
        self,
        async_client,
        semaphore,
        prompt_builder,
        xml_validation_service,
        json_chunk_path,
        xml_chunk_path,
        prompt_path,
        llm_model,
        max_completion_length,
    ):
        self.async_client = async_client
        self.semaphore = semaphore
        self.prompt_builder = prompt_builder
        self.xml_validation_service = xml_validation_service
        self.json_chunk_path = Path(json_chunk_path)
        self.xml_chunk_path = Path(xml_chunk_path)
        self.prompt_path = Path(prompt_path)
        self.llm_model = llm_model
        self.max_completion_length = max_completion_length


    async def _call_llm_for_chunk(
        self,
        prompt: str,
    ) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一名军事仿真想定脚本生成专家。"
                    "请严格确保输出的XML格式正确无误，"
                    "仅做字段级填充，不修改结构。"
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        async with self.semaphore:
            response = await self.async_client.chat.completions.create(
                model=self.llm_model,
                messages=messages,
                max_tokens=self.max_completion_length,
                extra_body={"enable_thinking": False},
                temperature=0.05,
                top_p=0.1,
                frequency_penalty=0.1,
                presence_penalty=0.1,
            )

        content = response.choices[0].message.content

        if not content:
            raise ValueError("LLM 返回内容为空")

        return content


    def _save_prompt_answer(
        self,
        instance_node_id: str,
        prompt: str,
        answer: str,
    ) -> Path:
        output_path = (
            self.prompt_path
            / f"prompt_ans_{instance_node_id}.txt"
        )

        output_path.write_text(
            f"PROMPT:\n{prompt}\n\n"
            f"ANSWER:\n{answer}\n",
            encoding="utf-8",
        )

        return output_path



    def _parse_instance_node_id(
        self,
        instance_node_id: str,
    ) -> tuple[int, int]:
        match = re.fullmatch(
            r"node_(\d+)(?:_(\d+))?",
            instance_node_id,
        )

        if match is None:
            raise ValueError(
                f"非法实例节点 ID: {instance_node_id}"
            )

        original_node_id = int(match.group(1))
        list_index = (
            int(match.group(2))
            if match.group(2) is not None
            else 0
        )

        return original_node_id, list_index



    def _load_chunk_inputs(
        self,
        instance_node_id: str,
    ) -> tuple[dict, str, Path, Path]:
        """
        读取一个实例对应的 JSON 分块和 XML 分块。

        返回：
            json_chunk
            xml_content
            json_chunk_path
            xml_chunk_path
        """
        json_chunk_path = (
            self.json_chunk_path
            / f"json_chunk_{instance_node_id}.json"
        )
        xml_chunk_path = (
            self.xml_chunk_path
            / f"xml_chunk_{instance_node_id}.xml"
        )

        if not json_chunk_path.exists():
            raise FileNotFoundError(
                f"JSON 分块不存在: {json_chunk_path}"
            )

        if not xml_chunk_path.exists():
            raise FileNotFoundError(
                f"XML 分块不存在: {xml_chunk_path}"
            )

        json_chunk = json.loads(
            json_chunk_path.read_text(encoding="utf-8")
        )

        xml_content = xml_chunk_path.read_text(
            encoding="utf-8"
        )

        return (
            json_chunk,
            xml_content,
            json_chunk_path,
            xml_chunk_path,
        )



    def _save_filled_xml(
                self,
                xml_path:str,
                xml_string:str,
            ) -> None:
        """"保存XML分块"""
        Path(xml_path).write_text(xml_string, encoding="utf-8")



    def _build_success_result(
        self,
        instance_node_id: str,
        original_node_id: int,
        list_index: int,
        filled_xml: str,
        xml_chunk_path: Path,
        json_chunk_path: Path,
    ) -> Dict:
        return {
            "instance_node_id": instance_node_id,
            "original_node_id": original_node_id,
            "list_index": list_index,
            "content": filled_xml,
            "status": "success",
            "xml_chunk_path": Path(xml_chunk_path).name,
            "json_chunk_path": str(json_chunk_path),
        }


    def _build_failed_result(
        self,
        instance_node_id: str,
        original_node_id: int,
        list_index: int,
        error_message: str,
    ) -> Dict:
        return {
                "instance_node_id": instance_node_id,
                "original_node_id": original_node_id,
                "list_index": list_index,
                "status": "failed",
                "error": error_message,
                }



    def _parse_xml_from_llm_output(self, llm_output: str) -> str:
        """
        从LLM的输出文本中解析出XML内容。
        这是一个复杂且需要鲁棒性处理的过程，实际实现可能需要使用正则表达式或尝试多种解析方式。
        此处为简化示例，假设LLM能返回纯净的XML。
        """
        # 示例：尝试提取可能被标记的XML代码块
        xml_match = re.search(r'```xml\n(.*?)\n```', llm_output, re.DOTALL)
        if xml_match:
            xml_str = xml_match.group(1)
        else:
            xml_str = llm_output # 假设整个输出就是XML

        return xml_str
    

    def _parse_and_validate_filled_xml(
        self,
        content: str,
    ) -> tuple[etree._Element, str]:
        normalized_content = (
            self._parse_xml_from_llm_output(content)
        )

        parser = etree.XMLParser(
            remove_blank_text=True,
            recover=False,
        )

        xml_tree = etree.fromstring(
            normalized_content.encode("utf-8")
            if isinstance(normalized_content, str)
            else normalized_content,
            parser,
        )

        filled_xml = etree.tostring(
            xml_tree,
            encoding="unicode",
            pretty_print=True,
        )

        return xml_tree, filled_xml



    async def generate_fragment_content(
        self, 
        instance_node_id: str,  # 带索引的实例ID（如node_100_0）
        fragment: Dict,  # 原始片段
        max_retries: int = 3
    ) -> Dict:
        """生成单个实例的片段内容（适配List元素）"""        
        try:
            # 解析实例ID（分离原始节点ID和List索引）
            original_node_id, list_index = (
                        self._parse_instance_node_id(
                            instance_node_id
                        )
                    )
            
            # 加载对应JSON和XML分块（已预填充List元素数据）
            (
                json_chunk,
                xml_content,
                json_chunk_path,
                xml_chunk_path,
            ) = self._load_chunk_inputs(
                instance_node_id
            )
            
            # 没有实际字段需要 LLM 填充时，直接使用模板
            if len(xml_content.splitlines()) <= 3:
                logger.info(f"实例 {instance_node_id} 直接使用模板默认值")
                # 验证XML格式并保存
                filled_xml = (
                    self.xml_validation_service
                    ._validate_template_xml(
                        xml_content
                    )
                )

                self._save_filled_xml(
                    xml_chunk_path,
                    filled_xml,
                )
    
                return self._build_success_result(
                    instance_node_id=instance_node_id,
                    original_node_id=original_node_id,
                    list_index=list_index,
                    filled_xml=filled_xml,
                    xml_chunk_path=xml_chunk_path,
                    json_chunk_path=json_chunk_path,
                )

            # 构建LLM提示词
            current_prompt = self.prompt_builder.build_prompt(
                fragment={
                    "node_id": instance_node_id,
                    "node_name": fragment["node_name"],
                },
                xml_content=xml_content,
                json_chunk=json_chunk,
            )
            
            last_error_message = None

            for attempt in range(max_retries):
                try: 
                    ## 异步调用大模型进行分块生成   
                    answer = await self._call_llm_for_chunk(
                        current_prompt
                    )
                    # 保存LLM输入输出
                    self._save_prompt_answer(
                        instance_node_id=instance_node_id,
                        prompt=current_prompt,
                        answer=answer,
                    )

                    # 验证XML格式并保存
                    _, filled_xml = (
                        self._parse_and_validate_filled_xml(
                            answer
                        )
                    )
                    self._save_filled_xml(
                        xml_chunk_path,
                        filled_xml,
                    )

                    logger.info(f"实例 {instance_node_id} 生成成功")
                    return self._build_success_result(
                        instance_node_id=instance_node_id,
                        original_node_id=original_node_id,
                        list_index=list_index,
                        filled_xml=filled_xml,
                        xml_chunk_path=xml_chunk_path,
                        json_chunk_path=json_chunk_path,
                    )
                                            
                except (
                    APIConnectionError, 
                    APIError, 
                    etree.XMLSyntaxError, 
                    PermissionError,
                    ValueError,
                ) as exc:
                    last_error_message = str(exc)
                    logger.warning(
                        "实例 %s 第 %d 次失败: %s",
                        instance_node_id,
                        attempt + 1,
                        exc,
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # 指数退避
 
            # 重试耗尽后的处理失败结果
            # 使用模板作为xml块
            return self._build_failed_result(
                instance_node_id=instance_node_id,
                original_node_id=original_node_id,
                list_index=list_index,
                error_message=last_error_message or "未知错误",
            )
            
        except Exception as exc:
            logger.exception(
                "实例 %s 处理失败",
                instance_node_id,
            )

            return {
                "instance_node_id": instance_node_id,
                "status": "failed",
                "error": str(exc),
            }



    async def _fill_chunk_instance_safe(
        self,
        instance_id: str,
        mapping_entry: Dict,
        fragment_by_id: Dict[int, Dict],
    ) -> Dict:
        """
        安全处理单个分块实例。

        无论成功、找不到原始 fragment，还是处理过程中抛出异常，
        都返回包含 instance_node_id 和 status 的结果字典。
        """
        original_node_id = None

        try:
            if not isinstance(mapping_entry, dict):
                raise TypeError(
                    f"分块映射条目必须是字典，实际为 "
                    f"{type(mapping_entry).__name__}"
                )

            raw_original_node_id = mapping_entry.get(
                "original_node_id"
            )
            if raw_original_node_id is None:
                raise ValueError(
                    "chunk_mapping 条目缺少 original_node_id"
                )

            original_node_id = int(raw_original_node_id)
            fragment = fragment_by_id.get(original_node_id)

            if fragment is None:
                error = (
                    f"未找到原始模板片段: "
                    f"original_node_id={original_node_id}"
                )
                logger.error(
                    "分块实例 %s 处理失败: %s",
                    instance_id,
                    error,
                )
                return {
                    "instance_node_id": instance_id,
                    "original_node_id": original_node_id,
                    "status": "failed",
                    "error": error,
                }

            # 如果 generate_fragment_content 的签名是：
            # async def generate_fragment_content(
            #     self, instance_node_id, fragment, ...
            # ):
            result = await self.generate_fragment_content(
                instance_node_id=instance_id,
                fragment=fragment,
            )

            if not isinstance(result, dict):
                raise TypeError(
                    "generate_fragment_content 必须返回字典，"
                    f"实际返回 {type(result).__name__}"
                )

            # 统一补齐结果字段，保证后续统计和报告可以使用。
            result = dict(result)

            result["instance_node_id"] = instance_id
            result["original_node_id"] = original_node_id
            result.setdefault("status", "success")
            result.setdefault("status", "success")

            return result

        except Exception as exc:
            logger.exception(
                "分块实例 %s 处理时发生异常",
                instance_id,
            )

            return {
                "instance_node_id": instance_id,
                "original_node_id": original_node_id,
                "status": "failed",
                "error": str(exc),
            }


    async def fill_all_chunk_instances(
        self,
        fragments: List[Dict],
        chunk_mapping: Dict,
    ) -> List[Dict]:
        """
        并行填充 chunk_mapping 中的所有分块实例。

        每个 mapping 实例都会对应一个结果；即使找不到对应的
        原始 fragment，也会返回一条 failed 结果，而不是跳过。
        """
        if not chunk_mapping:
            raise ValueError(
                "chunk_mapping 为空，无法填充分块"
            )

        # 建立原始 fragment 索引，避免每个实例都遍历 fragments。
        fragment_by_id: Dict[int, Dict] = {}

        for fragment in fragments:
            try:
                original_node_id = int(fragment["node_id"])
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "跳过无效的 fragment，缺少有效 node_id: %s",
                    fragment,
                )
                continue

            if original_node_id in fragment_by_id:
                logger.warning(
                    "发现重复的原始 fragment node_id: %s",
                    original_node_id,
                )

            fragment_by_id[original_node_id] = fragment

        # 为每个 chunk_mapping 条目创建一个任务。
        # gather 会按照 tasks 的顺序返回结果，结果顺序与 mapping 顺序一致。
        tasks = [
            self._fill_chunk_instance_safe(
                instance_id=str(instance_id),
                mapping_entry=mapping_entry,
                fragment_by_id=fragment_by_id,
            )
            for instance_id, mapping_entry in chunk_mapping.items()
        ]

        results = await asyncio.gather(*tasks)

        # 防止后续代码或辅助方法意外返回不完整结果。
        expected_ids = {
            str(instance_id)
            for instance_id in chunk_mapping
        }
        actual_ids = {
            result.get("instance_node_id")
            for result in results
        }

        missing_ids = expected_ids - actual_ids
        if missing_ids:
            raise RuntimeError(
                "分块处理结果缺失实例: "
                f"{sorted(missing_ids)}"
            )

        return results

class XMLValidationService:

    def _validate_template_xml(
        self,
        xml_content: str,
    ) -> str:
        """解析并验证 XML。"""
        parser = etree.XMLParser(
            remove_blank_text=True
        )

        xml_tree = etree.fromstring(
            xml_content,
            parser,
        )

        return etree.tostring(
            xml_tree,
            encoding="unicode",
            pretty_print=True,
        )

    def validate_fragment_xml(
        self,
        xml_content: str,
    ) -> str:
        return self._validate_template_xml(
            xml_content
        )

    def serialize_and_validate_final_xml(
        self,
        root_xml_elem: etree._Element,
    ) -> str:
        """序列化并严格校验最终 XML。"""
        try:
            final_xml_bytes = etree.tostring(
                root_xml_elem,
                encoding="utf-8",
                pretty_print=True,
                xml_declaration=True,
                with_comments=True,
            )

            etree.fromstring(
                final_xml_bytes,
                parser=etree.XMLParser(
                    recover=False
                ),
            )
        except (
            TypeError,
            ValueError,
            etree.XMLSyntaxError,
        ) as exc:
            raise ValueError(
                "最终 XML 序列化或校验失败"
            ) from exc

        return final_xml_bytes.decode(
            "utf-8"
        )

class XMLAssemblyService:
    """
    根据 chunk_mapping 和已生成的 XML 微分块，
    递归还原完整 XML 树。

    负责：
    - 建立 XML 节点索引；
    - 确定根节点；
    - 解析父子路径；
    - 递归组装 XML；
    - 调用 XMLValidationService 完成最终校验。

    """

    def __init__(
        self,
        xml_validation_service,
        xml_chunk_path,
    ):
        self.xml_validation_service = (
            xml_validation_service
        )
        self.xml_chunk_path = Path(
            xml_chunk_path
        )


    def _get_xml_chunk_path(
        self,
        node: dict,
    ) -> Path:
        """
        根据节点信息获取 XML 分块路径。

        如果 chunk_mapping 中保存了 xml_chunk_path，
        优先使用该路径；否则根据 node_id 拼接路径。
        """
        xml_chunk_path = node.get(
            "xml_chunk_path"
        )

        if xml_chunk_path:
            path = Path(xml_chunk_path)

            if not path.is_absolute():
                path = self.xml_chunk_path / path

            return path

        node_id = node.get("node_id")

        if not node_id:
            raise ValueError(
                "节点缺少 node_id，无法定位 XML 分块"
            )

        return self.xml_chunk_path / (
            f"{node_id}.xml"
        )


    def _parse_xml_chunk(
        self,
        xml_chunk_path: Path | str,
    ) -> etree._Element:
        """
        严格解析 XML 分块。

        这里只负责读取和解析，不负责：
        - 删除内部锚点属性；
        - 挂载子节点；
        - 创建替代节点。
        """
        xml_chunk_filename = Path(
            str(xml_chunk_path)
        ).name

        xml_chunk_path = (
            Path(self.xml_chunk_path)
            / xml_chunk_filename
        )

        if not xml_chunk_path.exists():
            raise FileNotFoundError(
                f"XML 分块不存在: {xml_chunk_path}"
            )

        parser = etree.XMLParser(
            remove_blank_text=True,
            recover=False,
        )

        try:
            xml_tree = etree.parse(
                str(xml_chunk_path),
                parser,
            )
        except (OSError, etree.XMLSyntaxError) as exc:
            logger.error(
                "解析 XML 分块失败: %s, error=%s",
                xml_chunk_path,
                exc,
            )
            raise ValueError(
                f"XML 分块解析失败: {xml_chunk_path}"
            ) from exc

        root = xml_tree.getroot()

        if root is None:
            raise ValueError(
                f"XML 分块没有根节点: {xml_chunk_path}"
            )

        return root


    def _remove_specific_attributes(
        self,
        elem: etree._Element,
    ) -> None:
        """递归删除 XML 内部使用的辅助属性。"""
        attrs_to_remove = {
            "xml-id",
            "parent-json-key",
            "full-json-path",
            "parent-id",
            "node-type",
            "中文描述",
            "生成约束",
            "举例",
        }

        for attr in attrs_to_remove:
            elem.attrib.pop(attr, None)

        for child in elem:
            self._remove_specific_attributes(child)


    def _find_root_node(
        self,
        chunk_mapping: dict,
    ) -> dict:
        root_nodes = [
            node
            for node in chunk_mapping.values()
            if node.get("node_type") == "Root_Node"
        ]

        if not root_nodes:
            raise ValueError(
                "chunk_mapping 中未找到根节点"
            )

        if len(root_nodes) > 1:
            raise ValueError(
                "chunk_mapping 中存在多个根节点"
            )

        return root_nodes[0]


    def _build_xml_node_indexes(
        self,
        chunk_mapping: dict,
    ) -> tuple[dict, dict]:
        """构建 XML 组装需要的节点索引。"""
        path2node = {}
        name_parent2node = {}

        for node in chunk_mapping.values():
            full_json_path = node.get("full_json_path")
            if full_json_path:
                path2node[full_json_path] = node

            json_node_name = node.get("json_node_name")
            if json_node_name:
                key = (
                    json_node_name,
                    node.get("parent_json_key"),
                )
                name_parent2node[key] = node

        return path2node, name_parent2node


    def _attach_child_node(
        self,
        current_node: dict,
        current_xml_elem: etree._Element,
        child_node: dict,
        chunk_mapping: dict,
        path2node: dict,
        name_parent2node: dict,
    ) -> None:
        """
        解析、挂载一个子节点，并递归处理其后代。
        """
        xml_chunk_path = (
            self._get_xml_chunk_path(child_node)
        )

        child_xml_elem = self._parse_xml_chunk(
            xml_chunk_path
        )

        current_xml_elem.append(
            child_xml_elem
        )

        self._recursively_attach_children(
            current_node=child_node,
            current_xml_elem=child_xml_elem,
            chunk_mapping=chunk_mapping,
            path2node=path2node,
            name_parent2node=name_parent2node,
        )



    def _get_node_identifier(
        self,
        node: dict,
    ) -> str:
        full_json_path = (
            node.get("full_json_path") or ""
        )

        path_fragments = [
            fragment.strip()
            for fragment in full_json_path.split("->")
            if fragment.strip()
        ]

        last_fragment = (
            path_fragments[-1]
            if path_fragments
            else (
                node.get("json_node_name")
                or node.get("node_name")
                or ""
            )
        )

        if (
            "(" in last_fragment
            and "," in last_fragment
            and ")" in last_fragment
        ):
            last_fragment = (
                last_fragment
                .replace("(", "")
                .replace(")", "")
                .replace(" ", "")
            )

            parts = last_fragment.split(",", 1)

            if len(parts) != 2:
                raise ValueError(
                    f"非法列表节点路径: {last_fragment}"
                )

            name, index = parts
            return f"{name}[{index}]"

        return last_fragment


    def _is_xml_only_child(
        self,
        current_node_id,
        child_node: dict,
    ) -> bool:
        """
        判断 XML-only 节点是否属于当前节点。

        保持原逻辑：
        - 优先读取 parent_id；
        - 其次读取 instance_parent_id；
        - 其次读取 parent_node_id；
        - 最后读取 original_parent_id；
        - 同时兼容带 node_ 前缀和不带前缀的 ID。
        """
        child_parent_id = (
            child_node.get("parent_id")
            or child_node.get("instance_parent_id")
            or child_node.get("parent_node_id")
            or child_node.get("original_parent_id")
            or []
        )

        if not isinstance(child_parent_id, list):
            child_parent_id = [child_parent_id]

        current_id_candidates = set()

        if current_node_id:
            current_node_id = str(current_node_id)

            current_id_candidates.add(
                current_node_id
            )

            current_id_candidates.add(
                current_node_id.replace(
                    "node_",
                    "",
                    1,
                )
            )

        child_parent_candidates = set()

        for parent_id in child_parent_id:
            if not parent_id:
                continue

            parent_id = str(parent_id)

            child_parent_candidates.add(
                parent_id
            )

            child_parent_candidates.add(
                parent_id.replace(
                    "node_",
                    "",
                    1,
                )
            )

        return bool(
            current_id_candidates.intersection(
                child_parent_candidates
            )
        )



    def _is_json_path_child(
        self,
        child_node: dict,
        current_full_path: str,
        current_path_depth: int,
        current_node_identifier,
    ) -> bool:
        """
        判断普通节点是否是当前节点的直接子节点。

        保持原来的三条规则：
        1. 子节点路径深度比当前节点多一层；
        2. 子节点父路径等于当前节点完整路径；
        3. 子节点 parent_json_key 等于当前节点标识。
        """
        child_full_path = (
            child_node.get("full_json_path") or ""
        )

        if not child_full_path:
            return False

        if child_full_path == "unknown":
            return False

        child_path_fragments = [
            fragment.strip()
            for fragment in child_full_path.split("->")
            if fragment.strip()
        ]

        # 条件 1：
        # 子节点路径深度 = 当前节点路径深度 + 1
        if len(child_path_fragments) != (
            current_path_depth + 1
        ):
            return False

        # 条件 2：
        # 子节点父路径 = 当前节点完整路径
        child_parent_path = " -> ".join(
            child_path_fragments[:-1]
        )

        if child_parent_path != current_full_path:
            return False

        # 条件 3：
        # child.parent_json_key = current node identifier
        if child_node.get("parent_json_key") != (
            current_node_identifier
        ):
            return False

        return True    


    def _recursively_attach_children(
        self,
        current_node: dict,
        current_xml_elem: etree._Element,
        chunk_mapping: dict,
        path2node: dict,
        name_parent2node: dict,
    ) -> None:
        """
        递归查找并挂载子节点。

        节点匹配规则保持原逻辑：
        - XML-only 节点：根据 XML 父节点 ID 匹配；
        - 普通节点：根据 JSON 路径和 parent_json_key 匹配。
        """
        xml_only_node_names = {
            "Cov",
            "Cov_Ill",
            "Ptc",
            "Ptc-track",
            "Weaps",
            "Navigator",
            "AI",
            "Kinematics",
            "Damage",
            "AirOps",
            "Aircraft_Sensory",
            "Doctrine",
        }

        current_node_id = current_node.get("node_id")
        current_full_path = (
            current_node.get("full_json_path") or ""
        )

        current_path_fragments = [
            fragment.strip()
            for fragment in current_full_path.split("->")
            if fragment.strip()
        ]

        current_path_depth = len(
            current_path_fragments
        )

        current_node_identifier = (
            self._get_node_identifier(current_node)
        )

        for child_node in chunk_mapping.values():
            # 保留原逻辑：跳过当前节点自身
            if child_node.get("node_id") == current_node_id:
                continue

            child_node_name = (
                child_node.get("node_name")
                or child_node.get("json_node_name")
                or ""
            )

            # XML-only 节点继续使用原来的 XML 父子关系匹配
            if child_node_name in xml_only_node_names:
                if not self._is_xml_only_child(
                    current_node_id=current_node_id,
                    child_node=child_node,
                ):
                    continue

                self._attach_child_node(
                    current_node=current_node,
                    current_xml_elem=current_xml_elem,
                    child_node=child_node,
                    chunk_mapping=chunk_mapping,
                    path2node=path2node,
                    name_parent2node=name_parent2node,
                )

                # 保留原逻辑：XML-only 节点不再走普通节点匹配
                continue

            # 普通节点继续使用原来的 JSON 路径匹配
            if not self._is_json_path_child(
                child_node=child_node,
                current_full_path=current_full_path,
                current_path_depth=current_path_depth,
                current_node_identifier=current_node_identifier,
            ):
                continue

            self._attach_child_node(
                current_node=current_node,
                current_xml_elem=current_xml_elem,
                child_node=child_node,
                chunk_mapping=chunk_mapping,
                path2node=path2node,
                name_parent2node=name_parent2node,
            )


    def assemble_final_xml(
        self,
        chunk_mapping: dict,
    ) -> str:
        """
        基于 JSON 路径递归查找父节点，
        构建完整 XML 树。
        """
        if not chunk_mapping:
            raise ValueError(
                "chunk_mapping 为空，无法组装 XML"
            )

        path2node, name_parent2node = (
            self._build_xml_node_indexes(
                chunk_mapping
            )
        )

        root_node = self._find_root_node(
            chunk_mapping
        )

        root_xml_elem = self._parse_xml_chunk(
            self._get_xml_chunk_path(root_node)
        )

        self._recursively_attach_children(
            current_node=root_node,
            current_xml_elem=root_xml_elem,
            chunk_mapping=chunk_mapping,
            path2node=path2node,
            name_parent2node=name_parent2node,
        )

        # 所有子节点挂载完成后，删除内部属性
        self._remove_specific_attributes(root_xml_elem)

        return (
            self.xml_validation_service
            .serialize_and_validate_final_xml(
                root_xml_elem
            )
        )

class OutputService:
    """
    负责生成流程最终输出的持久化。

    负责：
    - 保存最终 XML；
    - 保存生成报告；
    - 保存生成统计信息；
    - 返回输出文件路径。

    """
    def __init__(
        self,
        final_xml_path,
        report_path=None,
        ex_info_file=None,
    ):
        self.final_xml_path = Path(
            final_xml_path
        )

        self.report_path = (
            Path(report_path)
            if report_path is not None
            else self.final_xml_path
        )

        self.ex_info_file = (
            Path(ex_info_file)
            if ex_info_file is not None
            else None
        )



    def write_final_xml(
        self,
        final_xml: str,
    ) -> str:
        final_xml_path = (
            self.final_xml_path
            / "final_tree.xml"
        )

        final_xml_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        final_xml_path.write_text(
            final_xml,
            encoding="utf-8",
        )

        logger.info(
            "完整 XML 树已保存至: %s",
            final_xml_path,
        )

        return str(final_xml_path)



    def write_report(
        self,
        report: Dict[str, Any],
    ) -> str:
        """
        保存生成报告。
        """
        report_path = (
            self.report_path
            / "generation_report.json"
        )

        report_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        logger.info(
            "生成报告已保存至: %s",
            report_path,
        )

        return str(report_path)



    def write_generation_info(
        self,
        ex_info: dict,
    ) -> str | None:
        if self.ex_info_file is None:
            return None

        self.ex_info_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.ex_info_file.write_text(
            json.dumps(
                ex_info,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        logger.info(
            "生成统计信息已保存至: %s",
            self.ex_info_file,
        )

        return str(self.ex_info_file)


class TDVAXMLGenerator:
    """
    编排单个 XML 文档的 TDVA 生成流程。

    只负责：
    - 初始化各个服务；
    - 准备工作目录；
    - 编排模板解析、实体映射、分块生成、LLM 填充、
      XML 组装和最终输出；
    - 汇总运行结果；
    - 关闭异步资源。

    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_auth: tuple[str, str],
        openai_api_key: str,
        openai_base_url: str,
        max_completion_length: int = 16000,
        llm_model: str = (
            "deepseek-ai/DeepSeek-V4-Flash"
        ),
        max_workers: int = 10,
        entity_id_map_file: str | None = None,
        prompt_template_path: str = (
            "./prompt/fill_xml_prompt_v4.0.txt"
        ),
        chunk_mapping_path: str = (
            "./chunk_mapping.json"
        ),
        entity_map_file: str = (
            "./entity_data_map.json"
        ),
        prompt_path: str = "./prompt",
        json_chunk_path: str = "./json_chunks",
        xml_chunk_path: str = "./xml_chunks",
        final_xml_path: str = "./final_xml",
        ex_info_path: str = "./info.txt",
    ) -> None:
        self.llm_model = llm_model
        self.max_workers = max_workers
        self.max_completion_length = max_completion_length

        self.async_client = AsyncOpenAI(
            api_key=openai_api_key,
            base_url=openai_base_url,
        )

        self.semaphore = asyncio.Semaphore(
            self.max_workers
        )

        self.entity_id_map_file = (
            Path(entity_id_map_file)
            if entity_id_map_file is not None
            else None
        )

        self.workspace_manager = (
            WorkspaceManager(
                prompt_path=prompt_path,
                json_chunk_path=json_chunk_path,
                xml_chunk_path=xml_chunk_path,
                final_xml_path=final_xml_path,
                chunk_mapping_path=(
                    chunk_mapping_path
                ),
                entity_map_file=entity_map_file,
                ex_info_path=ex_info_path,
            )
        )

        self.workspace = (
            self.workspace_manager.workspace
        )


        self._initialize_services(
            neo4j_uri=neo4j_uri,
            neo4j_auth=neo4j_auth,
            openai_api_key=openai_api_key,
            openai_base_url=openai_base_url,
            prompt_template_path=(
                prompt_template_path
            ),
        )

    def _initialize_services(
        self,
        neo4j_uri: str,
        neo4j_auth: tuple[str, str],
        openai_api_key: str,
        openai_base_url: str,
        prompt_template_path: str,
    ) -> None:
        """
        初始化所有业务服务。

        这里仅负责依赖组装，不执行实际业务。
        """

        self.neo4j_template_service = (
            Neo4jTemplateService(
                neo4j_uri=neo4j_uri,
                neo4j_auth=neo4j_auth,
            )
        )

        self.entity_mapping_service = (
            EntityMappingService(
                # neo4j_uri=neo4j_uri,
                # neo4j_auth=neo4j_auth,
                entity_id_map_file=(
                    self.entity_id_map_file
                ),
                entity_map_file=(
                    self.workspace.entity_map_file
                ),
            )
        )

        self.prompt_builder = PromptBuilder(
            prompt_template_path=Path(
                prompt_template_path
            ),
        )

        self.xml_validation_service = (
            XMLValidationService()
        )

        self.chunk_service = ChunkService(
            workspace=self.workspace,
        )


        self.llm_generation_service = (
            LLMGenerationService(
                async_client=self.async_client,
                semaphore=self.semaphore,
                prompt_builder=self.prompt_builder,
                xml_validation_service=(
                    self.xml_validation_service
                ),
                json_chunk_path=(
                    self.workspace.json_chunk_dir
                ),
                xml_chunk_path=(
                    self.workspace.xml_chunk_dir
                ),
                prompt_path=(
                    self.workspace.prompt_dir
                ),
                llm_model=self.llm_model,
                max_completion_length=(
                    self.max_completion_length
                ),
            )
        )

        self.xml_assembly_service = (
            XMLAssemblyService(
                xml_validation_service=(
                    self.xml_validation_service
                ),
                xml_chunk_path=(
                    self.workspace.xml_chunk_dir
                ),
            )
        )

        self.output_service = OutputService(
            final_xml_path=(
                self.workspace.final_xml_dir
            ),
            ex_info_file=(
                self.workspace.extra_info_file
            ),
        )

    async def run(
        self,
        json_data: dict,
    ) -> dict:
        """
        执行完整 XML 生成流程。
        """
        start_time = time.perf_counter()

        try:
            self.workspace = (
                self.workspace_manager.prepare()
            )

            self.neo4j_template_service.create_indexes()

            # 1. 从 Neo4j 模板图谱生成基础 fragments
            fragments = (
                self.neo4j_template_service
                .generate_fragments()
            )

            if not fragments:
                raise ValueError(
                    "未生成任何模板 fragments"
                )

            # 2. 根据输入 JSON 和模板 fragments
            #    构建实体数据映射
            entity_data_map = (
                self.entity_mapping_service
                .prepare_entity_data_map(
                    json_data=json_data,
                    fragments=fragments,
                )
            )
            
            if not entity_data_map:
                raise ValueError(
                    "未生成实体数据映射"
                )

            # 3. 生成并保存 XML/JSON 分块
            chunk_mapping = (
                self.chunk_service
                .build_and_save_chunks(
                    fragments=fragments,
                    entity_data_map=(
                        entity_data_map
                    ),
                )
            )

            if not chunk_mapping:
                raise ValueError(
                    "未生成任何 chunk_mapping"
                )

            # 4. 调用 LLM 填充所有分块
            fragment_results = (
                await self.llm_generation_service
                .fill_all_chunk_instances(
                    fragments=fragments,
                    chunk_mapping=chunk_mapping,
                )
            )

            # 5. 组装并严格校验最终 XML
            final_xml = (
                self.xml_assembly_service
                .assemble_final_xml(
                    chunk_mapping=chunk_mapping,
                )
            )

            # 6. 保存最终 XML
            final_xml_path = (
                self.output_service
                .write_final_xml(
                    final_xml=final_xml,
                )
            )

            elapsed_seconds = (
                time.perf_counter() - start_time
            )

            # 7. 构造并保存生成统计信息
            generation_info = (
                self._build_generation_info(
                    fragment_results=(
                        fragment_results
                    ),
                    elapsed_seconds=(
                        elapsed_seconds
                    ),
                )
            )

            report_path = (
                self.output_service
                .write_generation_info(
                    ex_info=generation_info,
                )
            )

            result = {
                "status": "success",
                "final_xml_path": final_xml_path,
                "chunk_mapping_path": str(
                    self.workspace.chunk_mapping_file
                ),
                "report_path": report_path,
                "fragment_results": (
                    fragment_results
                ),
                "elapsed_seconds": (
                    elapsed_seconds
                ),
            }

            logger.info(
                "TDVA XML 生成完成，耗时 %.2f 秒",
                elapsed_seconds,
            )

            return result

        except Exception:
            logger.exception(
                "TDVA XML 生成流程失败"
            )
            raise

        finally:
            await self._close_resources()


    def _build_generation_info(
        self,
        fragment_results: list[dict],
        elapsed_seconds: float,
    ) -> dict[str, Any]:
        """
        构造最终生成统计信息。

        OutputService 只负责保存该字典，
        不负责解释生成结果。
        """

        total_count = len(fragment_results)

        success_count = sum(
            result.get("status") == "success"
            for result in fragment_results
        )

        fallback_count = sum(
            result.get("status") == "fallback"
            for result in fragment_results
        )

        failed_results = [
            result
            for result in fragment_results
            if result.get("status") == "failed"
        ]

        failed_ids = [
            result.get("instance_node_id")
            for result in failed_results
        ]

        success_ratio = (
            success_count / total_count
            if total_count > 0
            else 0.0
        )

        return {
            "elapsed_seconds": elapsed_seconds,
            "total_count": total_count,
            "success_count": success_count,
            "fallback_count": fallback_count,
            "failed_count": len(
                failed_results
            ),
            "success_ratio": success_ratio,
            "failed_instance_ids": failed_ids,
        }

    async def _close_resources(self) -> None:
        """
        关闭异步资源。

        LLMGenerationService 内部创建的
        AsyncOpenAI 客户端由该服务负责关闭。
        """

        close = getattr(
            self.llm_generation_service,
            "aclose",
            None,
        )

        if close is None:
            return

        result = close()

        if hasattr(result, "__await__"):
            await result




async def main() -> None:
    # Neo4j 配置
    neo4j_uri = os.getenv(
        "NEO4J_URI",
        "bolt://workspace.featurize.cn:18613",
    )

    neo4j_user = os.getenv(
        "NEO4J_USER",
        "neo4j",
    )

    neo4j_password = os.getenv(
        "NEO4J_PASSWORD",
        "1234567890",
    )

    # LLM 配置
    openai_api_key = os.getenv(
        "OPENAI_API_KEY",
        "sk-wocixetrhbgthoepmdjdycdadrcyudtepyujtqwcirkxmzvh",
    )

    openai_base_url = os.getenv(
        "OPENAI_BASE_URL",
        "https://api.siliconflow.cn/v1",
    )

    # 输入文件
    input_json_path = Path(
        os.getenv(
            "TDVA_INPUT_JSON",
            r"D:\非shemi工作内容\陈xz_论文相关\data_generater\测试节点结构数据\L1\01.想定精细度想定_llm_extract_with_guid.json",
        )
    )

    if not input_json_path.is_file():
        raise FileNotFoundError(
            f"输入 JSON 文件不存在: {input_json_path}"
        )

    if not openai_api_key:
        raise ValueError(
            "未设置 OPENAI_API_KEY"
        )

    # 加载输入 JSON
    json_data = json.loads(
        input_json_path.read_text(
            encoding="utf-8"
        )
    )

    # 创建生成器
    generator = TDVAXMLGenerator(
        neo4j_uri=neo4j_uri,
        neo4j_auth=(
            neo4j_user,
            neo4j_password,
        ),
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        llm_model=os.getenv(
            "LLM_MODEL",
            "deepseek-ai/DeepSeek-V4-Flash",
        ),
        max_workers=int(
            os.getenv(
                "MAX_WORKERS",
                "16",
            )
        ),
        entity_id_map_file=os.getenv(
            "ENTITY_ID_MAP_FILE",
            "./entity_id_map.json",
        ),
        prompt_template_path=os.getenv(
            "PROMPT_TEMPLATE_PATH",
            "./prompt/fill_xml_prompt_v4.0.txt",
        ),
        chunk_mapping_path=os.getenv(
            "CHUNK_MAPPING_PATH",
            "./output/chunk_mapping.json",
        ),
        entity_map_file=os.getenv(
            "ENTITY_MAP_FILE",
            "./output/entity_data_map.json",
        ),
        prompt_path=os.getenv(
            "PROMPT_PATH",
            "./output/prompt",
        ),
        json_chunk_path=os.getenv(
            "JSON_CHUNK_PATH",
            "./output/json_chunks",
        ),
        xml_chunk_path=os.getenv(
            "XML_CHUNK_PATH",
            "./output/xml_chunks",
        ),
        final_xml_path=os.getenv(
            "FINAL_XML_PATH",
            "./output/final_xml",
        ),
        ex_info_path=os.getenv(
            "EX_INFO_PATH",
            "./output/info.json",
        ),
    )

    try:
        result = await generator.run(
            json_data=json_data
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    finally:
        # 如果 run() 内部没有关闭资源，则执行关闭
        close = getattr(
            generator,
            "close",
            None,
        )

        if close is not None:
            result = close()

            if hasattr(result, "__await__"):
                await result

if __name__ == "__main__":
    asyncio.run(main())