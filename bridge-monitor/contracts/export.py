import json
from pathlib import Path

from contracts.events import EventEnvelope
from contracts.content import ContentObject
from contracts.queries import DashboardSnapshot, MetricSet


SCHEMAS = {"EventEnvelope": EventEnvelope, "ContentObject": ContentObject,
           "DashboardSnapshot": DashboardSnapshot, "MetricSet": MetricSet}


def export_schemas(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        (destination / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2), encoding="utf-8")
