scenario_xml_generator/

├── core/                              # 与平台无关的核心能力
│   ├── document_generator.py          # 单 XML 文档生成器，可复用
│   ├── json_chunker.py                # JSON 分块逻辑
│   ├── xml_chunker.py                 # XML 分块逻辑
│   ├── chunk_aligner.py               # XML/JSON 微分块对齐
│   ├── chunk_validator.py             # 微分块校验
│   ├── xml_assembler.py               # 单 XML 递归组装
│   ├── global_id_registry.py          # 全局实体 ID 管理
│   ├── reference_registry.py          # 跨文档引用管理
│   └── llm_client.py                  # LLM 调用封装
│
├── package/                           # 多 XML 包级能力
│   ├── package_manifest.py            # 解析 package_manifest.yaml
│   ├── scenario_partitioner.py        # 将同一想定内容投影到多个 XML
│   ├── json_dispatcher.py             # 给每个 XML 分配对应 JSON
│   ├── package_orchestrator.py        # 多 XML 总控
│   └── package_validator.py           # 跨 XML 包级校验
│
├── platforms/                         # 平台差异层
│   ├── mozi/
│   │   ├── package_manifest.yaml
│   │   ├── templates/
│   │   ├── graphs/
│   │   ├── profiles/
│   │   └── field_map.yaml
│   │
│   ├── target_platform/
│   │   ├── package_manifest.yaml
│   │   ├── templates/
│   │   ├── graphs/
│   │   ├── profiles/
│   │   ├── field_map.yaml
│   │   └── document_rules.yaml
│   │
│   └── base_platform.py
|
├── extraction/
│   ├── extract_entities_with_llm.py
│   ├── scenario_ir_builder.py
│   └── entity_normalizer.py
|
├── graph_build/
│   ├── connect_neo4j_adjust.py
│   ├── xml_graph_builder.py
│   └── graph_loader.py
|
├── output/
│   └── run_001/
│       ├── common/
│       │   ├── scenario_ir.json
│       │   ├── entity_registry.json
│       │   ├── reference_registry.json
│       │   └── package_report.json
│       │
│       ├── scenario/
│       │   ├── input_view.json
│       │   ├── graph.json
│       │   ├── chunk_mapping.json
│       │   ├── xml_chunks/
│       │   ├── json_chunks/
│       │   ├── prompts/
│       │   └── scenario.xml
│       │
│       ├── units/
│       │   ├── input_view.json
│       │   ├── graph.json
│       │   ├── chunk_mapping.json
│       │   ├── xml_chunks/
│       │   ├── json_chunks/
│       │   ├── prompts/
│       │   └── units.xml
│       │
│       └── missions/
│           ├── input_view.json
│           ├── graph.json
│           ├── chunk_mapping.json
│           ├── xml_chunks/
│           ├── json_chunks/
│           ├── prompts/
│           └── missions.xml