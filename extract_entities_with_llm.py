import re
import os
import json
import time
import logging
from pathlib import Path
import pandas as pd
import time
import traceback
from typing import List, Dict, Any, Tuple

from docx import Document
from markitdown import MarkItDown


import asyncio
from asyncio import Lock
import aiohttp
from openai import OpenAI, AsyncOpenAI, APIError, APIConnectionError


from xml.dom import minidom
import xmlschema
from lxml import etree
from py2neo import Graph, NodeMatcher

import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from collections import defaultdict


import pymysql
from fuzzywuzzy import fuzz
from typing import Dict, Optional, List, Tuple



# 配置日志
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

def sanitize_string(string):
    """
    去除中文字符和冒号
    """
    # 去除中文字符和冒号
    # 匹配标签名并替换中文字符和冒号为下划线
    sanitized = re.sub(r'[\u4e00-\u9fff]+', '', string)
    sanitized = re.sub(r'\s+', '', sanitized).strip()
    sanitized  = sanitized.replace("/", "_")
    sanitized  = sanitized.replace("+", "_")
    sanitized = sanitized.replace(" ", "")
    sanitized = sanitized.replace("，", "")
    sanitized = sanitized.replace("：", "")

    sanitized = escape(sanitized)
    return sanitized


def is_nan(value):
    """检查值是否为 NaN"""
    if value is None:
        return True
    try:
        return value != value  # NaN 不等于自身
    except:
        return False


def process_document(input_path):
    """主处理流程：优化表格转换逻辑"""
    md = MarkItDown(
        docx_options={
            "detect_merged_cells": True,
            "preserve_merged_cell_position": True
        },
        markdown_options={"table_indentation": 0}
    )
    
    # DOCX转Markdown
    result = md.convert(input_path)
    # 应用表格处理
    merger = EnhancedTableMerger()
    processed_md = merger.process_text_block(result.text_content)

    return processed_md



class XMLGenerator:
    def __init__(self):
        self.processed_nodes = set()  # 记录已处理的节点
        self.field_info = {}  # 存储节点字段信息
        
    def add_field_info(self, node_name, fields):
        """添加节点的字段信息"""
        self.field_info[node_name] = fields
        
    def build_node_tree(self, paths):
        """从路径列表构建节点树结构"""
        # 创建根节点
        root = {'name': 'Scenario', 'children': defaultdict(list), 'level': 0}
        node_map = {'Scenario': root}
        # 处理所有路径
        for path in paths:
            current = root
            for i, node_name in enumerate(path[1:], 1):  # 跳过第一个'Side'节点
                if node_name not in current['children']:
                    new_node = {
                        'name': node_name, 
                        'children': defaultdict(list),
                        'level': i,
                        'parent': current['name']
                    }
                    current['children'][node_name] = new_node
                    node_map[node_name] = new_node
                current = current['children'][node_name]
                
        return root, node_map
    
    def generate_xml_for_node(self, node, parent_element=None):
        """为节点生成XML（避免重复）"""
        node_name = node['name']

        # 如果已经处理过这个节点，跳过
        # if node_name in self.processed_nodes:
        #     return None
            
        # 标记为已处理
        # self.processed_nodes.add(node_name)
        
        # 创建当前节点元素
        if parent_element is None:
            node_element = ET.Element(node_name)
        else:
            node_element = ET.SubElement(parent_element, node_name)

        # 添加字段信息（如果有）
        if node_name in self.field_info:
            for field in self.field_info[node_name]:
                field_name = sanitize_string(field.get('name', 'unknown'))
                field_desc = field.get('description', '')
                field_example = field.get('example', '')
                field_note = field.get('note', '')
                
                # 创建字段元素
                field_elem = ET.SubElement(node_element, field_name)

                # 添加属性
                if field_desc and not is_nan(field_desc):
                    field_desc = "".join(field_desc.strip().split("\n"))
                    field_elem.set('description', str(field_desc))
                # if field_example and not is_nan(field_example):
                #     field_elem.set('example', str(field_example))
                if field_note and not is_nan(field_note):
                    field_note = "；".join(field_note.strip().split("\n"))
                    field_elem.set('note', str(field_note))
                
                # # 设置文本内容（使用示例值）
                # if field_example and not is_nan(field_example):
                #     field_elem.text = str(field_example)
        
        # 递归处理子节点
        for child_name, child_node in node['children'].items():
            self.generate_xml_for_node(child_node, node_element)
            
        return node_element
    
    def generate_complete_xml(self, paths):
        """生成完整的XML文档"""
        # 构建节点树
        root_node, node_map = self.build_node_tree(paths)
        
        # 从根节点开始生成XML
        xml_root = self.generate_xml_for_node(root_node)
        
        # 格式化XML
        rough_string = ET.tostring(xml_root, encoding='unicode')

        # 特别注意 e.position 或错误信息中可能包含的行号和列号
        reparsed = minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="  ")
    
    def get_xml_for_specific_node(self, node_name):
        """获取特定节点的XML（不重复生成）"""
        # if node_name not in self.processed_nodes:
        #     # 如果节点尚未处理，需要先构建树
        #     return None
            
        # 创建该节点的XML元素
        node_element = ET.Element(node_name)
        
        # 添加字段信息
        if node_name in self.field_info:
            for field in self.field_info[node_name]:
                field_name = field.get('name', 'unknown')
                field_elem = ET.SubElement(node_element, field_name)
                
                # 添加属性和内容（与上面相同）
                field_desc = field.get('description', '')
                field_example = field.get('example', '')
                field_note = field.get('note', '')
                
                if field_desc and not is_nan(field_desc):
                    field_desc = "".join(field_desc.strip().split("\n"))
                    field_elem.set('description', str(field_desc))
                # if field_example and not is_nan(field_example):
                #     field_elem.set('example', str(field_example))
                #     field_elem.text = str(field_example)
                if field_note and not is_nan(field_note):
                    field_note = "；".join(field_note.strip().split("\n"))
                    field_elem.set('note', str(field_note))
        
        return node_element



class EnhancedTableMerger:
    def __init__(self):
        self.current_table = []
        self.parent_node = None
        self.in_table = False

    def is_table_line(self, line):
        """检测表格行逻辑优化"""
        stripped = line.strip()
        
        # 基础表格行检测
        if stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 3:
            # 排除特殊标题行
            exclusion_patterns = [
                r'联合作战实验想定',
                r'想定格式设计文档',
                r'^\|?\s*(\*\*)?[章节].+(\*\*)?\s*\|?$'
            ]
            
            return not any(re.search(pattern, stripped) for pattern in exclusion_patterns)
        return False

    def process_table_line(self, line):
        """改进的表格行处理逻辑，保持原结构"""
        line = line.strip()
        # 保留原始管道符结构
        cells = [cell.strip() for cell in line.split('|')]
        cells = cells[1:-1] if len(cells) > 2 else cells  # 移除首尾空列
        
        # 跳过格式分隔行
        if re.match(r'^[\s:|-]+$', ''.join(cells)):
            return
        
        # 父节点检测逻辑优化
        if cells and len(cells) > 1 and cells[0].isdigit() and cells[1]:
            self.parent_node = cells[1]
        
        # 空节点填充逻辑
        if cells and len(cells) > 1 and not cells[1] and self.parent_node:
            cells[1] = self.parent_node
        
        self.current_table.append(cells)

    def finalize_table(self):
        """构建Markdown表格并重置状态"""
        if not self.current_table:
            return ""
        
        # 计算最大列数
        max_cols = max(len(row) for row in self.current_table)
        
        # 构建表格
        output_lines = []
        header_processed = False
        
        for i in range(len(self.current_table)):
            # 保证所有行具有相同的列数
            #padded_row = row + [''] * (max_cols - len(row))
            row = self.current_table[i]
            if max_cols - len(row) == 0 and "节点名称" in row:
                print(self.current_table[i+1])
            padded_row = [''] * (max_cols - len(row)) + row
            # 添加表头分隔符
            row_str = "".join([r.strip() for r in row if r.strip() != ""])
            if len(row_str.strip()) == 0:
                continue
            if not header_processed and len(row) > 0:
                output_lines.append("| " + " | ".join(padded_row) + " |")
                output_lines.append("| " + " | ".join(['---'] * max_cols) + " |")
                header_processed = True
            else:
                output_lines.append("| " + " | ".join(padded_row) + " |")
                #print(output_lines)
        
        self.in_table = False
        self.current_table = []
        self.parent_node = None
        return "\n".join(output_lines)

    def process_text_block(self, text):
        """处理文本块，保留原始结构"""
        processed_lines = []
        lines = text.split('\n')
        
        for i, line in enumerate(lines):
            # 表格开始检测
            if not self.in_table and self.is_table_line(line):
                self.in_table = True
                self.current_table = []
                self.parent_node = None
            
            # 表格结束检测
            if self.in_table and not self.is_table_line(line):
                processed_lines.append(self.finalize_table())
                self.in_table = False
            
            # 表格行处理
            if self.in_table:
                self.process_table_line(line)
            else:
                processed_lines.append(line)
        
        # 处理文件末尾的表格
        if self.in_table:
            processed_lines.append(self.finalize_table())
        
        return '\n'.join(processed_lines)



class MilitaryScenarioProcessor:
    def __init__(self, llm_api_key, llm_base_url):
        """
        初始化处理器配置LLM
            :param llm_api_key: LLM（如OpenAI）的API密钥
            :param max_completion_length :模型最大输出长度
        """
        self.api_key = llm_api_key
        self.base_url = llm_base_url
        self.max_completion_length = 32000
        self.client = OpenAI(api_key = self.api_key, base_url = self.base_url)    # 调用LLM API。此处以OpenAI ChatGPT模型为例，实际可替换为其他LLM


    def extract_entities_with_llm(self, scenario_text):
        """
        使用LLM从想定文本中提取实体和关系（核心步骤）
        :param scenario_text: 作战想定文本
        :return: 结构化的实体和关系字典
        """
        entities_extract_prompt_file = r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\prompt\entities_extract_prompt_minmax.txt"
        with open(entities_extract_prompt_file, "r", encoding="utf-8") as prompt_file:
            prompt_template = prompt_file.read()

        prompt_ = prompt_template.format(scenario_text=scenario_text)

        try:
            start_time = time.time()
            # 非流式调用
            # response = self.client.chat.completions.create(
            #     model="Qwen/Qwen3-VL-32B-Instruct",
            #     # "gpt-4" is available, too. Check the available models in https://platform.openai.com/docs/models/
            #     messages=[{"role":"system", "content":"你是一名军事领域专家。"}, {"role":"user", "content": prompt_}],
            #     temperature=0.05,  # 低温度以保证输出稳定性
            #     max_tokens=self.max_completion_length,
            #     top_p=0.1,
            #     frequency_penalty=0.1,
            #     presence_penalty=0.1,
            #     timeout=100000,
            # )

            # llm_output = response.choices[0].message.content.strip()
            
            # 实时流式输出
            # 流式输出核心：添加 stream=True，遍历响应块实时输出
            response = self.client.chat.completions.create(
                model="MiniMax/MiniMax-M2.7",
                # "gpt-4" is available, too. Check the available models in https://platform.openai.com/docs/models/
                messages=[{"role":"system", "content":"你是一名军事领域专家。"}, {"role":"user", "content": prompt_}],
                temperature=0.05,  # 低温度以保证输出稳定性
                max_tokens=self.max_completion_length,
                top_p=0.1,
                frequency_penalty=0.1,
                presence_penalty=0.1,
                timeout=10000,
                stream=True,  # 开启流式输出
            )

            #流式输出处理：逐块接收响应，实时打印，最终拼接完整结果
            llm_output = ""
            #遍历流式响应的每个数据块
            for chunk in response:
                # 提取当前块的文本内容（delta是流式增量数据）
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    llm_output += content  # 拼接完整结果
                    # print(content, end="", flush=True)  # 实时逐字输出（不换行、强制刷新缓冲区）

            # 最后去除首尾空白字符（和原代码逻辑一致）
            llm_output = llm_output.strip()
           
            structured_data = self.parse_json_from_llm_output(llm_output)
            end_time = time.time()
            total_time = end_time - start_time
            print(total_time)
            return structured_data
        except Exception as e:
            print(f"LLM解析失败: {e}")
            logger.error(f"LLM解析失败: {e}")
            return None


    # --- 辅助函数 ---
    def parse_json_from_llm_output(self, llm_output):
        """
        从LLM的输出文本中解析出JSON内容。
        这是一个复杂且需要鲁棒性处理的过程，实际实现可能需要使用正则表达式或尝试多种解析方式。
        此处为简化示例，假设LLM能返回纯净的JSON。
        """
        # 示例：尝试提取可能被标记的JSON代码块
        json_match = re.search(r'```json\n(.*?)\n```', llm_output, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            json_str = llm_output # 假设整个输出就是JSON

        # 这里应使用json.loads解析json_str，示例直接返回一个模拟字典
        # 实际使用时请替换为真正的解析逻辑
        return json_str
    


def get_all_docx_file(file_path):
    file_list = list()
    for root, dirs, files in os.walk(file_path):
        for f in files:
            if f.endswith(".docx"):
                file_path = os.path.join(root, f)
                file_list.append(file_path)
    return file_list



if __name__ == "__main__":
    LLM_BASE_URL = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    LLM_API_KEY = "sk-4070f3c735854ed89d23ffda6d9ad870" # API密钥
    processor = MilitaryScenarioProcessor(LLM_API_KEY, LLM_BASE_URL)
    file_path = r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\example\scenario_text.txt"
    output_path =  r"D:\非shemi工作内容\陈xz_论文相关\Scenario_Generation_TDVA\example\scenario_text_llm_extract.json"
            
    #scenario_text = process_document(file_path)
    with open(file_path, "r", encoding="utf-8") as scen:
        scenario_text = scen.read()
    
    try:
        llm_output = processor.extract_entities_with_llm(scenario_text)
        output_json = json.loads(llm_output)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_json, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.info(F"{file_path} 文件实体抽取失败！！")