import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from xml.dom import minidom
from xml.sax.saxutils import escape

def sanitize_xml_name(value: str) -> str:
    """
    将字段名清理为可用于 XML 标签的名称。
    """
    value = "" if value is None else str(value)

    value = re.sub(r"[\u4e00-\u9fff]+", "", value)
    value = re.sub(r"\s+", "", value)
    value = value.replace("/", "_")
    value = value.replace("+", "_")
    value = value.replace("，", "")
    value = value.replace("：", "")

    return escape(value)

def is_nan(value) -> bool:
    """检查值是否为 NaN。"""
    try:
        return value != value
    except Exception:
        return False

class TemplateXmlBuilder:
    """
    根据字段路径和字段描述生成 XML 模板骨架。

    该类只负责构建模板，不负责：
    - XML/JSON 分块
    - LLM 调用
    - 最终 XML 组装
    - 输出目录管理
    """

    def __init__(self):
        self.field_info = {}

    def add_field_info(self, node_name, fields):
        """添加节点字段信息。"""
        self.field_info[node_name] = fields

    def build_node_tree(self, paths):
        """根据路径列表构建节点树。"""
        root = {
            "name": "Scenario",
            "children": defaultdict(dict),
            "level": 0,
        }
        node_map = {"Scenario": root}

        for path in paths:
            current = root

            # 保留原来的行为：跳过路径中的第一个节点
            for level, node_name in enumerate(path[1:], 1):
                if node_name not in current["children"]:
                    new_node = {
                        "name": node_name,
                        "children": defaultdict(dict),
                        "level": level,
                        "parent": current["name"],
                    }
                    current["children"][node_name] = new_node
                    node_map[node_name] = new_node

                current = current["children"][node_name]

        return root, node_map

    def generate_xml_for_node(self, node, parent_element=None):
        """将节点树转换为 XML 元素。"""
        node_name = node["name"]

        if parent_element is None:
            node_element = ET.Element(node_name)
        else:
            node_element = ET.SubElement(parent_element, node_name)

        for field in self.field_info.get(node_name, []):
            field_name = sanitize_xml_name(
                field.get("name", "unknown")
            )
            field_elem = ET.SubElement(node_element, field_name)

            field_desc = field.get("description", "")
            field_note = field.get("note", "")

            if field_desc and not is_nan(field_desc):
                field_desc = "".join(str(field_desc).strip().split("\n"))
                field_elem.set("description", field_desc)

            if field_note and not is_nan(field_note):
                field_note = "；".join(str(field_note).strip().split("\n"))
                field_elem.set("note", field_note)

        for child_node in node["children"].values():
            self.generate_xml_for_node(child_node, node_element)

        return node_element

    def generate_complete_xml(self, paths):
        """生成完整的 XML 模板字符串。"""
        root_node, _ = self.build_node_tree(paths)
        xml_root = self.generate_xml_for_node(root_node)

        rough_string = ET.tostring(
            xml_root,
            encoding="unicode",
        )
        reparsed = minidom.parseString(rough_string)

        return reparsed.toprettyxml(indent="  ")

    def get_xml_for_specific_node(self, node_name):
        """生成指定节点的 XML 模板。"""
        node_element = ET.Element(node_name)

        for field in self.field_info.get(node_name, []):
            field_name = sanitize_xml_name(
                field.get("name", "unknown")
            )
            field_elem = ET.SubElement(node_element, field_name)

            field_desc = field.get("description", "")
            field_note = field.get("note", "")

            if field_desc and not is_nan(field_desc):
                field_desc = "".join(str(field_desc).strip().split("\n"))
                field_elem.set("description", field_desc)

            if field_note and not is_nan(field_note):
                field_note = "；".join(str(field_note).strip().split("\n"))
                field_elem.set("note", field_note)

        return node_element