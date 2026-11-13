#!/usr/bin/env python3
"""Deployed-weights contestant: own sensors and official report/drone HTTP only.

No engine import, organizer state, training labels, object IDs, enemy HP or scores.
Known starting slots and sector boundaries below are public-plan calibration
assumptions, not GPS measurements. M1/M6 use explicit fixture or configured text API adapters.
M2 uses supplied photo features; M4 automatic combat is not implemented.
"""
import argparse
import collections
import copy
from datetime import datetime
import hashlib
import heapq
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import time
from robot_client import TeamClient, ApiError, SafeMotion, ReportWorkflow, forward_clearance, sensors_healthy

from transport_facade import ParticipantClient
from llm_adapter import TextLLM, LatestTask
from facility_detector import FacilityDetector


def photo_decision(detection):
    photo=detection.get('photo')
    if not isinstance(photo,dict):
        return dict(accepted=False,reason='no_reference_photo_match')
    # Preserve the existing strong template decision.
    if photo.get('accepted'):
        return dict(accepted=True,type=photo['type'],confidence=float(photo['score']),reason='strong_reference_photo')
    confidence=float(detection.get('confidence',0.))
    if (confidence>=.85 and photo.get('type')==detection.get('type')
            and float(photo.get('score',0.))>=.55 and float(photo.get('margin',0.))>=.02):
        return dict(accepted=True,type=detection['type'],confidence=confidence,
                    reason='learned_and_reference_agree')
    return dict(accepted=False,reason='insufficient_joint_photo_evidence')


def moving_evidence(state,previous,captured_wall,now_wall):
    """Allow a slow pass only with fresh registered lidar and measured odometry.

    Missing timing/pose information fails closed. Three distinct, spatially
    consistent frames are still required by ObservationStore before counting.
    """
    try:
        age=float(now_wall)-float(captured_wall)
        if not 0<=age<=.5:return dict(accepted=False,reason='camera_age')
        registration=state.get('scan_registration')
        if not registration or not 0<=float(registration['age_s'])<=.2:
            return dict(accepted=False,reason='unregistered_or_stale_scan')
        if previous is None or not state.get('camera_available'):
            return dict(accepted=False,reason='missing_motion_history')
        dt=float(state['measured_at'])-float(previous['measured_at'])
        if not .02<=dt<=.75:return dict(accepted=False,reason='motion_time_gap')
        pose=state['odom'];old=previous['odom']
        speed=math.hypot(float(pose['x'])-float(old['x']),float(pose['y'])-float(old['y']))/dt
        dyaw=float(pose['yaw'])-float(old['yaw'])
        yaw_rate=abs(math.atan2(math.sin(dyaw),math.cos(dyaw)))/dt
        if not all(math.isfinite(v) for v in (speed,yaw_rate)):
            return dict(accepted=False,reason='nonfinite_motion')
        if speed>.25 or yaw_rate>.35:return dict(accepted=False,reason='motion_too_fast',speed=speed,yaw_rate=yaw_rate)
        return dict(accepted=True,reason='fresh_registered_slow_motion',speed=speed,yaw_rate=yaw_rate,camera_age=age,scan_age=float(registration['age_s']))
    except (KeyError,TypeError,ValueError,OverflowError):
        return dict(accepted=False,reason='incomplete_sensor_evidence')


CLASSES=['military_truck','mortar','tank','missile','ammo_box','drum']
LIMITATIONS=['Public-plan spawn and sector calibration is approximate; no GPS.',
    'M1/M6 LLM mode is explicit practice_fixture or provided_api; organizer endpoint/model remains unverified.',
    'M2 matches provided facility photos; receiver/emitter geometry is local calibration, not physical validation; no M4 robot combat.',
    'M6 ranks observed visual motion, not unobservable HP/ammo/M3 score.',
    'Three-robot survey priorities use own slot only; no teammate coordination API or optimality guarantee.',
    'Collision margins, retreat thresholds and timing require local simulation validation; not Jetson/ROS validation.',
    'Six supplied example-photo textures do not prove real-competition generalization.']


def atomic_json(path,data):
    path=Path(path);fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def angle(value):return math.atan2(math.sin(value),math.cos(value))


def pose_from_odom(state):
    slot=state['slot'];start_y=4.15-(int(slot[-1])-1)*1.65
    start_x=.28 if slot.startswith('B') else 5.84;start_yaw=0. if slot.startswith('B') else math.pi
    odom=state['odom'];x=float(odom['x']);y=float(odom['y'])
    return (start_x+x*math.cos(start_yaw)-y*math.sin(start_yaw),
            start_y+x*math.sin(start_yaw)+y*math.cos(start_yaw),angle(start_yaw+float(odom['yaw'])))


def sector_for(x,y):
    if 2.76<=x<=3.36 and .1<=y<=4.9:return 'CM'
    camp='B' if .65<=x<=2.66 else 'R' if 3.46<=x<=5.47 else None
    if camp:
        for i,center in enumerate((4.15,2.5,.85),1):
            if abs(y-center)<=.75:return f'{camp}-S{i}'
    return None


def public_base_approach(name,inside=False):
    """Public-plan calibration only; staging is outside either entry strip."""
    if not isinstance(name,str) or not re.fullmatch(r'(BLUE|RED)_(COMMAND_POST|MAINTENANCE|SUPPLY_DEPOT)',name):
        raise ValueError('Unsupported official base identifier')
    camp,kind=name.split('_',1);row=['COMMAND_POST','MAINTENANCE','SUPPLY_DEPOT'].index(kind)
    x=(.36 if camp=='BLUE' else 5.76) if inside else (.62 if camp=='BLUE' else 5.50)
    return x,round(4.15-row*1.65,2)


def parse_local_order(text,camp=None):
    """Constrained language-to-schema adapter, never an arbitrary LLM executor.

    Accept explicit canonical identifiers or unambiguous Korean facility names.
    Preserve sector mention order. Negation, conditional and relative orders need
    an official semantic interpreter; fail closed instead of guessing their scope.
    """
    if not isinstance(text,str) or not text.strip() or len(text)>4096:
        raise ValueError('Empty or oversized tactical command')
    normalized=text.upper()
    # LOCAL MOCK: the published mock command includes both a relative label and
    # an explicit canonical identifier. Accept only that narrow agreement, not
    # arbitrary relative language or a guess at the organizer's LLM behavior.
    relative=re.compile(r'상대\s*진영\s*(통신시설|감시초소|레이더시설)\s*\(((?:BLUE|RED)_(?:COMMS|OUTPOST|RADAR))\)')
    def explicit_enemy(match):
        kinds={'통신시설':'COMMS','감시초소':'OUTPOST','레이더시설':'RADAR'}
        if camp not in ('BLUE','RED'):
            raise ValueError('Own camp required to validate explicit relative target; motion held')
        enemy='RED' if camp=='BLUE' else 'BLUE'
        if match[2]!=enemy+'_'+kinds[match[1]]:
            raise ValueError('Relative target conflicts with explicit identifier; motion held')
        return match[2]
    normalized=relative.sub(explicit_enemy,normalized)
    if re.search(r'금지|말고|제외|아닌|않|하지\s*마|대신|취소|정지|멈|중지|만약|경우|아군|적군|우리|상대|\b(?:NOT|EXCEPT|CANCEL|STOP|IF|UNLESS|FRIENDLY|ENEMY)\b',normalized):
        raise ValueError('Order requires semantic interpretation; motion held')
    camps={'블루':'BLUE','청군':'BLUE','파랑':'BLUE','레드':'RED','홍군':'RED','빨강':'RED'}
    for label,value in camps.items():normalized=normalized.replace(label,value)
    facilities={'통신소':'COMMS','통신시설':'COMMS','전초기지':'OUTPOST','전초':'OUTPOST',
                '레이더':'RADAR','지휘소':'COMMAND_POST','정비소':'MAINTENANCE','보급소':'SUPPLY_DEPOT'}
    for label,value in facilities.items():
        normalized=re.sub(r'(BLUE|RED)\s*(?:측\s*)?'+label,lambda m:m[1]+'_'+value,normalized)
    normalized=re.sub(r'(BLUE|RED)\s*(?:측\s*)?(?:구역|섹터)\s*([123])',
                      lambda m:m[1][0]+'-S'+m[2],normalized)
    normalized=normalized.replace('중앙 구역','CM').replace('중앙구역','CM')
    targets=list(dict.fromkeys(re.findall(r'(?<![A-Z0-9_])(?:BLUE|RED)_(?:COMMS|OUTPOST|RADAR)(?![A-Z0-9_])',normalized)))
    bases=list(dict.fromkeys(re.findall(r'(?<![A-Z0-9_])(?:BLUE|RED)_(?:COMMAND_POST|MAINTENANCE|SUPPLY_DEPOT)(?![A-Z0-9_])',normalized)))
    sectors=list(dict.fromkeys(re.findall(r'(?<![A-Z0-9_-])(?:[BR]-S[123]|CM)(?![A-Z0-9_-])',normalized)))
    if len(targets)!=1 or len(bases)!=1 or not sectors:
        raise ValueError('Need one explicit target, one base and explicit search sectors; motion held')
    return {'target':targets[0],'search_sectors':sectors,'base':bases[0]}


def detection_position(pred,state,width=640):
    """Estimate face position by own yaw/pan plus measured lidar at image bearing."""
    x,y,yaw=pose_from_odom(state);center=(pred['xyxy'][0]+pred['xyxy'][2])/2
    focal=width/2/math.tan(math.radians(80))
    bearing=-math.radians(float(state.get('pan',0))*.5)+math.atan((width/2-center)/focal)
    registration=state.get('scan_registration')
    if 'scan_registration' in state and registration is None:return None
    if registration:
        world_bearing=yaw+bearing
        x,y,yaw=pose_from_odom({**state,'odom':registration['odom']})
        bearing=angle(world_bearing-yaw)
    scan=state.get('scan',{});ranges=scan.get('ranges',[])
    if not ranges:return None
    start=scan['angle_min'];step=scan['angle_increment']
    i=min(range(len(ranges)),key=lambda k:abs(angle(start+k*step-bearing)))
    if not isinstance(ranges[i],(int,float)):return None
    distance=float(ranges[i])
    if not math.isfinite(distance) or not .12<distance<min(3.,float(scan.get('range_max',8))):return None
    # Local physical-simulator calibration, physics_worker.scan origin x=.065 m.
    # Real hardware needs measured camera/LiDAR extrinsics, not this constant.
    lidar_x=x+.065*math.cos(yaw);lidar_y=y+.065*math.sin(yaw)
    return lidar_x+distance*math.cos(yaw+bearing),lidar_y+distance*math.sin(yaw+bearing)


_facility_detector=None
def beacon_candidates(jpeg):
    global _facility_detector
    if _facility_detector is None:_facility_detector=FacilityDetector()
    return _facility_detector.detect(jpeg)

def select_beacon(candidates,state,order):
    if not order:return None
    name=order.get('target','');camp,kind=name.split('_',1)
    for c in candidates:
        if c['kind']!=kind:continue
        location=detection_position(c,state,c['width'])
        if not location:continue
        # Own odometry confirms camp side only; class never implies a fixed row.
        if (camp=='RED' and location[0]<3.36) or (camp=='BLUE' and location[0]>2.76):continue
        focal=c['width']/2/math.tan(math.radians(80))
        error=math.atan((c['width']/2-c['cx'])/focal)
        distance=math.dist(location,pose_from_odom(state)[:2])
        # .34 receiver / .256 emitter height, .07 forward offset: local, uncalibrated.
        tilt=max(-100.,min(100.,math.degrees(math.atan2(.34-.256,max(.12,distance-.07)))/.15))
        return dict(c,error=error,tilt=tilt,world_bearing=pose_from_odom(state)[2]-math.radians(float(state.get('pan',0))*.5)+error,position=location,sector=sector_for(*location))
    return None


def source_frame_key(headers, image_hash):
    """Do not turn repeated inference on one sensor frame into repeated evidence."""
    return (str(headers.get('X-Camera', 'own-camera')),
            str(headers.get('X-Frame-Seq') or headers.get('X-Captured-At') or image_hash))


def m3_report_snapshot(store, camp, accepted_order):
    """Fixed scoring areas; M1 search order is not the M3 scoring definition."""
    if not accepted_order or camp not in ('BLUE', 'RED'):
        return []
    prefix = 'R' if camp == 'BLUE' else 'B'
    allowed = {f'{prefix}-S1', f'{prefix}-S2', f'{prefix}-S3', 'CM'}
    # Unobserved is not asserted to be empty. Keep all currently known eligible areas.
    return [sector for sector in store.report() if sector['sector'] in allowed]


import base64
import cv2
import numpy as np
PUBLIC_REFERENCE_PHOTOS={'missile': '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', 'ammo_box': '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', 'drum': 'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAKsklEQVRIDVXBCXBc5WEA4P//333uO3a1q1uyZVnCMTYY2SQQfBBsXEOwQ+Jppm2adhpSmrEhBHI04YiBDBlmcMJMaTOZaYdMMyQ4DkedUNIAFhhb2NiRL0mWkCzJsqxd7fXe7rvf//8lmelM833w396tAgopRZQSCBEAgFICIYKABf8fTAgFgFLwRxRAAiFOU5BnGchVp2aiFl0k1ekP3ntFjLx1g117tm9943dvjZwZhS8O1ylIKcUAIPB/CMEAIPBnMAAIAAAhBAgDCgjlCUrnDh+8774tvx7hdCU9+eqLbvmMIuRu3bL5zs9se+2N4bPnx+DP3l1iIQMAIJBSiliIUkoAoQSCP0EAEAgApQRRCACkCIOPESqRMOQzk4efvvOODc/+6JCO45WDPV4aVyvlrbd84vzYVGnZQZTAl05UAIkBAARwmACNI4uzi2Z3ByT0YxhCAACklIEQEAoI1Thudup4h8VDUTpz4nxl9kyz6cgiVg07akSBU078YM+9n//9yOnxs2eqy4vw8PElCADkGIoxhCzHUa9elVQbQQgACCIfMhAnmGH5JEk4BC/+4cPL7/8Kule6VnZ+MNscMA0o8otXZopLs0NDn37//d/qXO7LX/2H0+dnTo0MLy9Nw9dOLL71zttc6AiEQpYzLBVFEZYKum3Ztq0bCsugFAP6MeCyLDM9dv7k739rworZ3nfj7gd/99NnSNr0GrWrs5cG+m88P/nB5//yER4uL1xrHDv6P0uLk3Dfd38CEeLSJk+hmQmXI1ZLnEDsEiAUU78gBBV3YWbiJMMKosgyjIwp+PDSlWKlTgKvgTk29fpWre3q7PTdUme+89ylif2PP8vWJt49M3N8+K3ytSvw0Sd+yEEmTCOWsJ4zKRfWtiJ/IUQGrbfw/pnJ+ddf/c1D939VLax0w9TWxSjBDPUJpwZeOaTS22++vnaw/8K5cVWzzHx2fOwiQOipbz788tGTp98/LvIUPv/0E+XyVbulr14plcqX+3o2WlnpwsVzqsB8ePxopREBQjJKhpUlxLKGpncUbAz5DdcPlktzim4ujI8yomRlc68NTxfa26Zmx9J6/cfPHfz5y4dX93VV3Age+MoXHnjon+q+b2mm23SDyI1CwrJaGIaPHTjgJTiKIgIQSGgAUpoGGVURGCab1Vptq7ujc+imm06dGJYk/cUjo5/atP72zbf84tBhVjCTwN16283DJ0fht7+05+FvfT0MQkTiUnXpo9nZQkvbilU3fv2hb2BCiuUqBSAkQEKcC9M4buqyoKhiTlcKtmEZ5k3rbzj9wXu2mQsCyerqOnVspLdv9ZFjp+KGc8/d26cmPoInXvnXyYmLhBCn5t111xdGz15YvFYbn5myLfXU2dFqvQwBBwUFYwwZhoShKvK6Iei6JPBoRW9vW65lfn5SVTLnpp0w4rwI562sE/qygDKiAYEH9/3151oUOdtm3rP73lJ1wfODd4aPrhsckjj58ae/H1OMOIVSRAHied7367Iq6BIva6qhKa05M6NKftDo7131+vFS7JE49TsKmfv+Zu+P/+MQSTnEYHjwG/dnM1p3X7uiKl7QnL5cnLgwuX3bUEY3HnjiKZYXUsqmmADA8jzfbNZEmW2xdF4QJJHv6SgUskalXlYYqat/U7FYnp270l6wNg32VBKheM0Zn7gAnVP/2WigqemzmqYVy7VcS4FgLBAqqMbfP/QdJWMEscAQHAIEAYOxB0VoZNSMKuiKUrCz7YX8NXcxy3F3bd750fRH7Z09gBWaDd8H4MypS3t33wydPxy5fHlmdPSkZWeXa9WVvX39vT0cr6SU+ezf7mMVijHV+YyTNFhWTsMYcbRgKKKEOInv6exQebHu+TomOcPSBNEN48VSaceO7XW3EYUJSetw7vjLSYoP//JlhmdEUTBURWBYnpXXrL1h//d/lAAtjmICvIgCxFO/Xsnwwm3r+xfqVUHN3n7rLdu2bZ2aOLc0NZ3RuLyVSzBOKeY4nlCIECCAwpGfPfLm8DlL1xVdWSou3nj9uiTGLaaVzdn3fe/ZJAlCN2IExcxYzaSREqJIap+piKZBANuSy165Or+ip80S+IH+Adu2MMYcx8VxXK47hUIhCiO4+JtnHKIwAAAWYIKTKGZYUeH4MKT7n3ouiENdyzhOiBhEMMHURwiJiqQbmayuDV23plpd4iXe4LmNQ7eKouQ4rqqo1Vq10Qx9vwkZDOf/+7kgYOeXrq6/4ROBH6VpyrI8T6Cs6Xv3/3PD98MoVXQT4xQBmmBKAVBUxjAMXRHbs1aXkfFIqDD86r7VY2Nja9askWUZQugFydLS4vT8Ipz+rx8GTRyQBFDKMKKqG+VKqSObhxz/5W8eCAD1At+QdEriCLAJCUVZFgmwbZuX2aypb+ruXXLKmJJd23YQkvI8RwhxHOeVI28LvH7X7r+Apw49XymVtuy6c25iqjg/f7VWumf7HaFbq/vRg489V4uiKE14XoQUxARGSaiogiYJoiCqlpbX1A0rVy67lTROdu/cVV4ulR3vatHZuHHdky+82p1B33rgK/CN5x9Ze90gFhhNkCrXKj4kMgIIR9l8xxfv/7aZL0zOzgiijACMMExwJIiMJgq5bI7yoNM2e1vz16rFNs269eZPjo9djBjl5Hsf7tp203IAfn3oV488vA9OHjnIc0K1Vk3DSBA1LmNmDQPREGDwdw8/FmEcJzTGiGfZAMMoaBqGKvHIMC1eEkyFXd3TtlSvWkjYtOFmjDElaTZfIEFQdoOZ6ekN69fB0y8dYESBQUyrYQdR0iBpo+q2t7T5Md3/vWfrXp2XEYMpZNgYQz8MW1osimNd02WFbckqvYXOUmX5uo7egYEBWdH8piNLoFaPGJaLokjgWXj1nZ9adr5Zd+M4JoSEYZhEAauIEPD/+N0fhFgCBDISHyYRjVNblxt+XZVFXVNbzEx394rP7dk7NXN+YWwUEcjzwsjI8Z0779R1vVKpiKJYKOThvzywa8untmq65jabYRjyH2M4wiPbNr/04KPFZYeBjKRZimIz1BMEwTS1fDaLGIZlYEZi3UZNkATERHdv3un7Qb1e1TRVFEXXdZMkYRgGzr35QhyRUrF0dPhoLpfr7+/v7e32U6CKwtcefabiehzHpWnCcRzDMI7jUgQzomgYRr7V1mQ2Z9iO76osvGfH3aZppml87Nh7Q0NDCKHx8XESRLA8/O8sI9VcByFULi8XCq1TU5cUM9fX0/1X+75Tqjm6rgeBLwgCACCIY0lTMzy0sxZFVObhJzduOnfugq2IKKa5XG5hYX5oaKijo8PzPIyxpWfg/NGfx4QILLk0dnHNwGoA0iCIxi9e0qzM1x5/IaKoLWsSkjIMk1ASRQkr8JbK85KoSXyLpa7q6JwqLrYo0t5d9xZa20ZGRq5fu/bs2bO3bbntly/9YvXAINxzxw4CEErrIIoxAJ7v2ZY9MNjf21l48uBPOB5ausIiACGMcEopFHkhm5Egzwoc1GXui7t3vzH8TkGRP3v7HQuLRbfZiBIyNze3cfP2Yqm4MHsZ3jI01NreWq97kVdHHOs0XchAd7khQurhWECpIrKqLEEKKKVeFMqa3qlISJdSHHbn8505e6G61G8WVq1s61nR33R9u9BRc6vVuiswwuLSwv8C6l5xLfxkw/8AAAAASUVORK5CYII=', 'military_truck': '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', 'mortar': 'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAJvElEQVRIDU3BC3iV9X0H8O/39/+/7zkn90AIBDDIHRcEWgEvqyC0sscrY8bq4lpApVq1aq2bDsuk1qqtTKxaJ66dqHMoSueFIQz7uNFJ1Qc0Cg0lIeGWC7mfc3KSk3Pe9/3/FtKnz9PPhxteb8IoAZUQUox4YqEkTSQOdAwt4UQEUECVoTVWCQJQiDEO6kNgRQE4tdY6RAbqBHzkrWNUQ0BJ/IkRIeBrLPQGXVRiJG+oAEgCUORhhABJACRFEROrqqSIECMYWYmHVD6+47RSAagqSQAkfaFzUN/s3fbCJQvPTUz/hudAGhEhIwA0gj9jQWMMSQAknXPGwBijqnx8x2kIVBWAiKg6EViJGBW+/s+3Drd9dPf37xmYersiRY0DMMaQFICkc05EAYiIc85aq6rGGI6QCICI5ZP/eRqjRIQkRoUmTETeUP+BNzbePO/8RV+/6+neMEEVkhD1IUqQBASISKpTYw1Jp84aa0TICHRQy6febQcUgDGWhKqSDF2Y0ChH9/PbvlpZPmH1nQ/mZlxhwwCAQn0KREiIwQh1SopnjdMRMMZYMZQo0tDQ59M72wAliVEkAQjOUmP//cGlqd6B733/TjPvTg0zAEgCkBEGgAMgIqpqKSMARxKAtVZVAfDZXWciF4oIRYR0qgAMzgopYevHv3zk9q8tuWhZ3c/SXjEAAiICQAwAHeF5HuAYUUSICAAJGkNSRPj0nnY6B0DEwqlnAJAKkmpYJLkf1S0oHld2611PxmYuIakaiTFQBZTiSAKgs2KUxCgRUVIANRC+uLcD0CiKrLWAiAZK9cSoasRQ6W2+87LBTN9tt95xztLvOedIFRFVNcaoKv7I0FgFhEqCVjhCEVAsX/hNpxIjqPRIwwg8CyMch3zXW/9f2598eOYFs26897UsRUCMEjEiBnAYJWJVQyCMWZAEYFTVWL704Rl1+COlepZRBA+ihIUGVhI2eLh2Qdm4ohvu2Txm5iV0aqw1IKBGROkwyhgDOHWB73sucgqloQ0st/5PhyqNoapylKp6FAA2CkM//NVPHjr4xUcVmlx3/31B4rzp85cIhQqepYYRSYwSkZyKjTJl8bjP3Jf7f/P5B9v5yr4uVYiAo3RUzNgoiqxz7/x6a7JhZ09QmGr5fMnimi/7ix547N9C57yYr44ABCFJ/EkEfPz6plPHGqaPLS4Mh1RifG1fN9T5xoAEICJQJQkiryhFetP6az/9vKU4gVlV1fc99a+5gvPjJqMwAKmAc3krVE1IwguzuXjxb5+5ww32NR85OpxrmzF3Gbd91E11niEAkgBUlWTkokcevL/9yL55M6pmTK9qPHo8mUp/8+bvnL/8NkheQUfjaGKG8SCj1jcxn/3tfmnl7hc3xjXzybGWh5/Y0f77vXzzky6FMxoBMMZQoaokgyDo6zrx5INrh061LV256sTR+sFMfvnXF1277umkqKi1GhZYemSk2TAM39/8g7+9esmvtu8eUzG2xEvUHz60YuXy8ngF3/n0jEYOVnCWUxKEVcmEEpdo00O3r1q5atbFK7579eLKUlMze+q6h7ekUSJE5MzhXa/qyd9l8pmB1ECQy0dGYeNTqmfG4oUHDtXf/u1vvfjCK/zvg92qysgBQlWFUjhobTzblUv1vvz842s3bnHK2HD3fX+3orKy/Oq198y9pFZcmBd/fK5t62P3+kV+wosb0XQqFy8p9uNFRfGC+kMNF180t73tDHcfaKOjg0cyYOhcZIxV5wol95MN62eV56IxNdevvlX82EPXL06HuZtqaxfXPaRhzEgQ89zbj37HWba2tnd3dy1estTBlsS8ZG9XQUGx0TAIAn5woJ1ETpSkcVRHUpSqiL7115dfMndy3R0bKiZMgXgn6/c99di65QsvXf2PP81gnK9JJMa//qNbunt7+gZSra2tV11+jY0lDDJWNJ8PGIVR5PhhfRdGuSgKqJ6DGMAxRH79d2843dL0H3s/E6WKJFx0y8r5UyZW/82a22qWXafZLD1/2+b17e3tw/lc+8nmpUv/yvcSLuq1oT187HBDw+GEF+cPNzyTz2bLy0oWLlw4pmJsUkPfS2S6ulLDfZnjhw82Nd+07u8djFEXl9jaa+dPqKj8yqKL6u79p5ONjV0tX3568EA6ne4fSJ1oarzqymvV+VHQ7UVe4+mmNTev/dmPH+WeX28PgiDKDrR35dt6kjkJY0aCwP/4s99OKeMFi5d3plwQRqWFUWVx4vEnNpYVli268IJ5Cy87dvSzmPVTmQC+bfzD0VPNTSuuWpnPu9xQR6E/prXj1P7PDsyZOouvPnH/0aNNcdXr6mozQ+lkbzYzPJyV/C+ee7M/1bvm+trudNI4ay1jhV40nNu5e/e4KVO/cellxzoGwyDM5gbhec1Njcmezq9c9LUwYn74TFwLepMtCy+89GRjN7c+UHtezdyB4YEgCIXe5MlTc8PDzR2Dx3vS//Ls5uc3/bi6onjXR1/29+X7w1xBovC9d983vtTdsLK+viEWN07zuWw0fsJY62xyKBjIDBXFkU4Nzqwa15XunzN7Hvc+c/dwLp9K93R2di5Y8NWS0nEMh1Y/+ua3r7v8jW2vPffIPxz+/RctPdmysvG/q/9iwZwp+/YfauvtXbFs6fgClHrusiuvfO2VnYnC2A/uvWPHW2+oZ7vbT1aONXS2fPy5A/1ZdnzwYkdX5/HjLRMnTTrU0DB/2jklYypq1/9yRmVxzZxp1yyaVVxa3pNOVlVNdp7N9vaczrgHHtuyYtnF88+bfuHU8kSiOBOGhYk4EPX19Xb29hQkykq9kobTLaGJZwdzbH53cz6KrIknCuJkDNnuTKzypbc//PiDt3/+1CZvaCCKhvo7u8XkmCgJUmkpq1z7w83V48euvOaKSZIaUzbpDyeOFxUl5syaxtAlEvG+gUyybzAWi53qaTun6lzmG/d8+r//V11dNZTNFhcU5IayO/Y3FSXiW156+b1Xnxvo6SiIFTQ1t5SXFZSWlNh4YUnFhAWrbikyhdOmTJg4saq58dBda+qKiooAVhfKiY7OmG/T/d2FZePgNAgCnnh/SxAEp1uPz549O8zlikpKL627e9vzz9TetGb/nrc8zQ2lB4eD4ERPKjmQe3br9vrGluHscMWY8fls3403fhMhqieNPXWq9cDBT1Zff93LO3beVbdq9tQJDYcO1dTUHDlyhMfe+0UymRTjfN8v8O2AK9z06p61K/9yw8aNb299oauvO8oPi9Mr7vlpcbzYGBOGuWmTJ8z9i5ljK4rbTnVY37fW9nT1ptJ9Xjze3pkaV17a3dG+8e41kydWvbNr1/8DvWbfRgWG+8YAAAAASUVORK5CYII=', 'tank': 'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAKVUlEQVRIDU3BCXBU5R0A8P//+96x773dTXazyWZDCElIOBUIEA4HROVqFaYC1qmOVceqjHbwqE6dltYO1XFaVEadKgzOWK1XrcjhAWgAOeQKEq4cJBuSkHvvI3u8t++97ysw06m/H77waahANJsxSglw+CmbMQAEQLiGc0op/B9yMJEAISgxCgBICFzHCWWIHBEBwEAFN34e0YkGN3DO4QbOOSICAAebcxsR4QZEhJ9AREKIABxvgOsYIpIbAMAACTfuihZQg+uQX8cQCAACYZwxIJwi2raFSAEAERljlFLOGQJyAEIIZRYhyBE5RWBcQAKcE0IAQLIl/MvuYYs4AYBzjoj8OrgGkfJrkAJwBoxxQEAAIIgCMn4dCAJFRMosROQEGQJyEAhFzgkhACAzCf+4f0i23PA/ZsEQBbRBZIic80KhIEkSXIecMslGQgjwgsEEVWRICedcYACINnBCCAdADgSQU2SMI8/gn78bUSwVbiAEjFxOlGQLkCPhnBNABGTIgTEVgROklNaUayODw0lagmARQjgwACCAAICIlNm5XB6srGVaTo8LX2sKM1uBGxCpocdk6gQEk1BgnBK0bBsI4WB7RYEju4aIDp9UGMlwgwtAkBCbA0cggABIWTqOlm7qumExj6zhq01hZjkQEQAYopWLOzUv55wBtywbCGWMUSoA2ggW4QCASEQEBgAMCCWEg4k3AABn5kjwsgBMUjSbSE5ZxM0HcgyIDeDgDABy+bAqehDRRptwziklJmOEIKUmN0VCAYCAxVEQgABYSIAAAkckIIAdGh2yx1KUEUuw3B6fRCX86FgWLMO2rCQqBDGVGdEUHyGEM04oAWZrkswZpwIpgF2wLOCIBIAjAQRkMgXGkDEWGxnKRkdE2yxARlP84PaIqsstUvzHtzlQuG2ZIhDOWCYbdbl8zLYZ40iIQIBwJgAIwEzCbUHiHAgiMA6IQECzgYkUgA8EOyPBDk4s2VdEZb/HP6Hr0CeTZ83E/gvNbe1tw+VrgDFKhNzYqOIsAqLZNnDRkBgyq0BlapqWKKgCEMZ1W5QpIOfIbCZzHQgyIH2XzvQH251OYWU9q5k0ZdfR9pP7Pu1PAq5duuLpZx7vpDc7nF7dsDJjsZJAwFVI9fT0UamMMd1X5KWiMJaJQ3YwnRxG21sw88htkBVbLXPLvFCwBlL62PCAi5LxlWU0deLAvr25fHb5yrVRy4WHP9vpGu/oTciJQmkiESOhjt07/xWL9Ytu9dlntsXzUcnKReOJErdLNI1J1SVN3x6f1jhvLJe/GmydOHmqaaMsOIORTD4zWjde6zpy8PLg5erq6hc2PG6Z/Kvj3Xh+z7vUKbYNi2Hdr2eyYj5cPG58yw9Nh5p2+AJVa9f8ev++99auuEsuDnS0NFdUFMUSQwvmzoqP2R1nTj94/33JtN7V2dM6lk31nhgcipaortqJk5YvXbFt+7bVy5a0DqVwwz23PvrIY5nEmHvcxHTWOH3m7OIFs/+zc3dzy7nyQElD461f7Xx/6vjSZevWN5862t16QVGlZ3+5tDcSKS+vEX01Xx48deDA11RUypwldfNu97scmj4wbdqUdz74/NH71m3Z+iEe2vZKRc2ERDadzGT+uvlNRVEm11fHQsmyyoDNjEDpuHw6xmzj/S8O+33u6qqasjLvLxZM64zGddPete9E/2h0RnXV9rfeONjS3jMYpZm0BrG1a9e8/9nuRQ03tfUM4sDhj6ladLrl4ubtH4qSPDbaJ3uKFbnYpSERVZeE86bXSYLj4uVe5NmJNbXto9FzZ8+rmjOesWZWeDz+wL13LR8Ijaqy+0RXt1/1yTQ/Eg5lUhmeT0V1wPM73zpy8uyRE81xLouimAuHJY9MiSI75GQ25/eUeCXiUUUiopUzZsyoOTkQK+TIvq/3rbl7XWN5XhHl5p54fX2dR1ZDHGIjY8yyzrVdWrxkaVWp++sDZ7Fz77uHjp/55tD30Sxz+0pjfQNVdaWqu0zP6gbHH44fk0XJzOsOVaGGefddtyaJe+euPcXFxel0+pn7l06orNh75Nyi+bOXNMze23yiSKqonxL4/uiFL/Y2fbB9y7/3HMDQ6d3hcDg4GE2M5dImxgaDu747OBxKqU5apLopw0gkmTZNy7Yff+BeGa0Pd+wvKi1OJKOEOq18+m+bNmqCNaW26otPdqy+d110ZCgej08I1OiMGol0d2oIN/xq5eDwaCgSc/qrbCM3d1p1azhZpahhI6uhoPECs1ncKFRNqTWSadksuKn5yBNP9sVyo5GE6GQdrV0yirfMn5mKpROJcGdb0FXkqq+Z+NY/tz+//oljB37A5x9YeehUm8cpWi7/ZB8pgBLOmg3VpUzTUqMRaujjKitbe3pzTHAoipvn5jU2/nzVapaPoqB+8NnuS8Geu1ffc8uCGarMtr7z3rGjx71Fzlc2/QEkurfpoJf4cOvGx1ouds2bM7G1Vw+Icer2tl4NyZTppqRpzjJZz+f1vEUGC4XhgXhFWXEiMuZykN88+btC3ly8aOGbW1/v7++fML4SIG1arsZysz+Z+eZw599f3i6XFTd9/ja+tH5NMmvePLWy72r8ynBycikUVYzvHrjafiV6+8JZyeGBurqJfX0DwUwuOpSQHNLZU+dvXzH/+MmzsupSnXTNqhVWQQbgVNUtWw7wbD6Vf3PnIVF01Jb67lw4HV986M6UiTUBxUL3cCjmVsXg6BChGgN6W0Nt28U2RRHy2XwE5P6egdZLPePGVz310KozP17yVozb27R/xfxZx9oGHAJYPIscvA5nNJVuDUY2PbVet7LL7mjE8Jkv/75l+/BIysZcNpONJOMgShxpIlNw2nrOgHAic9Pk0gs94axuVXgcc2fNXjq79ujpC2WBIl0vnGrpzFF0q1JVsZNnIlkdhlJkKFnY8qffOhVh4dyZ2Hvko5aL/d8dOWzk8lSkkVQ2mcpbTDcRNcri8Uw4kQ64BZ1r/bF4XaXf71Ya6stLvZWHfzzT0tFdWhbgpj51cm0oNBpPZtya+tJzG55++dVqv+/he9ZMmz4Vj3/8WjRtXeq80nG5TXM6I4lkKJ4s8foGhwcr/Z7uvhCCMGWC9+KVgdGkPbnOP7PS39XeJjicl7oHdYom52DwWdMndfUGMwaW+7x2IjZzRv2SeXMWNzZUVVbg2889yBQFBPVKT69h6BaDM23tPm9J88nLP1u2aDA62N4RXDb/poPHe6bOrS0nJJ8aMoj3QteVAoAmKprXGx26+uLG329+/Y1UXne7XbOnTVi1YM70SVNEB3T3XMGHl83gkmYYOhREqmhenzMUy1tGpv1q2Fvk9JT6QBCFbCKRHlOc0tWuy3miprOmadsSWJoqapqWjI/JoqAXDMYtl0N69qF1k6oDnDrdbvfQ6DCefm9TR2fHHUvviCazO/bsvXnO1P6roeW33XbwcHMw2O4trZEUMZTOh0ZaRdsxGhmt8Lrraic6uBlLRqZPbygqcguCHIpEMqnQlKn1p079OKuh0WKcCtL58+dFSfovvVh000iZ8aIAAAAASUVORK5CYII='}

class PhotoVerifier:
    def __init__(self, photo_directory=None, score_threshold=.65, margin_threshold=.08, size=32):
        self.score_threshold=score_threshold
        self.margin_threshold=margin_threshold
        self.size=size
        self.bank=[]
        self.classes=[]
        corners=np.float32([[0,0],[size-1,0],[size-1,size-1],[0,size-1]])
        for name, encoded in sorted(PUBLIC_REFERENCE_PHOTOS.items()):
            source=cv2.imdecode(np.frombuffer(base64.b64decode(encoded),np.uint8),cv2.IMREAD_COLOR)
            if source is None:continue
            self.classes.append(name)
            source=cv2.resize(source,(size,size)).astype(np.float32)
            for side in (0,1):
                for shrink in (0,.05,.1,.15,.2,.25,.32,.4):
                    high=shrink*(size-1);low=(1-shrink)*(size-1)
                    quad=np.float32([[0,high if side==0 else 0],[size-1,high if side==1 else 0],
                        [size-1,low if side==1 else size-1],[0,low if side==0 else size-1]])
                    transform=cv2.getPerspectiveTransform(corners,quad)
                    warped=cv2.warpPerspective(source,transform,(size,size))
                    mask=cv2.warpPerspective(np.ones((size,size),np.float32),transform,(size,size))>.95
                    values=warped[mask];values=values-values.mean(axis=0)
                    values=values/(np.linalg.norm(values)+1e-8)
                    center=cv2.perspectiveTransform(np.float32([[[(size-1)/2,(size-1)/2]]]),transform)[0,0]
                    self.bank.append((name,mask,values,quad/(size-1),center/(size-1)))
        if len(self.classes)!=6:raise ValueError('Expected exactly six supplied class photo files')

    def classify(self, image, xyxy=None, color_order='BGR'):
        """Return type/score/margin/accepted; rejected results must not count as evidence.

        image: uint8 HxWx3 numpy array, BGR default (cv2.imdecode), or color_order='RGB'.
        xyxy: detector proposal box or None for an already cropped image.
        quad/cx/cy describe the best visual fit, not calibrated depth or truth.
        """
        image=np.asarray(image)
        if color_order=='RGB':image=image[:,:,::-1]
        height,width=image.shape[:2]
        if xyxy is None:x1,y1,x2,y2=0,0,width-1,height-1
        else:x1,y1,x2,y2=xyxy
        x1=max(0,int(x1));y1=max(0,int(y1));x2=min(width,int(x2)+1);y2=min(height,int(y2)+1)
        crop=image[y1:y2,x1:x2]
        if crop.size==0 or min(crop.shape[:2])<5:return None
        crop=cv2.resize(crop,(self.size,self.size)).astype(np.float32)
        scores={};geometries={}
        for kind,mask,template,quad,center in self.bank:
            values=crop[mask];values=values-values.mean(axis=0)
            values=values/(np.linalg.norm(values)+1e-8)
            score=float(np.sum(template*values))
            if score>scores.get(kind,-1):scores[kind]=score;geometries[kind]=(quad,center)
        ranking=sorted(scores.items(),key=lambda kv:-kv[1]);kind,score=ranking[0];margin=score-ranking[1][1]
        quad,center=geometries[kind];scale=np.array([x2-x1,y2-y1]);offset=np.array([x1,y1])
        quad=quad*scale+offset;center=center*scale+offset
        return {'type':kind,'score':score,'margin':margin,
            'accepted':score>=self.score_threshold and margin>=self.margin_threshold,
            'scores':scores,'xyxy':[x1,y1,x2,y2], 'quad':quad.tolist(),'cx':float(center[0]),'cy':float(center[1])}


class ObservationStore:
    """Class-compatible spatial tracks; count physical faces, never unique classes."""
    def __init__(self):self.tracks=[];self.next_id=1
    def add(self,kind,x,y,confidence,frame,observed_at=None):
        now=time.monotonic() if observed_at is None else float(observed_at)
        if not math.isfinite(now):return
        sector=sector_for(x,y)
        if kind not in CLASSES or not sector or confidence<.65:return
        near=[t for t in self.tracks if t['sector']==sector and t['type']==kind and math.hypot(t['x']-x,t['y']-y)<.25]
        track=min(near,key=lambda t:math.hypot(t['x']-x,t['y']-y)) if near else None
        if track is None:
            track={'track_id':self.next_id,'x':x,'y':y,'sector':sector,'type':kind,'hits':0,'frames':set(),'points':[],
                   'confidence':0.,'last_seen':now,'recent_samples':[],'confirmed':False};self.next_id+=1;self.tracks.append(track)
        if frame in track['frames']:return
        track['frames'].add(frame);track['hits']+=1;track['points'].append((x,y));track['last_seen']=now
        track['x'],track['y']=map(float,np.median(track['points'],axis=0));track['confidence']=max(track['confidence'],confidence)
        track['recent_samples']=[sample for sample in track['recent_samples'] if now-sample[0]<=1.5]
        track['recent_samples'].append((now,x,y))
        if len(track['recent_samples'])>=3:
            points=[(px,py) for _,px,py in track['recent_samples']];center=np.median(points,axis=0)
            if all(math.dist(point,center)<=.10 for point in points):track['confirmed']=True
    def confirmed(self):return [t for t in self.tracks if t.get('confirmed') and t['confidence']>=.65]
    def report(self):
        counts=collections.defaultdict(collections.Counter)
        for t in self.confirmed():counts[t['sector']][t['type']]+=1
        return [{'sector':sector,'objects':[{'type':kind,'count':count} for kind,count in sorted(items.items())]}
                for sector,items in sorted(counts.items())]
    def summary(self):return [{k:v for k,v in t.items() if k not in ('frames','points','recent_samples')} for t in self.tracks]


class DroneTracks:
    """Color-component tracks. Opponent callsigns are uncertain initial-row associations."""
    def __init__(self,camp):self.camp=camp;self.tracks={};self.frames=0;self.last_capture=None;self.identity_assumption=False
    def update(self,components,captured):
        if captured==self.last_capture:return
        self.last_capture=captured;self.frames+=1
        enemy='RED' if self.camp=='BLUE' else 'BLUE';detections=[d for d in components if d['camp']==enemy]
        prefix='R' if enemy=='RED' else 'B'
        if not self.tracks and len(detections)==3:
            self.identity_assumption=True
            for i,d in enumerate(sorted(detections,key=lambda d:d['cy']),1):
                self.tracks[f'{prefix}-{i}']={'cx':d['cx'],'cy':d['cy'],'observed_frames':1,'distance':0.,'observed_at':captured}
            return
        unmatched=set(self.tracks)
        for d in detections:
            if not unmatched:break
            name=min(unmatched,key=lambda n:math.hypot(self.tracks[n]['cx']-d['cx'],self.tracks[n]['cy']-d['cy']))
            track=self.tracks[name];distance=math.hypot(track['cx']-d['cx'],track['cy']-d['cy'])
            if distance>.20:continue
            unmatched.remove(name);track['distance']+=distance;track['cx']=d['cx'];track['cy']=d['cy'];track['observed_frames']+=1;track['observed_at']=captured
    def ranking(self):
        prefix='R' if self.camp=='BLUE' else 'B';names=[f'{prefix}-{i}' for i in (1,2,3)]
        ranking=sorted(names,key=lambda n:-(self.tracks.get(n,{}).get('distance',0)+.01*self.tracks.get(n,{}).get('observed_frames',0)))
        return ranking,{'method':'visible-motion/visibility estimate','insufficient_observations':len(self.tracks)<3 or self.frames<3,
                        'identity_assumption':'initial image row ordering; may be wrong after crossing/occlusion',
                        'frames':self.frames,'tracks':copy.deepcopy(self.tracks),'uses_enemy_hp_ammo_score':False}


def drone_components(jpeg):
    import cv2,numpy as np
    frame=cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise ValueError('Drone JPEG could not be decoded')
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV);height,width=frame.shape[:2];output=[]
    # Rendered blue paint includes cyan highlights. Color alone can also select
    # photo-texture strips, so retain compact groups with enough colored area.
    masks={'BLUE':cv2.inRange(hsv,np.array([80,115,65]),np.array([135,255,255])),
           'RED':cv2.bitwise_or(cv2.inRange(hsv,np.array([0,115,65]),np.array([12,255,255])),
                               cv2.inRange(hsv,np.array([165,115,65]),np.array([179,255,255])))}
    for camp,mask in masks.items():
        n,_,stats,centers=cv2.connectedComponentsWithStats(mask)
        parts=[]
        for i in range(1,n):
            x,y,w,h,area=stats[i]
            if max(3,width*height*.000006)<=area<=width*height*.025:
                parts.append((int(x),int(y),int(w),int(h),int(area),*map(float,centers[i])))
        # A robot's two side panels and antenna are disconnected color regions.
        # Join nearby same-camp rectangles by edge distance, not a forced top-3
        # count. Touching/occluded robots remain an explicit heuristic limitation.
        remaining=set(range(len(parts)));radius=min(width,height)*.04
        while remaining:
            first=min(remaining);remaining.remove(first);group=[first];pending=[first]
            while pending:
                x,y,w,h,*_=parts[pending.pop()]
                for index in sorted(remaining):
                    X,Y,W,H,*_=parts[index]
                    gap=math.hypot(max(x-X-W,X-x-w,0),max(y-Y-H,Y-y-h,0))
                    if gap<=radius:remaining.remove(index);group.append(index);pending.append(index)
            members=[parts[index] for index in group];area=sum(p[4] for p in members)
            x=min(p[0] for p in members);y=min(p[1] for p in members)
            w=max(p[0]+p[2] for p in members)-x;h=max(p[1]+p[3] for p in members)-y
            if (area<max(12,width*height*.00006) or not .35<w/max(1,h)<2.8
                    or max(w,h)>min(width,height)*.12 or area/(w*h)<.04):continue
            output.append({'camp':camp,'cx':sum(p[4]*p[5] for p in members)/area/width,
                           'cy':sum(p[4]*p[6] for p in members)/area/height,
                           'bbox':[x,y,w,h],'area':area,'parts':len(members),
                           'method':'grouped nearby HSV parts; uncalibrated visual heuristic, not a learned detector'})
    return output


class TemporalScan:
    """Repair sparse missing rays from recent raw own measurements, never free space.

    Bounds are explicit local calibration: .22 s age, .025 m translation,
    .10 rad yaw and at most 10% missing rays. Historical ranges are shortened
    for pose/range uncertainty and an assumed .4 m/s approaching obstacle.
    Large gaps, full dropout, stale history and fast motion remain unknown.
    Only raw source frames enter history; repaired values cannot extend expiry.
    """
    def __init__(self):self.history=[];self.key=None;self.poses=[]
    def update(self,state,now=None):
        now=time.monotonic() if now is None else now
        result=dict(state);scan=state.get('scan',{});ranges=scan.get('ranges',[])
        odom=state.get('odom',{});pose=tuple(float(odom.get(k,0)) for k in ('x','y','yaw'))
        clock=float(state.get('measured_at',0.))
        if self.poses and clock<self.poses[-1][0]:self.poses=[];self.history=[];self.key=None
        if self.poses and clock==self.poses[-1][0]:self.poses[-1]=(clock,pose)
        else:self.poses.append((clock,pose))
        self.poses=[v for v in self.poses if clock-v[0]<=2.]
        source_time=scan.get('received_at')
        if source_time is None and scan.get('clock_domain')==state.get('clock_domain'):source_time=scan.get('measured_at')
        registration=None
        if isinstance(source_time,(int,float)) and 0<=clock-source_time<=.25:
            before=[v for v in self.poses if v[0]<=source_time];after=[v for v in self.poses if v[0]>=source_time]
            if before and after and after[0][0]-before[-1][0]<=.25:
                a,pa=before[-1];b,pb=after[0];f=0 if b==a else (source_time-a)/(b-a)
                aligned=(pa[0]+f*(pb[0]-pa[0]),pa[1]+f*(pb[1]-pa[1]),angle(pa[2]+f*angle(pb[2]-pa[2])))
                older=[v for v in self.poses if .15<=source_time-v[0]<=.40]
                stationary=bool(older and math.dist(aligned[:2],older[0][1][:2])<.01 and abs(angle(aligned[2]-older[0][1][2]))<.02)
                registration=dict(odom=dict(zip(('x','y','yaw'),aligned)),source_time=source_time,age_s=round(clock-source_time,5),stationary=stationary,method='own odometry interpolated at same-domain scan receive time; capture-time lower-bound approximation')
        result['scan_registration']=registration
        self.history=[h for h in self.history if 0<=now-h[0]<=.22]
        key=(scan.get('clock_domain'),scan.get('frame_seq'),scan.get('measured_at'))
        step=scan.get('angle_increment',0.);start=scan.get('angle_min',0.);maximum=scan.get('range_max',8.)
        def valid(v):return not isinstance(v,bool) and isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=maximum
        missing=[i for i,v in enumerate(ranges) if not valid(v)]
        repaired=list(ranges);sources=[]
        if ranges and 0<abs(step)<=.15 and len(missing)<=len(ranges)*.10:
            for i in missing:
                candidates=[]
                for at,old_pose,old_scan in self.history:
                    translation=math.dist(pose[:2],old_pose[:2]);dyaw=angle(pose[2]-old_pose[2]);age=now-at
                    if translation>.025 or abs(dyaw)>.10 or old_scan.get('angle_increment')!=step:continue
                    bearing=angle(start+i*step+dyaw)
                    index=round((bearing-old_scan['angle_min'])/step)
                    old_ranges=old_scan['ranges']
                    if not 0<=index<len(old_ranges) or not valid(old_ranges[index]):continue
                    err=abs(angle(old_scan['angle_min']+index*step-bearing))
                    if err>abs(step)*.51:continue
                    distance=old_ranges[index]
                    lower=max(0.,distance-translation-distance*math.sin(err)-.02-.4*age)
                    candidates.append((lower,age,index))
                if candidates:
                    lower,age,index=min(candidates);repaired[i]=lower;sources.append(dict(index=i,age_s=round(age,4),source_index=index,lower_bound=round(lower,4)))
        result['navigation_scan']={**scan,'ranges':repaired}
        result['scan_repair']=dict(policy='raw-temporal-v1',missing=len(missing),repaired=len(sources),sources=sources,age_limit_s=.22,translation_limit_m=.025)
        if key!=self.key:
            self.history.append((now,pose,copy.deepcopy(scan)));self.key=key
        return result


def arc_clearance(state,center=0.,half_angle=math.radians(40)):
    """Require finite measurements covering the requested arc; unknown is unsafe."""
    scan=state.get('navigation_scan',state.get('scan',{}));ranges=scan.get('ranges',[])
    try:
        start=float(scan['angle_min']);step=float(scan['angle_increment'])
        maximum=float(scan['range_max'])
        if not all(math.isfinite(v) for v in (start,step,maximum)) or not 0<abs(step)<=.15 or maximum<=0:return None
        samples=[]
        for i,d in enumerate(ranges):
            offset=angle(start+i*step-center)
            if abs(offset)<=half_angle:
                if isinstance(d,bool) or not isinstance(d,(int,float)) or not math.isfinite(d) or not 0<=d<=maximum:return None
                samples.append((offset,float(d)))
        samples.sort()
        tolerance=abs(step)*1.6
        if not samples or samples[0][0]>-half_angle+tolerance or samples[-1][0]<half_angle-tolerance:return None
        if any(b[0]-a[0]>tolerance for a,b in zip(samples,samples[1:])):return None
        return min(d for _,d in samples)
    except (KeyError,TypeError,ValueError):return None


# Public local robot envelope: .25 m long, .23 m wide; lidar is .065 m
# forward of body center. These are simulator calibration, not venue validation.
BODY_HALF_LENGTH=.125
BODY_HALF_WIDTH=.115
BODY_MARGIN=.03
BODY_ROTATION_RADIUS=math.hypot(BODY_HALF_LENGTH,BODY_HALF_WIDTH)+BODY_MARGIN

def rotation_clearance(state):
    if arc_clearance(state,0.,math.pi) is None:return None
    scan=state.get('navigation_scan',state.get('scan',{}))
    return min(math.hypot(.065+d*math.cos(scan['angle_min']+i*scan['angle_increment']),d*math.sin(scan['angle_min']+i*scan['angle_increment'])) for i,d in enumerate(scan['ranges']))

def translation_clearance(state,direction=1):
    # Verify the whole intended cone is measured before selecting obstacle points
    # inside the swept body corridor. Side walls outside the body path are not
    # frontal obstacles. Range is measured from body nose/tail plus safety margin.
    if arc_clearance(state,0. if direction>0 else math.pi) is None:return None
    scan=state.get('navigation_scan',state.get('scan',{}));clearances=[]
    for i,d in enumerate(scan['ranges']):
        if not isinstance(d,(int,float)) or not math.isfinite(d):continue
        a=scan['angle_min']+i*scan['angle_increment'];x=.065+d*math.cos(a);y=d*math.sin(a)
        if direction*x>=0 and abs(y)<=BODY_HALF_WIDTH+BODY_MARGIN+.015:
            clearances.append(direction*x-BODY_HALF_LENGTH-BODY_MARGIN)
    return min(clearances) if clearances else None


class SurveyNavigator:
    """Bounded recovery, clearance-aware planning and slot-prioritized missions.

    Safety distances are local simulation assumptions, not measured footprint or
    braking guarantees. Unknown lidar coverage always stops the relevant motion.
    """
    def __init__(self,camp,slot):
        self.camp=camp;self.slot=slot;self.cell=.05;self.occupied={};self.risk={}
        self.static_cells=set();self.hit_votes={};self.free_votes={};self.map_source=None
        self.route=[];self.route_index=0;self.path=[];self.last_plan=-math.inf
        self.entry_changes=0;self.entry_tried=set();self.entry_adjust_at=-math.inf;self.objective_name=None
        self.objective=None;self.order=None;self.order_ready=False;self.hold=False
        self.staging_goal=None;self.staging_base=None;self.mission_stage='await_order'
        self.m2_done=False;self.m2_started=None;self.m2_goal=None;self.m2_target_position=None;self.m2_acquired_at=None;self.m2_updated_at=0.;self.m2_cooldown_until=0.
        self.prepare_base_first=(slot.endswith('-1'))
        self.approach_corridor=[];self.corridor_index=0
        self.goal_reason='public-plan sector survey';self.mode='track';self.until=0.
        self.attempts=0;self.anchor=None;self.anchor_at=0.;self.last_tick=None
        self.heading_waypoint=None;self.heading_best=None
        self.progress_goal=None;self.best_goal_distance=math.inf
        self.recovery_cause=None;self.no_route_since=None
        self.path_committed=False;self.path_replan_reason=None;self.path_plan_count=0
        self.reverse_start=None;self.turn_start=0.;self.turn_sign=1.;self.scan_start=0.;self.scan_angle=0.;self.scan_yaw=None
        self.last_hp=None;self.retreat=False;self.retreat_hold_started=None
        self.failed_goals=[];self.completed=[]
        self.home=(.40 if camp=='BLUE' else 5.72,4.15-(int(slot[-1])-1)*1.65)
        self.goal=None;self.reason='initializing'
        enemy='R' if camp=='BLUE' else 'B'
        self.set_route([f'{enemy}-S{i}' for i in (1,2,3)]+['CM'])
    def grid(self,x,y):return round(x/self.cell),round(y/self.cell)
    def set_route(self,sectors):
        # First visit the own-slot sector. Remaining sectors provide fallback
        # coverage because no teammate completion/position API was supplied.
        row=int(self.slot[-1]);ordered=list(dict.fromkeys(sectors))
        route=[]
        for sector in ordered:
            if sector=='CM':
                route.append((3.06,4.15-(row-1)*1.65));continue
            y=4.15-(int(sector[-1])-1)*1.65
            xs=(2.42,1.62) if sector.startswith('B') else (3.70,4.50)
            route.extend((x,y) for x in xs)
        self.route=route;self.route_index=0;self.path=[];self.attempts=0;self.anchor=None;self.mode='track'
        self.progress_goal=None;self.best_goal_distance=math.inf;self.no_route_since=None
    def apply_order(self,order):
        self.entry_changes=0;self.entry_tried=set()
        self.order=copy.deepcopy(order)
        enemy='R' if self.camp=='BLUE' else 'B'
        eligible=[f'{enemy}-S{i}' for i in (1,2,3)]+['CM']
        self.set_route(list(order['search_sectors'])+[s for s in eligible if s not in order['search_sectors']])
        self.staging_goal=None;self.staging_base=None;self.m2_goal=None
        self.approach_corridor=[];self.corridor_index=0;self.mission_stage='survey'
        self.order_ready=True;self.hold=False;self.goal_reason='M1 search only; base movement waits for official M5'
    def acquire_target(self,goal,now=None):
        now=time.monotonic() if now is None else now
        if self.m2_done or now<self.m2_cooldown_until:return
        if self.m2_goal is None:
            self.m2_goal=tuple(goal);self.m2_acquired_at=now;self.m2_updated_at=now;self.m2_started=None
        elif math.dist(self.m2_goal,goal)>.20 and now-self.m2_updated_at>=2.:
            self.m2_goal=tuple(goal);self.m2_updated_at=now
    def expire_target(self,now):
        if not self.m2_done and self.m2_goal is not None and self.m2_acquired_at is not None and now-self.m2_acquired_at>35.:
            self.failed_goals.append({'goal':self.m2_goal,'reason':'M2 approach deadline; resume survey'})
            self.m2_goal=None;self.m2_started=None;self.m2_acquired_at=None;self.m2_cooldown_until=now+15.
            self.path=[];self.anchor=None;self.mode='track'
    def suspend(self):
        # Do not count pause, stale camera or reporting time as a navigation stall.
        self.last_tick=None;self.anchor=None;self.path=[];self.last_plan=-math.inf
        self.retreat_hold_started=None
        self.no_route_since=None
        if self.mode not in ('hold','arrived'):self.mode='track'
    def update_scan(self,state):
        registration=state.get('scan_registration')
        if 'scan_registration' in state and registration is None:return
        mapped={**state,'odom':registration['odom']} if registration else state
        x,y,yaw=pose_from_odom(mapped);scan=state.get('scan',{});now=time.monotonic()
        self.map_registration=copy.deepcopy(registration)
        # The own lidar originates 6.5 cm ahead of the odometry frame. This is
        # fixed sensor calibration, not an obstacle position from the organizer.
        x+=.065*math.cos(yaw);y+=.065*math.sin(yaw)
        self.occupied={key:t for key,t in self.occupied.items() if key in self.static_cells or now-t<5.}
        source=(scan.get('clock_domain'),scan.get('frame_seq'),scan.get('measured_at'))
        if source==self.map_source:return
        self.map_source=source
        self.hit_votes={key:v for key,v in self.hit_votes.items() if now-v[1]<3.}
        self.risk={key:t for key,t in self.risk.items() if now-t<20.}
        # Ray clearing removes outdated obstacles, including moving robots.
        # Accumulate endpoints separately so one ray cannot erase another hit.
        free=set();hits=set()
        for i,d in enumerate(scan.get('ranges',[])):
            if not isinstance(d,(int,float)) or not math.isfinite(d) or not .05<d<=scan.get('range_max',8):continue
            a=yaw+scan['angle_min']+i*scan['angle_increment'];distance=min(d,7.)
            step=self.cell*.5
            for j in range(1,max(1,int((distance-.15)/step))):
                free.add(self.grid(x+j*step*math.cos(a),y+j*step*math.sin(a)))
            if d<scan.get('range_max',8)-.05:hits.add(self.grid(x+d*math.cos(a),y+d*math.sin(a)))
        for key in free-hits:
            self.hit_votes.pop(key,None)
            if key in self.static_cells:
                self.free_votes[key]=self.free_votes.get(key,0)+1
                if self.free_votes[key]<3:continue
                self.static_cells.discard(key);self.free_votes.pop(key,None)
            self.occupied.pop(key,None)
        for key in hits:
            self.free_votes.pop(key,None)
            votes=self.hit_votes.get(key,(0,now))[0]+1;self.hit_votes[key]=(votes,now)
            if votes>=4 and (registration is None or registration.get('stationary')):self.static_cells.add(key)
            self.occupied[key]=now
    def set_objective(self,name):
        # Public-plan approach point inside the base entry strip. The last
        # approach uses a tighter tolerance than broad survey waypoints.
        self.objective=public_base_approach(name,inside=True);self.objective_name=name;self.entry_changes=0;self.entry_tried=set();self.entry_adjust_at=-math.inf;self.mission_stage='capture'
        # M5 is a new time-limited official command. It preempts surveying and
        # wakes completed/failed survey states; sensor and match gates remain.
        self.path=[];self.last_plan=-math.inf;self.mode='track';self.anchor=None
        self.attempts=0;self.retreat=False;self.retreat_hold_started=None
        self.progress_goal=None;self.best_goal_distance=math.inf;self.no_route_since=None
    def alternate_entry(self,position,goal,base):
        """Select another point in the SAME public base strip from own lidar."""
        occupied=self.inflated_cells()
        def blocked(point):return self.grid(*point) in occupied
        if self.entry_changes>=3 or (self.entry_changes and not blocked(goal)):return None
        steps=max(1,math.ceil(math.dist(position,goal)/.05))
        if not blocked(goal) and not any(blocked((position[0]+(goal[0]-position[0])*i/steps,position[1]+(goal[1]-position[1])*i/steps)) for i in range(2,steps+1)):return None
        center=public_base_approach(base)[1];options=[]
        for delta in (-.45,-.30,-.15,.15,.30,.45):
            candidate=(goal[0],center+delta)
            if math.dist(candidate,goal)<.10 or blocked(candidate) or candidate in self.entry_tried:continue
            path=self.plan(self.grid(*position),self.grid(*candidate))
            if path:
                length=sum(math.dist(a,b) for a,b in zip([position]+path,path))
                options.append((length+.1*abs(delta),candidate))
        if not options:return None
        choice=min(options)[1];self.entry_tried.update([goal,choice]);self.entry_changes+=1
        return choice

    def inflated_cells(self):
        # Minkowski sum: conservative occupied-cell square and swept body disk.
        # Same measured body envelope as the rotation gate, including margin.
        reach=math.ceil((BODY_ROTATION_RADIUS+self.cell/2)/self.cell)
        offsets=[(dx,dy) for dx in range(-reach,reach+1) for dy in range(-reach,reach+1)
            if math.hypot(max(0.,abs(dx)*self.cell-self.cell/2),max(0.,abs(dy)*self.cell-self.cell/2))<=BODY_ROTATION_RADIUS]
        return {(x+dx,y+dy) for x,y in self.occupied for dx,dy in offsets}

    def plan(self,start,goal):
        blocked=self.inflated_cells();penalty=collections.defaultdict(float)
        outer=math.ceil(.40/self.cell)
        for x,y in self.occupied:
            for dx in range(-outer,outer+1):
                for dy in range(-outer,outer+1):
                    p=(x+dx,y+dy)
                    # Sampling a wall more densely must not multiply its cost.
                    if p not in blocked:penalty[p]=max(penalty[p],.5)
        risk_radius=round(.60/self.cell)
        for x,y in self.risk:
            for dx in range(-risk_radius,risk_radius+1):
                for dy in range(-risk_radius,risk_radius+1):penalty[(x+dx,y+dy)]+=6./(1+(abs(dx)+abs(dy))*self.cell/.20)
        xmin=math.ceil(.20/self.cell);xmax=math.floor(5.80/self.cell+1e-8)
        ymin=math.ceil(.20/self.cell);ymax=math.floor(4.80/self.cell+1e-8)
        def valid(p):return xmin<=p[0]<=xmax and ymin<=p[1]<=ymax and p not in blocked
        blocked.discard(start)
        reach=math.ceil(.21/self.cell)
        candidates=[(goal[0]+dx,goal[1]+dy) for dx in range(-reach,reach+1) for dy in range(-reach,reach+1)
                    if math.hypot(dx,dy)*self.cell<=.21 and valid((goal[0]+dx,goal[1]+dy))]
        if not candidates:return []
        targets=set(candidates);queue=[(0.,0.,start)];cost={start:0.};previous={};end=None
        while queue:
            _,g,node=heapq.heappop(queue)
            if g>cost[node]:continue
            if node in targets:end=node;break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nxt=(node[0]+dx,node[1]+dy)
                if not valid(nxt):continue
                value=g+1.+penalty[nxt]
                if value<cost.get(nxt,math.inf):
                    cost[nxt]=value;previous[nxt]=node
                    h=min(abs(nxt[0]-p[0])+abs(nxt[1]-p[1]) for p in targets)
                    heapq.heappush(queue,(value+h,value,nxt))
        if end is None:return []
        path=[end]
        while path[-1]!=start:path.append(previous[path[-1]])
        # The continuous pose is already inside the start cell. Reintroducing
        # its rounded center can turn the robot backwards after every replan.
        return [(x*self.cell,y*self.cell) for x,y in list(reversed(path))[1:]]
    def local_path_clear(self,x,y):
        """Validate the next four cells against the freshly measured map.

        Distant route-cost changes do not reverse an unfinished local turn.
        A newly blocked nearby segment, recovery or new goal still invalidates it.
        """
        if not self.path:return False
        blocked=self.inflated_cells()
        blocked.discard(self.grid(x,y));point=(x,y)
        for target in self.path[:4]:
            steps=max(1,math.ceil(math.dist(point,target)/(self.cell*.5)))
            for step in range(1,steps+1):
                sample=(point[0]+(target[0]-point[0])*step/steps,
                        point[1]+(target[1]-point[1])*step/steps)
                if self.grid(*sample) in blocked:return False
            point=target
        return True
    def select_waypoint(self,x,y):
        """Look ahead only through the SAME measured, inflated free corridor."""
        if not self.path:return None
        blocked=self.inflated_cells()
        blocked.discard(self.grid(x,y));best=0
        for index,target in enumerate(self.path):
            distance=math.dist((x,y),target)
            if distance>.50+1e-8:break
            steps=max(1,math.ceil(distance/(self.cell*.25)))
            if any(self.grid(x+(target[0]-x)*j/steps,y+(target[1]-y)*j/steps) in blocked for j in range(1,steps+1)):break
            best=index
        if best:self.path=self.path[best:]
        return self.path[0]
    def wait_scan(self,reason):
        # Missing evidence is a sensor wait, not a failed route/recovery attempt.
        self.anchor_at=time.monotonic();self.reason=reason
        return 0.,0.
    def safe(self,state,linear,angular):
        # Every command, including recovery and survey turns, passes this gate.
        if linear:
            clearance=translation_clearance(state,1 if linear>0 else -1)
            required=abs(linear)*.65+linear*linear/(2*.35)
            if clearance is None:return self.wait_scan('await fresh translation scan')
            if clearance<=0.:
                self.reason='translation clearance insufficient';return 0.,0.
            if clearance<=required:
                # Solve the SAME reaction/braking inequality for an admissible
                # velocity. The calibrated body envelope and margin have already
                # been subtracted; keep 2% below the braking boundary.
                cap=(-.455+math.sqrt(.455*.455+2.8*clearance))/2.
                linear=math.copysign(min(abs(linear),cap*.98),linear)
                self.reason='translation speed reduced to preserve stopping clearance'
        if angular:
            clearance=rotation_clearance(state)
            if clearance is None:return self.wait_scan('await fresh rotation scan')
            if clearance<=BODY_ROTATION_RADIUS:
                self.reason='rotation footprint clearance insufficient';return 0.,0.
        return linear,angular
    def fail_goal(self,now,local_escape=True):
        self.failed_goals.append({'goal':self.goal,'reason':self.reason})
        self.failed_goals=self.failed_goals[-20:];self.path=[];self.anchor=None;self.attempts=0
        if not self.retreat and self.objective is None and self.route_index<len(self.route):
            self.route_index+=1;self.mode='yield' if local_escape else 'track';self.until=now+1.
            self.reason='unreachable survey point deferred; not marked complete'
        else:
            self.mode='hold';self.recovery_retry_at=now+3.;self.reason='recovery budget exhausted; wait for measured route to clear'
            if not self.retreat and self.objective is None and self.staging_goal is not None:
                self.mission_stage='preposition_blocked';self.reason='M1 staging unreachable; safe hold awaiting accepted M5'
    def escape_direction(self,state):
        # Connect a continuous pose trapped by conservative grid quantization
        # using only straight, 5 cm motions away from measured obstacles.
        if arc_clearance(state,0.,math.pi) is None:return 0
        scan=state.get('navigation_scan',state.get('scan',{}));points=[]
        for i,d in enumerate(scan['ranges']):
            a=scan['angle_min']+i*scan['angle_increment']
            points.append((.065+d*math.cos(a),d*math.sin(a)))
        initial=min(math.hypot(x,y) for x,y in points);options=[]
        for direction in (1,-1):
            clearance=translation_clearance(state,direction)
            if clearance is None or clearance<.14:continue
            distances=[min(math.hypot(x-direction*step,y) for x,y in points) for step in (.0125,.025,.0375,.05)]
            if min(distances)>=initial-.002 and distances[-1]>initial+.015:
                options.append((distances[-1],direction))
        return max(options)[1] if options else 0
    def start_escape(self,state,now,x,y):
        direction=self.escape_direction(state)
        if not direction:return None
        self.mode='escape';self.escape_sign=direction;self.escape_start=(x,y);self.until=now+2.
        self.path=[];self.anchor=None;self.reason='measured straight escape from inflated start cell'
        return self.safe(state,.08*direction,0.)

    def begin_recovery(self,state,now):
        self.recovery_cause=self.reason
        self.attempts+=1;self.path=[];self.last_plan=-math.inf;self.anchor=None
        if self.attempts>3:self.fail_goal(now);return 0.,0.
        self.mode='yield';self.until=now+.5+.15*int(self.slot[-1])
        self.reason='blocked: yield before bounded reverse and replan'
        return 0.,0.
    def recovery(self,state,now,x,y,yaw):
        if self.mode=='escape':
            if now<self.until and math.dist((x,y),self.escape_start)<.05 and self.escape_direction(state)==self.escape_sign:
                self.reason='measured straight escape from inflated start cell'
                return self.safe(state,.08*self.escape_sign,0.)
            self.mode='track';self.path=[];self.last_plan=-math.inf;self.anchor=None;self.no_route_since=None
            return 0.,0.
        if self.mode=='yield':
            if now<self.until:return 0.,0.
            rear=arc_clearance(state,math.pi)
            if rear is not None and rear>.43:
                self.mode='reverse';self.until=now+1.6;self.reverse_start=(x,y)
            else:self.mode='choose_turn'
        if self.mode=='reverse':
            if now<self.until and math.dist((x,y),self.reverse_start)<.16:
                command=self.safe(state,-.10,0.)
                if command[0]:self.reason='bounded reverse with rear clearance';return command
            self.mode='choose_turn'
        if self.mode=='choose_turn':
            left=arc_clearance(state,math.pi/2);right=arc_clearance(state,-math.pi/2)
            if left is None or right is None or arc_clearance(state,0.,math.pi) is None:
                return self.wait_scan('await fresh recovery scan; attempt retained')
            if self.safe(state,0.,.5)==(0.,0.):
                escape=self.start_escape(state,now,x,y)
                if escape is not None:return escape
                self.reason='no safe reverse/turn corridor';return self.begin_recovery(state,now)
            self.turn_sign=1. if left>right else -1.
            self.turn_start=yaw;self.until=now+6.;self.mode='turn'
        if self.mode=='turn':
            if now<self.until and abs(angle(yaw-self.turn_start))<.65:
                command=self.safe(state,0.,self.turn_sign*min(2.4,max(.6,(.65-abs(angle(yaw-self.turn_start)))*6.)))
                if command[1]:self.reason='bounded turn toward measured free side';return command
                if self.reason.startswith('await fresh'):return 0.,0.
            self.mode='track';self.path=[];self.last_plan=-math.inf;self.anchor=None
            self.reason='replan after recovery';return 0.,0.
        return 0.,0.
    def snapshot(self):
        return {'goal_reason':self.goal_reason,'survey_index':self.route_index,'goal':self.goal,
                'mission_stage':self.mission_stage,'staging_goal':self.staging_goal,
                'staging_base':self.staging_base,'staging_source':'accepted M1 base' if self.staging_goal is not None else None,
                'approach_corridor':self.approach_corridor,'corridor_index':self.corridor_index,
                'path':list(self.path),'state':self.mode,'reason':self.reason,'recovery_attempts':self.attempts,
                'order_ready':self.order_ready,'order':self.order,'retreat':self.retreat,
                'retreat_hold_elapsed_s':None if self.retreat_hold_started is None else round(max(0.,time.monotonic()-self.retreat_hold_started),2),
                'recovery_cause':self.recovery_cause,
                'best_goal_distance':None if not math.isfinite(self.best_goal_distance) else round(self.best_goal_distance,3),
                'no_route_wait_s':None if self.no_route_since is None else round(max(0.,time.monotonic()-self.no_route_since),2),
                'path_committed':self.path_committed,'path_replan_reason':self.path_replan_reason,
                'path_plan_count':self.path_plan_count,'map_source':self.map_source,'map_cells':list(self.occupied),'map_cell_m':self.cell,'static_map_cells':list(self.static_cells),'persistent_obstacle_cells':len(self.static_cells),'map_registration':getattr(self,'map_registration',None),
                'completed_survey_points':list(self.completed),'failed_goals':copy.deepcopy(self.failed_goals),
                'coordination':'own-slot priorities, no shared teammate state'}
    def command(self,state):
        now=time.monotonic();x,y,yaw=pose_from_odom(state)
        if not all(math.isfinite(v) for v in (x,y,yaw)):
            self.reason='invalid odometry';return 0.,0.
        if state.get('cmd') in ('pause','stop') or not SafeMotion.allowed(state):
            self.suspend();self.reason='operator or match stop';return 0.,0.
        if (self.hold or (state.get('mode')!='practice' and not self.order_ready)) and self.objective is None:
            self.mission_stage='await_order';self.reason='waiting for an accepted unambiguous order';return 0.,0.
        if self.last_tick is None or now-self.last_tick>.7:
            self.anchor=None;self.retreat_hold_started=None;self.no_route_since=None
            if self.mode not in ('hold','arrived'):self.mode='track'
        self.last_tick=now
        hp=state.get('hp');maximum=state.get('hp_max');battery=state.get('battery_pct')
        hit=isinstance(hp,(int,float)) and self.last_hp is not None and hp<self.last_hp
        if isinstance(hp,(int,float)) and math.isfinite(hp):self.last_hp=hp
        low_hp=isinstance(hp,(int,float)) and isinstance(maximum,(int,float)) and maximum>0 and 0<hp<=maximum*.5
        low_battery=isinstance(battery,(int,float)) and battery<=20
        if hit:
            self.risk[self.grid(x,y)]=now;self.path=[];self.last_plan=-math.inf
            self.retreat_hold_started=None
        # The capture runner keeps moving toward cover/objective after damage.
        # A long return along the same exposed corridor lost the first trial.
        # HP-zero damage stun is enforced by the server; it is not a permanent halt.
        damage_retreat=(hit or low_hp) and not (self.prepare_base_first and self.order_ready)
        if (damage_retreat or low_battery) and not self.retreat:
            self.retreat=True;self.mode='track';self.path=[];self.attempts=0;self.anchor=None
            self.retreat_hold_started=None
        if self.retreat:
            if low_hp or low_battery or math.dist((x,y),self.home)>=.28:
                self.retreat_hold_started=None
            elif self.retreat_hold_started is not None and now-self.retreat_hold_started>=6.:
                # A single noncritical hit must not retire the robot for the
                # entire match. Six continuously observed seconds at home end
                # this temporary retreat; the risk map and original task stay.
                self.retreat=False;self.retreat_hold_started=None;self.mode='track'
                self.path=[];self.last_plan=-math.inf;self.anchor=None;self.attempts=0
        if self.retreat:
            goal=self.home;self.mission_stage='retreat';self.goal_reason='own damage/resource retreat to public starting corridor'
        elif self.objective:
            goal=self.objective;self.mission_stage='capture';self.goal_reason='M5 time-limited objective; survey suspended'
        elif self.order_ready and getattr(self,'m2_goal',None) is not None and not self.m2_done:
            goal=self.m2_goal;self.mission_stage='m2_approach';self.goal_reason='provided photo and own LiDAR observed target'
        elif self.route_index<len(self.route):
            goal=self.route[self.route_index];self.mission_stage='survey'
            self.goal_reason='accepted order survey, own-slot priority' if self.order_ready else 'public-plan sector survey'
        elif self.order_ready and self.staging_goal is not None:
            goal=self.staging_goal;self.mission_stage='preposition'
            self.goal_reason='accepted M1 base approach outside entry; official M5 not yet known'
        else:
            self.goal=None;self.mode='hold';self.reason='survey attempts ended; awaiting official objective'
            return 0.,0.
        if not self.retreat and self.mission_stage in ('capture','preposition') and math.dist((x,y),goal)<1. and now-self.entry_adjust_at>2.:
            self.entry_adjust_at=now;self.update_scan(state)
            base=self.objective_name if self.objective is not None else self.staging_base
            alternate=self.alternate_entry((x,y),goal,base)
            if alternate:
                goal=alternate
                if self.objective is not None:self.objective=alternate
                else:self.staging_goal=alternate
                self.path=[];self.last_plan=-math.inf;self.mode='track';self.anchor=None
                self.reason='same base: lidar-blocked entry replaced by clear approach'
        if not self.retreat and self.mission_stage=='preposition' and math.dist((x,y),goal)<.25 and now-getattr(self,'staged_entry_check',-math.inf)>2.:
            self.staged_entry_check=now;self.update_scan(state)
            inside=(public_base_approach(self.staging_base,inside=True)[0],goal[1])
            alternate=self.alternate_entry((x,y),inside,self.staging_base)
            if alternate:
                self.staging_goal=(goal[0],alternate[1]);goal=self.staging_goal
                self.path=[];self.last_plan=-math.inf;self.mode='track';self.anchor=None
                self.reason='own lidar: prepare a clear entry before official M5 without entering'
        if self.goal!=goal:
            self.path=[];self.last_plan=-math.inf
        self.goal=goal
        distance=math.dist((x,y),goal)
        if self.progress_goal!=goal:
            self.progress_goal=goal;self.best_goal_distance=distance;self.attempts=0;self.no_route_since=None
        elif distance<self.best_goal_distance-.12:
            # Returning to the same place after reversing is not new progress.
            self.best_goal_distance=distance;self.attempts=0
        if self.mode=='hold':
            if now<getattr(self,'recovery_retry_at',math.inf):return 0.,0.
            self.recovery_retry_at=now+3.;self.update_scan(state)
            self.path=self.plan(self.grid(x,y),self.grid(*goal))
            escape=self.start_escape(state,now,x,y) if not self.path else None
            if escape is not None:return escape
            if not self.path:return 0.,0.
            self.mode='track';self.attempts=0;self.anchor=None;self.no_route_since=None
            self.reason='fresh measured route reopened after recovery hold'
        if self.mode in ('yield','reverse','choose_turn','turn','escape'):return self.recovery(state,now,x,y,yaw)
        if self.mode=='scan':
            if self.scan_yaw is not None:self.scan_angle+=abs(angle(yaw-self.scan_yaw))
            self.scan_yaw=yaw
            if now-self.scan_start<12. and self.scan_angle<2*math.pi:
                command=self.safe(state,0.,.55)
                if command[1]:self.reason='bounded observation sweep';return command
            self.route_index+=1;self.mode='track';self.path=[];self.anchor=None
            self.reason='observation sweep ended';return 0.,0.
        objective_active=self.objective is not None and not self.retreat
        staging_active=self.objective is None and not self.retreat and self.staging_goal is not None and self.mission_stage=='preposition'
        precise_approach=objective_active or staging_active or self.mission_stage=='m2_approach'
        arrival=.28 if self.mission_stage=='corridor' else (.05 if precise_approach else .28)
        if self.mission_stage=='m2_approach' and distance<.06:
            if self.m2_started is None:self.m2_started=now
            if now-self.m2_started>25.:
                self.m2_done=True;self.mode='track';self.path=[];return 0.,0.
            self.mission_stage='m2_aim';self.reason='bounded camera target search; no blind firing'
            if self.m2_target_position:
                heading=math.atan2(self.m2_target_position[1]-y,self.m2_target_position[0]-x)
                return self.safe(state,0.,max(-2.4,min(2.4,angle(heading-yaw)*6)))
            return self.safe(state,0.,.35)
        if distance<arrival:
            self.path=[];self.anchor=None;self.attempts=0
            if self.mission_stage=='corridor':
                self.corridor_index+=1;self.last_plan=-math.inf;self.mode='track'
                self.reason='public corridor waypoint reached; proceeding to next leg'
                return 0.,0.
            if staging_active:
                self.mode='arrived';self.mission_stage='await_m5'
                self.reason='outside M1 base entry; orienting for possible M5, no entry'
                heading=0. if self.staging_base.startswith('RED_') else math.pi
                error=angle(heading-yaw)
                if abs(error)>.035:return self.safe(state,0.,max(-2.4,min(2.4,error*6)))
                return 0.,0.
            if self.retreat or objective_active or self.route_index>=len(self.route):
                if self.retreat:
                    if low_hp or low_battery:
                        self.reason='at home; critical resources require safe hold'
                    else:
                        if self.retreat_hold_started is None:self.retreat_hold_started=now
                        self.reason='temporary hit retreat: observe safe home hold for 6 seconds'
                    self.mode='arrived';return 0.,0.
                self.mode='arrived';self.reason='at estimated destination; mission success requires official confirmation'
                return 0.,0.
            self.completed.append(goal);self.mode='scan';self.scan_start=now;self.scan_angle=0.;self.scan_yaw=yaw
            return 0.,0.
        if self.anchor is None:
            self.anchor=(x,y);self.anchor_at=now;self.heading_waypoint=None;self.heading_best=None
        elif math.dist((x,y),self.anchor)>=.12:
            self.anchor=(x,y);self.anchor_at=now
            self.heading_waypoint=None;self.heading_best=None
        if now-self.last_plan>.6:
            self.update_scan(state)
            self.path_committed=self.local_path_clear(x,y)
            if not self.path_committed:
                self.path_replan_reason='local prefix blocked' if self.path else 'path empty or goal changed'
                self.path=self.plan(self.grid(x,y),self.grid(*goal));self.path_plan_count+=1
            self.last_plan=now
        # A grid endpoint can be 20 cm short of the entry strip. Follow the
        # measured final half metre slowly; the same lidar safety gate applies.
        if precise_approach and distance<.5:self.path=[goal]
        front=translation_clearance(state,1)
        if front is None:
            return self.wait_scan('await fresh front scan')
        if front<.04:
            if self.mode=='wait_path':
                self.reason='waiting stationary for clear local corridor and route';return 0.,0.
            self.reason='front obstruction';return self.begin_recovery(state,now)
        # Keep the final precise goal until the arrival check above accepts it.
        # Popping at 6 cm while arriving at 5 cm created a 1 cm dead zone.
        while self.path and (not precise_approach or len(self.path)>1) and math.dist((x,y),self.path[0])<min(.12,self.cell*.6):self.path.pop(0)
        if not self.path:
            if self.grid(x,y) in self.inflated_cells():
                escape=self.start_escape(state,now,x,y)
                if escape is not None:return escape
            # A disconnected distant goal does not justify reversing in a
            # locally clear corridor. Retry fresh scans, then defer finitely.
            self.reason='no traversable global path; waiting for fresh map'
            if self.no_route_since is None:self.no_route_since=now
            if now-self.no_route_since>=2.:
                self.recovery_cause=self.reason;self.no_route_since=None
                if self.objective is not None or self.retreat or self.mission_stage=='corridor':
                    # A moving robot can disconnect the map briefly. Preserve
                    # M5/home and retry fresh scans without blind motion.
                    self.mode='wait_path';self.reason='waiting stationary for M5/home route to reopen'
                else:self.fail_goal(now,local_escape=False)
            return 0.,0.
        self.no_route_since=None
        if self.mode=='wait_path':
            self.mode='track';self.anchor=(x,y);self.anchor_at=now
            self.heading_waypoint=None;self.heading_best=None
        # Track the next cell, never cut across an inflated grid corner.
        waypoint=self.select_waypoint(x,y);error=angle(math.atan2(waypoint[1]-y,waypoint[0]-x)-yaw)
        if self.heading_waypoint!=waypoint:
            self.heading_waypoint=waypoint;self.heading_best=abs(error)
        elif self.heading_best-abs(error)>=.10:
            # Real skid steering may need more than six seconds to align.
            # Count measured reduction toward the SAME waypoint, not arbitrary
            # spinning or a changed planner target, as useful progress.
            self.heading_best=abs(error);self.anchor_at=now
        if now-self.anchor_at>6.:
            self.reason='odometry shows no translation or heading progress';return self.begin_recovery(state,now)
        # Local MuJoCo own-IMU trials measured ~0.16 actual yaw/command ratio.
        # This feedback cap was tested with both signs; it is NOT Jetson/UGV
        # calibration. Robot SDK +/-3 limit and rotation clearance stay intact.
        angular=max(-2.4,min(2.4,error*6.0))
        # More speed only when heading is aligned; preserve existing lidar
        # stopping-distance gate and precise final approach caps.
        cruise=.22 if abs(error)<.20 and (front>.65 or objective_active) else .14
        linear=cruise*max(0.,math.cos(error)) if abs(error)<.65 else 0.
        if precise_approach and distance<.5:linear=min(linear,.22 if objective_active else (max(.04,min(.14,distance*.8)) if self.mission_stage=='m2_approach' else .04))
        if self.retreat:linear=min(linear,.10)
        self.reason='following clearance-weighted path'
        return self.safe(state,linear,angular)


def observed_block_center(location,state):
    """Fit the local LiDAR face plane. RGB provides the face association only."""
    registration=state.get('scan_registration')
    if 'scan_registration' in state and registration is None:return None
    mapped={**state,'odom':registration['odom']} if registration else state
    x,y,yaw=pose_from_odom(mapped);origin=np.array([x+.065*math.cos(yaw),y+.065*math.sin(yaw)])
    scan=state.get('scan',{});ranges=scan.get('ranges',[])
    if len(ranges)<5:return None
    bearing=angle(math.atan2(location[1]-origin[1],location[0]-origin[0])-yaw)
    i=min(range(len(ranges)),key=lambda j:abs(angle(scan['angle_min']+j*scan['angle_increment']-bearing)))
    points=[]
    for j in range(max(0,i-2),min(len(ranges),i+3)):
        if not isinstance(ranges[j],(int,float)) or not isinstance(ranges[i],(int,float)):continue
        distance=float(ranges[j]);a=yaw+scan['angle_min']+j*scan['angle_increment']
        if not .12<distance<3. or abs(distance-float(ranges[i]))>.18:continue
        points.append(origin+distance*np.array([math.cos(a),math.sin(a)]))
    if len(points)<3:return None
    values=np.asarray(points);values=values-values.mean(axis=0);cov=values.T@values/len(values)
    eigen,basis=np.linalg.eigh(cov)
    if eigen[1]<.0001 or eigen[0]>.0004 or eigen[0]/eigen[1]>.025:return None
    normal=basis[:,0];axis=int(np.argmax(np.abs(normal)))
    if abs(normal[axis])<.95:return None
    normal=np.zeros(2);normal[axis]=1 if origin[axis]>location[axis] else -1
    center=np.asarray(location)-.20*normal
    return tuple(map(float,center))


class CoverageNavigator(SurveyNavigator):
    """Inspect the far side of an observed block, before proceeding to M2.

    Center is estimated from own RGB-associated LiDAR face samples and the
    visible 40 cm box dimension. No organizer coordinates or object labels.
    """
    def __init__(self,camp,slot):
        super().__init__(camp,slot)
        self.block_samples=[];self.coverage_tasks=[];self.coverage_started=False
        self.coverage_index=0;self.coverage_center=None;self.coverage_sector=None
        self.coverage_settled=None;self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_frames=set()
        self.internal_coverage=False;self.official_capture=False;self.m2_observations=[];self.covered_blocks=[];self.coverage_block_started=None
    def apply_order(self,order):
        super().apply_order(order)
        self.coverage_generation=getattr(self,'coverage_generation',0)+1
        self.block_samples=[];self.coverage_tasks=[];self.coverage_started=False;self.coverage_index=0
        self.coverage_center=None;self.coverage_sector=None;self.coverage_settled=None;self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_frames=set()
    def observe_panel(self,location,state,frame):
        if self.coverage_started or not self.order_ready:return
        sector=sector_for(*location)
        enemy='R' if self.camp=='BLUE' else 'B'
        if sector not in {f'{enemy}-S1',f'{enemy}-S2',f'{enemy}-S3','CM'}:return
        center=observed_block_center(location,state)
        if center is None:return
        if sector_for(*center)!=sector or any(math.dist(center,old)<.35 for old in self.covered_blocks):return
        if any(v[0]==frame for v in self.block_samples):return
        self.block_samples.append((frame,center,sector));self.block_samples=self.block_samples[-25:]
    def consider_target(self,goal,position,frame,now=None):
        now=time.monotonic() if now is None else now
        if self.official_capture or self.m2_done or now<self.m2_cooldown_until:return
        self.m2_observations=[item for item in self.m2_observations if now-item[0]<=1.2]
        if any(item[1]==frame for item in self.m2_observations):return
        self.m2_observations.append((now,frame,tuple(position)));self.m2_observations=self.m2_observations[-8:]
        stable=[item for item in self.m2_observations if math.dist(item[2],position)<.20]
        if len(stable)<3:return
        first=self.m2_goal is None
        self.acquire_target(goal,now)
        if self.m2_goal is None:return
        self.m2_target_position=tuple(position)
        if first:
            # Suspend inspection without asserting completion or deleting evidence.
            self.coverage_started=False;self.coverage_tasks=[];self.block_samples=[];self.coverage_index=0
            self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_settled=None;self.coverage_frames=set()
            self.coverage_reset_motion()

    def reviewing_location(self,location):
        return bool(self.coverage_started and self.coverage_center and math.dist(location,self.coverage_center)<.36)
    def set_objective(self,name):
        self.official_capture=True;self.internal_coverage=False
        super().set_objective(name)
    def alternate_entry(self,position,goal,base):
        if self.internal_coverage:return None
        return super().alternate_entry(position,goal,base)
    def suspend(self):
        super().suspend()
        self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_settled=None;self.coverage_frames=set()
    def coverage_reset_motion(self):
        self.path=[];self.mode='track';self.anchor=None;self.last_plan=-math.inf
        self.progress_goal=None;self.best_goal_distance=math.inf;self.no_route_since=None;self.attempts=0
    def command(self,state):
        self.expire_target(time.monotonic())
        if self.m2_goal is not None and not self.m2_done:return super().command(state)
        if self.official_capture or self.hold or not self.order_ready or not SafeMotion.allowed(state) or state.get('cmd') in ('pause','stop') or state.get('battery_pct',100)<=20 or self.retreat:
            return super().command(state)
        x,y,yaw=pose_from_odom(state);now=time.monotonic()
        if self.coverage_started and (self.coverage_index>=len(self.coverage_tasks) or now-self.coverage_block_started>120.):
            if self.coverage_index<len(self.coverage_tasks):self.failed_goals.append({'goal':self.coverage_center,'reason':'block observation budget expired; partial evidence retained'})
            self.covered_blocks.append(self.coverage_center);self.coverage_started=False;self.coverage_tasks=[];self.coverage_index=0;self.block_samples=[]
            self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_settled=None;self.coverage_frames=set();self.coverage_reset_motion()
        groups=[[v for v in self.block_samples if v[2]==sample[2] and math.dist(v[1],sample[1])<.18] for sample in self.block_samples]
        group=max(groups,key=len,default=[])
        if not self.coverage_started and len(group)>=2 and (self.m2_done or self.m2_goal is None):
            self.coverage_started=True;self.coverage_block_started=now
            cx,cy=map(float,np.median([v[1] for v in group],axis=0))
            self.coverage_center=(cx,cy);self.coverage_sector=group[-1][2]
            ring=[(max(.40,cx-.50),cy),(cx,max(.40,cy-.50)),(min(5.72,cx+.50),cy),(cx,min(4.60,cy+.50))]
            first=min(range(4),key=lambda i:math.dist((x,y),ring[i]))
            options=[[ring[(first+direction*j)%4] for j in range(4)] for direction in (1,-1)]
            ordered=min(options,key=lambda points:sum(math.dist(a,b) for a,b in zip([(x,y)]+points,points)))
            self.coverage_tasks=[(point,True) for point in ordered]
            self.coverage_reset_motion()
        if self.coverage_index>=len(self.coverage_tasks):
            if self.coverage_started:self.corridor_index=len(self.approach_corridor)
            return super().command(state)
        point,observe=self.coverage_tasks[self.coverage_index]
        if self.coverage_point_started is None:self.coverage_point_started=now
        if math.dist((x,y),point)>=.13 and self.coverage_arrived_at is None and now-self.coverage_point_started>35.:
            self.failed_goals.append({'goal':point,'reason':'coverage waypoint timed out; not counted complete'})
            self.coverage_index+=1;self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_settled=None;self.coverage_reset_motion();return 0.,0.
        if math.dist((x,y),point)<.13:
            if self.coverage_arrived_at is None:self.coverage_arrived_at=now
            if now-self.coverage_arrived_at>18.:
                self.failed_goals.append({'goal':point,'reason':'alignment/observation deadline; no fabricated completion'})
                self.coverage_index+=1;self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_settled=None;self.coverage_reset_motion();return 0.,0.
            error=angle(math.atan2(self.coverage_center[1]-y,self.coverage_center[0]-x)-yaw)
            self.mission_stage='m3_align';self.goal=point
            if observe and abs(error)>.04:
                self.coverage_settled=None;self.reason='aligning own camera to observed block'
                return self.safe(state,0.,max(-2.4,min(2.4,error*6)))
            if self.coverage_settled is None:self.coverage_settled=now;self.coverage_settled_wall=time.time();self.coverage_frames=set()
            self.mission_stage='m3_observe';self.reason='stationary fresh-frame verification of observed block'
            if not observe or (now-self.coverage_settled>=3. and len(self.coverage_frames)>=3) or now-self.coverage_settled>6.:
                if observe and len(self.coverage_frames)<3:self.failed_goals.append({'goal':point,'reason':'not enough distinct target-block frames; observation incomplete'})
                else:self.completed.append(point)
                self.coverage_index+=1;self.coverage_point_started=None;self.coverage_arrived_at=None;self.coverage_settled=None;self.coverage_reset_motion()
            return 0.,0.
        original=self.objective;self.objective=point;self.internal_coverage=True
        try:command=super().command(state)
        finally:self.objective=original;self.internal_coverage=False
        self.mission_stage='m3_approach';self.goal_reason='own RGB and LiDAR block estimate; far-side coverage'
        return command
    def snapshot(self):
        result=super().snapshot();result.update(coverage_center=self.coverage_center,coverage_sector=self.coverage_sector,
          coverage_index=self.coverage_index,coverage_tasks=self.coverage_tasks,coverage_samples=len(self.block_samples))
        return result


class LearnedTeam:
    def __init__(self,args):
        self.args=args;self.output=args.output.resolve();self.output.mkdir(parents=True,exist_ok=True)
        self.stop_event=threading.Event();self.lock=threading.RLock();self.motion=SafeMotion()
        self.client=ParticipantClient(args.server,args.mission_code,timeout=.3,
            api_prefix='/api/rehearsal' if args.mode=='rehearsal' else '/api/report',
            robot_prefix='/sim/robot' if args.mode=='match' else '/sim/'+args.mode+'/robot')
        self.drone_client=TeamClient(args.server,args.mission_code,timeout=.8)
        self.llm=TextLLM();self.order_task=LatestTask();self.risk_task=LatestTask();self.reporting_deadline=None;self.risk_started=False
        self.report=ReportWorkflow(self.client);self.observations=ObservationStore()
        self.scan_memory=TemporalScan()
        self.state=None;self.me={};self.state_at=0.;self.frame_at=0.;self.loaded=False;self.nav=None;self.drone=None
        self.me_at=0.;self.pending_order=None;self.pending_objective=None;self.order_error=None
        self.beacon=None;self.beacon_at=0.;self.beacon_seq=None;self.aim_stable=0;self.aim_seq=None;self.firing_until=0.
        self.frame_seq=0;self.previous_cmd=None;self.finished=False;self.error=None
        self.weights=args.weights.resolve();self.weights_hash=hashlib.sha256(self.weights.read_bytes()).hexdigest()
        self.status={'status':'starting','loaded':False,'ready':False,'server_ready':False,'weights_sha256':self.weights_hash,
            'frame_seq':0,'image_sha256':None,'predictions':[],'observations':[],'policy':args.policy,'mode':args.mode,
            'llm_mode':self.llm.mode,'robot_transport':self.client.kind,'command_owner':'manual_allowed' if args.policy=='observe' else 'mission_controller',
            'limitations':LIMITATIONS,'drone':{'frames':0},'reports':{},'admin_truth_used':False,'public_map_calibration':'approximate fixed initial slots, no GPS'}
        self.threads=[];self.logfile=(self.output/'events.jsonl').open('a');self.tracefile=(self.output/'trace.jsonl').open('a');self.next_trace=0.
    def log(self,event,**data):
        record={'time':time.time(),'event':event,**data}
        with self.lock:self.logfile.write(json.dumps(record,ensure_ascii=False)+'\n');self.logfile.flush()
        print(json.dumps(record,ensure_ascii=False),flush=True)
    def publish_status(self):
        with self.lock:
            self.status['updated_at']=time.time();self.status['observations']=self.observations.summary()
            atomic_json(self.output/'status.json',self.status)
    def sensor_worker(self):
        while not self.stop_event.is_set():
            start=time.monotonic()
            try:
                state=self.scan_memory.update(self.client.sim_get('state'));fire_request=False;level_gimbal=False;desired_tilt=0.
                with self.lock:
                    self.state=state;self.state_at=time.monotonic();self.motion.observe(state)
                    if self.nav is None:
                        camp='BLUE' if state['slot'].startswith('B') else 'RED';self.nav=CoverageNavigator(camp,state['slot']);self.drone=DroneTracks(camp)
                        if self.pending_order:self.nav.apply_order(self.pending_order)
                        if self.pending_objective:self.nav.set_objective(self.pending_objective)
                        self.nav.hold=bool(self.order_error)
                    fresh=self.loaded and time.monotonic()-self.frame_at<1. and sensors_healthy(state)
                    phase_allowed=self.args.mode=='practice' or (self.me.get('phase')=='running' and time.monotonic()-self.me_at<2.)
                    allowed=SafeMotion.allowed(state) and state['cmd'] not in ('pause','stop') and phase_allowed
                    if self.args.policy=='mission' and fresh and allowed and not self.error:
                        linear,angular=self.nav.command(state)
                        if self.nav.mission_stage=='m2_aim':
                            if time.monotonic()>=getattr(self,'next_level_gimbal',0.) and not state.get('laser'):
                                level_gimbal=True;desired_tilt=self.beacon.get('tilt',0.) if self.beacon else 0.;self.next_level_gimbal=time.monotonic()+1.
                            if self.firing_until:
                                linear=angular=0.
                                if time.monotonic()>=self.firing_until:self.nav.m2_done=True;self.firing_until=0.
                            elif self.beacon and time.monotonic()-self.beacon_at<.7:
                                error=angle(self.beacon['world_bearing']-pose_from_odom(state)[2]-(-math.radians(float(state.get('pan',0))*.5)))
                                linear,angular=self.nav.safe(state,0.,max(-2.4,min(2.4,error*6)))
                                if abs(error)<.04 and abs(float(state.get('pan',0)))<.8 and abs(float(state.get('tilt',0))-self.beacon.get('tilt',0.))<1.5:
                                    linear=angular=0.
                                    if self.aim_seq!=self.beacon_seq:self.aim_stable+=1;self.aim_seq=self.beacon_seq
                                    fire_request=self.aim_stable>=3 and state.get('ammo',0)>0
                                else:self.aim_stable=0
                            else:self.aim_stable=0
                        # Emission hold survives a new M5 command or target timeout.
                        # Replan may proceed, but drive only after beam completion.
                        if state.get('laser') or time.monotonic()<self.firing_until:linear=angular=0.
                        self.motion.command(linear,angular)
                    else:self.motion.clear();self.nav.suspend()
                    linear,angular=self.motion.output()
                    self.status.update(own_state=state,phase=self.me.get('phase'),velocity_command=[linear,angular],
                                       sensor_healthy=fresh,navigation=self.nav.snapshot())
                command_ack=None
                if self.args.policy=='mission':command_ack=self.client.sim_post('cmd_vel',{'linear_x':linear,'angular_z':angular})
                if time.monotonic()>=self.next_trace:
                    self.next_trace=time.monotonic()+.5
                    with self.lock:
                        trace={'time':time.time(),'monotonic':time.monotonic(),'own_state':state,'desired_cmd':{'linear_x':linear,'angular_z':angular},'command_ack':command_ack,'phase':self.me.get('phase'),'navigation':self.nav.snapshot(),'camera':{k:self.status.get(k) for k in ('frame_seq','image_sha256','captured_at','camera_source')},'source_frame_key':self.beacon_seq,'m2_observation':self.beacon,'domain':'participant own sensors/decisions only; no organizer truth'}
                        self.tracefile.write(json.dumps(trace,ensure_ascii=False,allow_nan=False)+'\n');self.tracefile.flush()
                if level_gimbal:self.client.sim_post('gimbal',{'pan':0.,'tilt':desired_tilt,'speed':80.,'acc':20.})
                if fire_request:
                    result=self.client.sim_post('fire',{})
                    if result.get('message')=='fired':
                        with self.lock:self.firing_until=time.monotonic()+5.3;self.aim_stable=0
                    self.log('m2_visual_fire',response=result,observation=self.beacon)
                if state['cmd']!=self.previous_cmd:
                    self.log('match',mode=state['mode'],cmd=state['cmd']);self.previous_cmd=state['cmd']
                    if self.args.policy=='mission' and state['cmd'] in ('start','resume'):
                        self.client.sim_post('gimbal',{'pan':0.,'tilt':0.,'speed':80.,'acc':20.})
                if state['cmd']=='stop':self.finished=True;self.stop_event.set()
            except (ApiError,ValueError,KeyError) as exc:
                with self.lock:
                    self.motion.clear();self.status['sensor_healthy']=False;self.status['transport_error']=str(exc)
                    if self.nav:self.nav.suspend()
                try:self.client.stop()
                except ApiError:pass
            self.publish_status();self.stop_event.wait(max(0.,.1-(time.monotonic()-start)))
    def inference_worker(self):
        os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(__file__).resolve().parents[1]/'training'/'.ultralytics'))
        os.environ.setdefault('YOLO_AUTOINSTALL','false')
        try:
            from ultralytics import YOLO
            from PIL import Image
            verifier=PhotoVerifier();model=YOLO(str(self.weights));classes=[model.names[k] for k in sorted(model.names)]
            previous_perception_state=None
            if classes!=CLASSES:raise ValueError('Deployed class schema does not match six M3 classes')
            self.log('model_initialized',weights_sha256=self.weights_hash,classes=classes)
            with self.lock:self.status['classes']=classes
            while not self.stop_event.is_set():
                started=time.monotonic()
                with self.lock:state=copy.deepcopy(self.state)
                if state is None or not state.get('camera_available'):
                    self.stop_event.wait(.1);continue
                try:
                    jpeg,headers=self.client.sim_get('camera')
                    captured=headers.get('X-Captured-At')
                    if captured and time.time()-datetime.fromisoformat(captured).timestamp()>1.0:
                        with self.lock:self.status['camera_error']='Camera source frame is older than one second'
                        self.stop_event.wait(.1);continue
                    image=Image.open(io.BytesIO(jpeg)).convert('RGB')
                    predicted=model.predict(image,imgsz=640,device='cpu',conf=.35,agnostic_nms=True,verbose=False)[0]
                    detections=[{'class_id':int(b.cls.item()),'type':classes[int(b.cls.item())],'confidence':float(b.conf.item()),
                                 'xyxy':[float(x) for x in b.xyxy[0].tolist()]} for b in predicted.boxes]
                    for detection in detections:
                        detection['photo']=verifier.classify(np.asarray(image),detection['xyxy'],color_order='RGB')
                    target_beacon=select_beacon(beacon_candidates(jpeg),state,self.pending_order)
                    image_hash=hashlib.sha256(jpeg).hexdigest()
                    decision_wall=time.time()
                    captured_wall=datetime.fromisoformat(captured).timestamp() if captured else None
                    motion_evidence=moving_evidence(state,previous_perception_state,captured_wall,decision_wall)
                    previous_perception_state=copy.deepcopy(state)
                    receipts=[]
                    with self.lock:
                        self.frame_seq+=1;seq=self.frame_seq
                        self.beacon=target_beacon;self.beacon_at=time.monotonic()-max(0.,time.time()-datetime.fromisoformat(captured).timestamp()) if captured else 0.;self.beacon_seq=source_frame_key(headers,image_hash)
                        self.status['m2_visual']=target_beacon
                        if target_beacon and self.nav and not self.nav.m2_done and not self.nav.official_capture:
                            x,y,_=pose_from_odom(state);tx,ty=target_beacon['position'];distance=math.hypot(tx-x,ty-y)
                            if distance>.12:self.nav.consider_target((tx+(x-tx)*.65/distance,ty+(y-ty)*.65/distance),(tx,ty),source_frame_key(headers,image_hash))
                        if self.nav and self.nav.coverage_started and getattr(self,'coverage_store_token',None)!=(self.nav.coverage_sector,self.nav.coverage_generation):
                            self.coverage_store_sector=self.nav.coverage_sector;self.coverage_store_token=(self.nav.coverage_sector,self.nav.coverage_generation)
                            # Existing confirmed evidence from this and other blocks is retained.
                        for detection in detections:
                            location=detection_position(detection,state,image.width)
                            decision=photo_decision(detection)
                            receipt={'prediction':detection,'position':location,'decision':decision,'offered_to_store':False}
                            if location and decision['accepted'] and self.pending_order:
                                targeted=self.nav and self.nav.reviewing_location(location)
                                stationary=targeted and self.nav.mission_stage=='m3_observe' and self.nav.coverage_settled and time.monotonic()-self.nav.coverage_settled>.6 and math.dist(location,self.nav.coverage_center)<.36 and captured and datetime.fromisoformat(captured).timestamp()>=getattr(self.nav,'coverage_settled_wall',math.inf)+.25
                                # Pending approaches retain sound slow-pass observations.
                                # Motion bounds plus three-frame spatial confirmation remain.
                                allowed=not targeted or stationary or motion_evidence['accepted']
                                receipt.update(targeted=bool(targeted),stationary=bool(stationary),gate='legacy_outside_review' if not targeted else 'stationary' if stationary else motion_evidence['reason'])
                                if allowed:
                                    hits_before=sum(track['hits'] for track in self.observations.tracks)
                                    receipt['observed_monotonic']=time.monotonic()
                                    self.observations.add(decision['type'],*location,decision['confidence'],source_frame_key(headers,image_hash),observed_at=receipt['observed_monotonic'])
                                    receipt.update(offered_to_store=True,new_track_hit=sum(track['hits'] for track in self.observations.tracks)>hits_before)
                                if self.nav:
                                    self.nav.observe_panel(location,state,source_frame_key(headers,image_hash))
                                    if stationary:
                                        self.nav.coverage_frames.add(source_frame_key(headers,image_hash))
                            receipts.append(receipt)
                        # Exact input replaces ambiguous later status snapshots for replay.
                        with (self.output/'perception-input.jsonl').open('a') as evidence:
                            evidence.write(json.dumps({'frame_seq':seq,'image_sha256':image_hash,'captured_at':captured,'decision_wall':decision_wall,'source_frame_key':source_frame_key(headers,image_hash),'input_state':state,'motion_evidence':motion_evidence,'order':self.pending_order,'navigation':self.nav.snapshot() if self.nav else None,'receipts':receipts,'report':self.observations.report()},ensure_ascii=False,allow_nan=False)+'\n')
                        self.loaded=True;self.frame_at=time.monotonic()
                        frame_name=f'frame-{seq:08d}.jpg';(self.output/frame_name).write_bytes(jpeg)
                        temp=self.output/'.frame.jpg';temp.write_bytes(jpeg);os.replace(temp,self.output/'frame.jpg')
                        packet={'frame_seq':seq,'image_sha256':image_hash,'weights_sha256':self.weights_hash,'predictions':detections,
                            'frame_path':frame_name,'received_at':time.time(),'inference_seconds':time.monotonic()-started,'inference_ms':(time.monotonic()-started)*1000,'captured_at':headers.get('X-Captured-At'),
                            'camera_source':headers.get('X-Camera',headers.get('X-Camera-Backend',headers.get('X-Physics-Backend','server own sensor')))}
                        atomic_json(self.output/'predictions.json',packet)
                        self.status.update(packet);self.status.update(status='running',loaded=True,ready=sensors_healthy(state))
                        for old in self.output.glob('frame-*.jpg'):
                            if old.name< f'frame-{max(0,seq-3):08d}.jpg':old.unlink(missing_ok=True)
                    self.publish_status()
                except ApiError as exc:
                    with self.lock:self.status['camera_error']=str(exc)
                self.stop_event.wait(max(0.,.2-(time.monotonic()-started)))
        except Exception as exc:
            self.error=str(exc);self.log('inference_failed',error=str(exc))
            with self.lock:self.loaded=False;self.motion.clear();self.status.update(status='failed',loaded=False,ready=False,error=str(exc))
            self.stop_event.set();self.publish_status()
    def target_worker(self):
        # Documented /target long polling. A separate thread keeps me, camera
        # and motion watchdogs fresh while waiting for the official M5 order.
        if self.args.mode=='practice':return
        while not self.stop_event.wait(.1):
            with self.lock:running=self.me.get('phase')=='running'
            if not running:continue
            try:
                response=self.client.report_get('target',wait=2)
                if not response.get('accepted'):continue
                objective=response['order']['objective']
                with self.lock:
                    if self.me.get('phase')!='running':continue
                    if objective!=self.pending_objective:
                        if self.nav:self.nav.set_objective(objective)
                        self.pending_objective=objective;self.status['objective']=response['order']
                self.log('m5_longpoll_received',order=response['order'])
                return  # Existing polling remains a fallback for later changes.
            except ApiError as exc:
                self.log('m5_longpoll_retry',error=str(exc))
            except (KeyError,TypeError,ValueError) as exc:
                self.log('m5_longpoll_invalid',error=str(exc))

    def http_worker(self):
        next_ready=0.;next_report=0.;next_drone=0.;order_done=False;target_done=False;last_detection=None
        last_order_text=None;pending_interpretation=None;next_order=0.;next_target=0.
        while not self.stop_event.wait(.2):
            try:
                if self.args.mode=='practice':me={'phase':'running','ready':True}
                else:me=self.client.report_get('me')
                with self.lock:self.me=me;self.me_at=time.monotonic();self.status['server_ready']=bool(me.get('ready'));state=copy.deepcopy(self.state)
                if me['phase']=='finished':self.finished=True;self.stop_event.set();continue
                if self.args.mode!='practice' and me['phase']=='ready' and self.loaded and state and sensors_healthy(state) and not me['ready'] and time.monotonic()>=next_ready:
                    next_ready=time.monotonic()+2;self.client.report_post('ready',{})
                    verified=self.client.report_get('me').get('ready');self.log('ready_ack',verified=verified,weights_sha256=self.weights_hash,frame_seq=self.frame_seq)
                if self.args.mode!='practice' and me['phase']=='running':
                    if time.monotonic()>=next_order:
                        next_order=time.monotonic()+1.
                        response=self.client.report_get('order',wait=0)
                        if response.get('accepted'):
                            text=response['order']['tactical_command']
                            if text!=last_order_text:
                                last_order_text=text;order_done=False;pending_interpretation=None
                                with self.lock:
                                    self.pending_order=None;self.order_error='new order awaiting validation'
                                    if self.nav:self.nav.hold=True
                                    self.motion.clear()
                                self.order_task.submit(text,lambda text=text,camp=me.get('camp'):self.llm.interpret(text,camp,parse_local_order))
                    result=self.order_task.poll(last_order_text) or pending_interpretation
                    if result and not order_done:
                        kind,value=result
                        if kind=='ok':
                            pending_interpretation=result
                            reply=self.client.report_post('interpretation',value);accepted=reply.get('accepted') is True
                            with self.lock:
                                self.status['reports']['interpretation']=reply
                                if accepted:
                                    self.pending_order=value;self.order_error=None
                                    if self.nav:
                                        self.nav.apply_order(value)
                                        if self.pending_objective:self.nav.set_objective(self.pending_objective)
                                else:self.order_error='interpretation rejected; motion held'
                            order_done=True;pending_interpretation=None;self.log('m1_llm_result',mode=self.llm.mode,accepted=accepted,received=reply.get('received'))
                        else:
                            self.order_error=value;order_done=True;self.log('m1_llm_failed',error=value,mode=self.llm.mode)
                        with self.lock:self.status['order_error']=self.order_error
                    if time.monotonic()>=next_target:
                        next_target=time.monotonic()+2.
                        response=self.client.report_get('target',wait=0)
                        if response.get('accepted'):
                            objective=response['order']['objective']
                            with self.lock:
                                if objective!=self.pending_objective:
                                    if self.nav:
                                        self.nav.set_objective(objective)
                                        if self.nav.mode=='hold' and self.nav.reason=='survey attempts ended; awaiting official objective':self.nav.mode='track'
                                    self.pending_objective=objective
                                    self.status['objective']=response['order']
                                    self.log('m5_received',order=response['order'])
                            target_done=True
                    if time.monotonic()>=next_report:
                        next_report=time.monotonic()+2
                        with self.lock:
                            sectors=m3_report_snapshot(self.observations,self.nav.camp if self.nav else None,self.pending_order)
                        signature=json.dumps(sectors,sort_keys=True)
                        if sectors and signature!=last_detection:
                            reply=self.client.report_post('detection',{'sectors':sectors})
                            if reply.get('accepted'):last_detection=signature
                            with self.lock:self.status['reports']['detection']=reply
                            self.log('m3_observations_report',accepted=reply.get('accepted'),received=reply.get('received'),coverage='partial observed faces only')
                if me['phase']=='reporting':
                    self.client.stop()
                    if not self.risk_started:
                        self.risk_started=True;self.reporting_deadline=time.monotonic()+25.
                        with self.lock:
                            _,metadata=self.drone.ranking() if self.drone else (None,{'insufficient_observations':True})
                            self.status['risk_estimate']=metadata
                        self.risk_task.submit('m6',lambda metadata=metadata,camp=me.get('camp'):self.llm.rank(metadata,camp,self.reporting_deadline-1.))
                    result=self.risk_task.poll('m6')
                    if result:
                        kind,value=result
                        if kind=='ok' and time.monotonic()<self.reporting_deadline:
                            self.report.begin(value,deadline=self.reporting_deadline);self.log('m6_estimate',mode=self.llm.mode,ranking=value)
                        else:self.report.failed=True;self.log('m6_llm_failed',error=value)
                    if time.monotonic()>=self.reporting_deadline and not self.report.done:self.report.failed=True
                    before=self.report.done;self.report.tick('reporting')
                    if self.report.done and not before:
                        with self.lock:self.status['reports']['situation']={'accepted':True,'received':self.report.payload}
                        self.log('m6_saved',ranking=self.report.payload['ranking'])
                elif me['phase']=='running' and time.monotonic()>=next_drone:
                    next_drone=time.monotonic()+1.
                    try:
                        kwargs={} if self.args.mode=='match' else {'camera':'test2' if self.args.mode=='rehearsal' else 'test1'}
                        jpeg,headers=self.drone_client.report_get('drone',**kwargs)
                        captured=headers.get('X-Captured-At');interval=float(headers.get('X-Frame-Interval-S','1'))
                        next_drone=time.monotonic()+max(1.,interval)
                        age=max(0.,time.time()-datetime.fromisoformat(captured).timestamp()) if captured else None
                        components=drone_components(jpeg)
                        with self.lock:
                            if self.drone and captured and age is not None and age<=max(2.,interval*2):self.drone.update(components,captured)
                            self.status['drone']={'frames':self.drone.frames if self.drone else 0,'captured_at':captured,'age_seconds':age,
                                'frame_interval':interval,'components':components,'source_camera':headers.get('X-Camera'),
                                'stale':age is None or age>max(2.,interval*2),'method':'HSV observation heuristic, not a trained drone detector'}
                            temp=self.output/'.drone.jpg';temp.write_bytes(jpeg);os.replace(temp,self.output/'drone.jpg')
                    except (ApiError,ValueError) as exc:
                        with self.lock:self.status['drone']['error']=str(exc)
            except ApiError as exc:
                with self.lock:self.status['report_error']=str(exc);self.me_at=0.;self.motion.clear()
                if exc.status in (401,422):self.error=str(exc);self.log('fatal_report_error',error=str(exc));self.stop_event.set()
            except Exception as exc:self.error=str(exc);self.log('http_worker_failed',error=str(exc));self.stop_event.set()
            self.publish_status()
    def run(self):
        self.client.sim_post('strategy',{'strategy':'external'});self.client.stop();self.publish_status()
        for target in (self.sensor_worker,self.inference_worker,self.http_worker,self.target_worker):
            thread=threading.Thread(target=target,daemon=True);self.threads.append(thread);thread.start()
        try:
            while not self.stop_event.wait(.2):pass
        finally:
            self.stop_event.set()
            for thread in self.threads:thread.join(timeout=3.)
            try:self.client.stop()
            except ApiError:pass
            with self.lock:self.status.update(status='failed' if self.error else 'stopped',error=self.error,ready=False)
            self.client.close();self.publish_status();self.logfile.close();self.tracefile.close()
        return 1 if self.error else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://127.0.0.1:8765');parser.add_argument('--mission-code',required=True)
    parser.add_argument('--mode',choices=['match','practice','rehearsal'],default='practice')
    parser.add_argument('--weights',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--policy',choices=['observe','mission'],default='observe')
    args=parser.parse_args()
    if not args.weights.is_file():parser.error('deployed weights file not found')
    team=LearnedTeam(args)
    def stop(*_):team.stop_event.set()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    raise SystemExit(team.run())

if __name__=='__main__':main()
