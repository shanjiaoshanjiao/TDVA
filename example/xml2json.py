import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

def remove_namespace(tag):
    """去除 XML 命名空间"""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag

def element_to_dict(element):
    """
    XML 节点转字典：
    - 保留节点结构
    - 保留节点文本值
    - 忽略节点属性
    - 同名节点转换为列表
    """
    children = list(element)

    # 没有子节点，直接返回节点文本
    if not children:
        return (element.text or "").strip()

    result = {}
    child_groups = defaultdict(list)

    for child in children:
        child_name = remove_namespace(child.tag)
        child_groups[child_name].append(child)

    for child_name, child_list in child_groups.items():
        values = [
            element_to_dict(child)
            for child in child_list
        ]

        result[child_name] = (
            values[0]
            if len(values) == 1
            else values
        )

    # 当前节点既有子节点又有文本时，保留文本
    text = (element.text or "").strip()
    if text:
        result["_text"] = text

    return result


def xml_to_json(xml_path, json_path):
    """
    将 GB18030 编码的 XML 转为 UTF-8 编码的 JSON。
    保留节点结构和值，忽略节点属性。
    """
    # 先由 Python 解码 GB18030，避免 XML 解析器直接处理该编码
    with open(xml_path, "r", encoding="gb18030") as file:
        xml_text = file.read()

    # 解析已经解码的 Unicode 字符串
    root = ET.fromstring(xml_text)

    result = {
        remove_namespace(root.tag): element_to_dict(root)
    }

    with open(json_path, "w", encoding="utf-8") as file:
        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=4
        )


if __name__ == "__main__":
    input_xml = r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\example\scenario_xd.xml"
    output_json = r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\example\scenario_xd.json"

    xml_to_json(input_xml, output_json)

    print(f"转换完成：{output_json}")