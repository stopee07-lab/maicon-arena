"""Reproducible sensor perturbations; coefficients are assumptions, not calibration.
Noise depends on seed/slot/measurement index, never call count. Repeated reads of a
cached scan preserve its measurement time. Engine clock is simulation seconds.
"""
import copy
import math
import random
PROFILES={
 'ideal':dict(drift_m_per_s=0.,yaw_rad_per_s=0.,scan_noise_m=0.,scan_dropout=0.,battery_pct_per_s=0.,extras=True),
 'mild':dict(drift_m_per_s=.00015,yaw_rad_per_s=.00008,scan_noise_m=.008,scan_dropout=.015,battery_pct_per_s=95/2400,extras=True),
 'stress':dict(drift_m_per_s=.0007,yaw_rad_per_s=.0003,scan_noise_m=.025,scan_dropout=.12,battery_pct_per_s=95/1500,extras=True),
 'official_only':dict(drift_m_per_s=.00015,yaw_rad_per_s=.00008,scan_noise_m=.008,scan_dropout=.015,battery_pct_per_s=95/2400,extras=False),
}
def get_profile(name):
    if not isinstance(name,str) or name not in PROFILES:raise ValueError('unknown sensor profile')
    return dict(name=name,version='sensor-v2',status='unmeasured simulation assumptions',scan_topic_status='D500 hardware documented; exact topic/rate/specification requires venue verification',**copy.deepcopy(PROFILES[name]))

def odometry(odom,seed,slot,now,profile):
    rng=random.Random(f'odom-v2:{seed}:{slot}')
    theta=rng.uniform(-math.pi,math.pi);speed=profile['drift_m_per_s']*now
    return dict(x=round(odom['x']+speed*math.cos(theta),6),y=round(odom['y']+speed*math.sin(theta),6),yaw=round(math.atan2(math.sin(odom['yaw']+profile['yaw_rad_per_s']*now),math.cos(odom['yaw']+profile['yaw_rad_per_s']*now)),6))

def scan(raw,seed,slot,profile,measured_at):
    out=copy.deepcopy(raw);rng=random.Random(f'scan-v2:{seed}:{slot}:{measured_at:.6f}')
    out['ranges']=[None if value is None or rng.random()<profile['scan_dropout'] else round(max(.02,min(raw.get('range_max',8.),value+rng.gauss(0,profile['scan_noise_m']))),3) for value in raw.get('ranges',[])]
    out.update(measured_at=round(measured_at,6),clock_domain='simulation_seconds',profile=profile['name'],dropout_encoding='null = no measurement; not free space')
    return out
