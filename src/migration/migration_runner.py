from typing import Dict, List, Any, Optional
import json
from pathlib import Path
from ..session.event_store import EventStore
from .legacy_converter import LegacySessionConverter


class MigrationRunner:
    def __init__(self, event_store: EventStore):
        self._event_store = event_store
        self._converter = LegacySessionConverter(event_store)

    def migrate_from_file(self, input_path: str, output_session_id: Optional[str] = None) -> str:
        with open(input_path, "r", encoding="utf-8") as f:
            legacy_data = json.load(f)
        if isinstance(legacy_data, dict):
            return self._converter.convert_session(legacy_data, output_session_id)
        elif isinstance(legacy_data, list):
            session_ids = []
            for i, session_data in enumerate(legacy_data):
                target_id = f"{output_session_id}_{i}" if output_session_id else None
                session_id = self._converter.convert_session(session_data, target_id)
                session_ids.append(session_id)
            return session_ids
        else:
            raise ValueError(f"Unsupported legacy data format: {type(legacy_data)}")

    def migrate_from_dict(self, legacy_data: Dict[str, Any], output_session_id: Optional[str] = None) -> str:
        return self._converter.convert_session(legacy_data, output_session_id)

    def batch_migrate(self, input_dir: str, output_session_prefix: str = "migrated") -> List[str]:
        input_path = Path(input_dir)
        session_ids = []
        for file_path in input_path.glob("*.json"):
            try:
                session_id = self.migrate_from_file(str(file_path), output_session_id=f"{output_session_prefix}_{file_path.stem}")
                if isinstance(session_id, list):
                    session_ids.extend(session_id)
                else:
                    session_ids.append(session_id)
            except Exception as e:
                print(f"Failed to migrate {file_path}: {e}")
                continue
        return session_ids

    def generate_migration_report(self, legacy_data: Dict[str, Any]) -> Dict[str, Any]:
        session_key = legacy_data.get("session_key", "unknown")
        history = legacy_data.get("conversation_history", [])
        user_messages = sum(1 for m in history if m.get("role") == "user")
        assistant_messages = sum(1 for m in history if m.get("role") == "assistant")
        system_messages = sum(1 for m in history if m.get("role") == "system")
        return {"session_key": session_key, "total_messages": len(history), "user_messages": user_messages, "assistant_messages": assistant_messages, "system_messages": system_messages, "will_create_events": 1 + user_messages + assistant_messages + system_messages}
