from copy import deepcopy
from datetime import UTC, datetime, timedelta

from app.models import (
    LabComponentType, LabDashboardDefinition, LabEdge, LabMetricDefinition,
    LabModelVersion, LabNode, LabProject, LabResourcePool, LabScenario,
    LabScenarioEventTemplate, LabScript, LabScriptTestCase, LabScriptVersion,
    LabWorkflow, LabWorkflowNode, LabWorkflowTransition, LabWorkflowVersion,
    LabEventType, LabMonitorTemplate, LabQualificationDefinition,
)
from app.participant_catalog import seed_participant_process_pilots
from app.qualification_cases import QUALIFICATION_CASES

SCRIPT = '''
def validate(parameters):
    return [] if parameters.get("efficiency", 1) > 0 else ["efficiency must be positive"]

def initialize(ctx):
    ctx.state.setdefault("online", True)
    ctx.state.setdefault("temperature_c", ctx.parameters.get("temperature_c", 20.0))
    ctx.state.setdefault("resource_mwh", ctx.parameters.get("resource_mwh", 1000000.0))
    ctx.state.setdefault("demand_mw", ctx.parameters.get("demand_mw", 0.0))

def on_tick(ctx, dt):
    if not ctx.state.get("online", True):
        ctx.outputs[ctx.parameters.get("output_key", "flow_mw")] = 0.0
        return
    mode = ctx.parameters.get("mode", "transfer")
    efficiency = ctx.parameters.get("efficiency", 0.98)
    capacity = ctx.parameters.get("capacity_mw", 100.0)
    if mode == "resource":
        rate = min(capacity, ctx.state["resource_mwh"] * 3600 / max(dt, 1))
        ctx.state["resource_mwh"] = max(0, ctx.state["resource_mwh"] - rate * dt / 3600)
        ctx.outputs["gas_energy_mw"] = rate
    elif mode == "generation":
        fuel = sum(ctx.inputs.values()) or ctx.parameters.get("fuel_mw", capacity / max(efficiency, .01))
        thermal = fuel * ctx.parameters.get("boiler_efficiency", .9)
        mechanical = thermal * ctx.parameters.get("turbine_efficiency", .42)
        ctx.outputs["electric_power_mw"] = min(capacity, mechanical * ctx.parameters.get("generator_efficiency", .98))
        ctx.outputs["gas_consumption_mw"] = fuel
        ctx.outputs["thermal_power_mw"] = thermal
        ctx.outputs["mechanical_power_mw"] = mechanical
        ctx.outputs["waste_heat_mw"] = fuel - ctx.outputs["electric_power_mw"]
        ctx.outputs["co2_tph"] = fuel * ctx.parameters.get("co2_t_per_mwh", .202)
        ctx.state["co2_t"] = ctx.state.get("co2_t", 0) + ctx.outputs["co2_tph"] * dt / 3600
    elif mode == "load":
        ctx.state["demand_mw"] = ctx.parameters.get("demand_mw", capacity)
        ctx.outputs["heat_mw"] = ctx.state["demand_mw"] * ctx.parameters.get("heat_factor", 1.0)
    elif mode == "refrigerator":
        rated = ctx.parameters.get("demand_mw", .02)
        cop = ctx.parameters.get("cop", 3.0)
        ambient = ctx.parameters.get("ambient_c", 22.0)
        setpoint = ctx.parameters.get("setpoint_c", 4.0)
        power = rated if ctx.state["temperature_c"] >= setpoint + .2 else 0.0
        hours = dt / 3600
        ctx.state["demand_mw"] = power
        ctx.state["temperature_c"] += ((ambient - ctx.state["temperature_c"]) / ctx.parameters.get("thermal_tau_hours", 48) - power * cop / ctx.parameters.get("thermal_mass_mwh_per_c", 500)) * hours
        ctx.outputs.update({"heat_removed_mw": power * cop, "room_heat_mw": power * (cop + 1)})
    elif mode == "led":
        power = ctx.parameters.get("demand_mw", .01); visible = power * ctx.parameters.get("visible_efficiency", .35)
        ctx.state["demand_mw"] = power
        ctx.outputs.update({"visible_radiation_mw": visible, "heat_mw": power - visible, "absorbed_light_mw": visible * .92})
    else:
        inflow = sum(ctx.inputs.values()) or capacity
        ctx.outputs[ctx.parameters.get("output_key", "flow_mw")] = min(capacity, inflow) * efficiency

def on_event(ctx, event):
    kind = event.get("type", "")
    if kind in {"failure", "external_damage", "generation_attack", "substation_attack", "transmission_attack", "trip", "line_fault", "distribution_fault", "substation_fault", "interconnector_trip", "corridor_trip", "voltage_excursion", "overload", "vegetation_contact", "operator_error", "fire_spread", "gas_supply_loss", "pipeline_rupture", "fire", "gas_accumulation", "barrier_failure", "uncontrolled_flow"}:
        ctx.state["online"] = False; ctx.state["damage"] = event.get("payload", {}).get("severity", .7); ctx.log("component tripped", event=kind)
    elif kind == "scada_loss":
        ctx.state["scada_available"] = False; ctx.log("SCADA unavailable")
    elif kind in {"repair", "restore", "black_start", "resynchronize"}:
        ctx.state["online"] = True; ctx.state["damage"] = 0; ctx.log("component restored", event=kind)
    elif kind == "set_parameter":
        ctx.state.update(event.get("payload", {}))
'''

EVENT_GENERATION_SCRIPT = '''
def validate(parameters):
    return [] if parameters.get("efficiency", 1) > 0 else ["efficiency must be positive"]

def initialize(ctx):
    ctx.state.setdefault("online", True)
    ctx.state.setdefault("co2_t", 0.0)

def on_tick(ctx, dt):
    capacity = ctx.parameters.get("capacity_mw", 0.0)
    if not ctx.state.get("online", True):
        ctx.outputs["electric_power_mw"] = 0.0
        return
    availability = ctx.parameters.get("availability", 1.0)
    output = capacity * availability
    efficiency = max(ctx.parameters.get("efficiency", 1.0), .01)
    primary_energy = output / efficiency
    fuel_kind = ctx.parameters.get("fuel_kind", "none")
    ctx.outputs["electric_power_mw"] = output
    ctx.outputs["primary_energy_mw"] = primary_energy
    ctx.outputs["waste_heat_mw"] = max(0.0, primary_energy - output)
    if fuel_kind == "natural_gas":
        ctx.outputs["gas_consumption_mw"] = primary_energy
    emissions = primary_energy * ctx.parameters.get("direct_co2_t_per_mwh", 0.0)
    ctx.outputs["co2_tph"] = emissions
    ctx.state["co2_t"] = ctx.state.get("co2_t", 0.0) + emissions * dt / 3600

def on_event(ctx, event):
    kind = event.get("type", "")
    if kind in {"failure", "external_damage", "trip", "line_fault", "voltage_excursion", "overload", "operator_error", "fire"}:
        ctx.state["online"] = False
        ctx.state["damage"] = event.get("payload", {}).get("severity", .7)
        ctx.log("generator tripped", event=kind)
    elif kind in {"repair", "restore", "black_start", "resynchronize"}:
        ctx.state["online"] = True
        ctx.state["damage"] = 0
        ctx.log("generator restored", event=kind)
'''

SCENARIO_SCRIPT = '''
from datetime import datetime

def validate(parameters):
    return [] if parameters.get("timeline") else ["timeline configuration is required"]

def before_start(ctx):
    timeline = ctx.parameters["timeline"]
    ctx.timeline.configure(datetime.fromisoformat(timeline["start"]), datetime.fromisoformat(timeline["incident"]), datetime.fromisoformat(timeline["end"]))
    for event in ctx.parameters.get("script_events", []):
        ctx.clock.schedule(event["offset_seconds"], event["event_type"], event.get("payload", {}), event.get("target"), event.get("priority", 100), phase=event.get("phase", "scenario"), title=event.get("title", event["event_type"]), evidence=event.get("evidence", "assumed"))
'''

PROCESS_SCRIPT = '''
def validate(parameters):
    return [] if isinstance(parameters.get("process_rules", {}), dict) else ["process_rules must be an object"]

def on_event(ctx, event):
    for operation in ctx.parameters.get("process_rules", {}).get(event.get("type"), []):
        kind = operation["kind"]
        args = operation.get("args", {})
        if kind == "work.create": ctx.work.create(**args)
        elif kind == "work.update": ctx.work.update(operation["work_id"], **args)
        elif kind == "work.complete": ctx.work.complete(operation["work_id"], **args)
        elif kind == "resource.reserve": ctx.resources.reserve(**args)
        elif kind == "resource.consume": ctx.resources.consume(**args)
        elif kind == "resource.release": ctx.resources.release(**args)
        elif kind == "resource.produce": ctx.resources.produce(**args)
        elif kind == "node.patch": ctx.nodes.patch_state(operation.get("node_id") or event.get("target"), args)
        elif kind == "metric.publish": ctx.metrics.publish(**args)
        elif kind == "event.schedule": ctx.clock.schedule(**args)

def on_command(ctx, command):
    on_event(ctx, {"type": command.get("type"), "payload": command.get("payload", {}), "target": command.get("target")})
'''

DASHBOARD_SCRIPT = '''
def collect(ctx):
    metrics = ctx.snapshot.get("metrics", {})
    for metric_id, spec in ctx.parameters.get("dashboard_metrics", {}).items():
        ctx.metrics.publish(metric_id, metrics.get(spec.get("source", metric_id), spec.get("default", 0)), spec.get("unit", ""))

def describe_panels():
    return []
'''

COMPONENTS = [
    ("gas-stock", "Глобальний запас газу", "resource", "L0", "resource", "gas_energy_mw", 500, 1.0),
    ("well", "Газова свердловина", "gas", "L3", "transfer", "gas_flow_mw", 470, .98),
    ("gas-treatment", "Підготовка й осушення", "gas", "L3", "transfer", "gas_flow_mw", 450, .97),
    ("pipeline", "Магістральний газопровід / linepack", "gas", "L3", "transfer", "gas_flow_mw", 430, .985),
    ("steam-plant", "Газова парова ТЕС", "generation", "L2", "generation", "electric_power_mw", 180, .36),
    ("step-up", "Підвищувальний трансформатор", "substation", "L3", "transfer", "electric_power_mw", 180, .985),
    ("hv-line", "Магістральна ЛЕП", "line", "L3", "transfer", "electric_power_mw", 170, .97),
    ("substation", "Районна підстанція", "substation", "L3", "transfer", "electric_power_mw", 160, .98),
    ("distribution", "Мережа СН/НН", "grid", "L2", "transfer", "electric_power_mw", 150, .95),
    ("refrigerator", "Холодильник", "end_product", "L3", "refrigerator", "room_heat_mw", .02, 1.0),
    ("led", "LED-лампа", "end_product", "L3", "led", "visible_radiation_mw", .01, 1.0),
]

# Component types used by event-specific qualification models.  They are kept
# out of the universal reference chain: a historical model may instantiate only
# the asset classes that actually belong to that event.
EVENT_COMPONENTS = [
    ("external-grid", "Зовнішня енергосистема", "grid", "L1", "transfer", "electric_power_mw", 5000, .99),
    ("nuclear-plant", "Атомна електростанція", "generation", "L2", "generation", "electric_power_mw", 1000, .34),
    ("hydro-plant", "Гідроелектростанція", "generation", "L2", "generation", "electric_power_mw", 500, .90),
    ("thermal-plant", "Теплова електростанція", "generation", "L2", "generation", "electric_power_mw", 500, .38),
    ("wind-plant", "Вітрова електростанція", "generation", "L2", "generation", "electric_power_mw", 300, .95),
    ("solar-plant", "Сонячна електростанція", "generation", "L2", "generation", "electric_power_mw", 300, .95),
    ("transmission-corridor", "Лінія або коридор передачі", "line", "L1", "transfer", "electric_power_mw", 1000, .98),
    ("transformer-group", "Трансформатор або група трансформаторів", "substation", "L1", "transfer", "electric_power_mw", 1000, .985),
    ("transformer-bushing", "Високовольтний трансформаторний ввід", "substation", "L2", "transfer", "electric_power_mw", 1000, .99),
    ("grid-zone", "Зона енергосистеми", "grid", "L1", "transfer", "electric_power_mw", 2000, .98),
    ("customer-group", "Група споживачів", "grid", "L1", "transfer", "electric_power_mw", 2000, 1.0),
    ("protection-system", "Система релейного захисту й автоматики", "control", "L1", "control", "control_signal", 1, 1.0),
    ("response-process", "Процес реагування та відновлення", "control", "L1", "control", "control_signal", 1, 1.0),
    ("external-hazard", "Зовнішня небезпека", "control", "L1", "control", "control_signal", 1, 1.0),
    ("system-state", "Системний стан або показник", "control", "L1", "control", "control_signal", 1, 1.0),
    ("hvdc-interconnector", "Міжсистемний зв’язок HVDC", "line", "L1", "transfer", "electric_power_mw", 1000, .98),
    ("distributed-generation", "Розподілена генерація", "generation", "L1", "generation", "electric_power_mw", 100, 1.0),
    ("generation-aggregate", "Агрегована генерація без технологічного припущення", "generation", "L1", "generation", "electric_power_mw", 1000, 1.0),
    ("gas-supply", "Зовнішнє джерело природного газу", "gas", "L1", "transfer", "gas_flow_mw", 2000, .99),
    ("gas-control-terminal", "Газовий диспетчерський термінал", "gas", "L1", "transfer", "gas_flow_mw", 1500, .99),
    ("pipeline-valve", "Магістральний газовий клапан", "gas", "L2", "transfer", "gas_flow_mw", 1500, .995),
    ("pipeline-defect", "Дефект або слабка ділянка трубопроводу", "gas", "L2", "transfer", "gas_flow_mw", 1500, .99),
    ("gas-distribution-network", "Газорозподільна мережа", "gas", "L1", "transfer", "gas_flow_mw", 1000, .98),
    ("gas-release-zone", "Зона викиду природного газу", "gas", "L1", "transfer", "gas_flow_mw", 1000, 1.0),
    ("industrial-facility", "Промисловий об’єкт або будівельний майданчик", "control", "L1", "control", "control_signal", 1, 1.0),
    ("workforce-group", "Група працівників на майданчику", "control", "L1", "control", "control_signal", 1, 1.0),
    ("offshore-reservoir", "Морський нафтогазовий пласт", "hydrocarbon", "L1", "transfer", "hydrocarbon_flow", 2000, .99),
    ("offshore-wellbore", "Морська свердловина та обсадна колона", "hydrocarbon", "L1", "transfer", "hydrocarbon_flow", 2000, .99),
    ("mud-gas-separator", "Система відділення бурового розчину та газу", "hydrocarbon", "L2", "transfer", "hydrocarbon_flow", 1000, .98),
    ("marine-spill-zone", "Зона морського викиду вуглеводнів", "hydrocarbon", "L1", "transfer", "hydrocarbon_flow", 2000, 1.0),
    ("well-barrier", "Бар’єр цілісності свердловини", "control", "L2", "control", "control_signal", 1, 1.0),
    ("blowout-preventer", "Противикидний превентор", "control", "L2", "control", "control_signal", 1, 1.0),
    ("offshore-drilling-rig", "Морська бурова установка", "control", "L1", "control", "control_signal", 1, 1.0),
]

EVENT_COMPONENT_PARAMETERS = {
    "nuclear-plant": {"fuel_kind": "nuclear", "efficiency": .34, "availability": 1.0, "direct_co2_t_per_mwh": 0.0},
    "hydro-plant": {"fuel_kind": "water", "efficiency": .90, "availability": 1.0, "direct_co2_t_per_mwh": 0.0},
    "thermal-plant": {"fuel_kind": "thermal_fuel", "efficiency": .38, "availability": 1.0, "direct_co2_t_per_mwh": 0.0},
    "wind-plant": {"fuel_kind": "wind", "efficiency": .95, "availability": .5, "direct_co2_t_per_mwh": 0.0},
    "solar-plant": {"fuel_kind": "solar", "efficiency": .95, "availability": .5, "direct_co2_t_per_mwh": 0.0},
    "distributed-generation": {"fuel_kind": "mixed", "efficiency": 1.0, "availability": 1.0, "direct_co2_t_per_mwh": 0.0},
    "generation-aggregate": {"fuel_kind": "unspecified", "efficiency": 1.0, "availability": 1.0, "direct_co2_t_per_mwh": 0.0},
}


def component_ports(component_id: str, category: str, output: str) -> list[dict]:
    electrical_variables = [
        {"key": "voltage_pu", "name": "Напруга", "unit": "pu"},
        {"key": "frequency_hz", "name": "Частота", "unit": "Hz"},
        {"key": "electric_power_mw", "name": "Активна потужність", "unit": "MW"},
        {"key": "reactive_power_mvar", "name": "Реактивна потужність", "unit": "Mvar"},
    ]
    if component_id == "gas-stock":
        return [{"id": "out", "name": "Газ", "direction": "output", "domain": "natural_gas", "role": "stream", "quantity": "gas_energy_flow", "unit": "MW", "variable": output}]
    if category == "gas":
        return [
            {"id": "in", "name": "Газ · вхід", "direction": "input", "domain": "natural_gas", "role": "stream", "quantity": "gas_energy_flow", "unit": "MW"},
            {"id": "out", "name": "Газ · вихід", "direction": "output", "domain": "natural_gas", "role": "stream", "quantity": "gas_energy_flow", "unit": "MW", "variable": output},
        ]
    if category == "hydrocarbon":
        return [
            {"id": "in", "name": "Вуглеводні · вхід", "direction": "input", "domain": "hydrocarbon", "role": "stream", "quantity": "hydrocarbon_flow", "unit": "MW"},
            {"id": "out", "name": "Вуглеводні · вихід", "direction": "output", "domain": "hydrocarbon", "role": "stream", "quantity": "hydrocarbon_flow", "unit": "MW", "variable": output},
        ]
    if component_id == "steam-plant":
        return [
            {"id": "in", "name": "Природний газ", "direction": "input", "domain": "natural_gas", "role": "stream", "quantity": "gas_energy_flow", "unit": "MW", "required": True},
            {"id": "combustion_air", "name": "Повітря", "direction": "input", "domain": "control", "role": "stream", "quantity": "air_mass_flow", "unit": "kg/s"},
            {"id": "feedwater", "name": "Живильна вода", "direction": "input", "domain": "water_steam", "role": "stream", "quantity": "water_mass_flow", "unit": "kg/s"},
            {"id": "cooling", "name": "Охолодження", "direction": "input", "domain": "thermal", "role": "flow", "quantity": "heat_flow", "unit": "MW"},
            {"id": "staff", "name": "Персонал", "direction": "input", "domain": "staff", "role": "availability", "quantity": "qualified_workers", "unit": "person", "capabilities": ["operate", "repair"]},
            {"id": "out", "name": "Генератор 3φ", "direction": "output", "domain": "electric.ac", "role": "flow", "quantity": "three_phase_power", "unit": "MW", "variable": output, "variables": electrical_variables, "capabilities": ["trip", "synchronize", "setpoint"]},
            {"id": "emissions", "name": "Викиди", "direction": "output", "domain": "emission", "role": "stream", "quantity": "co2_mass_flow", "unit": "t/h"},
        ]
    if component_id == "thermal-plant":
        return [
            {"id": "control", "name": "Керування", "direction": "input", "domain": "control", "role": "signal", "quantity": "control_signal", "unit": ""},
            {"id": "fuel", "name": "Первинне паливо", "direction": "input", "domain": "fuel", "role": "stream", "quantity": "fuel_energy_flow", "unit": "MWth"},
            {"id": "out", "name": "Генератор 3φ", "direction": "output", "domain": "electric.ac", "role": "flow", "quantity": "three_phase_power", "unit": "MW", "variable": output, "variables": electrical_variables, "capabilities": ["trip", "synchronize", "setpoint"]},
        ]
    if category == "generation":
        return [
            {"id": "control", "name": "Керування", "direction": "input", "domain": "control", "role": "signal", "quantity": "control_signal", "unit": ""},
            {"id": "out", "name": "Генератор 3φ", "direction": "output", "domain": "electric.ac", "role": "flow", "quantity": "three_phase_power", "unit": "MW", "variable": output, "variables": electrical_variables, "capabilities": ["trip", "synchronize", "setpoint"]},
        ]
    if category == "control":
        return [
            {"id": "in", "name": "Сигнал · вхід", "direction": "input", "domain": "control", "role": "signal", "quantity": "control_signal", "unit": ""},
            {"id": "out", "name": "Сигнал · вихід", "direction": "output", "domain": "control", "role": "signal", "quantity": "control_signal", "unit": ""},
        ]
    if category in {"substation", "line", "grid"}:
        return [
            {"id": "in", "name": "AC · вхід", "direction": "input", "domain": "electric.ac", "role": "flow", "quantity": "three_phase_power", "unit": "MW", "required": True, "variables": electrical_variables},
            {"id": "out", "name": "AC · вихід", "direction": "output", "domain": "electric.ac", "role": "flow", "quantity": "three_phase_power", "unit": "MW", "multiple": category == "grid", "variable": output, "variables": electrical_variables, "capabilities": ["trip", "isolate"]},
        ]
    return [
        {"id": "in", "name": "Живлення", "direction": "input", "domain": "electric.ac", "role": "flow", "quantity": "three_phase_power", "unit": "MW", "required": True, "variables": electrical_variables},
        {"id": "out", "name": "Корисний вихід", "direction": "output", "domain": "thermal" if component_id == "refrigerator" else "control", "role": "flow", "quantity": output, "unit": "MW", "variable": output},
    ]


def seed_model_lab(db):
    for cid, name, category, level, mode, output, capacity, efficiency in [*COMPONENTS, *EVENT_COMPONENTS]:
        parameters = {"mode": mode, "capacity_mw": capacity, "efficiency": efficiency, **EVENT_COMPONENT_PARAMETERS.get(cid, {})}
        component_script = EVENT_GENERATION_SCRIPT if cid in EVENT_COMPONENT_PARAMETERS else SCRIPT
        db.merge(LabComponentType(id=cid, name=name, category=category, level=level, description="Абстрактний безтериторіальний компонент", parameters_schema=parameters, initial_state={"online": True}, ports=component_ports(cid, category, output), python_code=component_script, tick_seconds=3600, repairable=cid != "gas-stock", version=2))
    project = db.get(LabProject, "model-lab-energy") or LabProject(id="model-lab-energy", name="Газ → ТЕС → мережа → споживач", description="Безтериторіальна виконувана принципова модель")
    project = db.merge(project)
    published = db.get(LabModelVersion, "energy-v1") or LabModelVersion(id="energy-v1", project_id=project.id, version=1, status="published", name="Еталонний енергетичний контур", timezone="Europe/Kyiv", settings={"checkpoint_every": 24}, validation={"valid": True}, published_at=datetime.now(UTC))
    draft = db.get(LabModelVersion, "energy-draft") or LabModelVersion(id="energy-draft", project_id=project.id, version=2, status="draft", name="Робоча чернетка", timezone="Europe/Kyiv", settings={"checkpoint_every": 24})
    draft.architecture_version = 2
    db.merge(published); db.merge(draft); db.flush(); project.published_version_id = published.id; project.active_draft_id = draft.id
    for version_id, prefix in [(published.id, "p"), (draft.id, "d")]:
        previous = None
        for index, (cid, name, category, level, mode, output, capacity, efficiency) in enumerate(COMPONENTS):
            node_id = f"{prefix}-{cid}"
            parameters = {"mode": mode, "capacity_mw": capacity, "efficiency": efficiency, "output_key": output}
            if cid == "steam-plant": parameters.update(fuel_mw=500)
            if cid == "refrigerator": parameters.update(demand_mw=55, cop=3, temperature_c=4, ambient_c=22, setpoint_c=4, thermal_tau_hours=48, thermal_mass_mwh_per_c=500)
            if cid == "led": parameters.update(demand_mw=25, visible_efficiency=.35)
            db.merge(LabNode(id=node_id, model_version_id=version_id, component_type_id=cid, name=name, instance_key=cid, x=70 + index * 205, y=170 + (70 if cid == "led" else 0), parameters=parameters, initial_state={"online": True}, refinement_contract={"boundary_ports": ["in", "out"], "time_scale": "hybrid", "tolerance": .001, "capabilities": ["trip", "repair", "restore"]}, port_overrides={}, observable_overrides={}))
            if previous:
                source = f"{prefix}-distribution" if cid in {"refrigerator", "led"} else previous
                flow_type = "gas" if cid in {"well", "gas-treatment", "pipeline", "steam-plant"} else "electricity"
                db.merge(LabEdge(id=f"{prefix}-edge-{index}", model_version_id=version_id, connection_key=f"{source.removeprefix(prefix + '-')}-to-{cid}", source_node_id=source, source_port="out", target_node_id=node_id, target_port="in", flow_type=flow_type, domain="natural_gas" if flow_type == "gas" else "electric.ac", enabled=True, parameters={"efficiency": efficiency, "resistance_pu": .01, "reactance_pu": .08}))
            if cid not in {"refrigerator", "led"}: previous = node_id
    scripts = [
        ("scenario-calendar", "Календар сценарію", "scenario", SCENARIO_SCRIPT),
        ("process-resource", "Універсальний процес ресурсів і робіт", "process", PROCESS_SCRIPT),
        ("dashboard-energy", "Енергетичний монітор", "dashboard", DASHBOARD_SCRIPT),
    ]
    for script_id, name, kind, code in scripts:
        db.merge(LabScript(id=script_id, name=name, kind=kind, description="Версійований Python-контракт без прямого доступу до БД"))
        db.merge(LabScriptVersion(id=f"{script_id}-v1", script_id=script_id, version=1, status="published", code=code, contract={"context_api": 1}, validation={"valid": True}, published_at=datetime.now(UTC)))
    db.merge(LabScriptTestCase(id="process-generic-test", script_version_id="process-resource-v1", name="Невідомий процес не потребує змін рушія", action="event", fixture={"parameters": {"process_rules": {"custom_event": [{"kind": "metric.publish", "args": {"metric_id": "custom", "value": 1}}]}}, "event": {"type": "custom_event"}}, expected={"operation_group": "metrics"}))
    workflow = db.merge(LabWorkflow(id="storm-restoration", name="Storm Arwen · реагування і відновлення", description="Граф процесу; назви станів є даними"))
    workflow_version = db.merge(LabWorkflowVersion(id="storm-restoration-v1", workflow_id=workflow.id, version=1, status="published", name="Storm Arwen scripted restoration", description="Ця схема BPMN 2.0 описує повний процес оперативного реагування на шторм «Арвен». Після повідомлення про масові пошкодження мережі процес передбачає оцінювання доступу, створення заявок, резервування бригад і запасних частин, ізоляцію та ремонт пошкоджених об’єктів, випробування обладнання і відновлення живлення споживачів.", settings={"commands": [{"id": "dispatch_external_crew", "label": "Залучити зовнішню бригаду", "parameters": {"quantity": "integer"}}]}, published_at=datetime.now(UTC)))
    workflow_steps = [
        ("start", "start", "Готовність", "baseline_start"), ("damage", "event_trigger", "Масові пошкодження", "line_fault"),
        ("assess", "python_action", "Оцінка доступу", "damage_assessment"), ("order", "python_action", "Створення робіт", "work_order_created"),
        ("crew", "resource_operation", "Резервування бригад", "crew_reserved"), ("spares", "resource_operation", "Резервування деталей", "spare_reserved"),
        ("travel", "wait", "Виїзд бригад", "crew_departed"), ("arrive", "event_trigger", "Прибуття", "crew_arrived"),
        ("isolate", "component_command", "Ізоляція і безпека", "isolation_and_safety"), ("diagnosis", "python_action", "Діагностика", "diagnosis"),
        ("repair", "python_action", "Ремонт", "repair_started"), ("testing", "metric_check", "Випробування", "testing"),
        ("restore", "component_command", "Відновлення навантаження", "load_restoration"), ("success", "success", "Роботи завершено", "restoration_complete"),
    ]
    workflow_nodes = []
    for index, (key, block, name, event_type) in enumerate(workflow_steps):
        node = db.merge(LabWorkflowNode(id=f"storm-wf-{key}", workflow_version_id=workflow_version.id, node_key=key, block_type=block, name=name, x=60 + index * 180, y=120 + (index % 2) * 90, config={"event_type": event_type, "phase": "repair" if index > 2 else "incident"}, python_code=""))
        workflow_nodes.append(node)
    db.flush()
    for index, (source, target) in enumerate(zip(workflow_nodes, workflow_nodes[1:]), 1):
        db.merge(LabWorkflowTransition(id=f"storm-transition-{index}", workflow_version_id=workflow_version.id, source_node_id=source.id, target_node_id=target.id, condition_code="True", priority=100))
    metric_specs = [("generation_mw", "Генерація", "MW", "power"), ("demand_mw", "Споживання", "MW", "power"), ("unserved_mw", "Дефіцит", "MW", "power"), ("frequency_hz", "Частота", "Hz", "grid"), ("voltage_pu", "Напруга", "pu", "grid"), ("gas_consumption_mw", "Витрата газу", "MWth", "resource"), ("gas_remaining_mwh", "Запас газу", "MWh", "resource"), ("co2_tph", "CO₂", "t/h", "emission"), ("heat_mw", "Тепло", "MW", "energy"), ("visible_radiation_mw", "Видиме випромінювання", "MW", "energy"), ("restoration_percent", "Відновлено", "%", "restoration")]
    for metric_id, name, unit, category in metric_specs:
        db.merge(LabMetricDefinition(id=metric_id, name=name, unit=unit, category=category, format={"precision": 2}))
    event_specs = [
        ("trip", "Аварійне відключення", ["component"], ["electric.ac"], ["trip"], False),
        ("line_fault", "Пошкодження лінії", ["component", "connection"], ["electric.ac"], ["trip"], False),
        ("distribution_fault", "Пошкодження розподільної мережі", ["component", "connection"], ["electric.ac"], ["trip"], False),
        ("interconnector_trip", "Відключення міжсистемної лінії", ["component", "connection"], ["electric.ac"], ["trip"], False),
        ("corridor_trip", "Відключення внутрішнього коридору", ["component", "connection"], ["electric.ac"], ["trip"], False),
        ("generation_attack", "Атака на об’єкти генерації", ["component"], ["electric.ac"], ["trip"], False),
        ("substation_attack", "Атака на магістральні підстанції", ["component"], ["electric.ac"], ["trip"], False),
        ("transmission_attack", "Атака на мережу передачі", ["component", "connection"], ["electric.ac"], ["trip"], False),
        ("gas_supply_loss", "Втрата газопостачання", ["component", "connection"], ["natural_gas"], [], False),
        ("repair", "Завершення ремонту", ["component"], [], ["repair"], False),
        ("restore", "Відновлення", ["component"], [], ["restore"], False),
    ]
    for event_id, name, targets, domains, capabilities, broadcast in event_specs:
        db.merge(LabEventType(id=event_id, name=name, kind="stimulus", target_kinds=targets, domains=domains, required_capabilities=capabilities, payload_schema={"type": "object", "properties": {"severity": {"type": "number", "minimum": 0, "maximum": 1}}}, severity="warning", allow_broadcast=broadcast))
    default_dashboard_panels = [
            {"type": "metric_grid", "metrics": [item[0] for item in metric_specs[:8]]},
            {"type": "energy_flow", "metrics": ["gas_consumption_mw", "generation_mw", "heat_mw", "visible_radiation_mw"]},
            {
                "type": "diagnostic_charts",
                "sets": [
                    {
                        "id": "voltage-break",
                        "name": "Напруга · ТЕС ↔ споживач",
                        "series": [
                            {"point_component_type": "steam-plant", "metric": "voltage_pu", "style": "line", "color": "#f0bd63"},
                            {"point_component_type": "refrigerator", "metric": "voltage_pu", "style": "line", "color": "#b7f34a"},
                        ],
                    },
                    {
                        "id": "plant-operation",
                        "name": "Режим ТЕС",
                        "series": [
                            {"point_component_type": "steam-plant", "metric": "frequency_hz", "style": "bar", "color": "#64d8cb"},
                            {"point_component_type": "steam-plant", "metric": "electric_power_mw", "style": "line", "color": "#f0bd63"},
                        ],
                    },
                ],
            },
        ]
    dashboard = db.get(LabDashboardDefinition, "energy-system-monitor")
    if not dashboard:
        db.add(LabDashboardDefinition(
            id="energy-system-monitor", name="Газ → електроенергія → середовище",
            script_version_id="dashboard-energy-v1", panels=default_dashboard_panels,
        ))
    elif not any(panel.get("type") == "diagnostic_charts" for panel in dashboard.panels):
        dashboard.panels = [*dashboard.panels, default_dashboard_panels[-1]]
    if not db.get(LabMonitorTemplate, "energy-draft-monitor"):
        db.add(LabMonitorTemplate(
            id="energy-draft-monitor", model_version_id=draft.id, name="Монітор робочої моделі", revision=1,
            panels=[{"id": "main", "name": "Основні показники", "type": "chart", "series": []}],
        ))
    sources = {
      "ukraine-attacks":"https://ukraine.ohchr.org/en/Attacks-on-Ukraines-Energy-Infrastructure-Harm-to-the-Civilian-Population","ua-md-2026":"https://www.entsoe.eu/news/2026/02/05/power-system-incident-in-ukraine-and-moldova/","iberia-2025":"https://www.entsoe.eu/news/2026/03/20/entso-e-publishes-expert-panel-final-report-on-28-april-2025-blackout-in-spain-and-portugal/","south-australia-2016":"https://www.aemo.com.au/-/media/files/electricity/nem/market_notices_and_events/power_system_incident_reports/2017/integrated-final-report-sa-black-system-28-september-2016.pdf","hokkaido-2018":"https://www.enecho.meti.go.jp/about/special/johoteikyo/blackout.html","india-2012":"https://www.cercind.gov.in/2012/orders/Final_Report_Grid_Disturbance.pdf","northeast-2003":"https://www.ferc.gov/sites/default/files/2020-05/ch1-3_0.pdf","southwest-2011":"https://www.ferc.gov/sites/default/files/2020-04/Arizona-Southern%20California%20Outages%20on%20September%208%2C%202011.pdf","gb-2019":"https://www.ofgem.gov.uk/sites/default/files/docs/2019/09/eso_technical_report_-_final.pdf","north-hyde-2025":"https://www.neso.energy/document/363891/download","storm-arwen-2021":"https://www.gov.uk/government/publications/storm-arwen-electricity-distribution-disruption-review","texas-2021":"https://www.ferc.gov/sites/default/files/2021-12/Cold%20Weather%20Report_%202021_120821.pdf","san-bruno-sissonville":"https://www.ntsb.gov/investigations/AccidentReports/Reports/PAR1101.pdf","kleen-energy":"https://www.csb.gov/kleen-energy-natural-gas-explosion/","macondo":"https://www.csb.gov/macondo-blowout-and-explosion/"}
    sources["san-bruno-2010"] = sources["san-bruno-sissonville"]
    profiles = [
      ("baseline","Базовий добовий цикл","2026-01-01",[] ,{"black_system":False}),
      ("ukraine-attacks","Україна, атаки 2022–2026","2026-01-01",[("generation_attack","p-steam-plant",1),("substation_attack","p-step-up",2),("transmission_attack","p-hv-line",3)],{"damaged_assets":3,"critical_impact":True,"inventory_stress":True}),
      ("ua-md-2026","Україна–Молдова, 31.01.2026","2026-01-31",[("interconnector_trip","p-hv-line",1),("corridor_trip","p-substation",2),("restore","p-steam-plant",20),("resynchronize","p-hv-line",30)],{"system_separation":True,"resynchronized":True}),
      ("iberia-2025","Іспанія–Португалія, 28.04.2025","2025-04-28",[("voltage_excursion","p-substation",1),("trip","p-steam-plant",2)],{"voltage_cascade":True,"black_system":True}),
      ("south-australia-2016","Південна Австралія, 2016","2016-09-28",[("line_fault","p-hv-line",1),("trip","p-steam-plant",2)],{"repeated_voltage_dips":True,"black_system":True}),
      ("hokkaido-2018","Хоккайдо, 2018","2018-09-06",[("trip","p-steam-plant",1),("black_start","p-steam-plant",30)],{"frequency_cascade":True,"black_start":True}),
      ("india-2012","Індія, 30–31.07.2012","2012-07-30",[("overload","p-hv-line",1),("line_fault","p-hv-line",2)],{"overload_cascade":True}),
      ("northeast-2003","Northeast Blackout, 2003","2003-08-14",[("vegetation_contact","p-hv-line",1),("scada_loss","p-substation",2),("trip","p-steam-plant",3)],{"situational_awareness_loss":True,"cascade":True}),
      ("southwest-2011","Southwest Blackout, 2011","2011-09-08",[("operator_error","p-substation",1),("overload","p-step-up",2)],{"operator_cascade":True}),
      ("gb-2019","Велика Британія, 2019","2019-08-09",[("line_fault","p-hv-line",1),("trip","p-steam-plant",2)],{"frequency_shedding":True}),
      ("north-hyde-2025","North Hyde, 2025","2025-03-20",[("substation_fault","p-step-up",1),("fire_spread","p-substation",2),("repair","p-step-up",20)],{"critical_impact":True,"staged_restoration":True}),
      ("storm-arwen-2021","Storm Arwen, 2021","2021-11-26",[("line_fault","p-hv-line",1),("distribution_fault","p-distribution",2)],{"crew_shortage":True,"long_restoration":True}),
      ("texas-2021","Texas, 2021","2021-02-15",[("gas_supply_loss","p-pipeline",1),("trip","p-steam-plant",2)],{"gas_power_common_cause":True,"frequency_reserve_shortage":True}),
      ("san-bruno-2010","San Bruno, 2010","2010-09-09",[("pipeline_rupture","p-pipeline",1),("fire","p-pipeline",2)],{"linepack_loss":True,"fuel_shortage":True}),
      ("kleen-energy","Kleen Energy","2010-02-07",[("gas_accumulation","p-steam-plant",1),("external_damage","p-steam-plant",2)],{"explosion":True,"response_required":True}),
      ("macondo","Macondo","2010-04-20",[("barrier_failure","p-well",1),("uncontrolled_flow","p-well",2),("fire","p-well",3)],{"barrier_loss":True,"long_duration_release":True}),
    ]
    legacy_san_bruno = db.get(LabScenario, "san-bruno-sissonville")
    if legacy_san_bruno:
        legacy_san_bruno.configuration = {
            **legacy_san_bruno.configuration,
            "deprecated": True,
            "replaced_by": "san-bruno-2010",
        }
    legacy_san_bruno_model = db.get(
        LabModelVersion, "qualification-san-bruno-sissonville"
    )
    if legacy_san_bruno_model:
        legacy_san_bruno_model.settings = {
            **legacy_san_bruno_model.settings,
            "deprecated": True,
            "replaced_by": "qualification-san-bruno-2010",
        }
    for profile_index,(sid,name,date,steps,expected) in enumerate(profiles):
        model_id = published.id
        target_prefix = "p"
        package = QUALIFICATION_CASES.get(sid)
        event_model = package.get("model") if package else None
        if sid != "baseline":
            model_id = f"qualification-{sid}"
            target_prefix = (
                "q-san-bruno" if sid == "san-bruno-2010" else f"q{profile_index}"
            )
            model_version_number = (
                116 if sid == "san-bruno-2010" else 100 + profile_index
            )
            qualification_model = LabModelVersion(
                id=model_id, project_id=project.id, version=model_version_number,
                status="published", name=f"Кваліфікаційна модель · {name}",
                timezone="Europe/Kyiv",
                settings={"qualification_scenario": sid, "territorial_binding": False, "immutable": True},
                validation={"valid": True, "qualification_model": True}, published_at=datetime.now(UTC),
            )
            if event_model:
                qualification_model.settings = {
                    **qualification_model.settings,
                    "event_model_id": event_model["id"],
                    "topology_revision": event_model["topology_revision"],
                    "dedicated_topology": True,
                }
            db.merge(qualification_model); db.flush()
            if event_model:
                db.query(LabEdge).filter(LabEdge.model_version_id == model_id).delete(synchronize_session=False)
                db.query(LabNode).filter(LabNode.model_version_id == model_id).delete(synchronize_session=False)
                object_definitions = {
                    item["id"]: item for item in package.get("objects", [])
                }
                for node_spec in event_model["nodes"]:
                    component = db.get(LabComponentType, node_spec["component_type_id"])
                    object_definition = object_definitions.get(node_spec.get("qualification_object_id"))
                    capacity = node_spec.get("capacity_mw", component.parameters_schema.get("capacity_mw", 0))
                    capabilities = sorted((object_definition or {}).get("capabilities", {}))
                    db.add(LabNode(
                        id=node_spec["id"], model_version_id=model_id,
                        component_type_id=node_spec["component_type_id"],
                        name=node_spec["name"], instance_key=node_spec["id"],
                        x=float(node_spec["x"]), y=float(node_spec["y"]),
                        parameters={
                            **component.parameters_schema,
                            "capacity_mw": capacity,
                            "qualification_scenario": sid,
                            "qualification_object_id": node_spec.get("qualification_object_id"),
                            **node_spec.get("parameters", {}),
                        },
                        initial_state={
                            **component.initial_state,
                            "online": node_spec.get("initial_online", component.initial_state.get("online", True)),
                            "qualification_state": (object_definition or {}).get("initial_state", "not_applicable"),
                        },
                        refinement_contract={
                            "boundary_ports": ["in", "out"],
                            "time_scale": "hybrid",
                            "tolerance": .001,
                            "capabilities": capabilities,
                            "scenario_model": sid,
                            "event_model_id": event_model["id"],
                        },
                        port_overrides={}, observable_overrides={},
                    ))
                db.flush()
                for edge_spec in event_model["edges"]:
                    db.add(LabEdge(
                        id=edge_spec["id"], model_version_id=model_id,
                        connection_key=edge_spec["id"],
                        source_node_id=edge_spec["source"], source_port=edge_spec.get("source_port", "out"),
                        target_node_id=edge_spec["target"], target_port=edge_spec.get("target_port", "in"),
                        flow_type=edge_spec.get("flow_type", "electricity"),
                        domain=edge_spec.get("domain", "electric.ac"), enabled=edge_spec.get("enabled", True),
                        parameters={"efficiency": .98, "evidence": "event_specific_l1", **edge_spec.get("parameters", {})},
                    ))
            else:
                mapping = {}
                for cid, component_name, *_ in COMPONENTS:
                    source_node = db.get(LabNode, f"p-{cid}")
                    node_id = f"{target_prefix}-{cid}"
                    mapping[source_node.id] = node_id
                    db.merge(LabNode(
                        id=node_id, model_version_id=model_id, component_type_id=cid,
                        name=source_node.name, x=source_node.x, y=source_node.y,
                        parameters={**source_node.parameters, "qualification_scenario": sid},
                        initial_state=source_node.initial_state, python_code=source_node.python_code,
                        refinement_contract={**source_node.refinement_contract, "scenario_model": sid},
                    ))
                db.flush()
                for edge_index, source_edge in enumerate(db.query(LabEdge).filter(LabEdge.model_version_id == published.id).all(), 1):
                    db.merge(LabEdge(
                        id=f"{target_prefix}-edge-{edge_index}", model_version_id=model_id,
                        source_node_id=mapping[source_edge.source_node_id], source_port=source_edge.source_port,
                        target_node_id=mapping[source_edge.target_node_id], target_port=source_edge.target_port,
                        flow_type=source_edge.flow_type, parameters=source_edge.parameters,
                    ))
        incident_at = datetime.fromisoformat(f"{date}T08:00:00+00:00")
        run_start = incident_at - timedelta(days=1)
        run_end = incident_at + timedelta(days=13 if sid == "storm-arwen-2021" else 1)
        event_target_map = event_model.get("event_targets", {}) if event_model else {}
        def qualification_target(kind, target):
            return event_target_map.get(kind, target.replace("p-", f"{target_prefix}-", 1))
        events=[{"at":f"{date}T{8+i:02d}:00:00+00:00","event_type":kind,"target_node_id":qualification_target(kind, target),"priority":priority,"payload":{"severity":.8}} for i,(kind,target,priority) in enumerate(steps)]
        # Same labels as migration 0046, which on a clean install runs before seeding.
        titles = {"generation_attack": "Атака на об’єкти генерації", "substation_attack": "Атака на магістральні підстанції", "transmission_attack": "Атака на мережу передачі"}
        script_events = [{"offset_seconds": 86400 + i * 3600, "event_type": kind, "target": qualification_target(kind, target), "priority": priority, "payload": {"severity": .8}, "phase": "incident", "title": titles.get(kind, kind), "evidence": "derived"} for i, (kind, target, priority) in enumerate(steps)]
        process_rules = {}
        if sid == "storm-arwen-2021":
            hv, distribution = f"{target_prefix}-hv-line", f"{target_prefix}-distribution"
            def se(hours, event_type, title, phase, target=None, evidence="assumed", payload=None):
                return {"offset_seconds": int(hours * 3600), "event_type": event_type, "target": target, "priority": 100, "payload": payload or {}, "phase": phase, "title": title, "evidence": evidence}
            script_events = [
                se(0, "baseline_start", "Стабільний передаварійний режим", "preparation", evidence="derived"),
                se(12, "emergency_plan_activated", "Активація аварійного плану", "preparation", evidence="reported"),
                se(24, "severe_weather_arrival", "Початок сильного вітру, снігу й обмерзання", "incident", evidence="reported"),
                se(24, "line_fault", "Множинні пошкодження повітряних ЛЕП", "incident", hv, "reported", {"severity": .8, "fault_count": 9700}),
                se(25, "distribution_fault", "Пошкодження розподільних фідерів", "incident", distribution, "reported", {"severity": .8, "fault_count": 9700}),
                se(27, "damage_assessment", "Оцінка масштабу та безпечності доступу", "response"),
                se(29, "work_order_created", "Формування пакета ремонтних робіт", "response"),
                se(31, "crew_reserved", "Резервування власних і взаємодопоміжних бригад", "resources"),
                se(33, "spare_reserved", "Резервування опор, проводів та ізоляторів", "resources"),
                se(35, "crew_departed", "Виїзд аварійних бригад", "response"),
                se(40, "crew_arrived", "Прибуття до доступної ділянки", "response"),
                se(42, "isolation_and_safety", "Ізоляція пошкодженого обладнання", "repair"),
                se(44, "diagnosis", "Детальна діагностика", "repair"),
                se(47, "repeated_fault_discovered", "Виявлено додатковий дефект після першого огляду", "repair", evidence="reported"),
                se(48, "repair_started", "Початок ремонту", "repair"),
                se(60, "mobile_generator_connected", "Підключення мобільного генератора", "restoration", evidence="reported"),
                se(68, "repair_completed", "Завершення ремонту магістральної ділянки", "repair", hv),
                se(70, "repair_completed", "Завершення ремонту розподільної ділянки", "repair", distribution),
                se(72, "testing", "Електричні випробування", "testing"),
                se(72, "load_restoration", "Відновлено 82% навантаження", "restoration", evidence="reported", payload={"percent": 82}),
                se(96, "restoration_84_1", "Відновлено 84,1%", "restoration", evidence="reported", payload={"percent": 84.1}),
                se(200, "restoration_99", "Відновлено 99%", "restoration", evidence="reported", payload={"percent": 99}),
                se(320, "restoration_complete", "Відновлено останню групу споживачів", "restoration", evidence="reported", payload={"percent": 100}),
            ]
            process_rules = {
                "work_order_created": [{"kind": "work.create", "args": {"logical_id": "storm-main", "node_id": distribution, "status": "new", "required_resources": {"crew": "crew-line", "spare": "spare-line-kit"}}}],
                "damage_assessment": [{"kind": "work.update", "work_id": "storm-main", "args": {"status": "awaiting_access", "hazard_delay_hours": 5}}],
                "crew_reserved": [{"kind": "resource.reserve", "args": {"resource_id": "crew-line", "quantity": 4, "unit": "crew"}}, {"kind": "work.update", "work_id": "storm-main", "args": {"status": "crew_reserved"}}],
                "spare_reserved": [{"kind": "resource.reserve", "args": {"resource_id": "spare-line-kit", "quantity": 8, "unit": "kit"}}],
                "crew_departed": [{"kind": "work.update", "work_id": "storm-main", "args": {"status": "travelling"}}],
                "crew_arrived": [{"kind": "work.update", "work_id": "storm-main", "args": {"status": "diagnosis"}}],
                "repair_started": [{"kind": "resource.consume", "args": {"resource_id": "spare-line-kit", "quantity": 8, "unit": "kit"}}, {"kind": "work.update", "work_id": "storm-main", "args": {"status": "repairing", "repair_hours": 20}}],
                "repair_completed": [{"kind": "node.patch", "args": {"online": True}}, {"kind": "work.update", "work_id": "storm-main", "args": {"status": "testing"}}],
                "testing": [{"kind": "work.update", "work_id": "storm-main", "args": {"status": "testing"}}],
                "load_restoration": [{"kind": "metric.publish", "args": {"metric_id": "restoration_percent", "value": 82, "unit": "%"}}],
                "restoration_84_1": [{"kind": "metric.publish", "args": {"metric_id": "restoration_percent", "value": 84.1, "unit": "%"}}],
                "restoration_99": [{"kind": "metric.publish", "args": {"metric_id": "restoration_percent", "value": 99, "unit": "%"}}],
                "restoration_complete": [{"kind": "metric.publish", "args": {"metric_id": "restoration_percent", "value": 100, "unit": "%"}}, {"kind": "work.complete", "work_id": "storm-main", "args": {"status": "completed"}}],
                "dispatch_external_crew": [{"kind": "resource.produce", "args": {"resource_id": "crew-line", "quantity": 2, "unit": "crew", "source": "external_assistance"}}],
            }
        configuration = {"scenario_script_version_id": "scenario-calendar-v1", "dashboard_script_version_id": "dashboard-energy-v1", "dashboard_id": "energy-system-monitor", "dashboard_metrics": {"generation_mw": {"unit": "MW"}, "demand_mw": {"unit": "MW"}, "unserved_mw": {"unit": "MW"}, "frequency_hz": {"unit": "Hz"}, "gas_consumption_mw": {"unit": "MWth"}, "co2_tph": {"unit": "t/h"}}, "timeline": {"start": run_start.isoformat(), "incident": incident_at.isoformat(), "end": run_end.isoformat()}, "script_events": script_events, "process_rules": process_rules}
        pilot_package_ids = {
            "san-bruno-2010": "san-bruno-2010-l1",
            "northeast-2003": "northeast-blackout-2003-l1",
            "storm-arwen-2021": "storm-arwen-2021-l1",
            "gb-2019": "gb-power-outage-2019-l1",
            "south-australia-2016": "south-australia-black-system-2016-l1",
            "hokkaido-2018": "hokkaido-blackout-2018-l1",
            "southwest-2011": "southwest-blackout-2011-l1",
            "india-2012": "india-grid-disturbances-2012-l1",
            "texas-2021": "texas-cold-weather-2021-l1",
            "iberia-2025": "iberian-blackout-2025-l1",
            "north-hyde-2025": "north-hyde-outage-2025-l1",
            "kleen-energy": "kleen-energy-explosion-2010-l1",
            "macondo": "macondo-blowout-2010-l1",
            "ua-md-2026": "ukraine-moldova-incident-2026-l1",
            "ukraine-attacks": "ukraine-energy-attacks-2022-2026-l1",
        }
        if sid in pilot_package_ids:
            configuration["qualification_package_id"] = pilot_package_ids[sid]
        if sid == "storm-arwen-2021": configuration.update({"process_script_version_id": "process-resource-v1", "workflow_version_id": "storm-restoration-v1"})
        expected = {**expected, "required_event_types": sorted({event[0] for event in steps}), "minimum_events": len(steps)}
        # The scenario goes first: merging a template autoflushes, and on a clean install
        # the template's scenario foreign key must already be satisfied.
        db.merge(LabScenario(id=sid,model_version_id=model_id,name=name,description="Окрема виконувана безтериторіальна модель кваліфікаційної події",origin="test" if sid=="baseline" else "historical",source_url=sources.get(sid),start_at=run_start,incident_at=incident_at,pre_event_seconds=86400,post_event_seconds=int((run_end-incident_at).total_seconds()),duration_seconds=int((run_end-run_start).total_seconds()),seed=42,events=events,expected=expected,configuration=configuration))
        for template_index, event in enumerate(script_events):
            db.merge(LabScenarioEventTemplate(id=f"tpl-{sid}-{template_index}", scenario_id=sid, offset_seconds=event["offset_seconds"], priority=event.get("priority", 100), event_type=event["event_type"], target_node_id=event.get("target"), phase=event.get("phase", "scenario"), title=event.get("title", event["event_type"]), payload=event.get("payload", {}), evidence_kind=event.get("evidence", "assumed"), source_url=sources.get(sid), source_note="Публічна контрольна точка" if event.get("evidence") == "reported" else "Параметризоване припущення Model Lab"))
    db.flush()
    for scenario_id, package in QUALIFICATION_CASES.items():
        version_id = f"qualification-{scenario_id}"
        existing_definition = db.query(LabQualificationDefinition).filter_by(
            model_version_id=version_id
        ).one_or_none()
        if not existing_definition:
            db.add(
                LabQualificationDefinition(
                    id=f"qualification-definition-{scenario_id}",
                    model_version_id=version_id,
                    package_id=package["id"],
                    scenario_id=scenario_id,
                    revision=1,
                    document=deepcopy(package),
                )
            )
        elif package.get("schema_version") == 2:
            stored_revision = (existing_definition.document.get("model") or {}).get(
                "topology_revision", 0
            )
            source_revision = package["model"]["topology_revision"]
            if stored_revision < source_revision:
                existing_definition.package_id = package["id"]
                existing_definition.scenario_id = scenario_id
                existing_definition.revision += 1
                existing_definition.document = deepcopy(package)
    for rid,kind,name,available,external,lead,attributes in [
      ("spare-transformer","inventory","Силовий трансформатор",2,3,720,{"states":["available","reserved","picked","in_transit","installed"]}),
      ("spare-breaker","inventory","Високовольтний вимикач",6,8,168,{}),("spare-line-kit","inventory","Комплект ремонту ЛЕП",12,30,24,{}),("spare-scada","inventory","SCADA/телеком-комплект",4,12,12,{}),
      ("crew-line","crew","Лінійна бригада",4,8,2,{"unit":"crew","qualifications":["line","tower","conductor"]}),("crew-transformer","crew","Трансформаторна бригада",2,4,4,{"unit":"crew","qualifications":["transformer","oil","bushing"]}),("crew-relay","crew","Релейний захист",2,3,3,{"unit":"crew","qualifications":["protection","scada","commissioning"]}),("mobile-generator","equipment","Мобільний генератор",6,10,12,{"unit":"unit"}),("mobile-fuel","consumable","Паливо мобільних генераторів",240,500,8,{"unit":"MWh"})]:
        db.merge(LabResourcePool(id=rid,resource_kind=kind,class_name=name,available=available,reserved=0,external_available=external,lead_time_hours=lead,attributes=attributes))
    seed_participant_process_pilots(db)
    db.commit()
