from datetime import UTC, datetime
from sqlalchemy import text

from app.db import SessionLocal
from app.models import Entity, LabProject, ProcessModel, Relation, Route, Scenario, Source
from app.lab_seed import seed_model_lab
from app.participant_catalog import seed_participant_process_pilots

ENTITIES = [
    ("ua", "state", "institutional", "Ukraine", "UA"),
    ("operator", "company", "institutional", "Aftertime Nitrogen Operator", None),
    ("plant", "facility", "object", "Cherkasy nitrogen node", None),
    ("line", "production_line", "object", "Ammonia synthesis line 1", None),
    ("tank", "storage_asset", "object", "Ammonia tank R-01", None),
    ("railcar", "transport_asset", "object", "Rail tank car C-01", None),
    ("warehouse", "warehouse", "object", "Poltava regional warehouse", None),
    ("gas", "infrastructure", "object", "Gas supply", None),
    ("grid", "infrastructure", "object", "Power grid", None),
    ("rail", "infrastructure", "object", "Rail network", None),
    ("workforce", "workforce_pool", "agent", "Production shift", None),
    ("n", "atom", "atomic", "Nitrogen", "N"),
    ("h", "atom", "atomic", "Hydrogen", "H"),
    ("n2", "molecule", "molecular", "Molecular nitrogen", "N₂"),
    ("h2", "molecule", "molecular", "Molecular hydrogen", "H₂"),
    ("nh3", "substance", "substance", "Ammonia", "NH₃"),
]


def seed(*, if_empty: bool = False) -> None:
    with SessionLocal() as db:
        if if_empty and db.get(LabProject, "model-lab-energy"):
            return
        sources = [
            Source(
                id="orb",
                name="Aftertime reference model",
                url="https://orb-srp-prototype.neokkv.chatgpt.site",
                license="Project data",
                adapter="bootstrap",
            ),
            Source(
                id="chebi",
                name="ChEBI 2.0",
                url="https://www.ebi.ac.uk/chebi/backend/api/docs/",
                license="CC BY 4.0",
                adapter="chebi",
                configuration={"default_external_ids": ["16134", "18276", "17790"]},
            ),
            Source(
                id="worldbank",
                name="World Bank Indicators API",
                url="https://api.worldbank.org/v2/",
                license="CC BY 4.0",
                adapter="worldbank",
                configuration={"country": "UA", "indicator": "AG.CON.FERT.ZS"},
            ),
        ]
        for source in sources:
            db.merge(source)
        for entity_id, kind, level, name, symbol in ENTITIES:
            if not db.get(Entity, entity_id):
                db.add(
                    Entity(
                        id=entity_id,
                        kind=kind,
                        level=level,
                        name=name,
                        symbol=symbol,
                        jurisdiction="UA"
                        if level in {"object", "institutional", "agent"}
                        else None,
                        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                        attributes={"seed": True},
                    )
                )
        db.flush()
        relations = [
            ("r1", "operator", "operates", "plant"),
            ("r2", "plant", "contains", "line"),
            ("r3", "n2", "composed_of", "n"),
            ("r4", "h2", "composed_of", "h"),
            ("r5", "nh3", "composed_of", "n"),
            ("r6", "nh3", "composed_of", "h"),
            ("r7", "plant", "connected_to", "gas"),
            ("r8", "plant", "connected_to", "grid"),
            ("r9", "plant", "connected_to", "rail"),
        ]
        for relation_id, subject, predicate, object_id in relations:
            if not db.get(Relation, relation_id):
                db.add(
                    Relation(
                        id=relation_id,
                        subject_id=subject,
                        predicate=predicate,
                        object_id=object_id,
                        confidence=1,
                        source_id="orb",
                    )
                )
        db.merge(
            ProcessModel(
                id="ammonia-circuit",
                name="One tonne of ammonia",
                version="ammonia-circuit-v0.3",
                owner_entity_id="line",
                duration_hours=18,
                yield_factor=0.985,
                specification={
                    "reaction": "N2 + 3H2 -> 2NH3",
                    "energy_intensity_gj_per_t": 28,
                    "electricity_mwh_per_t": 0.7,
                    "water_kg_per_kg": 1.58,
                },
                source_id="orb",
            )
        )
        db.merge(
            Route(
                id="route-rail",
                origin_id="plant",
                destination_id="warehouse",
                mode="rail",
                distance_km=286,
                capacity_per_day=60,
                duration_hours=8,
                availability=0.9,
                loss_factor=0.001,
                recovery_hours=24,
            )
        )
        db.merge(
            Route(
                id="route-road",
                origin_id="plant",
                destination_id="warehouse",
                mode="road",
                distance_km=312,
                capacity_per_day=18,
                duration_hours=6.5,
                availability=0.86,
                loss_factor=0.002,
                recovery_hours=8,
            )
        )
        scenarios = [
            (
                "base",
                "Baseline",
                0.55,
                {"gas": 0.74, "grid": 0.88, "logistics": 0.67, "workforce": 0.91},
            ),
            (
                "resilient",
                "Resilient",
                0.25,
                {"gas": 0.92, "grid": 0.94, "logistics": 0.88, "workforce": 0.95},
            ),
            (
                "stress",
                "Logistics stress",
                0.20,
                {"gas": 0.48, "grid": 0.79, "logistics": 0.42, "workforce": 0.86},
            ),
        ]
        for scenario_id, name, probability, assumptions in scenarios:
            db.merge(
                Scenario(
                    id=scenario_id,
                    name=name,
                    description=f"Pilot scenario: {name.lower()}",
                    probability=probability,
                    assumptions=assumptions,
                    model_version="ammonia-circuit-v0.3",
                )
            )
        db.commit()
        seed_model_lab(db)


def seed_participants() -> None:
    """Apply additive, idempotent participant/process pilots to an existing lab."""
    with SessionLocal() as db:
        seed_participant_process_pilots(db)
        db.commit()
        mark_builtin_energy(db)


def mark_builtin_energy(db) -> None:
    """The energy model still seeded by the core belongs to the ``energy`` pack.

    Processes bound to tests come from their own packs and are left alone.
    """
    if not db.get(LabProject, "model-lab-energy"):
        return
    db.execute(text(
        "INSERT INTO lab_packs (id, title, version, description, source) VALUES "
        "('energy', 'Енергосистема', 'builtin', 'Вбудована модель Model Lab: газ → ТЕС → мережа → споживач', 'core') "
        "ON CONFLICT (id) DO NOTHING"
    ))
    db.execute(text("UPDATE lab_projects SET pack_id = 'energy' WHERE pack_id IS NULL"))
    db.execute(text(
        "UPDATE lab_workflows SET pack_id = 'energy' WHERE pack_id IS NULL AND id NOT IN ("
        "SELECT workflow_id FROM lab_workflow_versions WHERE bpmn_xml LIKE '%aftertime:testBinding=%')"
    ))
    db.commit()


if __name__ == "__main__":
    import sys

    if "--participants" in sys.argv:
        seed_participants()
    else:
        seed(if_empty="--if-empty" in sys.argv)
