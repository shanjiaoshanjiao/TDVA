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
from typing import List, Dict, Any, Tuple, Optional, Union

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


def sanitize_string(string):
    """
    去除中文字符和冒号，生成合法的XML节点名
    【修改】新增：处理XML节点名不能以数字开头的情况（XML规范要求）
    """
    if is_nan(string):
        return "unknown_node"
    # 去除中文字符和特殊符号
    sanitized = re.sub(r'[\u4e00-\u9fff]+', '', string)
    sanitized = re.sub(r'\s+', '', sanitized).strip()
    sanitized = sanitized.replace("/", "_")
    sanitized = sanitized.replace("+", "_")
    sanitized = sanitized.replace(" ", "")
    sanitized = sanitized.replace("，", "")
    sanitized = sanitized.replace("：", "")
    sanitized = escape(sanitized)
    # 【修改1】确保XML节点名不以数字开头（XML规范强制要求）
    if sanitized and sanitized[0].isdigit():
        sanitized = f"node_{sanitized}"
    return sanitized


def is_nan(value):
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


def normalize_string(s: str) -> str:
    """
    字符串归一化：统一大小写 + 处理常见单复数后缀
    解决"Aircraft/Aircrafts"、"Ship/Ships"、"City/Cities"等前后缀差异
    """
    if not isinstance(s, str):
        return ""
    
    # 1. 统一转小写
    s_lower = s.lower().strip()
    
    # # 2. 处理常见单复数后缀（可根据业务扩展）
    # plural_suffixes = [
    #     ("ies", "y"),    # cities -> city
    #     ("ves", "f"),    # knives -> knife (可选，根据业务是否需要)
    #     ("es", ""),      # buses -> bus
    #     ("s", "")        # ships -> ship
    # ]
    
    normalized = s_lower
    # for suffix, replace in plural_suffixes:
    #     if normalized.endswith(suffix):
    #         normalized = normalized[:-len(suffix)] + replace
    #         break  # 匹配到一个后缀即停止，避免过度处理
    
    return normalized


def extract_nested_entity_data(
    nested_data: Union[Dict, List], 
    fragment: List,
    current_path: List[Union[str, Tuple[str, int]]] = None,
    parent_key: str = None,
    current_dict_key: str = None
) -> List[Dict]:
    """
    递归遍历多层嵌套JSON，精准提取匹配元素并记录树形层级位置
    【核心修改】适配parent_node_name为字典的场景：
    - 提取字典所有value作为允许的父节点列表
    - 只要当前父节点在列表中，即判定匹配成功
    """
    matched_data_list = []
    target_node_name = fragment["node_name"]
    # ========== 关键修改1：解析允许的父节点列表 ==========
    parent_node_dict = fragment.get("parent_node_name", {})  # 取出父节点字典
    # 提取字典所有value，过滤空值后归一化，转为集合（去重+快速匹配）
    allowed_parent_nodes = set()
    if isinstance(parent_node_dict, dict):
        for parent_name in parent_node_dict.values():
            if isinstance(parent_name, str) and parent_name.strip():
                normalized_parent = normalize_string(parent_name)
                if normalized_parent:
                    allowed_parent_nodes.add(normalized_parent)

    # 初始化路径
    if current_path is None:
        current_path = []
    else:
        current_path = current_path.copy()    
    # 递归终止条件
    if not isinstance(nested_data, (dict, list)):
        return matched_data_list
    
    # 预处理目标节点（归一化）
    target_normalized = normalize_string(target_node_name)
    if not target_normalized:
        return matched_data_list
    
    # 处理字典类型
    if isinstance(nested_data, dict):
        matched_key = None
        
        # 1. 找到语义匹配的key
        for key in nested_data.keys():
            if not isinstance(key, str):
                continue
            key_normalized = normalize_string(key)
            if not key_normalized:
                continue
            if key_normalized == target_normalized:
                matched_key = key
        
        # 2. 处理匹配到的key对应值（父节点校验+精准记录层级）
        if matched_key:
            # ===== 解析当前节点的父节点名称（原有逻辑保留）=====
            current_parent_name = ""
            if current_path:
                parent_path_item = current_path[-1]
                if isinstance(parent_path_item, tuple):
                    current_parent_name = parent_path_item[0]
                elif isinstance(parent_path_item, str):
                    current_parent_name = parent_path_item
            # 归一化当前父节点名称
            current_parent_normalized = normalize_string(current_parent_name)
            
            # ========== 关键修改2：多父节点匹配校验 ==========
            if allowed_parent_nodes:  # 允许的父节点列表非空时才校验
                if current_parent_normalized not in allowed_parent_nodes:
                    # 父节点不在允许列表中，跳过该节点
                    logger.info(
                        f"节点[{matched_key}]父节点[{current_parent_name}]不在允许列表[{allowed_parent_nodes}]中，跳过"
                    )

                    # 继续递归遍历子节点（不影响后续匹配）
                    for key, value in nested_data.items():
                        if isinstance(value, dict):
                            recursive_path = current_path + [key]
                            recursive_dict_key = key
                            
                        else:
                            recursive_path = current_path.copy()
                            recursive_dict_key = current_dict_key
                            
                        recursive_result = extract_nested_entity_data(
                            value, fragment, recursive_path,
                            parent_key=key if isinstance(value, list) else None,
                            current_dict_key=recursive_dict_key
                        )
                        if recursive_result:
                            matched_data_list.extend(recursive_result)
                    return matched_data_list  # 父节点不匹配，直接返回
            

            # ===== 原有逻辑：处理匹配节点的value =====
            target_value = nested_data[matched_key]     
            if isinstance(target_value, dict):
                target_value_ = {key:target_value[key] for key in target_value.keys() if not isinstance(target_value[key], dict) and not isinstance(target_value[key], list)}
                target_value_["_position"] = current_path + [matched_key]
                
                if len(target_value_["_position"]) > 1:
                    if isinstance(target_value_["_position"][-2], tuple):
                        tuple_value = target_value_["_position"][-2]
                        target_value_["_parent_json_key"] = f"{tuple_value[0]}[{tuple_value[1]}]"
                    else:
                        target_value_["_parent_json_key"] = target_value_["_position"][-2]
                else:
                    target_value_["_parent_json_key"] = None
                    
                target_value_["_full_path_str"] = " -> ".join([
                    str(p) if not isinstance(p, tuple) else f"{p[0]}[{p[1]}]" 
                    for p in current_path + [matched_key]
                ])
                matched_data_list.append({matched_key:target_value_})
            
            elif isinstance(target_value, list) and len(target_value) > 0:
                for idx, item in enumerate(target_value):
                    if isinstance(item, dict):
                        item_ = {key:item[key] for key in item.keys() if not isinstance(item[key], dict) and not isinstance(item[key], list)}
                        item_["_position"] = current_path + [(matched_key, idx)]
                        
                        if len(item_["_position"]) > 1:
                            if isinstance(item_["_position"][-2], tuple):
                                tuple_value = item_["_position"][-2]
                                item_["_parent_json_key"] = f"{tuple_value[0]}[{tuple_value[1]}]"
                            else:
                                item_["_parent_json_key"] = item_["_position"][-2]
                        else:
                            item_["_parent_json_key"] = None
                        
                        item_["_full_path_str"] = " -> ".join([
                            str(p) if not isinstance(p, tuple) else f"{p[0]}[{p[1]}]" 
                            for p in current_path + [(matched_key, idx)]
                        ])
                        matched_data_list.append({matched_key: item_})

                    elif isinstance(item, str):
                        item_ = {"Name":item}
                        item_["_position"] = current_path + [(matched_key, idx)]
                        
                        if len(item_["_position"]) > 1:
                            if isinstance(item_["_position"][-2], tuple):
                                tuple_value = item_["_position"][-2]
                                item_["_parent_json_key"] = f"{tuple_value[0]}[{tuple_value[1]}]"
                            else:
                                item_["_parent_json_key"] = item_["_position"][-2]
                        else:
                            item_["_parent_json_key"] = None

                        item_["_full_path_str"] = " -> ".join([
                            str(p) if not isinstance(p, tuple) else f"{p[0]}[{p[1]}]" 
                            for p in current_path + [(matched_key, idx)]
                        ])
                        matched_data_list.append({matched_key: item_})

        # 3. 递归遍历字典所有值（原有逻辑保留）
        for key, value in nested_data.items():
            if isinstance(value, dict):
                recursive_path = current_path + [key]
                recursive_dict_key = key
            else:
                recursive_path = current_path.copy()
                recursive_dict_key = current_dict_key
            recursive_result = extract_nested_entity_data(
                value, fragment, recursive_path,
                parent_key=key if isinstance(value, list) else None,
                current_dict_key=recursive_dict_key
            )
            if recursive_result:
                matched_data_list.extend(recursive_result)
    
    # 处理列表类型（原有逻辑保留）
    elif isinstance(nested_data, list):
        for idx, item in enumerate(nested_data):
            if parent_key is None:
                list_tuple = ("unknown", idx)
            else:
                list_tuple = (parent_key, idx)
            
            new_path = current_path.copy()
            new_path.append(list_tuple)
            
            recursive_result = extract_nested_entity_data(
                item, fragment, new_path,
                parent_key=None,
                current_dict_key=current_dict_key
            )
            if recursive_result:
                matched_data_list.extend(recursive_result)
    
    return matched_data_list



# 格式化XML（移除编码声明，直接生成带缩进的Unicode字符串）
def serialize_xml_element(elem: etree._Element, indent: str = "  ") -> str:
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



@dataclass
class GlobalIdRegistry:
    """
    全局实体ID注册表。

    同一个 entity_path 始终返回同一个ID；
    不同 entity_path 不允许复用ID。
    """
    entity_ids: dict[str, str] = field(default_factory=dict)
    used_ids: set[str] = field(default_factory=set)
    lock: threading.Lock = field(
        default_factory=threading.Lock
    )

    def get_or_create(
        self,
        entity_path: str,
        entity_type: str,
    ) -> str:
        key = f"{entity_type}:{entity_path}"

        with self.lock:
            if key in self.entity_ids:
                return self.entity_ids[key]

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
        """加载已有ID映射时使用。"""
        key = f"{entity_type}:{entity_path}"
        entity_id = str(entity_id).strip()

        try:
            UUID(entity_id)
        except ValueError as exc:
            raise ValueError(
                f"实体 {key} 的ID不是合法UUID: {entity_id}"
            ) from exc

        with self.lock:
            old_id = self.entity_ids.get(key)
            if old_id and old_id != entity_id:
                raise ValueError(
                    f"实体 {key} 的ID发生变化: {old_id} -> {entity_id}"
                )

            other_key = next(
                (
                    item_key
                    for item_key, item_id in self.entity_ids.items()
                    if item_id == entity_id and item_key != key
                ),
                None,
            )
            if other_key:
                raise ValueError(
                    f"ID {entity_id} 已被其他实体使用: "
                    f"{other_key}，当前实体: {key}"
                )

            self.entity_ids[key] = entity_id
            self.used_ids.add(entity_id)
            return entity_id

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        temp_path = f"{path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(
                self.entity_ids,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_path, path)

    @classmethod
    def load(cls, path: str) -> "GlobalIdRegistry":
        registry = cls()

        if not os.path.exists(path):
            return registry

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        for key, entity_id in data.items():
            try:
                entity_type, entity_path = key.split(":", 1)
            except ValueError as exc:
                raise ValueError(f"非法ID映射键: {key}") from exc

            registry.register_existing(
                entity_path,
                entity_type,
                entity_id,
            )

        return registry



class TDVAXMLGenerator:
    """
    执行单个 XML 文档的 TDVA 生成流程。

    负责：
    1. 解析模板和输入 JSON；
    2. 建立 XML/JSON 分块；
    3. 调用 LLM 填充分块；
    4. 验证生成结果；
    5. 组装最终 XML。
    """
    def __init__(
            self,
            neo4j_uri: str, 
            neo4j_auth: tuple, 
            openai_api_key: str, 
            openai_base_url: str, 
            max_workers: int = 10, 
            entity_id_map_file: str | None = None,
            chunk_mapping_path: str = "./chunk_mapping.json", 
            entity_map_file: str = "./entity_data_map.json", 
            json_chunk_path: str = "./json_chunks", 
            xml_chunk_path: str = "./xml_chunks", 
            final_xml_path: str = "./final_xml", 
            prompt_path: str = "./prompt", 
            ex_info_path: str = "./ex_info.txt"
            ):
        self.openai_api_key = openai_api_key
        self.openai_base_url = openai_base_url
        self.neo4j_graph = Graph(neo4j_uri, auth=neo4j_auth)
        self.matcher = NodeMatcher(self.neo4j_graph)
        self.async_client = AsyncOpenAI(api_key=self.openai_api_key, base_url = self.openai_base_url, timeout=120)
        self.max_completion_length = 32000
        self.semaphore = asyncio.Semaphore(max_workers)
        self.id_map = {}  # 全局ID映射表: {node_id: filename}
        # 分块映射表存储路径
        self.chunk_mapping_path = Path(chunk_mapping_path)
        self.entity_map_file = Path(entity_map_file)
        self.prompt_path = Path(prompt_path)
        # JSON分块存储路径
        self.json_chunk_path = Path(json_chunk_path)
        # XML分块存储路径
        self.xml_chunk_path = Path(xml_chunk_path)
        # 最终XML输出路径（新增：按List元素分文件）
        self.final_xml_path = Path(final_xml_path)
        # 成功生成微分块比例与用时保存
        self.ex_info_file = Path(ex_info_path)
        # 实体业务ID注册表，与Node ID分开管理。
        self.entity_id_registry = GlobalIdRegistry()
        
        # 建议作为构造参数传入；没有传入时使用默认路径。
        self.entity_id_map_file = Path(
            entity_id_map_file
            if entity_id_map_file
            else os.path.join(self.final_xml_path, "entity_id_map.json")
        )

        if os.path.exists(self.entity_id_map_file):
            self.entity_id_registry = GlobalIdRegistry.load(
                self.entity_id_map_file
            )


    def prepare_workspace(self) -> None:
        """创建本次生成所需的目录。"""
        directories = (
            self.prompt_path,
            self.json_chunk_path,
            self.xml_chunk_path,
            self.final_xml_path,
        )

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

        # 这些路径是文件，需要创建其父目录，而不是把文件路径建成目录。
        file_paths = (
            self.chunk_mapping_path,
            self.entity_map_file,
            self.ex_info_file,
            self.entity_id_map_file,
        )

        for file_path in file_paths:
            file_path.parent.mkdir(parents=True, exist_ok=True)


    def create_indexes(self):
        """创建必要的数据库索引"""
        try:
            self.neo4j_graph.run("CREATE INDEX node_id_index IF NOT EXISTS FOR (n:Node) ON (n.id)")
            self.neo4j_graph.run("CREATE INDEX field_name_index IF NOT EXISTS FOR (f:FIELD) ON (f.name)")
            logger.info("数据库索引创建/验证完成")
        except Exception as e:
            logger.error(f"创建索引失败: {e}")

    # ===================== 生成多实例分块（适配匹配后List每个元素） =====================
    def generate_aligned_chunks(
        self, 
        fragments: List[Dict], 
        entity_data_map: Dict[str, List[Dict]]  # 新增：节点类型->List元素字典
    ) -> Tuple[Dict, Dict]:
        """
        根据模板片段和 JSON 实例创建：
        - XML chunks
        - JSON chunks
        - chunk mapping
        """        
        xml_chunks_dict = dict()
        chunk_mapping = {}  # 分块映射表（key: node_id_索引）
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
            node_type = fragment.get("node_type")
            node_name = sanitize_string(fragment.get("node_name", "unknown"))
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


                xml_chunk = self.serialize_fragment_as_xml_chunk(fragment, instance_node_id, instance_parent_id, node_type, parent_json_key, full_path_str)
                xml_filename = f"xml_chunk_{instance_node_id}.xml"
                

                xml_filepath = os.path.join(self.xml_chunk_path, xml_filename)
                Path(xml_filepath).write_text(xml_chunk, encoding="utf-8")
                xml_chunks_dict[instance_node_id] = xml_chunk
                
                # 2. 生成JSON分块（绑定锚点与父子关系+填充当前List元素数据）
                json_chunk = self.generate_json_chunk(fragment, instance_node_id, instance_parent_id, node_type, entity_data)
                json_filename = f"json_chunk_{instance_node_id}.json"
                json_filepath = os.path.join(self.json_chunk_path, json_filename)
                Path(json_filepath).write_text(json.dumps(json_chunk, ensure_ascii=False, indent=2), encoding="utf-8")
                
                # 3. 构建分块映射表条目（核心修改：补全缺失的映射表构建逻辑 + 新增上层JSON键记录）
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
                    "xml_chunk_path": xml_filepath,
                    "json_chunk_path": json_filepath,
                    "children_node_ids": [f"node_{child['child_id']}_{idx}" for child in fragment.get("children", [])]
                }
        

        
        # 核心修改：补充分块映射表保存逻辑（原代码被注释导致映射表丢失）
        Path(self.chunk_mapping_path).write_text(
            json.dumps(chunk_mapping, ensure_ascii=False, indent=2), 
            encoding="utf-8"
        )
        logger.info(f"分块映射表已保存至: {self.chunk_mapping_path}")
        
        # 验证分块对齐性
        #self.validate_chunk_alignment(chunk_mapping)
        
        return xml_chunks_dict, chunk_mapping
    

    def _get_node_name_from_path(self, entity_path: str) -> str:
        if not entity_path:
            return ""

        node_name = entity_path.rsplit("/", 1)[-1]

        # 例如 Side[0] 转成 Side
        if "[" in node_name:
            node_name = node_name.split("[", 1)[0]

        return node_name


    def _get_entity_type(self, value: dict, entity_path: str = ""):
        if not isinstance(value, dict):
            return None

        node_name = self._get_node_name_from_path(entity_path)
        
        if node_name in CONTAINER_NODE_NAMES:
            return None

        return ENTITY_NODE_TYPES.get(node_name)


    # ===================== 业务实体ID预分配 =====================
    def _is_entity_dict(self, value: dict, entity_path: str = "") -> bool:
        return self._get_entity_type(value, entity_path) is not None


    def _set_entity_id(self, entity: dict, entity_id: str) -> None:
        """
        为业务实体写入系统生成的 ID。

        原始 GUID 是输入 JSON 中已有的业务标识，需要保留。
        ID 是系统为跨分块一致性生成的内部唯一标识，两者含义不同。
        """

        if not isinstance(entity, dict):
            return

        entity["ID"] = entity_id


    def _normalize_full_path(self, full_path_str):
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


    def _inject_ids_by_full_path(self, value):
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



    def _inject_ids_into_entity_data_map(self, entity_data_map):
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


    # ===================== JSON分块填充List元素数据 =====================

    def generate_json_chunk(
        self,
        fragment: Dict,
        node_id: str,
        parent_node_id: list,
        node_type: str,
        entity_data: Dict = None
    ) -> Dict:
        """生成JSON分块（绑定锚点与父子关系+填充List元素数据）"""
        entity_data = entity_data or {}
        # 核心修改：修复子节点ID生成逻辑，确保父子索引一致
        # 【修改4】健壮的ID索引提取：只取最后两段，且校验数字
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
            # 【新增】保留JSON分块的上层键和完整路径，便于溯源
            "parent_json_key": entity_data.get("_parent_json_key", "unknown"),
            "full_json_path": entity_data.get("_full_path_str", "unknown"),
            "children_node_ids": children_node_ids
        }
        
        return json_chunk





    # 在XML中嵌入上层JSON键信息
    def serialize_fragment_as_xml_chunk(self, 
                                    fragment: Dict, 
                                    node_id: str, 
                                    parent_node_ids: list, 
                                    node_type: str, 
                                    parent_json_key: str = "unknown", 
                                    full_path_str: str = "unknown"):
        """生成带锚点属性的XML分块（修复编码声明+序列化冲突）"""
        # 1. 改用lxml.etree创建节点（替换标准库ET）
        node_name = sanitize_string(fragment.get("node_name", "unknown"))
        # 关键：用lxml.etree创建Element，而非标准库xml.etree
        node_element = etree.Element(node_name)  # 这里用lxml的etree（代码开头已导入from lxml import etree）
        
        # 2. 嵌入锚点属性（保留原有逻辑，优化parent-id处理）
        node_element.set("xml-id", node_id)
        # 处理parent_node_ids（列表转字符串，避免XML属性语法错误）
        parent_id_str = ",".join(parent_node_ids) if isinstance(parent_node_ids, list) else str(parent_node_ids)
        node_element.set("parent-id", parent_id_str)
        node_element.set("node-type", node_type)
        node_element.set("parent-json-key", sanitize_string(parent_json_key))
        node_element.set("full-json-path", sanitize_string(full_path_str))
        
        # 3. 处理字段属性：生成注释节点 + 字段元素（保留逻辑，改用lxml的Comment）
        attrs = fragment.get("attributes", [])
        for field_attr in attrs:
            field_name = field_attr.get("name", "unknown")
            field_des = field_attr.get("des")
            field_exp = field_attr.get("example")
            field_note = field_attr.get("note")

            if is_nan(field_name):
                continue
            field_name = sanitize_string(field_name)

            # 拼接注释内容
            comment_parts = []
            if field_des and not is_nan(field_des):
                field_des = "".join(field_des.strip().split("\n"))
                comment_parts.append(f"{field_name} 中文描述：{str(field_des)}")
            if field_note and not is_nan(field_note):
                field_note = "；".join(field_note.strip().split("\n"))
                comment_parts.append(f"生成约束：{str(field_note)}")
            if field_exp and not is_nan(field_exp):
                comment_parts.append(f"举例：{str(field_exp)}")
            
            comment_text = " | ".join(comment_parts) if comment_parts else f"{field_name}：无辅助信息"
            # 关键：用lxml.etree.Comment创建注释节点（而非标准库ET.Comment）
            comment_node = etree.Comment(comment_text)
            node_element.append(comment_node)

            # 创建字段元素
            field_elem = etree.SubElement(node_element, field_name)
        
        # 调用lxml格式化函数
        return serialize_xml_element(node_element)



    def generate_fragments(self) -> List[Dict]:
        """基于图结构生成XML片段"""
        fragments = []
        self.id_map = {}
        
        try:
            # 1. 获取所有节点的直接子节点
            children_query = """
                MATCH (parent)-[:CONTAINS]->(child:Node)
                WHERE parent:Node OR parent:Root_Node
                RETURN id(parent) AS parent_id, 
                        id(child) AS child_id,
                        child.name AS child_name 
            """
            children_data = self.neo4j_graph.run(children_query).data()
            # 按父节点ID分组子节点
            children_map = {}
            for item in children_data:
                parent_id = item["parent_id"]
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append({
                    "child_id": item["child_id"],
                    "child_name": item["child_name"]
                })

            # 2. 获取根节点
            root_query = """
                MATCH (root:Root_Node)-[:HAS_FIELD]->(field:Field)
                RETURN root.name AS node_name,
                    root.description AS node_des,
                    id(root) AS node_id,
                    COLLECT({name: field.name, des: field.description, note: field.note, example: field.example}) AS attributes
            """
            root_result = self.neo4j_graph.run(root_query).data()
            

            if root_result:
                root_data = root_result[0]
                fragments.append({
                    "node_id": root_data["node_id"],
                    "node_name": root_data["node_name"],
                    "node_des": root_data["node_des"],
                    "parent_node_id": None,
                    "parent_node_name": None,
                    "node_type": "Root_Node",
                    "attributes": root_data["attributes"],
                    "children": children_map.get(root_data["node_id"], []),
                    "is_root": True
                })
                self.id_map[root_data["node_id"]] = f"fragment_{root_data['node_id']}.xml"
                logger.info(f"找到根节点: {root_data['node_name']} (ID: {root_data['node_id']})")

            # 3. 批量获取所有节点及其属性
            node_ids = [frag["node_id"] for frag in fragments]
            node_ids.extend(self._get_all_node_ids())
            
            batch_query = """
                UNWIND $node_ids AS node_id
                MATCH (n:Node) WHERE id(n) = node_id
                OPTIONAL MATCH (parent)-[:CONTAINS]->(n)
                OPTIONAL MATCH (n)-[:HAS_FIELD]->(field:Field)
                RETURN node_id, 
                    n.name AS node_name,
                    n.description AS node_des,
                    COLLECT(DISTINCT id(parent)) AS parent_ids,
                    COLLECT({name: field.name, des: field.description, note: field.note, example: field.example}) AS attributes
            """
            nodes = self.neo4j_graph.run(batch_query, node_ids=node_ids).data()
            
            # 4. 构建片段
            id2name = {node["node_id"]: node["node_name"] for node in nodes}
            id2name[root_data["node_id"]] =  root_data["node_name"]

            for node in nodes:
                node_id = node["node_id"]
                # 获取父节点ID（取第一个父节点）
                parent_ids = node.get("parent_ids", [])
                if node_id != root_data["node_id"] and len(parent_ids) == 0:
                    continue

                parent_node_id = ['node_root' if parent_id == root_data['node_id'] else parent_id for parent_id in parent_ids if parent_id]
                
                children = children_map.get(node_id, [])

                # 跳过已处理的根节点
                if any(frag["node_id"] == node_id for frag in fragments):
                    continue
                
                ###  对field节点去重
                unique_attributes = list()
                field_name = list()
                for attr in node["attributes"]:
                    if attr['name'] not in field_name:
                        field_name.append(attr['name'])
                        unique_attributes.append(attr)
            
                fragment = {
                    "node_id": node_id,
                    "node_name": node["node_name"],
                    "parent_node_id": parent_node_id,
                    "parent_node_name": {id: id2name.get(id) for id in parent_node_id},
                    #"node_type": sanitize_string(node["node_name"]),
                    "node_type": "Node",
                    "attributes": unique_attributes,
                    "children": children,
                    "is_root": False
                }
                    

                fragments.append(fragment)
                self.id_map[node_id] = f"fragment_{node_id}.xml"
            
            logger.info(f"共生成 {len(fragments)} 个基础XML片段")
            return fragments
            
        except Exception as e:
            logger.error(f"生成片段失败: {e}\n{traceback.format_exc()}")
            return []
    
    def _get_all_node_ids(self) -> List[int]:
        """获取所有Node节点的ID"""
        query = "MATCH (n:Node) RETURN id(n) AS node_id"
        return [record["node_id"] for record in self.neo4j_graph.run(query).data()]
    

    def _build_llm_prompt(self, fragment: Dict, xml_content: str, json_chunk: Dict) -> str:
        """重构LLM提示词：仅做字段级填充（保留原有逻辑）"""
        exclude_keys = {"_position", "_parent_json_key", "_full_path_str"}
        # 过滤JSON中指定的特殊键，生成新的嵌套字典
        json_content = {
            k: {v_k: v_v for v_k, v_v in v.items() if v_k not in exclude_keys}
            if isinstance(v, dict)  # 增加类型校验，避免非字典值导致报错
            else v
            for k, v in json_chunk["data"].items()
        }
        json_data = json.dumps(json_content, ensure_ascii=False, indent=2)

        fill_xml_prompt_file = "D:\\非shemi工作内容\陈xz_论文相关\\llm_xiangding_test_code\\prompt\\fill_xml_prompt_v4.0.txt"
        with open(fill_xml_prompt_file, "r", encoding="utf-8") as f:
            prompt_template = f.read()
        
        user_prompt = prompt_template.format(
            node_name=fragment["node_name"],
            node_id=fragment["node_id"],
            xml_content=xml_content,
            json_data=json_data
        )
        return user_prompt


    def _parse_xml_from_llm_output(self, llm_output):
        """
        从LLM的输出文本中解析出XML内容。
        这是一个复杂且需要鲁棒性处理的过程，实际实现可能需要使用正则表达式或尝试多种解析方式。
        此处为简化示例，假设LLM能返回纯净的XML。
        """
        # 示例：尝试提取可能被标记的JSON代码块
        xml_match = re.search(r'```xml\n(.*?)\n```', llm_output, re.DOTALL)
        if xml_match:
            xml_str = xml_match.group(1)
        else:
            xml_str = llm_output # 假设整个输出就是JSON

        # 这里应使用json.loads解析json_str，示例直接返回一个模拟字典
        # 实际使用时请替换为真正的解析逻辑
        return xml_str


    # ===================== 核心修改4：适配多实例的片段内容生成 =====================
    async def generate_fragment_content(
        self, 
        session: aiohttp.ClientSession, 
        instance_node_id: str,  # 带索引的实例ID（如node_100_0）
        fragment: Dict,  # 原始片段
        max_retries: int = 3
    ) -> Dict:
        """生成单个实例的片段内容（适配List元素）"""        
        try:
            # 解析实例ID（分离原始节点ID和List索引）
            id_parts = instance_node_id.split("_")
            original_node_id = int(id_parts[1]) if len(id_parts)>=2 else 0
            list_index = int(id_parts[2]) if len(id_parts)>=3 else 0
            
            # 加载对应JSON分块（已预填充List元素数据）
            json_chunk_path = os.path.join(self.json_chunk_path, f"json_chunk_{instance_node_id}.json")
            if not os.path.exists(json_chunk_path):
                raise FileNotFoundError(f"JSON分块文件不存在: {json_chunk_path}")
            
            with open(json_chunk_path, "r", encoding="utf-8") as f:
                json_chunk = json.load(f)
            
            # 加载XML分块模板
            xml_chunk_path = os.path.join(self.xml_chunk_path, f"xml_chunk_{instance_node_id}.xml")
            if not os.path.exists(xml_chunk_path):
                raise FileNotFoundError(f"XML分块文件不存在: {xml_chunk_path}")
            
            with open(xml_chunk_path, "r", encoding="utf-8") as f:
                xml_content = f.read()
            
            ## 测试.跳过没有子节点的节点

            if len(xml_content.split("\n")) > 3:
                # 构建LLM提示词
                current_prompt = self._build_llm_prompt({
                    "node_id": instance_node_id,
                    "node_name": fragment["node_name"]
                }, xml_content, json_chunk)
                last_error_message = None

                # 初始化异步客户端
                if not hasattr(self, 'async_client') or self.async_client is None:
                    self.async_client = AsyncOpenAI(
                        api_key=self.openai_api_key,
                        base_url=self.openai_base_url,
                        timeout=120
                    )
                
                for attempt in range(max_retries):
                    try:    
                        async with self.semaphore:
                            # 构建提示词（增强格式校验）
                            messages = [
                                {"role":"system", "content":"你是一名军事仿真想定脚本生成专家。请严格确保输出的XML格式正确无误，仅做字段级填充，不修改结构。"},
                                {"role": "user", "content": current_prompt}
                            ]
                        
                            # 核心修改：注释LLM调用，直接返回原XML（测试用）
                            response = await self.async_client.chat.completions.create(
                                    #model = "Qwen/Qwen3.6-35B-A3B",
                                    model="deepseek-ai/DeepSeek-V4-Flash",
                                    #model = "gpt-5.5",
                                    #model="gemini-3.1-pro-preview",
                                    #model = "deepseek-v3.1",
                                    #model="deepseek-v4-flash",
                                    messages = messages,
                                    max_tokens = self.max_completion_length,
                                    extra_body={"enable_thinking": False},
                                    temperature = 0.05,
                                    top_p = 0.1,
                                    frequency_penalty = 0.1,
                                    presence_penalty = 0.1,
                                    #timeout=600000,
                            )
                            content = response.choices[0].message.content
                            #content = xml_content
                            #print(content)
                            # 保存LLM输入输出
                            prompt_file = os.path.join(self.prompt_path, f"prompt_ans_{instance_node_id}.txt")
                            with open(prompt_file, "w", encoding="utf-8") as pr_out:
                                print("PROMPT:", file=pr_out)
                                print(current_prompt, file=pr_out)
                                print("ANSWER:", file=pr_out)
                                print(content, file=pr_out)
                        
                        # 解析并验证XML
                        parser = etree.XMLParser(remove_blank_text=True, recover=True)
                        content = self._parse_xml_from_llm_output(content)
                        xml_tree = etree.fromstring(content, parser)
                        # print(content)
                        # print(xml_tree)
                        
                        # 保存填充后的XML分块
                        filled_xml = etree.tostring(xml_tree, encoding="unicode", pretty_print=True)
                        Path(xml_chunk_path).write_text(filled_xml, encoding="utf-8")
                        
                        result = {
                            "instance_node_id": instance_node_id,
                            "original_node_id": original_node_id,
                            "list_index": list_index,
                            "content": filled_xml,
                            "status": "success",
                            "xml_chunk_path": xml_chunk_path,
                            "json_chunk_path": json_chunk_path
                        }
                        logger.info(f"实例 {instance_node_id} 生成成功")
                        return result
                            
                    # 【修改7】补充异常捕获：包含XML解析错误和权限错误
                    except (APIConnectionError, APIError, etree.XMLSyntaxError, PermissionError) as e:
                        last_error_message = f"执行错误: {getattr(e, 'status_code', '未知')} - {e}"
                        logger.warning(f"实例 {instance_node_id} 第 {attempt + 1} 次尝试失败: {last_error_message}")
                    except Exception as e:
                        last_error_message = str(e)
                        logger.warning(f"实例 {instance_node_id} 第 {attempt + 1} 次尝试失败: {e}")
                    
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # 指数退避
                    else:
                        # 重试耗尽后的处理失败结果
                        # 使用模板作为xml块
                        result = {
                            "instance_node_id": instance_node_id,
                            "original_node_id": original_node_id,
                            "list_index": list_index,
                            #"content": xml_content,
                            "status": "failed",
                            "error": last_error_message
                        }
                        #logger.info(f"实例 {instance_node_id} 生成失败，直接使用模板默认值")
                        return result
            else:
                # 解析并验证XML
                parser = etree.XMLParser(remove_blank_text=True)
                xml_tree = etree.fromstring(xml_content, parser)

                # 移除锚点属性（不影响仿真引擎解析）- 保留parent-json-key和full-json-path
                # for attr in ["中文描述", "单位或取值或数据类型", "举例"]:
                #     if attr in xml_tree.attrib:
                #         del xml_tree.attrib[attr]

                # 保存填充后的XML分块
                filled_xml = etree.tostring(xml_tree, encoding="unicode", pretty_print=True)
                Path(xml_chunk_path).write_text(filled_xml, encoding="utf-8")
                result = {
                    "instance_node_id": instance_node_id,
                    "original_node_id": original_node_id,
                    "list_index": list_index,
                    "content": filled_xml,
                    "status": "success",
                    "xml_chunk_path": xml_chunk_path,
                    "json_chunk_path": json_chunk_path
                }
                logger.info(f"实例 {instance_node_id} 直接使用模板默认值")

        except Exception as e:
            logger.error(f"实例 {instance_node_id} 生成过程异常: {e}\n{traceback.format_exc()}")
            result = {
                "instance_node_id": instance_node_id,
                "status": "failed",
                "error": str(e)
            }
            return result

    # ===================== 核心修改5：批量生成所有实例的片段内容 =====================
    async def fill_all_chunk_instances(
        self, 
        fragments: List[Dict], 
        chunk_mapping: Dict
    ) -> List[Dict]:
        """并行生成所有List元素实例的片段内容"""
        # 【修改8】移除冗余的aiohttp.ClientSession（LLM调用被注释）
        # 构建任务列表
        tasks = []
        for instance_id in chunk_mapping.keys():
            # 找到原始片段（通过original_node_id匹配）
            original_node_id = int(chunk_mapping[instance_id]["original_node_id"])
            fragment = next((f for f in fragments if f["node_id"] == original_node_id), None)
            if fragment is None:
                logger.warning(f"未找到原始片段（node_id={original_node_id}），跳过实例{instance_id}")
                continue
            # 直接调用，无需session
            tasks.append(self.generate_fragment_content(None, instance_id, fragment))
        
        # 并行执行
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 过滤异常结果
        final_results = []
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"任务执行异常: {res}")
                final_results.append({
                    "status": "failed",
                    "error": str(res)
                })
            else:
                final_results.append(res)
        return final_results
    

    def assemble_final_xml(self, chunk_mapping):
        """
        基于JSON路径递归查找父节点，构建完整XML树
        """
        # 2. 构建节点索引（支持多维度查找）
        # - 按完整JSON路径索引
        path2node = {node["full_json_path"]: node for node in chunk_mapping.values()}
        # - 按JSON节点名+父键索引（兼容短路径匹配）
        name_parent2node = {}
        for node in chunk_mapping.values():
            key = (node["json_node_name"], node["parent_json_key"])
            name_parent2node[key] = node
        
        # 3. 找到根节点（JSON路径最顶层）
        root_nodes = [
            node for node in chunk_mapping.values()
            if node["parent_json_key"] == "Scenario" or node["parent_json_key"] is None
            or len(node["full_json_path"].split("->")) == 1  # 路径只有一层（根）
        ]
        if not root_nodes:
            # 兜底：按node_type找Root_Node
            root_nodes = [node for node in chunk_mapping.values() if node["node_type"] == "Root_Node"]
        if not root_nodes:
            raise ValueError("未找到XML根节点")
        root_node = root_nodes[0]

        # 4. 递归构建XML树（从根节点向下拼接子节点）
        root_xml_elem = self._parse_xml_chunk(root_node["xml_chunk_path"])
        
        self._recursively_attach_children(
            current_node=root_node,
            current_xml_elem=root_xml_elem,
            chunk_mapping=chunk_mapping,
            path2node=path2node,
            name_parent2node=name_parent2node
        )

        # 5. 保存最终XML文件
        # 改用lxml序列化，避免minidom和编码声明冲突
        # 1. 确保root_xml_elem是lxml.etree.Element类型（如果是标准库ET的，先转换）
        if isinstance(root_xml_elem, etree.Element):
            # 标准库ET节点转lxml节点（兼容旧代码）
            root_xml_elem = etree.fromstring(ET.tostring(root_xml_elem, encoding='utf-8'))

        # 2. lxml格式化（无编码声明，带缩进）
        final_xml_bytes = etree.tostring(
            root_xml_elem,
            encoding='utf-8',
            pretty_print=True,
            xml_declaration=False,  # 不生成编码声明
            with_comments=True
        )
        final_xml = final_xml_bytes.decode('utf-8')
        # 可选：如果需要XML声明，最后手动加（仅在最终文件最顶部加一次）
        final_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + final_xml
        
        return final_xml

    def _remove_specific_attributes(self, elem: etree._Element):
        """递归遍历所有元素，删除指定属性"""
        # 要删除的属性列表（包含中文属性）
        attrs_to_remove = [
            "xml-id", "parent-json-key", "full-json-path", 
            "parent-id", "node-type", "中文描述", 
            "生成约束", "举例"
        ]
        
        # 删除当前元素的指定属性
        for attr in attrs_to_remove:
            if attr in elem.attrib:
                del elem.attrib[attr]
        
        # 递归处理所有子元素
        for child in elem:
            self._remove_specific_attributes(child)


    def _parse_xml_chunk(self, xml_chunk_path: str) -> etree._Element:
        try:
            # 核心修改：使用lxml解析，保留命名空间
            parser = etree.XMLParser(remove_blank_text=True, recover=True)
            xml_tree = etree.parse(xml_chunk_path, parser)
            current_elem = xml_tree.getroot()
            # 移除锚点属性（不影响仿真引擎解析）
            self._remove_specific_attributes(current_elem)
            return current_elem
        except Exception as e:
            logger.error(f"解析XML分块 {xml_chunk_path} 失败: {e}")
            # 创建替代元素
            node_name = os.path.basename(xml_chunk_path).replace("xml_chunk_", "").replace(".xml", "")
            return etree.Element(sanitize_string(node_name))            


    def _recursively_attach_children(
        self,
        current_node: dict,
        current_xml_elem: ET.Element,
        chunk_mapping: dict,
        path2node: dict,
        name_parent2node: dict
    ):
        """
        递归查找并挂载子节点
        - 普通节点：基于JSON路径匹配父节点
        - Cov / Cov_Ill：没有对应JSON分块，按XML图结构父子关系直接挂载
        """
        xml_only_node_names = {"Cov", "Cov_Ill", "Ptc", "Ptc-track", "Weaps", "Navigator", "AI", "Kinematics", "Damage", "AirOps", "Aircraft_Sensory", "Doctrine"}

        current_node_id = current_node.get("node_id")
        current_full_path = current_node.get("full_json_path", "")
        current_path_fragments = [
            f.strip() for f in current_full_path.split("->") if f.strip()
        ]
        current_path_depth = len(current_path_fragments)

        for node_id, child_node in chunk_mapping.items():
            # 跳过自身，避免循环
            if child_node.get("node_id") == current_node_id:
                continue

            child_node_name = (
                child_node.get("node_name")
                or child_node.get("json_node_name")
                or ""
            )

            # 特殊处理：Cov / Cov_Ill 没有对应JSON路径，不能用JSON路径判断父节点
            if child_node_name in xml_only_node_names:
                child_parent_id = (
                    child_node.get("parent_id")
                    or child_node.get("instance_parent_id")
                    or child_node.get("parent_node_id")
                    or child_node.get("original_parent_id")
                    or []
                )

                if not isinstance(child_parent_id, list):
                    child_parent_id = [child_parent_id]

                current_id_candidates = {
                    current_node_id,
                    current_node_id.replace("node_", "", 1),
                }

                child_parent_candidates = set()
                for pid in child_parent_id:
                    if not pid:
                        continue
                    pid = str(pid)
                    child_parent_candidates.add(pid)
                    child_parent_candidates.add(pid.replace("node_", "", 1))

                if not current_id_candidates.intersection(child_parent_candidates):
                    continue
                
                child_xml_elem = self._parse_xml_chunk(child_node["xml_chunk_path"])
                
                current_xml_elem.append(child_xml_elem)

                self._recursively_attach_children(
                    current_node=child_node,
                    current_xml_elem=child_xml_elem,
                    chunk_mapping=chunk_mapping,
                    path2node=path2node,
                    name_parent2node=name_parent2node
                )
                continue

            # 普通节点仍然走原来的JSON路径挂载逻辑
            child_full_path = child_node.get("full_json_path", "")
            if not child_full_path or child_full_path == "unknown":
                continue

            child_path_fragments = [
                f.strip() for f in child_full_path.split("->") if f.strip()
            ]

            # 条件1：子节点路径深度 = 当前节点路径深度 + 1
            if len(child_path_fragments) != current_path_depth + 1:
                continue

            # 条件2：子节点的父路径 = 当前节点的完整路径
            child_parent_path = " -> ".join(child_path_fragments[:-1])
            if child_parent_path != current_full_path:
                continue

            # 条件3：子节点的parent_json_key匹配当前节点标识
            current_node_identifier = self._get_node_identifier(current_node)
            if child_node.get("parent_json_key") != current_node_identifier:
                continue

            child_xml_elem = self._parse_xml_chunk(child_node["xml_chunk_path"])
            current_xml_elem.append(child_xml_elem)

            self._recursively_attach_children(
                current_node=child_node,
                current_xml_elem=child_xml_elem,
                chunk_mapping=chunk_mapping,
                path2node=path2node,
                name_parent2node=name_parent2node
            )

    def _get_node_identifier(self, node: dict) -> str:
        """
        获取节点的唯一标识（用于匹配子节点的parent_json_key）
        兼容两种格式：
        - 列表节点：Aircraft[1]
        - 字典节点：Scenario
        """
        # 从full_json_path最后一段解析标识
        path_fragments = [f.strip() for f in node["full_json_path"].split("->") if f.strip()]
        last_fragment = path_fragments[-1] if path_fragments else node["json_node_name"]
        
        # 处理列表元组格式 (Aircraft, 1) → Aircraft[1]
        if "(" in last_fragment and "," in last_fragment and ")" in last_fragment:
            last_fragment = last_fragment.replace("(", "").replace(")", "").replace(" ", "")
            name, idx = last_fragment.split(",")
            return f"{name}[{idx}]"
        # 处理普通字典节点
        else:
            return last_fragment


    # ===================== 新增：主执行函数（整合所有逻辑） =====================
    async def run(
        self, 
        json_data: Dict  # 输入的长JSON数据
    ) -> List[str]:
        """
        主执行流程：
        1. 从Neo4j生成基础片段
        2. 提取JSON中各节点的List元素
        3. 生成多实例分块
        4. 异步填充分块内容
        5. 为每个List元素生成独立XML + 合并完整XML
        """
        try:
            ## 记录开始时间
            start_time = time.time()

            self.prepare_workspace()

            # 1. 创建索引
            self.create_indexes()
            
            # 2. 生成基础片段
            fragments = self.generate_fragments()
            if not fragments:
                raise ValueError("未从Neo4j生成任何片段")
                            
            # 3. 提取JSON中各节点类型对应的List元素
            entity_data_map = {}
            for fragment in fragments:
                node_name = fragment["node_name"]
                node_id = fragment["node_id"]
                entity_data_map[node_id] = extract_nested_entity_data(nested_data=json_data, fragment=fragment)
           

            # 对全部实体统一分配业务ID
            # ==========================================================
            entity_data_map = self._inject_ids_into_entity_data_map(
                entity_data_map
            )
    
            # 【新增】立即保存全局实体ID映射。
            # 后续并行生成或失败重试时必须复用这份映射。
            self.entity_id_registry.save(
                self.entity_id_map_file
            )

            logger.info(
                "实体业务ID预分配完成，共分配 %d 个ID，映射文件：%s",
                len(self.entity_id_registry.entity_ids),
                self.entity_id_map_file,
            )

            # 保存entity_data_map（调试用）
            #entity_map_file = "./middle_output/entity_data_map.json"
            Path(self.entity_map_file).write_text(
                json.dumps(entity_data_map, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info(f"实体数据映射已保存至: {self.entity_map_file}")

            # 4. 生成多实例分块
            xml_chunks, chunk_mapping = self.generate_aligned_chunks(fragments, entity_data_map)
            

            # 5. 异步填充分块内容
            fragment_results = await self.fill_all_chunk_instances(fragments, chunk_mapping)
        
            # 6. 检查失败实例
            failed_instances = [r for r in fragment_results if isinstance(r, dict) and r["status"] == "failed"]
            if failed_instances:
                sussess_ratio = (len(fragment_results) - len(failed_instances)) / len(fragment_results)
                logger.warning(f"共{len(failed_instances)}个实例生成失败: {[r['instance_node_id'] for r in failed_instances]}, 生成成功的分块比率为 {sussess_ratio:.2f}")
            else:
                logger.info(f"所有实例生成成功!")
                sussess_ratio = 1
            
            # 7. 为每个List元素生成独立XML + 合并完整XML
            final_xml = self.assemble_final_xml(chunk_mapping)

            final_xml_path = os.path.join(self.final_xml_path, "final_tree.xml")
            #os._exit(0)
            with open(final_xml_path, "w", encoding="utf-8") as f:
                f.write(final_xml)
            logger.info(f"完整XML树已保存至: {final_xml_path}")
            
            end_time = time.time()
            total_time = end_time - start_time
            logger.info(f"总耗时: {total_time:.2f} 秒")  
            with open(self.ex_info_file, "w", encoding="utf-8") as f:
                f.write(f"总耗时: {total_time:.2f} 秒\n")
                f.write(f"生成成功的分块比率: {sussess_ratio:.2f}\n")
                f.write(f"失败实例数量: {len(failed_instances)}\n")
                f.write(f"失败实例节点ID: {[r['instance_node_id'] for r in failed_instances]}\n")   
            
        except Exception as e:
            logger.error(f"主流程执行失败: {e}\n{traceback.format_exc()}")
            raise


async def main() -> None:
    # 配置参数
    NEO4J_URI = "bolt://workspace.featurize.cn:20735"
    NEO4J_USER = "neo4j"
    NEO4J_PASSWORD = "1234567890"
    LLM_BASE_URL = "https://api.siliconflow.cn/v1"
    LLM_API_KEY ="sk-wocixetrhbgthoepmdjdycdadrcyudtepyujtqwcirkxmzvh" # 硅基API密钥
    max_workers = 16

    final_output_dir = r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\output"
    llm_generated_json_file = r"D:\非shemi工作内容\陈xz_论文相关\data_generater\测试节点结构数据\L1\01.想定精细度想定_llm_extract_with_guid.json"

          
    # 加载长JSON数据
    with open(llm_generated_json_file, "r", encoding="utf-8") as f:
        json_data = json.load(f)
    
    file_name = os.path.basename(llm_generated_json_file).replace("_llm_extract_with_guid.json", "")
    sub_dir = os.path.basename(os.path.dirname(llm_generated_json_file))
    exp_final_output_dir = os.path.join(final_output_dir, sub_dir, file_name)
    output_root = Path(exp_final_output_dir)
    middle_root = output_root / "middle_output"
    json_chunk_path = middle_root / "json_chunks"
    xml_chunk_path = middle_root / "xml_chunks"
    prompt_path = middle_root / "prompt"
    chunk_mapping_path = middle_root / "chunk_mapping.json"
    entity_map_file = middle_root / "entity_data_map.json"
    ex_info_path = middle_root / "info.txt"
    final_xml_path = output_root / "final_xml"
    
    # # 初始化生成器
    generator = TDVAXMLGenerator(
        neo4j_uri=NEO4J_URI,
        neo4j_auth=(NEO4J_USER, NEO4J_PASSWORD),
        openai_api_key=LLM_API_KEY,
        openai_base_url=LLM_BASE_URL,
        max_workers=max_workers,
        chunk_mapping_path=str(chunk_mapping_path),
        entity_map_file=str(entity_map_file),
        prompt_path=str(prompt_path),
        json_chunk_path=str(json_chunk_path),
        xml_chunk_path=str(xml_chunk_path),
        final_xml_path=str(final_xml_path),  # 核心修改：指定最终XML输出目录
        ex_info_path=str(ex_info_path)
    )

    # 执行生成流程    
    await generator.run(json_data)



if __name__ == "__main__":
    asyncio.run(main())