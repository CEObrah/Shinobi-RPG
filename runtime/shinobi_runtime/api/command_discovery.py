"""Compact Jianghu semantic-command discovery."""
from __future__ import annotations
from typing import Any, Mapping

def command_domain(command_type:str)->str:
    if command_type=='advance_time': return 'time'
    if command_type.startswith('jianghu_training'): return 'training'
    if command_type.startswith('jianghu_service') or command_type.startswith('jianghu_local_travel') or command_type.startswith('jianghu_market_trade'): return 'local_world'
    if command_type.startswith('jianghu_contract'): return 'contracts'
    if command_type.startswith('jianghu_tournament'): return 'tournaments'
    if command_type.startswith('jianghu_calendar'): return 'calendar_events'
    if command_type.startswith('jianghu_deployment'): return 'field_command'
    if command_type.startswith('jianghu_infrastructure'): return 'infrastructure'
    if command_type.startswith('jianghu_recruitment'): return 'recruitment'
    return 'other'

def compact_commands(surface:Mapping[str,Any])->dict[str,Any]:
    supported=sorted({str(x) for x in surface.get('supported_command_types',[]) if isinstance(x,str)})
    grouped={}
    for name in supported: grouped.setdefault(command_domain(name),[]).append(name)
    return {
      'supported_command_types':supported,
      'intent_domains':grouped,
      'availability_overrides':dict(surface.get('availability_overrides',{})) if isinstance(surface.get('availability_overrides'),Mapping) else {},
      'contract_lookup':'Call get_command_contract for the one selected command before preview.',
      'limits':surface.get('limits',{}),
    }

def compact_play_context(context:Mapping[str,Any])->dict[str,Any]:
    """Return the bounded wire context with command schemas demand-loaded."""
    out=dict(context)
    surface=out.get('commands',{})
    if isinstance(surface,Mapping):
        out['commands']=compact_commands(surface)
    return out
