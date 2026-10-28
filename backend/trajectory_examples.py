"""Small deterministic synthetic tracks for development checks, never field evidence."""
from __future__ import annotations
import math

BASE = "2026-10-24T10:00:{:02d}Z"

def _point(x_m: float, y_m: float, altitude_m: float, seconds: int) -> dict:
    lat0=3.0
    return {"latitude":lat0+y_m/111132.0,
            "longitude":1.0+x_m/(111320.0*math.cos(math.radians(lat0))),
            "altitude_m":altitude_m,"timestamp":BASE.format(seconds)}

def synthetic_examples() -> dict[str, dict]:
    return {
      "drone_like_straight": {"expected":"drone","points":[_point(x,0,100,t) for t,x in enumerate((0,5,10,15))]},
      "bird_like_turning": {"expected":"bird","points":[_point(0,0,0,0),_point(5,0,1,2),_point(7.5,4.33,2,4),_point(6.6,5.83,3,6),_point(2.27,3.33,4,8)]},
      "hover_like": {"expected":"drone","points":[_point(0,0,100,t) for t in (0,2,4,6)]},
      "insufficient": {"expected":"unknown","points":[_point(0,0,0,0),_point(2,1,1,1)]},
    }
