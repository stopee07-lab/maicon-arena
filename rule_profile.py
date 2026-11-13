"""Versioned provisional rules: never a claim about final event-day scoring."""
import copy
PROFILE_ID='provisional-2026-11-12-v2'
CAPTURE_TIE_POLICY='event times within 1e-8 seconds: lexicographic camp, then base id, then slot; simulation assumption, not official 10ms hardware policy'
PROFILES={PROFILE_ID:{
    'id':PROFILE_ID,'status':'provisional; final organizer/hardware confirmation required',
    'sources':['local API snapshot 2026-11-04','local training PDFs','Q&A snapshot 2026-11-12'],
    'm1':{'target':3,'search_sectors':3,'base':4},'m2':{'assigned':10,'alliance_all':5},
    'm3':{'sector':7,'CM':9},'m4':[0,3,8,12,15], 'm5':10,
    'm6':{'all':15,'first':10,'other':5,'equal_risk_order':'any'},'alliance_bonus':5,
    'duration':300.,'m5_publish_at':240.,'report_duration':30.,
    'capture_tie_policy':CAPTURE_TIE_POLICY,
    'capture_dwell_seconds':0.,'geometry':{'emitter_forward':.07,'emitter_z':.256,'target_receiver_z':.34,'target_receiver_radius':.04,'robot_receiver_z':.256,'robot_receiver_radius':.025,'robot_receiver_forward':.13,'robot_receiver_lateral':.12,'robot_length':.25,'robot_width':.23},
    'policy_support':'Only zero dwell, any equal-risk order, and documented lexicographic capture tie policy are implemented; alternative policies are rejected and remain pending.',
    'geometry_status':'receiver size/height and line location are unmeasured simulation assumptions',
}}
def get_profile(name=PROFILE_ID):
    if not isinstance(name,str) or name not in PROFILES:raise ValueError('unknown rule profile')
    profile=copy.deepcopy(PROFILES[name])
    validate_profile(profile)
    return profile


def validate_profile(profile):
    """Reject unsupported policy semantics instead of silently running defaults.

    Numeric scoring weights are configurable. Alternative dwell/tie algorithms
    require a new implemented and tested rule version; they are not available yet.
    Cheap checks also protect direct mutations of an Engine profile in diagnostics.
    """
    if type(profile.get('capture_dwell_seconds')) not in (int,float) or profile['capture_dwell_seconds']!=0:
        raise ValueError('unsupported capture_dwell_seconds: only 0 seconds is implemented')
    if profile.get('capture_tie_policy')!=CAPTURE_TIE_POLICY:
        raise ValueError('unsupported capture_tie_policy: only the documented lexicographic policy is implemented')
    if profile.get('m6',{}).get('equal_risk_order')!='any':
        raise ValueError('unsupported equal_risk_order: only any ordering of equal-risk opponents is implemented')
