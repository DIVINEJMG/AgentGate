from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORTS = ROOT / "backend" / "app" / "application" / "ports.py"

required = {
    "OrganizationRepository",
    "MembershipRepository",
    "AgentRepository",
    "WorkerRepository",
    "JobRepository",
    "WorkItemRepository",
    "IntegrationRepository",
    "CapabilityRepository",
    "PolicyRepository",
    "RiskRepository",
    "ApprovalRepository",
    "IncidentRepository",
    "ActionRepository",
    "AuditRepository",
    "MemoryRepository",
    "ResultRepository",
    "ObjectStorage",
    "CacheStore",
    "QueueBroker",
    "SecretVault",
    "IdentityProvider",
    "IntegrationAdapter",
}

tree = ast.parse(PORTS.read_text(encoding="utf-8"), filename=str(PORTS))
classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
missing = sorted(required - classes)
if missing:
    raise SystemExit(f"F28 canonical service contracts missing: {missing}")

print("F28 canonical service contracts verified.")
