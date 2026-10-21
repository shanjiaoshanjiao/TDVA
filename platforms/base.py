from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List

class PlatformAdapter(ABC):
    """
    平台适配器。

    负责平台相关的输入解析、模板生成、实体映射、
    分块、提示词和结果组装。
    """

    name: str

    @abstractmethod
    async def generate_fragments(
        self,
        graph_service: Any,
    ) -> List[Dict]:
        """
        从平台对应的图谱结构生成模板 fragments。
        """
        raise NotImplementedError

    @abstractmethod
    def prepare_entity_data_map(
        self,
        json_data: Dict,
        fragments: List[Dict],
    ) -> Dict:
        """
        将平台输入 JSON 映射到 fragments。
        """
        raise NotImplementedError

    @abstractmethod
    def build_and_save_chunks(
        self,
        json_data: Dict,
        fragments: List[Dict],
        entity_data_map: Dict,
        workspace: Path,
    ) -> Dict:
        """
        生成 JSON/XML 分块及分块映射。
        """
        raise NotImplementedError

    @abstractmethod
    def build_prompt(
        self,
        fragment: Dict,
        xml_content: str,
        json_chunk: Dict,
    ) -> str:
        """
        生成平台对应的 LLM 提示词。
        """
        raise NotImplementedError

    @abstractmethod
    def validate_filled_content(
        self,
        content: str,
    ) -> Any:
        """
        校验 LLM 填充结果。
        """
        raise NotImplementedError

    @abstractmethod
    def assemble_result(
        self,
        filled_chunks: List[Any],
        workspace: Path,
    ) -> Path:
        """
        将平台分块结果组装为最终产物。
        """
        raise NotImplementedError