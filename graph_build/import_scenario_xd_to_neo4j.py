#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
将 scenario_xd.xml 导入 Neo4j，构建 XML 模板图。

图模型：

1. 普通 XML 节点：
       (:Node {id: "...", name: "..."})

2. XML 根节点 ScenarioData：
       (:Node:Root_Node {
           id: "scenario_root",
           name: "ScenarioData"
       })

3. XML 标签属性：
       (:Node {
           id: "...",
           name: "ScenarioBackground",
           Type: "ScenarioBackground"
       })

4. XML 叶子元素：
       (:Field {
           id: "...",
           name: "Name",
           example: "Scenario A"
       })

4. XML 父子关系：
       (:Node)-[:CONTAINS {order: 0}]->(:Node)

5. XML 节点与字段关系：
       (:Node)-[:HAS_FIELD {order: 0}]->(:Field)

设计目标：
1. 不改变现有 run.py 的整体处理流程；
2. DataList/主题列表/Item_n 不作为具体业务数据节点保存；
3. 将 Item_n 归一为一个 Item_TEMPLATE 模板节点；
4. 后续由 run.py 根据 JSON 数据动态生成 Item_0、Item_1、Item_2；
5. XML 节点属性直接保存为 Node 节点属性；
6. 没有子元素的 XML 元素保存为 Field 节点；
6. XML 节点名称统一保存为 Node.name；
7. ScenarioData 使用 Root_Node 标签，同时保留 Node 标签，兼容 run.py；
8. 导入前清空 Neo4j 当前数据库。

依赖：
    pip install lxml py2neo
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lxml import etree
from py2neo import Graph, Node, Relationship

# ============================================================================
# 日志配置
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

logger = logging.getLogger("scenario_xml_import")

# ============================================================================
# Neo4j 图模型配置
# ============================================================================

NODE_LABEL = "Node"
ROOT_NODE_LABEL = "Root_Node"
FIELD_LABEL = "Field"

CHILD_RELATION = "CONTAINS"
FIELD_RELATION = "HAS_FIELD"

# ============================================================================
# XML 配置
# ============================================================================

ITEM_PATTERN = re.compile(r"^Item_\d+$")

ROOT_NODE_NAME = "ScenarioData"
ROOT_NODE_ID = "scenario_root"

TOPIC_LIST_PATH = (
    ROOT_NODE_NAME,
    "DataList",
    "主题列表",
)

# ============================================================================
# 工具函数
# ============================================================================

def remove_namespace(tag: str) -> str:
    """
    去除 XML 命名空间。

    例如：
        {http://example.com}ScenarioData

    转换为：
        ScenarioData
    """
    if not tag:
        return tag

    if tag.startswith("{"):
        return tag.split("}", 1)[1]

    return tag

def is_item_tag(tag: str) -> bool:
    """
    判断节点是否为 Item_n。
    """
    return bool(
        ITEM_PATTERN.match(
            remove_namespace(tag)
        )
    )

def make_stable_id(
    value: str,
    prefix: str = "xml",
) -> str:
    """
    根据 XML 路径生成稳定 ID。
    """
    digest = hashlib.sha1(
        value.encode("utf-8")
    ).hexdigest()[:20]

    return f"{prefix}_{digest}"

def make_node_path(
    path_parts: Tuple[str, ...],
) -> str:
    """
    根据 XML 路径生成逻辑路径。
    """
    return "/" + "/".join(path_parts)

def get_element_attributes(
    element: etree._Element,
) -> Dict[str, str]:
    """
    获取 XML 节点属性，并去除属性名命名空间。
    """
    attributes: Dict[str, str] = {}

    for key, value in element.attrib.items():
        attributes[remove_namespace(key)] = value

    return attributes

def get_element_children(
    element: etree._Element,
) -> List[etree._Element]:
    """
    获取 XML 的有效子节点。
    """
    return [
        child
        for child in element
        if isinstance(child.tag, str)
    ]

def get_element_text(
    element: etree._Element,
) -> str:
    """
    获取 XML 节点的直接文本。
    """
    return (element.text or "").strip()

def merge_xml_elements(
    base_element: etree._Element,
    other_element: etree._Element,
) -> None:
    """
    将其他 XML 节点的结构合并到 base_element。

    这里只合并结构、属性和必要的示例文本，
    不保留不同 Item_n 的具体业务数据。
    """
    base_children = get_element_children(base_element)
    other_children = get_element_children(other_element)

    # 合并 XML 属性。
    for key, value in other_element.attrib.items():
        if key not in base_element.attrib:
            base_element.set(key, value)

    # 如果基础节点没有文本，则保留其他节点的示例文本。
    if not get_element_text(base_element):
        other_text = get_element_text(other_element)

        if other_text:
            base_element.text = other_text

    # 按子节点名称合并结构。
    existing_children: Dict[str, etree._Element] = {}

    for child in base_children:
        child_name = remove_namespace(child.tag)

        if is_item_tag(child_name):
            child_name = "Item_TEMPLATE"

        if child_name not in existing_children:
            existing_children[child_name] = child

    for other_child in other_children:
        child_name = remove_namespace(other_child.tag)

        if is_item_tag(child_name):
            child_name = "Item_TEMPLATE"

        if child_name in existing_children:
            merge_xml_elements(
                existing_children[child_name],
                other_child,
            )
            continue

        copied_child = copy.deepcopy(other_child)

        if is_item_tag(
            remove_namespace(copied_child.tag)
        ):
            copied_child.tag = "Item_TEMPLATE"

        base_element.append(copied_child)
        existing_children[child_name] = copied_child

# ============================================================================
# Neo4j 写入函数
# ============================================================================
def create_or_update_node(
    graph: Graph,
    node_id: str,
    node_name: str,
    example: str = "",
    attributes: Optional[Dict[str, str]] = None,
    xml_path: str = "",
    is_root: bool = False,
) -> Node:
    """
    创建 XML Node 节点。

    有子元素的 XML 元素保存为 Node。

    XML 属性直接保存到 Node 属性中，例如：

        <ScenarioBackground Type="ScenarioBackground">

    保存为：

        (:Node {
            id: "...",
            name: "ScenarioBackground",
            Type: "ScenarioBackground"
        })
    """
    node_properties = {
        "id": node_id,
        "name": node_name,
        "example": example,
        "xml_path": xml_path,
    }

    if attributes:
        for key, value in attributes.items():
            # 避免 XML 属性覆盖图模型自己的保留属性。
            if key in {
                "id",
                "name",
                "example",
                "xml_path",
            }:
                node_properties[
                    f"xml_attr_{key}"
                ] = value
            else:
                node_properties[key] = value

    if is_root:
        node = Node(
            NODE_LABEL,
            ROOT_NODE_LABEL,
            **node_properties,
        )
    else:
        node = Node(
            NODE_LABEL,
            **node_properties,
        )

    graph.create(node)

    return node

def create_or_update_field(
    graph: Graph,
    parent_node: Node,
    field_id: str,
    field_name: str,
    field_value: str,
    field_path: str,
    field_order: int,
    attributes: Optional[Dict[str, str]] = None,
) -> Node:
    """创建 Field，保存叶子元素的文本及 XML 属性。"""
    field_properties = {
        "id": field_id,
        "name": field_name,
        "example": field_value,
        "xml_path": field_path,
    }

    # 与 create_or_update_node() 的保留属性规则保持一致。
    reserved_properties = {
        "id",
        "name",
        "example",
        "xml_path",
    }

    for key, value in (attributes or {}).items():
        property_key = (
            f"xml_attr_{key}"
            if key in reserved_properties
            else key
        )
        field_properties[property_key] = value

    field = Node(
        FIELD_LABEL,
        **field_properties,
    )

    graph.create(field)

    relation = Relationship(
        parent_node,
        FIELD_RELATION,
        field,
        order=field_order,
        child_order=field_order,
    )

    graph.create(relation)

    return field


def create_child_relation(
    graph: Graph,
    parent_node: Node,
    child_node: Node,
    child_order: int,
) -> None:
    """
    创建 XML 父子关系。
    """
    relation = Relationship(
        parent_node,
        CHILD_RELATION,
        child_node,
        order=child_order,
        child_order=child_order,
    )

    graph.create(relation)


def create_leaf_field(
    graph: Graph,
    parent_node: Node,
    element: etree._Element,
    field_path: str,
    field_order: int,
) -> Node:
    """将没有子元素的 XML 元素保存为 Field，保留 XML 属性。"""
    field_name = remove_namespace(element.tag)
    field_value = get_element_text(element)
    field_attributes = get_element_attributes(element)

    field_id = make_stable_id(
        field_path,
        prefix="field",
    )

    return create_or_update_field(
        graph=graph,
        parent_node=parent_node,
        field_id=field_id,
        field_name=field_name,
        field_value=field_value,
        field_path=field_path,
        field_order=field_order,
        attributes=field_attributes,
    )


# ============================================================================
# XML 导入逻辑
# ============================================================================

class ScenarioXmlImporter:
    """
    scenario_xd.xml 导入器。
    """

    def __init__(
        self,
        graph: Graph,
    ) -> None:
        self.graph = graph
        self.imported_node_count = 0
        self.imported_field_count = 0

    def import_file(
        self,
        xml_path: Path,
    ) -> int:
        """
        导入 XML 文件。
        """
        logger.info(
            "开始解析 XML：%s",
            xml_path,
        )

        parser = etree.XMLParser(
            remove_blank_text=True,
            resolve_entities=False,
            no_network=True,
            recover=False,
            huge_tree=True,
        )

        tree = etree.parse(
            str(xml_path),
            parser,
        )

        root = tree.getroot()
        root_name = remove_namespace(root.tag)

        if root_name != ROOT_NODE_NAME:
            raise ValueError(
                "XML 根节点错误："
                f"期望 {ROOT_NODE_NAME}，"
                f"实际为 {root_name}"
            )

        topic_item_template = (
            self._build_topic_item_template(root)
        )

        self._import_root(
            root=root,
            topic_item_template=topic_item_template,
        )

        logger.info(
            "XML 导入完成：节点 %d 个，字段 %d 个",
            self.imported_node_count,
            self.imported_field_count,
        )

        return self.imported_node_count

    def _build_topic_item_template(
        self,
        root: etree._Element,
    ) -> Optional[etree._Element]:
        """
        从主题列表下的所有 Item_n 构建通用 Item_TEMPLATE。
        """
        topic_list = self._find_topic_list(root)

        if topic_list is None:
            logger.warning(
                "XML 中没有找到路径：%s",
                "/".join(TOPIC_LIST_PATH),
            )
            return None

        item_elements = [
            child
            for child in get_element_children(topic_list)
            if is_item_tag(child.tag)
        ]

        if not item_elements:
            logger.warning(
                "主题列表下没有发现 Item_n 节点"
            )
            return None

        template = copy.deepcopy(
            item_elements[0]
        )

        template.tag = "Item_TEMPLATE"

        for item_element in item_elements[1:]:
            merge_xml_elements(
                template,
                item_element,
            )

        return template

   
    def _build_item_template(
        self,
        parent: etree._Element,
    ) -> Optional[etree._Element]:
        """合并同一父节点下的 Item_n，生成一个独立模板。"""
        items = [
            child
            for child in get_element_children(parent)
            if is_item_tag(child.tag)
        ]

        if not items:
            return None

        template = copy.deepcopy(items[0])
        template.tag = "Item_TEMPLATE"

        for item in items[1:]:
            merge_xml_elements(template, item)

        return template

   
    def _find_topic_list(
        self,
        root: etree._Element,
    ) -> Optional[etree._Element]:
        """
        按固定路径查找主题列表。
        """
        current = root

        if (
            remove_namespace(current.tag)
            != TOPIC_LIST_PATH[0]
        ):
            return None

        for expected_name in TOPIC_LIST_PATH[1:]:
            found = None

            for child in get_element_children(current):
                if (
                    remove_namespace(child.tag)
                    == expected_name
                ):
                    found = child
                    break

            if found is None:
                return None

            current = found

        return current

    def _import_root(
        self,
        root: etree._Element,
        topic_item_template: Optional[etree._Element],
    ) -> None:
        """
        导入 XML 根节点。
        """
        self._visit_element(
            element=root,
            parent_node=None,
            path_parts=(ROOT_NODE_NAME,),
            sibling_order=0,
            topic_item_template=topic_item_template,
        )

    def _visit_element(
        self,
        element: etree._Element,
        parent_node: Optional[Node],
        path_parts: Tuple[str, ...],
        sibling_order: int,
        topic_item_template: Optional[etree._Element],
        is_reusable_item: bool = False,
    ) -> Node:
        """
        递归导入有子元素的 XML 节点。

        当前函数只创建 Node。
        没有子元素的 XML 元素由父节点循环创建为 Field。
        """
        raw_name = remove_namespace(element.tag)

        is_root = (
            parent_node is None
            and raw_name == ROOT_NODE_NAME
            and path_parts == (ROOT_NODE_NAME,)
        )

        is_topic_list = (
            path_parts == TOPIC_LIST_PATH
        )

        is_topic_item = (
            len(path_parts)
            == len(TOPIC_LIST_PATH) + 1
            and path_parts[:len(TOPIC_LIST_PATH)]
            == TOPIC_LIST_PATH
            and is_item_tag(raw_name)
        )

        node_name = raw_name
        normalized_path_parts = path_parts

        if is_topic_item:
            node_name = "Item_TEMPLATE"
            normalized_path_parts = (
                TOPIC_LIST_PATH
                + ("Item_TEMPLATE",)
            )
            is_reusable_item = True

        elif is_reusable_item and is_item_tag(raw_name):
            node_name = "Item_TEMPLATE"

        xml_path = make_node_path(
            normalized_path_parts
        )

        text = get_element_text(element)
        attributes = get_element_attributes(element)

        node_id = (
            ROOT_NODE_ID
            if is_root
            else make_stable_id(
                xml_path,
                prefix="node",
            )
        )

        node = create_or_update_node(
            graph=self.graph,
            node_id=node_id,
            node_name=node_name,
            example=text,
            attributes=attributes,
            xml_path=xml_path,
            is_root=is_root,
        )

        self.imported_node_count += 1

        if parent_node is not None:
            create_child_relation(
                graph=self.graph,
                parent_node=parent_node,
                child_node=node,
                child_order=sibling_order,
            )

        children = get_element_children(element)
        item_template = self._build_item_template(element)
        item_template_inserted = False

        for child_index, child in enumerate(children):
            child_name = remove_namespace(child.tag)

            if is_item_tag(child_name):
                if item_template_inserted:
                    continue

                if item_template is None:
                    continue

                child = item_template
                child_name = "Item_TEMPLATE"
                item_template_inserted = True

            child_path_parts = (
                normalized_path_parts
                + (child_name,)
            )

            child_path = make_node_path(
                child_path_parts
            )

            child_children = get_element_children(
                child
            )

            if not child_children:
                # 叶子 XML 元素创建为 Field。
                create_leaf_field(
                    graph=self.graph,
                    parent_node=node,
                    element=child,
                    field_path=child_path,
                    field_order=child_index,
                )

                self.imported_field_count += 1
                continue

            # 非叶子 XML 元素创建为 Node，
            # 通过 CONTAINS 连接到当前 Node。
            self._visit_element(
                element=child,
                parent_node=node,
                path_parts=child_path_parts,
                sibling_order=child_index,
                topic_item_template=topic_item_template,
                is_reusable_item=is_reusable_item,
            )

        return node

# ============================================================================
# Neo4j 索引和数据库操作
# ============================================================================

def create_indexes(
    graph: Graph,
) -> None:
    """
    创建 Neo4j 索引。
    """
    index_queries = [
        """
        CREATE INDEX node_id_index IF NOT EXISTS
        FOR (n:Node) ON (n.id)
        """,
        """
        CREATE INDEX field_id_index IF NOT EXISTS
        FOR (f:Field) ON (f.id)
        """,
    ]

    for query in index_queries:
        try:
            graph.run(query)
        except Exception as exc:
            logger.warning(
                "创建索引失败，继续执行：%s",
                exc,
            )

def clear_database(
    graph: Graph,
) -> None:
    """
    清空当前 Neo4j 数据库。

    注意：
    该操作会删除当前数据库中的全部节点和关系。
    """
    logger.warning(
        "正在清空 Neo4j 数据库……"
    )

    graph.run(
        """
        MATCH (n)
        DETACH DELETE n
        """
    )

    logger.warning(
        "Neo4j 数据库清空完成"
    )

def test_connection(
    graph: Graph,
) -> None:
    """
    测试 Neo4j 连接。
    """
    result = graph.run(
        "RETURN 1 AS result"
    ).data()

    if (
        not result
        or result[0].get("result") != 1
    ):
        raise RuntimeError(
            "Neo4j 连接测试失败"
        )

# ============================================================================
# main
# ============================================================================

def main() -> None:
    """
    程序入口。
    """
    # ========================================================================
    # 配置
    # ========================================================================
    XML_PATH = Path(
        r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\example\scenario_xd.xml"
    )

    NEO4J_URI = (
        "bolt://workspace.featurize.cn:13533"
    )

    NEO4J_USER = "neo4j"
    NEO4J_PASSWORD = "1234567890"
    NEO4J_DATABASE = "neo4j"

    # 每次导入前清空当前 Neo4j 数据库。
    CLEAR_DATABASE = True

    # ========================================================================
    # 配置结束
    # ========================================================================

    if not XML_PATH.exists():
        raise FileNotFoundError(
            f"XML 文件不存在：{XML_PATH}"
        )

    if not XML_PATH.is_file():
        raise FileNotFoundError(
            f"XML 路径不是文件：{XML_PATH}"
        )

    logger.info(
        "XML 文件：%s",
        XML_PATH,
    )

    logger.info(
        "Neo4j 地址：%s",
        NEO4J_URI,
    )

    logger.info(
        "Neo4j 数据库：%s",
        NEO4J_DATABASE,
    )

    graph = Graph(
        NEO4J_URI,
        auth=(
            NEO4J_USER,
            NEO4J_PASSWORD,
        ),
        name=NEO4J_DATABASE,
    )

    test_connection(graph)

    logger.info(
        "Neo4j 连接成功"
    )

    if CLEAR_DATABASE:
        clear_database(graph)

    create_indexes(graph)

    importer = ScenarioXmlImporter(
        graph=graph,
    )

    imported_count = importer.import_file(
        xml_path=XML_PATH,
    )

    logger.info(
        "导入完成，共处理 Node 节点：%d",
        imported_count,
    )

    logger.info(
        "根节点标签：%s",
        ROOT_NODE_LABEL,
    )

    logger.info(
        "根节点 ID：%s",
        ROOT_NODE_ID,
    )

    logger.info(
        "普通节点标签：%s",
        NODE_LABEL,
    )

    logger.info(
        "字段节点标签：%s",
        FIELD_LABEL,
    )

    logger.info(
        "父子关系：%s",
        CHILD_RELATION,
    )

    logger.info(
        "字段关系：%s",
        FIELD_RELATION,
    )

    logger.info(
        "DataList/主题列表下使用 Item_TEMPLATE，"
        "不会固定生成 Item_0、Item_1、Item_2"
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.error(
            "用户中断程序"
        )
        sys.exit(1)
    except Exception as exc:
        logger.exception(
            "程序执行失败：%s",
            exc,
        )
        sys.exit(1)