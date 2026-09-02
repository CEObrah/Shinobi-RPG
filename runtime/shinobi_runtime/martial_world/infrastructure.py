"""Exact L0-L5 building and enterprise upgrade requirement calculators.

Buildings are physical projects: cash + registered physical materials + general
and skilled labor + minimum calendar time. Enterprise levels are organizational
maturity: cash + setup/management labor + real operating scale + supporting
buildings. Neither level creates free revenue, land, stock, or capacity.
"""
from __future__ import annotations
import json
from pathlib import Path
from functools import lru_cache
from typing import Any, Mapping
from .economy import lot_value_cash
_ROOT=Path(__file__).resolve().parents[3]
_MW=_ROOT/'game/data/martial-world'

@lru_cache(maxsize=None)
def _load(name:str)->Mapping[str,Any]:
    data=json.loads((_MW/name).read_text(encoding='utf-8'))
    if not isinstance(data,Mapping): raise ValueError(name)
    return data

def building_upgrade_requirements(building_type:str,target_level:int)->dict[str,Any]:
    spec=_load('buildings.json')['building_types'].get(building_type)
    if not isinstance(spec,Mapping): raise KeyError(building_type)
    level=spec.get('levels',{}).get(str(target_level))
    if not isinstance(level,Mapping): raise ValueError('target level must be 1..5')
    recipe=level.get('upgrade_to_this_level')
    if not isinstance(recipe,Mapping): raise ValueError('building upgrade recipe missing')
    return json.loads(json.dumps(recipe))

def enterprise_upgrade_requirements(enterprise_type:str,target_level:int)->dict[str,Any]:
    spec=_load('enterprises.json')['finite_types'].get(enterprise_type)
    if not isinstance(spec,Mapping): raise KeyError(enterprise_type)
    level=spec.get('levels',{}).get(str(target_level))
    if not isinstance(level,Mapping): raise ValueError('target level must be 1..5')
    recipe=level.get('upgrade_to_this_level')
    if not isinstance(recipe,Mapping): raise ValueError('enterprise upgrade recipe missing')
    return json.loads(json.dumps(recipe))

def project_material_value_cash(requirements:Mapping[str,Any])->int:
    req=requirements.get('materials')
    return lot_value_cash(req) if isinstance(req,Mapping) else 0

def compact_project_state(project: Mapping[str, Any], *, project_ref: str | None = None) -> dict[str, Any]:
    """Persist only the active project, never its start-transaction receipt."""
    out=json.loads(json.dumps(project))
    for key in ("treasury_cash_after_start", "material_stock_after_start", "quote", "commitment_ref"):
        out.pop(key, None)
    if out.get("completed") is False:
        out.pop("completed", None)
    if project_ref is not None and out.get("project_ref") == project_ref:
        out.pop("project_ref", None)
    return out


def building_upgrade_quote(building_type:str,target_level:int)->dict[str,Any]:
    r=building_upgrade_requirements(building_type,target_level)
    econ=_load('economy.json'); labor=econ['labor']
    material_value=project_material_value_cash(r)
    general=int(r['general_labor_hours'])*int(labor['general_labor_cash_per_hour'])
    skilled=int(r['skilled_labor_hours'])*int(labor['skilled_labor_cash_per_hour'])
    cash=int(r['cash_overhead'])
    return {'building_type':building_type,'target_level':target_level,'requirements':r,
            'material_value_cash':material_value,'general_labor_value_cash':general,'skilled_labor_value_cash':skilled,
            'total_economic_value_cash':cash+material_value+general+skilled}

def consume_building_start(*,treasury_cash:int,material_stock:Mapping[str,int],building_type:str,target_level:int)->dict[str,Any]:
    """Reserve start-only cash/materials. Labor/time remain project obligations."""
    q=building_upgrade_quote(building_type,target_level); r=q['requirements']; cash=int(r['cash_overhead'])
    if treasury_cash<cash: raise ValueError('insufficient treasury')
    stock={str(k):int(v) for k,v in material_stock.items()}
    for ref,qty in r['materials'].items():
        if stock.get(ref,0)<int(qty): raise ValueError(f'insufficient material:{ref}')
    for ref,qty in r['materials'].items(): stock[ref]-=int(qty)
    return {'treasury_cash_after':treasury_cash-cash,'material_stock_after':stock,
            'general_labor_hours_remaining':int(r['general_labor_hours']),'skilled_labor_hours_remaining':int(r['skilled_labor_hours']),
            'minimum_calendar_days':int(r['minimum_calendar_days']),'quote':q}

def enterprise_upgrade_quote(enterprise_type:str,target_level:int)->dict[str,Any]:
    r=enterprise_upgrade_requirements(enterprise_type,target_level); econ=_load('economy.json'); labor=econ['labor']
    mgmt=int(r['management_labor_hours'])*int(labor['skilled_labor_cash_per_hour'])
    general=int(r['general_setup_labor_hours'])*int(labor['general_labor_cash_per_hour'])
    cash=int(r['cash_overhead'])
    return {'enterprise_type':enterprise_type,'target_level':target_level,'requirements':r,
            'management_labor_value_cash':mgmt,'general_labor_value_cash':general,'total_economic_value_cash':cash+mgmt+general}

def _require_next_level(current_level:int,target_level:int)->None:
    if isinstance(current_level,bool) or isinstance(target_level,bool):
        raise ValueError('building/enterprise levels must be integers')
    if current_level < 0 or current_level > 4 or target_level != current_level + 1:
        raise ValueError('upgrades must advance exactly one level from 0 through 5')


def start_building_upgrade(
    *,
    treasury_cash:int,
    material_stock:Mapping[str,int],
    building_type:str,
    current_level:int,
    target_level:int,
    crafting_or_administration:int,
)->dict[str,Any]:
    """Validate and start one physical building upgrade.

    Cash and physical materials are consumed immediately. The higher level does
    not become active until both labor obligations and minimum calendar time are
    satisfied by :func:`advance_building_upgrade`.
    """
    _require_next_level(current_level,target_level)
    requirements=building_upgrade_requirements(building_type,target_level)
    required_skill=int(requirements.get('required_crafting_or_administration',0))
    if int(crafting_or_administration) < required_skill:
        raise ValueError('insufficient crafting/administration capability')
    consumed=consume_building_start(
        treasury_cash=treasury_cash,
        material_stock=material_stock,
        building_type=building_type,
        target_level=target_level,
    )
    return {
        'project_type':'building_upgrade',
        'building_type':building_type,
        'from_level':current_level,
        'target_level':target_level,
        'elapsed_calendar_days':0,
        'minimum_calendar_days':int(consumed['minimum_calendar_days']),
        'general_labor_hours_remaining':int(consumed['general_labor_hours_remaining']),
        'skilled_labor_hours_remaining':int(consumed['skilled_labor_hours_remaining']),
        'treasury_cash_after_start':int(consumed['treasury_cash_after']),
        'material_stock_after_start':consumed['material_stock_after'],
        'completed':False,
        'active_level':current_level,
        'quote':consumed['quote'],
    }


def advance_building_upgrade(
    project:Mapping[str,Any],
    *,
    elapsed_calendar_days:int,
    general_labor_hours:int,
    skilled_labor_hours:int,
)->dict[str,Any]:
    """Settle a deterministic chunk of an already-funded building project."""
    if project.get('project_type') != 'building_upgrade':
        raise ValueError('building upgrade project required')
    if min(elapsed_calendar_days,general_labor_hours,skilled_labor_hours) < 0:
        raise ValueError('project progress cannot be negative')
    out=json.loads(json.dumps(project))
    if out.get('completed'):
        return out
    out['elapsed_calendar_days']=int(out.get('elapsed_calendar_days',0))+int(elapsed_calendar_days)
    general_remaining=max(0,int(out.get('general_labor_hours_remaining',0))-int(general_labor_hours))
    skilled_remaining=max(0,int(out.get('skilled_labor_hours_remaining',0))-int(skilled_labor_hours))
    out['general_labor_hours_remaining']=general_remaining
    out['skilled_labor_hours_remaining']=skilled_remaining
    done=(out['elapsed_calendar_days']>=int(out['minimum_calendar_days']) and general_remaining==0 and skilled_remaining==0)
    out['completed']=bool(done)
    out['active_level']=int(out['target_level']) if done else int(out['from_level'])
    return out


def start_enterprise_upgrade(
    *,
    treasury_cash:int,
    enterprise_type:str,
    current_level:int,
    target_level:int,
    operating_scale:int,
    supporting_building_levels:Mapping[str,int],
)->dict[str,Any]:
    """Validate and fund one organizational enterprise upgrade."""
    _require_next_level(current_level,target_level)
    quote=enterprise_upgrade_quote(enterprise_type,target_level)
    req=quote['requirements']
    cash=int(req['cash_overhead'])
    if treasury_cash < cash:
        raise ValueError('insufficient treasury')
    if int(operating_scale) < int(req.get('minimum_operating_scale',0)):
        raise ValueError('insufficient operating scale')
    for building_ref,minimum in req.get('supporting_buildings_minimum_levels',{}).items():
        if int(supporting_building_levels.get(building_ref,0)) < int(minimum):
            raise ValueError(f'insufficient supporting building:{building_ref}')
    return {
        'project_type':'enterprise_upgrade',
        'enterprise_type':enterprise_type,
        'from_level':current_level,
        'target_level':target_level,
        'elapsed_calendar_days':0,
        'minimum_calendar_days':int(req['minimum_calendar_days']),
        'management_labor_hours_remaining':int(req['management_labor_hours']),
        'general_setup_labor_hours_remaining':int(req['general_setup_labor_hours']),
        'treasury_cash_after_start':int(treasury_cash)-cash,
        'completed':False,
        'active_level':current_level,
        'quote':quote,
    }


def advance_enterprise_upgrade(
    project:Mapping[str,Any],
    *,
    elapsed_calendar_days:int,
    management_labor_hours:int,
    general_setup_labor_hours:int,
)->dict[str,Any]:
    """Settle a deterministic chunk of an already-funded enterprise project."""
    if project.get('project_type') != 'enterprise_upgrade':
        raise ValueError('enterprise upgrade project required')
    if min(elapsed_calendar_days,management_labor_hours,general_setup_labor_hours) < 0:
        raise ValueError('project progress cannot be negative')
    out=json.loads(json.dumps(project))
    if out.get('completed'):
        return out
    out['elapsed_calendar_days']=int(out.get('elapsed_calendar_days',0))+int(elapsed_calendar_days)
    mgmt=max(0,int(out.get('management_labor_hours_remaining',0))-int(management_labor_hours))
    setup=max(0,int(out.get('general_setup_labor_hours_remaining',0))-int(general_setup_labor_hours))
    out['management_labor_hours_remaining']=mgmt
    out['general_setup_labor_hours_remaining']=setup
    done=(out['elapsed_calendar_days']>=int(out['minimum_calendar_days']) and mgmt==0 and setup==0)
    out['completed']=bool(done)
    out['active_level']=int(out['target_level']) if done else int(out['from_level'])
    return out


def building_level_row(building_type: str, level: int) -> dict[str, Any]:
    spec=_load('buildings.json')['building_types'].get(building_type)
    if not isinstance(spec,Mapping): raise KeyError(building_type)
    row=spec.get('levels',{}).get(str(max(0,min(5,int(level)))))
    if not isinstance(row,Mapping):
        if int(level)<=0: return {'quality_milli':0}
        raise KeyError(f'{building_type}:{level}')
    return json.loads(json.dumps(row))


def building_physical_model(building_type: str) -> dict[str, Any]:
    spec=_load('buildings.json')['building_types'].get(building_type)
    if not isinstance(spec,Mapping): raise KeyError(building_type)
    model=spec.get('physical_model')
    if not isinstance(model,Mapping): raise ValueError(f'physical model missing:{building_type}')
    return json.loads(json.dumps(model))


def building_quality_milli(building_type: str, level: int) -> int:
    return max(0,int(building_level_row(building_type,level).get('quality_milli',0)))


def _facility_row(infrastructure: Mapping[str,Any] | None, building_type: str) -> dict[str,Any]:
    if not isinstance(infrastructure,Mapping): return {}
    facilities=infrastructure.get('facilities',{})
    if not isinstance(facilities,Mapping): return {}
    row=facilities.get(building_type)
    return dict(row) if isinstance(row,Mapping) else {}


def facility_footprint_m2(infrastructure: Mapping[str,Any] | None, building_type: str) -> int:
    row=_facility_row(infrastructure,building_type)
    return max(0,int(row.get('footprint_m2',0)))


def facility_physical_effects(buildings: Mapping[str,Any], infrastructure: Mapping[str,Any] | None, building_type: str) -> dict[str,int]:
    """Derive physical capacity from mutable scale and quality from level.

    No capacity is inferred from level.  A Level-5 facility with 1,000 m2 and a
    Level-2 facility with 1,000 m2 have the same physical number of beds/seats;
    quality affects the work done inside them through the domain mechanic.
    """
    level=max(0,int(buildings.get(building_type,0))) if isinstance(buildings,Mapping) else 0
    if level<=0:return {}
    model=building_physical_model(building_type)
    row=_facility_row(infrastructure,building_type)
    footprint=max(0,int(row.get('footprint_m2',0)))
    if footprint<=0:return {}
    out={'footprint_m2':footprint}
    if building_type=='residential_compound':
        ratio=max(0,int(model.get('floor_area_ratio_milli',1000)))
        out['floor_area_m2']=footprint*ratio//1000
    elif building_type=='training_hall':
        out['floor_area_m2']=footprint
    elif building_type=='training_grounds':
        out['training_area_m2']=footprint
    rules=model.get('capacity_rules',{}) if isinstance(model.get('capacity_rules'),Mapping) else {}
    for key,rule in rules.items():
        if not isinstance(rule,Mapping):continue
        if int(rule.get('m2_per_unit',0))>0:
            out[str(key)]=footprint//int(rule['m2_per_unit'])
        elif int(rule.get('units_per_m2_milli',0))>0:
            out[str(key)]=footprint*int(rule['units_per_m2_milli'])//1000
    if building_type=='walls_gate':
        perimeter=max(0,int(row.get('defended_perimeter_m',0)))
        height=max(1,int(row.get('wall_height_m',model.get('base_wall_height_m',3))))
        quality=building_quality_milli(building_type,level)
        gate_spacing=max(1,int(model.get('gate_spacing_m',200)))
        watch_spacing=max(1,int(model.get('watch_spacing_m',25)))
        out.update({
            'defended_perimeter_m':perimeter,
            'wall_height_m':height,
            'gate_count':max(1,(perimeter+gate_spacing-1)//gate_spacing) if perimeter>0 else 0,
            'watch_positions':perimeter//watch_spacing,
            # Total barrier work scales with actual wall length and construction quality.
            'barrier_integrity':max(1,perimeter*6*max(500,quality)//1250) if perimeter>0 else 0,
            'climb_difficulty_milli':max(500,quality+max(0,height-5)*25),
        })
    return out


def estate_land_summary(infrastructure: Mapping[str,Any] | None) -> dict[str,int]:
    if not isinstance(infrastructure,Mapping): return {'estate_area_m2':0,'used_footprint_m2':0,'remaining_land_m2':0}
    estate=max(0,int(infrastructure.get('estate_area_m2',0)))
    facilities=infrastructure.get('facilities',{})
    used=0
    if isinstance(facilities,Mapping):
        for row in facilities.values():
            if isinstance(row,Mapping):used += max(0,int(row.get('footprint_m2',0)))
    return {'estate_area_m2':estate,'used_footprint_m2':used,'remaining_land_m2':max(0,estate-used)}


def building_expansion_requirements(building_type:str, *, current_level:int, additional_footprint_m2:int)->dict[str,Any]:
    if building_type=='walls_gate':
        raise ValueError('walls expand only with estate boundary')
    if current_level<=0:
        raise ValueError('facility quality level required before expansion')
    model=building_physical_model(building_type)
    added=int(additional_footprint_m2)
    if added<int(model.get('minimum_expansion_m2',1)):
        raise ValueError('expansion below minimum coherent increment')
    quality=max(500,building_quality_milli(building_type,current_level))
    standard=model.get('expansion_standard_per_100_m2',{})
    if not isinstance(standard,Mapping):raise ValueError('expansion recipe missing')
    # Charge by actual area and current construction quality. Ceil each conserved input.
    import math
    def scaled(v:int)->int:
        return max(1,math.ceil(int(v)*added*quality/(100*1000)))
    materials={str(k):scaled(int(v)) for k,v in (standard.get('materials',{}) or {}).items()}
    days=max(3,math.ceil(int(standard.get('minimum_calendar_days_per_1000_m2',3))*added/1000))
    return {
        'additional_footprint_m2':added,
        'cash_overhead':scaled(int(standard.get('cash_overhead',1))),
        'materials':materials,
        'general_labor_hours':scaled(int(standard.get('general_labor_hours',1))),
        'skilled_labor_hours':scaled(int(standard.get('skilled_labor_hours',1))),
        'minimum_calendar_days':days,
        'required_crafting_or_administration':max(0,(current_level-1)*20),
    }


def building_expansion_quote(building_type:str, *, current_level:int, additional_footprint_m2:int)->dict[str,Any]:
    r=building_expansion_requirements(building_type,current_level=current_level,additional_footprint_m2=additional_footprint_m2)
    econ=_load('economy.json'); labor=econ['labor']; material_value=project_material_value_cash(r)
    general=int(r['general_labor_hours'])*int(labor['general_labor_cash_per_hour'])
    skilled=int(r['skilled_labor_hours'])*int(labor['skilled_labor_cash_per_hour'])
    cash=int(r['cash_overhead'])
    return {'building_type':building_type,'current_level':current_level,'requirements':r,
            'material_value_cash':material_value,'general_labor_value_cash':general,
            'skilled_labor_value_cash':skilled,'total_economic_value_cash':cash+material_value+general+skilled}


def start_building_expansion(*, treasury_cash:int, material_stock:Mapping[str,int], buildings:Mapping[str,Any],
                             infrastructure:Mapping[str,Any], building_type:str, additional_footprint_m2:int,
                             crafting_or_administration:int)->dict[str,Any]:
    level=max(0,int(buildings.get(building_type,0)))
    q=building_expansion_quote(building_type,current_level=level,additional_footprint_m2=additional_footprint_m2)
    r=q['requirements']
    if int(crafting_or_administration)<int(r.get('required_crafting_or_administration',0)):
        raise ValueError('insufficient crafting/administration capability')
    land=estate_land_summary(infrastructure)
    if int(additional_footprint_m2)>int(land['remaining_land_m2']):raise ValueError('insufficient estate land')
    cash=int(r['cash_overhead'])
    if int(treasury_cash)<cash:raise ValueError('insufficient treasury')
    stock={str(k):int(v) for k,v in material_stock.items()}
    for ref,qty in r['materials'].items():
        if stock.get(ref,0)<int(qty):raise ValueError(f'insufficient material:{ref}')
    for ref,qty in r['materials'].items():stock[ref]-=int(qty)
    return {
        'project_type':'building_expansion','building_type':building_type,'quality_level':level,
        'additional_footprint_m2':int(additional_footprint_m2),'elapsed_calendar_days':0,
        'minimum_calendar_days':int(r['minimum_calendar_days']),
        'general_labor_hours_remaining':int(r['general_labor_hours']),
        'skilled_labor_hours_remaining':int(r['skilled_labor_hours']),
        'treasury_cash_after_start':int(treasury_cash)-cash,'material_stock_after_start':stock,
        'completed':False,'quote':q,
    }


def advance_building_expansion(project:Mapping[str,Any], *, elapsed_calendar_days:int,
                               general_labor_hours:int, skilled_labor_hours:int)->dict[str,Any]:
    if project.get('project_type')!='building_expansion':raise ValueError('building expansion project required')
    if min(elapsed_calendar_days,general_labor_hours,skilled_labor_hours)<0:raise ValueError('project progress cannot be negative')
    out=json.loads(json.dumps(project))
    if out.get('completed'):return out
    out['elapsed_calendar_days']=int(out.get('elapsed_calendar_days',0))+int(elapsed_calendar_days)
    out['general_labor_hours_remaining']=max(0,int(out.get('general_labor_hours_remaining',0))-int(general_labor_hours))
    out['skilled_labor_hours_remaining']=max(0,int(out.get('skilled_labor_hours_remaining',0))-int(skilled_labor_hours))
    out['completed']=bool(out['elapsed_calendar_days']>=int(out['minimum_calendar_days']) and out['general_labor_hours_remaining']==0 and out['skilled_labor_hours_remaining']==0)
    return out



def estate_boundary_expansion_requirements(*, infrastructure: Mapping[str,Any], walls_level: int,
                                           additional_land_m2: int, settlement_kind: str) -> dict[str,Any]:
    """Quote one lawful adjacent urban-estate acquisition plus outer-wall extension.

    The current estate remains one asset.  Added land increases the estate area;
    the existing outer wall is extended to a perimeter consistent with the
    estate's current shape factor.  Rural holdings are intentionally unrelated.
    """
    import math
    added=max(0,int(additional_land_m2))
    if added<500:
        raise ValueError('estate expansion below minimum coherent adjacent parcel')
    estate=max(0,int(infrastructure.get('estate_area_m2',0))) if isinstance(infrastructure,Mapping) else 0
    facilities=infrastructure.get('facilities',{}) if isinstance(infrastructure,Mapping) else {}
    wall=facilities.get('walls_gate',{}) if isinstance(facilities,Mapping) else {}
    old_perimeter=max(0,int(wall.get('defended_perimeter_m',0))) if isinstance(wall,Mapping) else 0
    wall_height=max(1,int(wall.get('wall_height_m',4))) if isinstance(wall,Mapping) else 4
    if estate<=0 or old_perimeter<=0 or int(walls_level)<=0:
        raise ValueError('existing defended estate boundary required')
    new_area=estate+added
    # Preserve the current estate's compactness/shape rather than pretending
    # every purchase creates a perfect square or an arbitrary straight wall.
    shape_factor=old_perimeter/max(1.0,math.sqrt(float(estate)))
    new_perimeter=max(old_perimeter+1,int(math.ceil(shape_factor*math.sqrt(float(new_area)))))
    extension_m=new_perimeter-old_perimeter
    quality=max(500,building_quality_milli('walls_gate',int(walls_level)))

    econ=_load('economy.json'); land=econ.get('land',{}) if isinstance(econ.get('land'),Mapping) else {}
    base=max(1,int(land.get('base_urban_land_cash_per_m2',18)))
    tiers=land.get('urban_tier_multiplier_milli',{}) if isinstance(land.get('urban_tier_multiplier_milli'),Mapping) else {}
    tier=max(250,int(tiers.get(str(settlement_kind),tiers.get('city',1000))))
    friction=max(500,int(land.get('adjacent_estate_purchase_friction_milli',1000)))
    land_purchase_cash=math.ceil(added*base*tier*friction/1_000_000)

    # Conserved wall quantities scale with newly required perimeter, height and
    # current construction quality.  This is intentionally expensive enough
    # that capital-city estate growth is a strategic investment, not free land.
    wall_scale=extension_m*wall_height*quality
    materials={
        'stone_kg': max(1,math.ceil(wall_scale*420/1000)),
        'brick_tile_kg': max(1,math.ceil(wall_scale*150/1000)),
        'lime_kg': max(1,math.ceil(wall_scale*45/1000)),
        'timber_kg': max(1,math.ceil(extension_m*quality*35/1000)),
        'metal_kg': max(1,math.ceil(extension_m*quality*4/1000)),
    }
    general_hours=max(1,math.ceil(extension_m*wall_height*quality*7/1000))
    skilled_hours=max(1,math.ceil(extension_m*wall_height*quality*3/1000))
    minimum_days=max(14,math.ceil(added/350)+math.ceil(extension_m/5))
    legal_admin_cash=max(1000,math.ceil(land_purchase_cash*80/1000))
    return {
        'additional_land_m2':added,
        'old_estate_area_m2':estate,
        'new_estate_area_m2':new_area,
        'old_perimeter_m':old_perimeter,
        'new_perimeter_m':new_perimeter,
        'perimeter_extension_m':extension_m,
        'wall_height_m':wall_height,
        'land_purchase_cash':land_purchase_cash,
        'legal_admin_cash':legal_admin_cash,
        'cash_overhead':land_purchase_cash+legal_admin_cash,
        'materials':materials,
        'general_labor_hours':general_hours,
        'skilled_labor_hours':skilled_hours,
        'minimum_calendar_days':minimum_days,
        'settlement_kind':str(settlement_kind),
    }


def estate_boundary_expansion_quote(*, infrastructure: Mapping[str,Any], walls_level: int,
                                    additional_land_m2: int, settlement_kind: str) -> dict[str,Any]:
    r=estate_boundary_expansion_requirements(
        infrastructure=infrastructure,walls_level=walls_level,
        additional_land_m2=additional_land_m2,settlement_kind=settlement_kind,
    )
    econ=_load('economy.json'); labor=econ['labor']; material_value=project_material_value_cash(r)
    general=int(r['general_labor_hours'])*int(labor['general_labor_cash_per_hour'])
    skilled=int(r['skilled_labor_hours'])*int(labor['skilled_labor_cash_per_hour'])
    return {
        'requirements':r,'material_value_cash':material_value,
        'general_labor_value_cash':general,'skilled_labor_value_cash':skilled,
        'total_economic_value_cash':int(r['cash_overhead'])+material_value+general+skilled,
    }


def start_estate_boundary_expansion(*, treasury_cash:int, material_stock:Mapping[str,int],
                                    infrastructure:Mapping[str,Any], walls_level:int,
                                    additional_land_m2:int, settlement_kind:str)->dict[str,Any]:
    q=estate_boundary_expansion_quote(
        infrastructure=infrastructure,walls_level=walls_level,
        additional_land_m2=additional_land_m2,settlement_kind=settlement_kind,
    )
    r=q['requirements']; cash=int(r['cash_overhead'])
    if int(treasury_cash)<cash: raise ValueError('insufficient treasury')
    stock={str(k):int(v) for k,v in material_stock.items()}
    for ref,qty in r['materials'].items():
        if stock.get(ref,0)<int(qty): raise ValueError(f'insufficient material:{ref}')
    for ref,qty in r['materials'].items(): stock[ref]-=int(qty)
    return {
        'project_type':'estate_boundary_expansion',
        'additional_land_m2':int(r['additional_land_m2']),
        'new_estate_area_m2':int(r['new_estate_area_m2']),
        'old_perimeter_m':int(r['old_perimeter_m']),
        'new_perimeter_m':int(r['new_perimeter_m']),
        'perimeter_extension_m':int(r['perimeter_extension_m']),
        'wall_height_m':int(r['wall_height_m']),
        'elapsed_calendar_days':0,'minimum_calendar_days':int(r['minimum_calendar_days']),
        'general_labor_hours_remaining':int(r['general_labor_hours']),
        'skilled_labor_hours_remaining':int(r['skilled_labor_hours']),
        'treasury_cash_after_start':int(treasury_cash)-cash,
        'material_stock_after_start':stock,'completed':False,'quote':q,
    }


def advance_estate_boundary_expansion(project:Mapping[str,Any], *, elapsed_calendar_days:int,
                                      general_labor_hours:int, skilled_labor_hours:int)->dict[str,Any]:
    if project.get('project_type')!='estate_boundary_expansion':
        raise ValueError('estate boundary expansion project required')
    if min(elapsed_calendar_days,general_labor_hours,skilled_labor_hours)<0:
        raise ValueError('project progress cannot be negative')
    out=json.loads(json.dumps(project))
    if out.get('completed'): return out
    out['elapsed_calendar_days']=int(out.get('elapsed_calendar_days',0))+int(elapsed_calendar_days)
    out['general_labor_hours_remaining']=max(0,int(out.get('general_labor_hours_remaining',0))-int(general_labor_hours))
    out['skilled_labor_hours_remaining']=max(0,int(out.get('skilled_labor_hours_remaining',0))-int(skilled_labor_hours))
    out['completed']=bool(out['elapsed_calendar_days']>=int(out['minimum_calendar_days']) and out['general_labor_hours_remaining']==0 and out['skilled_labor_hours_remaining']==0)
    return out


def training_domain_capacity(buildings: Mapping[str, Any], domain: str, infrastructure: Mapping[str,Any] | None=None) -> int:
    hall=facility_physical_effects(buildings,infrastructure,'training_hall').get('simultaneous_indoor_trainees',0)
    grounds=facility_physical_effects(buildings,infrastructure,'training_grounds').get('simultaneous_outdoor_trainees',0)
    qi=facility_physical_effects(buildings,infrastructure,'qi_hall')
    workshop=facility_physical_effects(buildings,infrastructure,'armory_workshop')
    infirmary=facility_physical_effects(buildings,infrastructure,'infirmary_apothecary')
    library=facility_physical_effects(buildings,infrastructure,'library_records')
    if domain in {'sword','unarmed'}: return max(0,hall+grounds)
    if domain in {'spear','bow','hidden_weapons','stealth_scouting','command'}: return max(0,grounds)
    if domain=='instruction': return max(0,hall)
    if domain=='qi': return max(0,qi.get('simultaneous_qi_trainees',0))
    if domain=='qi_control': return max(0,min(qi.get('simultaneous_qi_trainees',0),qi.get('quiet_chambers',0)))
    if domain=='medicine': return max(0,infirmary.get('treatment_stations',0)+infirmary.get('apothecary_workstations',0))
    if domain=='crafting': return max(0,workshop.get('craft_workstations',0))
    if domain in {'administration','commerce'}: return max(0,library.get('research_seats',0))
    return max(0,hall)


def administrative_workload_units(*, population: int, active_enterprises: int, landholding_units: int,
                                  active_contracts: int, active_projects: int, external_holdings: int) -> int:
    return max(1, (max(0,int(population))+9)//10 + max(0,int(active_enterprises))*6
               + max(0,int(landholding_units))*2 + max(0,int(active_contracts))*4
               + max(0,int(active_projects))*6 + max(0,int(external_holdings))*3)


def staffed_administrative_capability(people: list[Mapping[str,Any]], *, main_hall_level: int, infrastructure:Mapping[str,Any]|None=None, unavailable_refs: set[str]|frozenset[str]=frozenset()) -> int:
    quality=max(500,building_quality_milli('main_hall',main_hall_level))
    workstations=max(0,int(facility_physical_effects({'main_hall':main_hall_level},infrastructure,'main_hall').get('administrative_workstations',0)))
    if workstations<=0:return 0
    total=0
    contributors=0
    for person in people:
        if contributors>=workstations:break
        if not isinstance(person,Mapping) or str(person.get('person_id') or '') in unavailable_refs: continue
        health=person.get('health',{}) if isinstance(person.get('health'),Mapping) else {}
        if health.get('status') in {'dead','incapacitated'}: continue
        prof=person.get('professional_skills',{}) if isinstance(person.get('professional_skills'),Mapping) else {}
        attrs=person.get('attributes',{}) if isinstance(person.get('attributes'),Mapping) else {}
        administration=max(0,int(prof.get('administration',0))); intelligence=max(0,int(attrs.get('intelligence',0)))
        if administration<=0: continue
        total += max(1,(administration*3+intelligence)//40); contributors += 1
    return max(0,total*quality//1000)


def administration_factor_milli(*, workload_units: int, capability_units: int) -> int:
    workload=max(1,int(workload_units)); capability=max(0,int(capability_units))
    if capability>=workload: return min(1200,1000+(capability-workload)*200//workload)
    return max(400,capability*1000//workload)


def storage_capacity_kg(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None) -> dict[str,int]:
    effects=facility_physical_effects(buildings,infrastructure,'storehouse')
    return {'dry_storage_kg':max(0,int(effects.get('dry_storage_kg',0))), 'secure_storage_kg':max(0,int(effects.get('secure_storage_kg',0)))}


def transport_yard_capacity(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None) -> dict[str,int]:
    effects=facility_physical_effects(buildings,infrastructure,'transport_yard')
    return {k:max(0,int(effects.get(k,0))) for k in ('mount_or_pack_slots','wagon_slots')}


def residential_capacity(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None) -> int:
    return max(0,int(facility_physical_effects(buildings,infrastructure,'residential_compound').get('resident_capacity',0)))


def workshop_capacity(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None) -> dict[str,int]:
    effects=facility_physical_effects(buildings,infrastructure,'armory_workshop')
    return {k:max(0,int(effects.get(k,0))) for k in ('craft_workstations','repair_bays')}


def infirmary_capacity(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None) -> dict[str,int]:
    effects=facility_physical_effects(buildings,infrastructure,'infirmary_apothecary')
    return {k:max(0,int(effects.get(k,0))) for k in ('beds','treatment_stations','apothecary_workstations')}


def library_capacity(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None=None) -> int:
    return max(0,int(facility_physical_effects(buildings,infrastructure,'library_records').get('research_seats',0)))


def inventory_storage_usage_kg(inventory: Mapping[str,Any], equipment_catalog: Mapping[str,Any]) -> dict[str,float]:
    from .equipment import resolve_equipment_item
    dry=float(max(0,int(inventory.get('food_ration_days',0)))) * 0.75
    raw=inventory.get('raw_materials',{}) if isinstance(inventory.get('raw_materials'),Mapping) else {}
    for ref,qty in raw.items():
        q=max(0,float(qty)); name=str(ref)
        if name.endswith('_kg'): dry += q
        elif name.endswith('_m'): dry += q * (0.20 if 'cloth' in name else 0.08)
        else: dry += q * 0.25
    herbs=inventory.get('herbs',{}) if isinstance(inventory.get('herbs'),Mapping) else {}
    dry += sum(max(0,float(q))*0.05 for q in herbs.values())
    secure=0.0
    equipment=inventory.get('equipment',{}) if isinstance(inventory.get('equipment'),Mapping) else {}
    for ref,qty in equipment.items():
        item=resolve_equipment_item(equipment_catalog,str(ref))
        if isinstance(item,Mapping): secure += max(0,float(qty))*max(0.0,float(item.get('mass_kg',0.0)))
    medicines=inventory.get('medicines',{}) if isinstance(inventory.get('medicines'),Mapping) else {}
    poisons=inventory.get('poisons',{}) if isinstance(inventory.get('poisons'),Mapping) else {}
    secure += sum(max(0,float(q))*0.10 for q in medicines.values())
    secure += sum(max(0,float(q))*0.05 for q in poisons.values())
    return {'dry_storage_kg':round(dry,3),'secure_storage_kg':round(secure,3)}


def storage_capacity_check(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None, inventory: Mapping[str,Any], equipment_catalog: Mapping[str,Any]) -> dict[str,Any]:
    capacity=storage_capacity_kg(buildings,infrastructure); usage=inventory_storage_usage_kg(inventory,equipment_catalog)
    return {'capacity_kg':capacity,'usage_kg':usage,'within_capacity':bool(usage['dry_storage_kg']<=capacity['dry_storage_kg'] and usage['secure_storage_kg']<=capacity['secure_storage_kg'])}


def transport_capacity_check(buildings: Mapping[str,Any], infrastructure:Mapping[str,Any]|None, inventory: Mapping[str,Any]) -> dict[str,Any]:
    from .aggregate_transport import freight_service_units
    capacity=transport_yard_capacity(buildings,infrastructure)
    pooled=inventory.get('transport_capacity',{}) if isinstance(inventory.get('transport_capacity'),Mapping) else {}
    rider_slots=max(0,int(pooled.get('rider_slots',0))); freight_kg=max(0,int(pooled.get('freight_capacity_kg',0)))
    service_units=rider_slots+freight_service_units(freight_kg)
    return {'capacity':capacity,'rider_slots_used':rider_slots,'freight_capacity_kg':freight_kg,'transport_service_units_used':service_units,'within_capacity':bool(service_units<=capacity['mount_or_pack_slots'])}



def enterprise_level_row(enterprise_type:str, level:int)->dict[str,Any]:
    spec=_load('enterprises.json')['finite_types'].get(enterprise_type)
    if not isinstance(spec,Mapping):raise KeyError(enterprise_type)
    row=spec.get('levels',{}).get(str(max(1,min(5,int(level))))) if int(level)>0 else None
    return json.loads(json.dumps(row)) if isinstance(row,Mapping) else {'operating_efficiency_milli':0,'management_load':0}


def enterprise_scale_basis(enterprise_type:str)->str:
    spec=_load('enterprises.json')['finite_types'].get(enterprise_type)
    if not isinstance(spec,Mapping):raise KeyError(enterprise_type)
    model=spec.get('scale_model',{})
    basis=model.get('scale_basis') if isinstance(model,Mapping) else None
    if not isinstance(basis,str) or not basis:raise ValueError('enterprise scale basis missing')
    return basis


def enterprise_operating_efficiency_milli(enterprise_type:str, level:int)->int:
    return max(0,int(enterprise_level_row(enterprise_type,level).get('operating_efficiency_milli',0)))


def enterprise_scale_value(faction:Mapping[str,Any], enterprise_type:str)->int:
    scales=faction.get('enterprise_scale',{}) if isinstance(faction.get('enterprise_scale'),Mapping) else {}
    row=scales.get(enterprise_type,{}) if isinstance(scales,Mapping) else {}
    basis=enterprise_scale_basis(enterprise_type)
    return max(0,int(row.get(basis,0))) if isinstance(row,Mapping) else 0


def enterprise_scale_expansion_requirements(enterprise_type:str, *, current_level:int, additional_scale:int)->dict[str,Any]:
    """Organizational scale expansion at current quality.

    This does not create land, workstations, people, vehicles or stock. Domain
    prerequisites are checked by the command using current physical holdings.
    """
    if current_level<=0:raise ValueError('enterprise quality level required')
    added=int(additional_scale)
    if added<=0:raise ValueError('additional enterprise scale must be positive')
    row=enterprise_level_row(enterprise_type,current_level)
    quality=max(700,int(row.get('operating_efficiency_milli',1000)))
    # Calibrate setup cost against the existing level-5 setup recipe per unit of
    # minimum supported scale, normalized to current organizational quality.
    spec=_load('enterprises.json')['finite_types'][enterprise_type]
    l5=spec['levels']['5']; l5req=l5['upgrade_to_this_level']; base_scale=max(1,int(l5req.get('minimum_operating_scale',1)))
    import math
    def per_scale(v:int)->int:
        return max(1,math.ceil(int(v)*added*quality/(base_scale*1300)))
    return {
        'scale_basis':enterprise_scale_basis(enterprise_type),
        'additional_scale':added,
        'cash_overhead':per_scale(int(l5req.get('cash_overhead',1))),
        'management_labor_hours':per_scale(int(l5req.get('management_labor_hours',1))),
        'general_setup_labor_hours':per_scale(int(l5req.get('general_setup_labor_hours',1))),
        'minimum_calendar_days':max(3,math.ceil(int(l5req.get('minimum_calendar_days',20))*added/base_scale)),
    }


def enterprise_scale_expansion_quote(enterprise_type:str, *, current_level:int, additional_scale:int)->dict[str,Any]:
    r=enterprise_scale_expansion_requirements(enterprise_type,current_level=current_level,additional_scale=additional_scale)
    econ=_load('economy.json'); labor=econ['labor']
    mgmt=int(r['management_labor_hours'])*int(labor['skilled_labor_cash_per_hour'])
    general=int(r['general_setup_labor_hours'])*int(labor['general_labor_cash_per_hour'])
    return {'enterprise_type':enterprise_type,'current_level':current_level,'requirements':r,
            'management_labor_value_cash':mgmt,'general_labor_value_cash':general,
            'total_economic_value_cash':int(r['cash_overhead'])+mgmt+general}


def start_enterprise_scale_expansion(*, treasury_cash:int, enterprise_type:str, current_level:int,
                                     additional_scale:int)->dict[str,Any]:
    q=enterprise_scale_expansion_quote(enterprise_type,current_level=current_level,additional_scale=additional_scale)
    r=q['requirements']; cash=int(r['cash_overhead'])
    if int(treasury_cash)<cash:raise ValueError('insufficient treasury')
    return {'project_type':'enterprise_scale_expansion','enterprise_type':enterprise_type,
            'quality_level':int(current_level),'scale_basis':str(r['scale_basis']),
            'additional_scale':int(r['additional_scale']),'elapsed_calendar_days':0,
            'minimum_calendar_days':int(r['minimum_calendar_days']),
            'management_labor_hours_remaining':int(r['management_labor_hours']),
            'general_setup_labor_hours_remaining':int(r['general_setup_labor_hours']),
            'treasury_cash_after_start':int(treasury_cash)-cash,'completed':False,'quote':q}


def advance_enterprise_scale_expansion(project:Mapping[str,Any], *, elapsed_calendar_days:int,
                                        management_labor_hours:int, general_setup_labor_hours:int)->dict[str,Any]:
    if project.get('project_type')!='enterprise_scale_expansion':raise ValueError('enterprise scale expansion project required')
    if min(elapsed_calendar_days,management_labor_hours,general_setup_labor_hours)<0:raise ValueError('project progress cannot be negative')
    out=json.loads(json.dumps(project))
    if out.get('completed'):return out
    out['elapsed_calendar_days']=int(out.get('elapsed_calendar_days',0))+int(elapsed_calendar_days)
    out['management_labor_hours_remaining']=max(0,int(out.get('management_labor_hours_remaining',0))-int(management_labor_hours))
    out['general_setup_labor_hours_remaining']=max(0,int(out.get('general_setup_labor_hours_remaining',0))-int(general_setup_labor_hours))
    out['completed']=bool(out['elapsed_calendar_days']>=int(out['minimum_calendar_days']) and out['management_labor_hours_remaining']==0 and out['general_setup_labor_hours_remaining']==0)
    return out
