"""Deterministic Jianghu Qi capacity, recovery and cultivation helpers.

Qi pills do not exist. Maximum Qi is the persistent ``qi`` capability. Current
Qi is a conserved transient resource bounded by that capability and recovers
only through elapsed physiology/rest under Qi Control.
"""
from __future__ import annotations
import math
from typing import Any


def control_efficiency_milli(qi_control:int)->int:
    c=max(0,int(qi_control))
    return c*1000//(c+100) if c else 0


def effective_cultivation_milli(qi:int,qi_control:int)->int:
    q=max(0,int(qi)); c=max(0,int(qi_control))
    if q+c==0: return 0
    return (2*q*c*1000)//(q+c)


def current_qi_capacity_milli(qi:int)->int:
    return max(0,int(qi))*1000


def qi_recovery_milli(*,qi:int,qi_control:int,current_qi_milli:int,elapsed_minutes:int,rest_state:str,health_milli:int=1000,fatigue_milli:int=0)->dict[str,Any]:
    if elapsed_minutes<0: raise ValueError('elapsed_minutes invalid')
    if rest_state not in {'combat','strenuous','travel','awake_rest','sleep'}: raise ValueError('rest_state invalid')
    cap=current_qi_capacity_milli(qi); current=max(0,min(cap,int(current_qi_milli)))
    if rest_state in {'combat','strenuous'} or cap<=0 or elapsed_minutes==0:
        return {'recovered_milli':0,'current_qi_milli_after':current,'capacity_milli':cap}
    base_per_hour_milli={'travel':15,'awake_rest':50,'sleep':100}[rest_state]
    efficiency=control_efficiency_milli(qi_control)
    control_factor=500+efficiency//2
    health=max(0,min(1200,int(health_milli)))
    fatigue_factor=max(250,1000-max(0,int(fatigue_milli))//3)
    recovered=cap*base_per_hour_milli*elapsed_minutes*control_factor*health*fatigue_factor
    recovered//=1000*60*1000*1000*1000
    recovered=max(0,min(cap-current,recovered))
    return {'recovered_milli':recovered,'current_qi_milli_after':current+recovered,'capacity_milli':cap,'control_efficiency_milli':efficiency}


def safe_flow_milli_per_second(qi:int,qi_control:int)->int:
    q=max(0,int(qi)); eff=control_efficiency_milli(qi_control)
    return math.isqrt(q*1000)*(1000+eff)//1000


def redistribution_latency_ms(qi_control:int)->int:
    return max(50,60000//(100+max(0,int(qi_control))))

__all__=['control_efficiency_milli','effective_cultivation_milli','current_qi_capacity_milli','qi_recovery_milli','safe_flow_milli_per_second','redistribution_latency_ms']
