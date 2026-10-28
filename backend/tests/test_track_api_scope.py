from __future__ import annotations
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app import ProjectData, create_server
from backend.auth import DEVELOPMENT_PASSWORD, DEVELOPMENT_USERNAME
from backend.config import get_settings
from backend.authorized_drones import classify_telemetry, register_approved_drone
from backend.headquarters_sectors import save_headquarters_sectors


def _request(base_url, path, method="GET", payload=None, cookie=None):
    headers={}
    body=None
    if payload is not None:
        headers["Content-Type"]="application/json"
        body=json.dumps(payload).encode()
    if cookie: headers["Cookie"]=cookie
    request=Request(base_url+path,data=body,headers=headers,method=method)
    try:
        with urlopen(request,timeout=5) as response:
            return response.status,json.loads(response.read()),response.headers
    except HTTPError as error:
        return error.code,json.loads(error.read()),error.headers


def test_track_api_scopes_sector_reads_and_reviews_by_server_role(tmp_path):
    root=tmp_path
    save_headquarters_sectors(root,{"rows":2,"columns":2,"west":0,"east":4,"south":0,"north":4},1)
    register_approved_drone(str(root),{"device_id":"FC-API-2","device_id_type":"flight_controller_serial","sector_id":"S2","valid_from":"2026-01-01T00:00:00Z","valid_until":"2027-01-01T00:00:00Z"},1)
    points=[
        {"latitude":1.0,"longitude":1.0,"altitude_m":100,"timestamp":"2026-10-24T10:00:00Z"},
        {"latitude":1.0,"longitude":1.0001,"altitude_m":100,"timestamp":"2026-10-24T10:00:05Z"},
        {"latitude":1.0,"longitude":1.0002,"altitude_m":100,"timestamp":"2026-10-24T10:00:10Z"},
    ]
    classify_telemetry(str(root),{"device_id":"FC-API-2","device_id_type":"flight_controller_serial","track_id":"S2-TRACK","points":points})
    data=ProjectData(get_settings(root),paths_override=get_settings().paths)
    server=create_server(project_root=root,project_data=data,port=0,auth_db_path=root/"auth.sqlite3",auth_username=DEVELOPMENT_USERNAME,auth_password=DEVELOPMENT_PASSWORD)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f"http://{server.server_address[0]}:{server.server_address[1]}"
    try:
        account=server.auth_manager.create_sector_account("S1",username="s1@example.test")
        status,_,headers=_request(base,"/api/auth/login","POST",{"username":"s1@example.test","password":account["temporary_password"],"account_type":"sector","sector_number":"S1"})
        assert status==200
        sector_cookie=headers["Set-Cookie"].split(";",1)[0]
        status,_,_=_request(base,"/api/auth/change-password","POST",{"current_password":account["temporary_password"],"new_password":"Sctr-2026!"},sector_cookie)
        assert status==200
        status,payload,_=_request(base,"/api/authorization/tracks",cookie=sector_cookie)
        assert status==200 and payload["tracks"]==[]
        status,_,_=_request(base,f"/api/authorization/tracks/1/S2-TRACK",cookie=sector_cookie)
        assert status==404
        status,_,_=_request(base,"/api/headquarters/tracks/1/S2-TRACK/review","POST",{"decision":"cleared","reason":"Reviewed by HQ"},sector_cookie)
        assert status==403

        status,_,headers=_request(base,"/api/auth/login","POST",{"username":DEVELOPMENT_USERNAME,"password":DEVELOPMENT_PASSWORD})
        assert status==200
        hq_cookie=headers["Set-Cookie"].split(";",1)[0]
        status,payload,_=_request(base,"/api/authorization/tracks",cookie=hq_cookie)
        assert status==200 and len(payload["tracks"])==1

        server.auth_manager.decommission_sector_account("S1","Sector closed for API test")
        status,_,_=_request(base,"/api/authorization/tracks",cookie=sector_cookie)
        assert status==401
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
