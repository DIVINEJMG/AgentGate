from app.migration.contracts import LegacyRecord, NormalizedRecord


class CanonicalNormalizer:
    _SUPPORTED = {
        "organizations",
        "human_identities",
        "agent_identities",
        "workers",
        "jobs",
        "integrations",
        "capabilities",
        "policies",
        "results",
    }

    def normalize(self, record: LegacyRecord) -> NormalizedRecord:
        if record.entity_type not in self._SUPPORTED:
            raise ValueError(f"Unsupported legacy entity type: {record.entity_type}")
        payload = dict(record.payload)
        payload.pop("id", None)
        return NormalizedRecord(
            entity_type=record.entity_type,
            legacy_id=record.legacy_id,
            payload=payload,
        )


class CanonicalMigrationValidator:
    def validate(self, record: NormalizedRecord) -> None:
        if not record.legacy_id.strip():
            raise ValueError("legacy_id is required.")
        if record.entity_type == "organizations":
            name = str(record.payload.get("name") or "").strip()
            if len(name) < 2:
                raise ValueError("Organization name must be at least two characters.")
