import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

@dataclass
class GlobalEntityRecord:
    biz_id: str
    entity_type: str = ""
    display_name: str = ""
    owner_xml: str = ""
    source_json_path: str = ""
    is_defined: bool = True

class GlobalIdRegistry:
    """
    包级全局实体注册表。

    一个目标平台生成任务只应创建一个实例，
    然后注入给所有 XMLGenerator。
    """

    def __init__(self, registry_path: str):
        self.registry_path = str(registry_path)
        self.records: dict[str, GlobalEntityRecord] = {}
        self.load()

    def load(self) -> None:
        path = Path(self.registry_path)

        if not path.exists():
            return

        with path.open("r", encoding="utf-8") as file:
            raw_data = json.load(file)

        self.records = {}

        for biz_id, raw_record in raw_data.items():
            self.records[biz_id] = GlobalEntityRecord(
                biz_id=raw_record.get("biz_id", biz_id),
                entity_type=raw_record.get("entity_type", ""),
                display_name=raw_record.get("display_name", ""),
                owner_xml=raw_record.get(
                    "owner_xml",
                    "",
                ),
                source_json_path=raw_record.get(
                    "source_json_path",
                    "",
                ),
                is_defined=raw_record.get(
                    "is_defined",
                    True,
                ),
            )

    def save(self) -> None:
        path = Path(self.registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            biz_id: asdict(record)
            for biz_id, record in self.records.items()
        }

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

    def get(
        self,
        biz_id: str,
    ) -> Optional[GlobalEntityRecord]:
        return self.records.get(biz_id)

    def ensure(
        self,
        biz_id: str,
        entity_type: str = "",
        display_name: str = "",
        owner_xml: str = "",
        source_json_path: str = "",
    ) -> GlobalEntityRecord:
        """
        注册实体。

        如果实体已经存在，不重新创建 ID。
        """

        existing = self.records.get(biz_id)

        if existing is not None:
            # 不覆盖已有的权威值。
            # 只补充尚未填写的字段。
            if not existing.entity_type:
                existing.entity_type = entity_type

            if not existing.display_name:
                existing.display_name = display_name

            if not existing.owner_xml:
                existing.owner_xml = owner_xml

            if not existing.source_json_path:
                existing.source_json_path = source_json_path

            return existing

        record = GlobalEntityRecord(
            biz_id=biz_id,
            entity_type=entity_type,
            display_name=display_name,
            owner_xml=owner_xml,
            source_json_path=source_json_path,
            is_defined=True,
        )

        self.records[biz_id] = record
        return record

    def register_reference(
        self,
        biz_id: str,
        entity_type: str = "",
        display_name: str = "",
    ) -> Optional[GlobalEntityRecord]:
        """
        注册或获取一个引用目标。

        当前阶段不自动生成新业务 ID。
        引用不存在时返回 None，交给后续包级校验处理。
        """

        record = self.records.get(biz_id)

        if record is None:
            return None

        if entity_type and not record.entity_type:
            record.entity_type = entity_type

        if display_name and not record.display_name:
            record.display_name = display_name

        return record

    def contains(self, biz_id: str) -> bool:
        return biz_id in self.records

    def all_ids(self) -> list[str]:
        return list(self.records.keys())

    def all_records(self) -> list[GlobalEntityRecord]:
        return list(self.records.values())