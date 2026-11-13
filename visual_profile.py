"""Deterministic image stressors, explicitly uncalibrated, never new real data."""
import numpy as np
import cv2

def apply_camera_profile(rgb,environment,seq,drone=False):
 profile=environment.get('profile',environment.get('sensor_profile','mild'))
 if profile in ('ideal','official_only'):return rgb
 rng=np.random.default_rng(int(environment.get('seed',0))+int(seq)*31+(997 if drone else 0))
 stress=profile=='stress';a=rgb.astype(np.float32)
 a*=.92 if not stress else .76
 a+=rng.normal(0,1.4 if not stress else 4.,a.shape)
 if stress:
  h,w=a.shape[:2];fog=np.linspace(.12,.35,h)[:,None,None]
  a=a*(1-fog)+np.array([190,190,185])*fog
  a=cv2.GaussianBlur(a,(3,3),.7)
  if not drone:
   # Uncalibrated radial warp; not an estimate of the venue lens coefficients.
   yy,xx=np.indices((h,w),dtype=np.float32);nx=(xx-w/2)/(w/2);ny=(yy-h/2)/(h/2)
   scale=1-.10*(nx*nx+ny*ny)
   a=cv2.remap(a,(nx*scale*w/2+w/2).astype(np.float32),(ny*scale*h/2+h/2).astype(np.float32),cv2.INTER_LINEAR,borderMode=cv2.BORDER_REFLECT_101)
 return np.clip(a,0,255).astype(np.uint8)
