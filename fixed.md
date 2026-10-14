可以。第一步先不修改代码逻辑，只把 `tdva_xml_generate.py` 中现有函数按照职责归类。

当前文件中的函数大致可以分为两类：

1. **全局辅助函数**：负责纯数据处理、格式转换；
2. **`TDVAXMLGenerator` 中的方法**：负责完整生成流程。

建议最终形成下面这些类：

```text
GlobalIdRegistry
WorkspaceManager
Neo4jTemplateService
EntityMappingService
ChunkService
LLMGenerationService
XMLValidationService
XMLAssemblyService
OutputService
TDVAXMLGenerator
```

其中，`TDVAXMLGenerator` 只保留总流程，不再承担所有具体业务。

---

# 一、整体归类结果

## 1. `GlobalIdRegistry`

这个类在当前代码中已经存在，主要负责实体业务 ID 管理。

### 负责内容

```text
实体路径
实体类型
全局业务 ID
已有 ID 映射加载
新 ID 分配
```

### 建议包含的函数

```python
class GlobalIdRegistry:
    def __init__(self):
        ...

    def register(
        self,
        entity_path: str,
        entity_type: str,
    ) -> str:
        """
        为实体分配新的业务 ID，或返回已有 ID。
        """
        ...

    def register_existing(
        self,
        entity_path: str,
        entity_type: str,
        entity_id: str,
    ) -> None:
        """
        注册已经存在的实体业务 ID。
        """
        ...

    def get(
        self,
        entity_path: str,
        entity_type: str,
    ) -> str | None:
        """
        根据实体路径和类型获取业务 ID。
        """
        ...

    def to_dict(self) -> dict:
        """
        将当前注册表转换为可序列化字典。
        """
        ...
```

如果当前代码中已经有以下函数：

```python
register(...)
register_existing(...)
```

可以直接保留在这个类中。

---

# 二、`WorkspaceManager`

这个类负责工作目录和文件路径，不负责业务处理。

当前生成器中与以下内容有关的代码，都应该归入此类：

```python
self.chunk_mapping_path = Path(...)
self.entity_map_file = Path(...)
self.prompt_path = Path(...)
self.json_chunk_path = Path(...)
self.xml_chunk_path = Path(...)
self.final_xml_path = Path(...)
self.ex_info_file = Path(...)
```

以及：

```python
mkdir
exists
创建输出目录
清理旧目录
```

## 建议包含的函数

```python
class WorkspaceManager:
    def __init__(
        self,
        output_root: str | Path,
    ):
        self.output_root = Path(output_root)

    def prepare(self) -> None:
        """
        创建本次任务所需的所有目录。
        """
        ...

    def prepare_middle_output(self) -> None:
        """
        创建中间结果目录。
        """
        ...

    def get_json_chunk_path(self) -> Path:
        ...

    def get_xml_chunk_path(self) -> Path:
        ...

    def get_prompt_path(self) -> Path:
        ...

    def get_chunk_mapping_path(self) -> Path:
        ...

    def get_entity_map_path(self) -> Path:
        ...

    def get_final_xml_path(self) -> Path:
        ...

    def get_report_path(self) -> Path:
        ...
```

更推荐使用一个路径对象：

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Workspace:
    root: Path
    json_chunk_dir: Path
    xml_chunk_dir: Path
    prompt_dir: Path
    final_xml_dir: Path
    chunk_mapping_file: Path
    entity_map_file: Path
    report_file: Path
    extra_info_file: Path
```

然后：

```python
class WorkspaceManager:
    def prepare(self) -> Workspace:
        ...
```

这样其他类只接收 `Workspace`，不需要自己拼接路径。

---

# 三、`Neo4jTemplateService`

这个类负责从 Neo4j 中读取 XML 模板和知识图谱相关结构。

论文中的对应功能是：

```text
知识图谱检索召回 XML 微分块
```

## 建议包含的函数

当前代码中类似以下职责的函数，都应该归入此类：

```python
class Neo4jTemplateService:
    def __init__(
        self,
        neo4j_uri: str,
        neo4j_auth: tuple,
    ):
        ...

    def create_indexes(self) -> None:
        """
        创建 Neo4j 索引。
        """
        ...

    def load_template_fragments(self) -> list:
        """
        从 Neo4j 查询并生成 XML 模板片段。
        """
        ...

    def query_template_nodes(self) -> list:
        """
        查询 XML 模板节点。
        """
        ...

    def query_entity_nodes(self, entity_type: str) -> list:
        """
        查询实体节点。
        """
        ...

    def query_parent_child_relations(self) -> list:
        """
        查询知识图谱中的父子关系。
        """
        ...

    def build_fragment_tree(self, records: list) -> list:
        """
        根据 Neo4j 查询结果构建模板树。
        """
        ...

    def close(self) -> None:
        """
        关闭 Neo4j 连接。
        """
        ...
```

需要注意：

```python
Neo4jTemplateService
```

只负责获取和整理 Neo4j 数据。

它不应该负责：

```text
调用 LLM
保存 XML 文件
生成 JSON 分块
组装最终 XML
```

---

# 四、`EntityMappingService`

这个类负责输入 JSON 中的实体识别、路径构造和业务 ID 处理。

当前代码中和以下内容相关的函数，都应该归入此类：

```text
prepare_entity_data_map
entity_id_registry
entity_id_map_file
实体 ID 映射读取
实体 ID 映射保存
```

## 建议包含的函数

```python
class EntityMappingService:
    def __init__(
        self,
        registry: GlobalIdRegistry,
        entity_id_map_file: str | Path | None = None,
    ):
        self.registry = registry
        self.entity_id_map_file = (
            Path(entity_id_map_file)
            if entity_id_map_file
            else None
        )

    def load_existing_id_map(self) -> None:
        """
        从文件加载已有实体业务 ID。
        """
        ...

    def prepare_entity_data_map(
        self,
        json_data: dict,
        template_fragments: list,
    ) -> dict:
        """
        从输入 JSON 和模板片段中建立实体数据映射。
        """
        ...

    def extract_entities(
        self,
        json_data: dict,
    ) -> list:
        """
        从 JSON 中提取实体。
        """
        ...

    def build_entity_path(
        self,
        current_path: str,
        entity_name: str,
    ) -> str:
        """
        构造实体路径。
        """
        ...

    def get_or_create_entity_id(
        self,
        entity_path: str,
        entity_type: str,
    ) -> str:
        """
        获取或创建实体业务 ID。
        """
        ...

    def save_entity_map(
        self,
        entity_map: dict,
        output_path: str | Path,
    ) -> None:
        """
        保存实体映射表。
        """
        ...
```

当前代码中从映射文件读取数据的逻辑类似：

```python
data = json.load(file)

for key, entity_id in data.items():
    entity_type, entity_path = key.split(":", 1)
    registry.register_existing(
        entity_path,
        entity_type,
        entity_id,
    )
```

这部分应当放到：

```python
EntityMappingService.load_existing_id_map()
```

而不是放在主生成器中。

---

# 五、`ChunkService`

这是整个系统最核心的业务类之一。

它负责论文中的“分治”部分：

```text
XML 模板递归分块
JSON 数据分块
XML 与 JSON 对齐
实体数据注入
父子关系记录
分块映射表生成
```

当前代码中的核心函数：

```python
build_and_save_chunks(...)
```

建议拆成两个层次：

```text
build_chunks()
save_chunks()
```

不要让一个函数同时负责“计算分块”和“保存分块”。

## 建议包含的函数

```python
class ChunkService:
    def __init__(
        self,
        json_chunk_path: str | Path,
        xml_chunk_path: str | Path,
        chunk_mapping_path: str | Path,
    ):
        ...

    def build_chunks(
        self,
        json_data: dict,
        template_fragments: list,
        entity_map: dict,
    ) -> dict:
        """
        构造所有 XML/JSON 微分块。
        """
        ...

    def build_xml_chunks(
        self,
        template_fragments: list,
    ) -> list:
        """
        根据 XML 模板构建 XML 分块。
        """
        ...

    def build_json_chunks(
        self,
        json_data: dict,
        xml_chunks: list,
    ) -> list:
        """
        根据 XML 分块构建对应的 JSON 分块。
        """
        ...

    def split_recursive(
        self,
        node,
        current_path: str,
        parent_chunk_id: str | None = None,
    ) -> list:
        """
        递归拆分 XML 节点。
        """
        ...

    def expand_list_items(
        self,
        node,
        json_data: dict,
    ) -> list:
        """
        处理 JSON 中的列表和多实例节点。
        """
        ...

    def align_xml_json_chunks(
        self,
        xml_chunks: list,
        json_chunks: list,
    ) -> list:
        """
        对齐 XML 分块和 JSON 分块。
        """
        ...

    def build_chunk_mapping(
        self,
        chunks: list,
    ) -> list:
        """
        构造全局分块映射表。
        """
        ...

    def save_chunks(
        self,
        chunk_result: dict,
    ) -> None:
        """
        保存 XML 分块和 JSON 分块。
        """
        ...

    def save_chunk_mapping(
        self,
        mapping: list,
    ) -> None:
        """
        保存分块映射关系。
        """
        ...
```

建议定义一个分块结果对象：

```python
from dataclasses import dataclass


@dataclass
class ChunkBuildResult:
    xml_chunks: list
    json_chunks: list
    chunk_mapping: list
```

那么主流程中可以写成：

```python
chunk_result = chunk_service.build_chunks(
    json_data=json_data,
    template_fragments=template_fragments,
    entity_map=entity_map,
)

chunk_service.save_chunks(chunk_result)
```

---

# 六、`PromptBuilder`

Prompt 构造建议单独作为一个类，不要放在 LLM 调用函数中。

当前代码中和以下内容相关的逻辑应归入此类：

```text
读取 prompt 模板
替换 XML 分块
替换 JSON 分块
替换实体信息
替换错误反馈
构造最终 Prompt
```

## 建议包含的函数

```python
class PromptBuilder:
    def __init__(
        self,
        prompt_template_path: str | Path,
    ):
        self.prompt_template_path = Path(prompt_template_path)
        self.template = self._load_template()

    def _load_template(self) -> str:
        """
        加载 Prompt 模板。
        """
        ...

    def build(
        self,
        xml_chunk: str,
        json_chunk: str,
        entity_context: dict | None = None,
        error_feedback: str | None = None,
    ) -> str:
        """
        构造单个 XML 分块的生成 Prompt。
        """
        ...

    def build_retry_prompt(
        self,
        original_prompt: str,
        error_feedback: str,
    ) -> str:
        """
        根据验证错误构造重试 Prompt。
        """
        ...
```

这个类可以单独进行单元测试，例如检查最终 Prompt 是否包含：

```text
XML 分块
JSON 分块
实体业务 ID
知识图谱约束
错误反馈
```

---

# 七、`LLMGenerationService`

这个类负责调用 LLM，不负责最终 XML 组装。

当前代码中和以下内容相关的逻辑应归入此类：

```text
AsyncOpenAI
asyncio.Semaphore
max_workers
异步调用
并发任务
重试
返回结果解析
```

## 建议包含的函数

```python
class LLMGenerationService:
    def __init__(
        self,
        openai_api_key: str,
        openai_base_url: str,
        llm_model: str,
        max_workers: int,
        prompt_builder: PromptBuilder,
    ):
        ...

    async def generate_all(
        self,
        chunks: list,
    ) -> list:
        """
        并行生成所有 XML 分块。
        """
        ...

    async def generate_one(
        self,
        chunk: dict,
    ) -> dict:
        """
        生成单个 XML 分块。
        """
        ...

    async def call_llm(
        self,
        prompt: str,
    ) -> str:
        """
        调用 LLM。
        """
        ...

    async def generate_with_retry(
        self,
        chunk: dict,
        max_retries: int = 1,
    ) -> dict:
        """
        生成、验证、失败反馈和重试。
        """
        ...

    def parse_response(
        self,
        response: str,
    ) -> str:
        """
        从 LLM 响应中提取 XML 内容。
        """
        ...

    async def close(self) -> None:
        """
        关闭异步客户端。
        """
        ...
```

并发限制：

```python
self.semaphore = asyncio.Semaphore(max_workers)
```

应该只存在于这个类中，不应该存在于 `TDVAXMLGenerator` 中。

---

# 八、`XMLValidationService`

这个类负责论文中的“验证”部分。

建议先把当前所有验证逻辑集中到一个类中，后续再拆成结构验证和语义验证两个类。

## 建议包含的函数

```python
class XMLValidationService:
    def validate(
        self,
        xml_content: str,
        expected_chunk: dict | None = None,
    ) -> dict:
        """
        对生成结果进行综合验证。
        """
        ...

    def validate_xml_syntax(
        self,
        xml_content: str,
    ) -> bool:
        """
        验证 XML 是否可以解析。
        """
        ...

    def validate_root_tag(
        self,
        xml_content: str,
        expected_root: str | None = None,
    ) -> bool:
        """
        验证根标签。
        """
        ...

    def validate_required_fields(
        self,
        xml_content: str,
        expected_fields: list[str],
    ) -> list[str]:
        """
        检查必需字段。
        """
        ...

    def validate_entity_ids(
        self,
        xml_content: str,
        entity_map: dict,
    ) -> list[str]:
        """
        验证实体业务 ID。
        """
        ...

    def validate_semantics(
        self,
        xml_content: str,
        entity_context: dict,
    ) -> list[str]:
        """
        验证实体和知识图谱语义一致性。
        """
        ...
```

返回结果建议不要直接返回 `True/False`，而是：

```python
@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
```

这样重试时可以把 `errors` 作为错误反馈传给 `PromptBuilder`。

---

# 九、`XMLAssemblyService`

这个类负责论文中的“组装”部分。

当前代码中的：

```python
assemble_final_xml(...)
```

以及与节点路径、列表索引转换有关的函数，都应该归入此类。

检索到当前代码中有一个类似：

```python
last_fragment = last_fragment.replace("(", "").replace(")", "").replace(" ", "")
name, idx = last_fragment.split(",")
return f"{name}[{idx}]"
```

这类函数属于 XML 路径处理和组装辅助逻辑，也应该放在 `XMLAssemblyService` 中。

## 建议包含的函数

```python
class XMLAssemblyService:
    def assemble(
        self,
        fragment_results: list,
        chunk_mapping: list,
    ) -> str:
        """
        将已经生成并验证的 XML 分块组装成完整 XML。
        """
        ...

    def create_root(self):
        """
        创建最终 XML 根节点。
        """
        ...

    def insert_fragment(
        self,
        root,
        fragment,
        mapping: dict,
    ) -> None:
        """
        将单个 XML 分块插入 XML 树。
        """
        ...

    def resolve_path(
        self,
        path: str,
    ) -> list:
        """
        将字符串路径解析为 XML 节点路径。
        """
        ...

    def normalize_list_path(
        self,
        path_fragment: str,
    ) -> str:
        """
        处理列表节点路径和索引。
        """
        ...

    def find_parent_node(
        self,
        root,
        parent_path: str,
    ):
        """
        查找分块对应的父节点。
        """
        ...

    def merge_children(
        self,
        parent,
        fragment,
    ) -> None:
        """
        合并分块节点。
        """
        ...

    def serialize(
        self,
        root,
    ) -> str:
        """
        将 XML 树序列化为字符串。
        """
        ...
```

该类不应该知道：

```text
Neo4j 地址
LLM 模型
Prompt 路径
并发数
```

它只处理：

```text
XML 分块
分块映射表
父子路径
最终 XML 树
```

---

# 十、`OutputService`

文件保存逻辑建议单独归类。

当前代码中类似以下方法应归入此类：

```python
write_final_xml(...)
write_generation_report(...)
保存实体映射
保存分块映射
保存 JSON 分块
保存 XML 分块
```

## 建议包含的函数

```python
class OutputService:
    def __init__(self, workspace):
        self.workspace = workspace

    def save_final_xml(
        self,
        final_xml: str,
    ) -> str:
        """
        保存最终 XML。
        """
        ...

    def save_json_chunks(
        self,
        json_chunks: list,
    ) -> None:
        ...

    def save_xml_chunks(
        self,
        xml_chunks: list,
    ) -> None:
        ...

    def save_chunk_mapping(
        self,
        mapping: list,
    ) -> str:
        ...

    def save_entity_map(
        self,
        entity_map: dict,
    ) -> str:
        ...

    def write_generation_report(
        self,
        total_time: float,
        fragment_results: list,
    ) -> str:
        """
        保存生成报告。
        """
        ...

    def write_extra_info(
        self,
        success_rate: float,
        elapsed_time: float,
    ) -> str:
        ...
```

注意：

```python
write_final_xml()
```

只负责保存文件。

而：

```python
assemble()
```

只负责生成 XML 字符串。

两者应该分开。

---

# 十一、重构后的 `TDVAXMLGenerator`

最后，原来的 `TDVAXMLGenerator` 应该变成一个“流程编排类”。

它只保留：

```python
__init__()
run()
```

必要时保留：

```python
close()
```

## 建议结构

```python
class TDVAXMLGenerator:
    def __init__(
        self,
        template_service: Neo4jTemplateService,
        entity_service: EntityMappingService,
        chunk_service: ChunkService,
        generation_service: LLMGenerationService,
        assembly_service: XMLAssemblyService,
        output_service: OutputService,
    ):
        self.template_service = template_service
        self.entity_service = entity_service
        self.chunk_service = chunk_service
        self.generation_service = generation_service
        self.assembly_service = assembly_service
        self.output_service = output_service

    async def run(
        self,
        json_data: dict,
    ) -> dict:
        # 1. 加载 XML 模板片段
        template_fragments = (
            self.template_service.load_template_fragments()
        )

        # 2. 加载已有实体 ID
        self.entity_service.load_existing_id_map()

        # 3. 构建实体映射
        entity_map = (
            self.entity_service.prepare_entity_data_map(
                json_data=json_data,
                template_fragments=template_fragments,
            )
        )

        # 4. 构建 XML/JSON 分块
        chunk_result = self.chunk_service.build_chunks(
            json_data=json_data,
            template_fragments=template_fragments,
            entity_map=entity_map,
        )

        # 5. 保存分块
        self.chunk_service.save_chunks(chunk_result)

        # 6. 调用 LLM 生成并验证
        fragment_results = (
            await self.generation_service.generate_all(
                chunk_result.xml_chunks
            )
        )

        # 7. 组装最终 XML
        final_xml = self.assembly_service.assemble(
            fragment_results=fragment_results,
            chunk_mapping=chunk_result.chunk_mapping,
        )

        # 8. 保存结果
        result = self.output_service.save_all(
            final_xml=final_xml,
            entity_map=entity_map,
            chunk_result=chunk_result,
            fragment_results=fragment_results,
        )

        return result
```

此时主流程变得非常清楚：

```text
读取模板
  ↓
准备实体 ID
  ↓
构建分块
  ↓
LLM 生成和验证
  ↓
组装 XML
  ↓
保存结果
```

---

# 十二、建议的类和函数总表

| 类 | 主要职责 | 主要函数 |
|---|---|---|
| `GlobalIdRegistry` | 管理实体业务 ID | `register`、`register_existing`、`get`、`to_dict` |
| `WorkspaceManager` | 管理工作目录和路径 | `prepare`、路径获取函数 |
| `Neo4jTemplateService` | 查询 Neo4j 和生成模板片段 | `create_indexes`、`load_template_fragments`、`query_*` |
| `EntityMappingService` | 构建实体数据映射 | `load_existing_id_map`、`prepare_entity_data_map`、`extract_entities` |
| `ChunkService` | XML/JSON 分块 | `build_chunks`、`split_recursive`、`align_xml_json_chunks` |
| `PromptBuilder` | 构造 LLM Prompt | `build`、`build_retry_prompt` |
| `LLMGenerationService` | 并发调用 LLM | `generate_all`、`generate_one`、`call_llm`、`generate_with_retry` |
| `XMLValidationService` | XML 和语义验证 | `validate`、`validate_xml_syntax`、`validate_semantics` |
| `XMLAssemblyService` | 组装完整 XML | `assemble`、`resolve_path`、`insert_fragment` |
| `OutputService` | 保存所有结果 | `save_final_xml`、`write_generation_report` |
| `TDVAXMLGenerator` | 编排整体流程 | `run`、`close` |

---

# 十三、第一轮不要拆得过细

虽然上面列出了多个类，但第一轮建议只先拆成以下六个类：

```text
GlobalIdRegistry
Neo4jTemplateService
EntityMappingService
ChunkService
LLMGenerationService
XMLAssemblyService
```

文件保存和路径管理可以暂时保留在原类中，避免一次性修改太多代码。

第一轮推荐顺序：

```text
1. GlobalIdRegistry       已经基本独立
2. EntityMappingService   实体 ID 和实体映射
3. ChunkService           分块逻辑
4. LLMGenerationService   LLM 调用和并发
5. XMLAssemblyService     XML 组装
6. 最后精简 TDVAXMLGenerator.run()
```

---

# 十四、当前文件中不建议放进业务类的函数

如果某些函数只做简单转换，例如：

```python
格式化路径
解析节点名称
转换字符串
计算标签名
生成 chunk_id
```

并且它们：

- 不访问 `self`；
- 不访问数据库；
- 不读写文件；
- 不调用 LLM；

那么可以保留为模块级函数，或者放到：

```python
utils.py
```

例如：

```python
def normalize_xml_path(path: str) -> str:
    ...


def make_chunk_id(path: str) -> str:
    ...


def safe_filename(name: str) -> str:
    ...
```

不要为了“全部放入 class”而把所有小函数都塞进类中。

---

# 十五、当前代码中需要先统一的命名问题

你提供的代码片段中存在一个明显问题。

构造函数中出现的是：

```python
prompt_template_path
```

但实例化时使用的是：

```python
prompt_path
```

另外，构造函数和调用处都涉及：

```python
json_chunk_path
xml_chunk_path
final_xml_path
ex_info_path
```

这些参数最好先统一命名。

建议统一为：

```python
prompt_template_path
json_chunk_dir
xml_chunk_dir
final_xml_dir
extra_info_file
chunk_mapping_file
entity_map_file
```

例如：

```python
generator = TDVAXMLGenerator(
    prompt_template_path=str(prompt_path),
    json_chunk_dir=str(json_chunk_path),
    xml_chunk_dir=str(xml_chunk_path),
    final_xml_dir=str(final_xml_path),
    extra_info_file=str(ex_info_path),
)
```

---

## 推荐的第一步实际操作

现在不要马上重写所有函数。建议先在原文件中完成如下调整：

```python
# 1. 保留 GlobalIdRegistry

# 2. 新增以下空类
class EntityMappingService:
    pass


class ChunkService:
    pass


class LLMGenerationService:
    pass


class XMLAssemblyService:
    pass


class Neo4jTemplateService:
    pass
```

然后按照上面的职责，将 `TDVAXMLGenerator` 中的函数逐个移动进去。

移动原则是：

```text
凡是实体 ID 和实体映射相关的函数
    → EntityMappingService

凡是 XML/JSON 分块相关的函数
    → ChunkService

凡是 AsyncOpenAI、并发、重试相关的函数
    → LLMGenerationService

凡是 XML 节点路径、递归组装相关的函数
    → XMLAssemblyService

凡是 Neo4j 查询相关的函数
    → Neo4jTemplateService

剩余的流程控制函数
    → TDVAXMLGenerator
```

第一轮重构完成后的目标不是立即实现完美架构，而是让 `TDVAXMLGenerator` 中最终只剩下：

```python
__init__()
run()
```

以及少量生命周期管理函数。