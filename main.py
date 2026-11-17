import os
import asyncio
import json
import logging
import inspect

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sonoff_manager import SonoffManager
import paho.mqtt.client as mqtt


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

log = logging.getLogger("sonoff-fastapi")


# ============================================================
# EWELINK CONFIG
# ============================================================

USERNAME = os.getenv(
    "EWELINK_USERNAME",
    "samba1.benderang@gmail.com"
)

PASSWORD = os.getenv(
    "EWELINK_PASSWORD",
    "aaa111BC"
)

COUNTRY_CODE = os.getenv(
    "EWELINK_COUNTRY_CODE",
    "+62"
)


# ============================================================
# MQTT CONFIG
# ============================================================

MQTT_HOST = os.getenv(
    "MQTT_HOST",
    "192.168.1.66"
)

MQTT_PORT = int(
    os.getenv(
        "MQTT_PORT",
        "1883"
    )
)

MQTT_TOPIC = "/sonoff_benderang"

MQTT_USERNAME = os.getenv(
    "benderang",
    "benderang"
)

MQTT_PASSWORD = os.getenv(
    "benderangmqtt",
    "benderangmqtt"
)


# ============================================================
# MQTT CLIENT
# ============================================================

mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id="sonoff-benderang"
)


# ============================================================
# GLOBAL
# ============================================================

manager: SonoffManager | None = None

latest_devices: list[dict] = []

data_lock = asyncio.Lock()


# ============================================================
# MQTT CALLBACK
# ============================================================

def mqtt_on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info(
            "MQTT CONNECTED | %s:%s",
            MQTT_HOST,
            MQTT_PORT
        )
    else:
        log.error(
            "MQTT CONNECT FAILED | reason=%s",
            reason_code
        )


def mqtt_on_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties
):
    log.warning(
        "MQTT DISCONNECTED | reason=%s",
        reason_code
    )


mqtt_client.on_connect = mqtt_on_connect
mqtt_client.on_disconnect = mqtt_on_disconnect


# ============================================================
# MQTT PUBLISH
# ============================================================

def publish_mqtt(devices: list[dict]):
    try:
        payload = {
            "event": "sonoff_data",
            "devices": devices
        }

        payload_json = json.dumps(
            payload,
            ensure_ascii=False
        )

        result = mqtt_client.publish(
            MQTT_TOPIC,
            payload_json,
            qos=0,
            retain=True
        )

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            log.info(
                "MQTT PUBLISH OK | topic=%s | devices=%d",
                MQTT_TOPIC,
                len(devices)
            )
        else:
            log.error(
                "MQTT PUBLISH FAILED | rc=%s",
                result.rc
            )

    except Exception:
        log.exception(
            "MQTT publish error"
        )


# ============================================================
# NORMALIZE SONOFF DATA
# ============================================================

def normalize_device(dev: dict) -> dict:
    params = dev.get("params") or {}
    switches = params.get("switches") or []
    channels = []

    for ch in switches:
        if (
            not isinstance(ch, dict)
            or ch.get("outlet") is None
        ):
            continue

        try:
            outlet = int(
                ch["outlet"]
            )
        except (
            TypeError,
            ValueError
        ):
            continue

        def num(key):
            try:
                return float(
                    params.get(
                        key,
                        0
                    ) or 0
                )
            except (
                TypeError,
                ValueError
            ):
                return 0.0

        channels.append({
            "outlet": outlet,
            "state": str(
                ch.get(
                    "switch",
                    "unknown"
                )
            ).lower(),
            "power_w": num(
                f"actPow_{outlet:02d}"
            ),
            "apparent_power_va": num(
                f"apparentPow_{outlet:02d}"
            ),
            "current_a": num(
                f"current_{outlet:02d}"
            ),
            "reactive_power_var": num(
                f"reactPow_{outlet:02d}"
            ),
            "voltage_v": num(
                f"voltage_{outlet:02d}"
            ),
            "energy_kwh": num(
                f"getKwh_{outlet:02d}"
            )
        })

    return {
        "name": dev.get(
            "name"
        ),
        "device_id": dev.get(
            "deviceid"
        ),
        "online": bool(
            dev.get(
                "online",
                False
            )
        ),
        "product_model": dev.get(
            "productModel"
        ),
        "brand": dev.get(
            "brandName"
        ),
        "parent_id": params.get(
            "parentid"
        ),
        "channels": channels
    }


# ============================================================
# DISCOVER DEVICES
# ============================================================

async def discover():
    global latest_devices

    if manager is None:
        raise RuntimeError(
            "Sonoff manager belum login"
        )

    raw = await manager.discover_switches()

    if not isinstance(
        raw,
        list
    ):
        raw = list(
            raw or []
        )

    normalized = [
        normalize_device(d)
        for d in raw
        if isinstance(
            d,
            dict
        )
    ]

    async with data_lock:
        latest_devices = normalized

    publish_mqtt(
        normalized
    )

    return normalized


# ============================================================
# CONTROL SONOFF
# ============================================================

async def call_control_method(
    device_id: str,
    outlet: int,
    state: str
):
    if manager is None:
        raise RuntimeError(
            "Sonoff manager belum login"
        )

    if state == "on":
        result = await manager.turn_on(
            device_id,
            str(outlet)
        )
    else:
        result = await manager.turn_off(
            device_id,
            str(outlet)
        )

    return {
        "device_id": device_id,
        "outlet": outlet,
        "state": state,
        "result": result
    }


# ============================================================
# BACKGROUND MQTT PUBLISHER WORKER
# ============================================================

async def background_mqtt_publisher():
    """Looping background task untuk fetch & publish MQTT otomatis."""
    while True:
        try:
            if manager is not None:
                await discover()
                log.info("Background MQTT publish sukses.")
            else:
                log.warning("Background worker: Manager belum siap / belum login.")
        except Exception:
            log.exception("Error pada background MQTT publisher")
        
        await asyncio.sleep(5)


# ============================================================
# FASTAPI LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager

    # MQTT Connect
    try:
        if MQTT_USERNAME:
            mqtt_client.username_pw_set(
                MQTT_USERNAME,
                MQTT_PASSWORD
            )

        mqtt_client.connect(
            MQTT_HOST,
            MQTT_PORT,
            60
        )

        mqtt_client.loop_start()

        log.info(
            "MQTT starting | %s:%s | topic=%s",
            MQTT_HOST,
            MQTT_PORT,
            MQTT_TOPIC
        )

    except Exception:
        log.exception(
            "MQTT connection failed"
        )

    # eWeLink Login
    manager = SonoffManager()

    try:
        await manager.login(
            username=USERNAME,
            password=PASSWORD,
            country_code=COUNTRY_CODE
        )
        log.info("eWeLink login OK")

    except Exception:
        log.exception("Login eWeLink gagal")
        manager = None

    # Start Background Task
    bg_task = asyncio.create_task(background_mqtt_publisher())

    yield

    # Stop Background Task
    bg_task.cancel()
    try:
        await bg_task
    except asyncio.CancelledError:
        pass

    # Close eWeLink
    if manager:
        close_fn = getattr(
            manager,
            "close",
            None
        )

        if close_fn:
            result = close_fn()

            if inspect.isawaitable(
                result
            ):
                await result

    # Close MQTT
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        log.info("MQTT disconnected")
    except Exception:
        pass


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="Sonoff Power Dashboard",
    lifespan=lifespan
)


# ============================================================
# STATIC FILES
# ============================================================

app.mount(
    "/static",
    StaticFiles(
        directory="."
    ),
    name="static"
)


# ============================================================
# REQUEST MODEL
# ============================================================

class SwitchRequest(BaseModel):
    device_id: str
    outlet: int
    state: str


# ============================================================
# HOME
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
async def home():
    return HTML


# ============================================================
# API DEVICES
# ============================================================

@app.get(
    "/api/devices"
)
async def api_devices():
    try:
        return {
            "event": "data",
            "switches": await discover()
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=str(e)
        )


# ============================================================
# API SWITCH
# ============================================================

@app.post(
    "/api/switch"
)
async def api_switch(
    req: SwitchRequest
):
    state = req.state.lower()

    if state not in (
        "on",
        "off"
    ):
        raise HTTPException(
            status_code=400,
            detail="state harus on atau off"
        )

    try:
        result = await call_control_method(
            req.device_id,
            req.outlet,
            state
        )

        await asyncio.sleep(
            0.5
        )

        devices = await discover()

        return {
            "ok": True,
            "control": result,
            "switches": devices
        }

    except Exception as e:
        log.exception(
            "Relay control gagal"
        )

        raise HTTPException(
            status_code=501,
            detail=str(e)
        )


# ============================================================
# HTML DASHBOARD
# ============================================================

HTML = r"""
<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sonoff Power Dashboard - Benderang</title>
<style>
:root{
    --bg:#0b1120;
    --panel:#121c2e;
    --line:#24334c;
    --text:#e8eef8;
    --muted:#8da0bd;
    --green:#22c55e;
    --red:#ef4444;
    --blue:#38bdf8;
    --yellow:#fbbf24;
}
*{box-sizing:border-box;}
body{
    margin:0;
    background:radial-gradient(circle at top,#17243a,#080d17 60%);
    color:var(--text);
    font:14px Inter,Segoe UI,Arial;
}
header{
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:16px 5%;
    border-bottom:1px solid var(--line);
    background:#0b1120ee;
    backdrop-filter:blur(8px);
    position:sticky;
    top:0;
    z-index:100;
}
.brand{display:flex;align-items:center;gap:14px;}
.logo-img{
    width:90px;
    height:90px;
    object-fit:contain;
    border-radius:10px;
    background:#fff;
    padding:2px;
    box-shadow:0 4px 14px rgba(56,189,248,0.2);
}
h1{font-size:20px;margin:0;letter-spacing:.02em;}
small,.muted{color:var(--muted);}
.pill{
    padding:7px 12px;
    border:1px solid var(--line);
    border-radius:20px;
    font-size:12px;
    background:#121c2e;
}
main{max-width:1400px;margin:auto;padding:24px 5%;}
.summary{
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:14px;
    margin-bottom:22px;
}
.card,.device{
    background:linear-gradient(145deg,#142039,#101827);
    border:1px solid var(--line);
    border-radius:16px;
    padding:18px;
    box-shadow:0 12px 30px #0002;
}
.metric{font-size:25px;font-weight:700;margin-top:8px;}
.label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;}
.devices{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
    gap:16px;
}
.device h2{font-size:16px;margin:0 0 8px;}
.devicehead{display:flex;justify-content:space-between;gap:8px;align-items:center;}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;}
.online{background:var(--green);}
.offline{background:var(--red);}
.channel{
    margin-top:15px;
    padding:14px;
    background:#0b1322;
    border:1px solid #22314a;
    border-radius:12px;
}
.chrow{display:flex;justify-content:space-between;align-items:center;}
.chname{font-weight:650;}
.state{font-size:11px;color:var(--muted);margin-top:4px;}
.btn{
    border:0;
    border-radius:9px;
    padding:9px 15px;
    font-weight:700;
    cursor:pointer;
    color:white;
    min-width:78px;
    transition:opacity .2s;
}
.btn.on{background:var(--red);}
.btn.off{background:#16a34a;}
.btn:disabled{opacity:.45;cursor:not-allowed;}
.stats{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:8px;
    margin-top:12px;
}
.stat{background:#111c2e;border-radius:8px;padding:9px;}
.stat b{display:block;margin-top:5px;font-size:14px;}
.stat span{font-size:10px;color:var(--muted);}
footer{padding:24px;text-align:center;color:var(--muted);}
.empty{padding:30px;border:1px dashed var(--line);border-radius:14px;text-align:center;}
@media(max-width:700px){
    .summary{grid-template-columns:repeat(2,1fr);}
    header{padding:14px 4%;}
    main{padding:18px 4%;}
    .metric{font-size:21px;}
}
</style>
</head>
<body>

<header>
<div class="brand">
<img src="/static/benderang.jpg" class="logo-img" alt="Benderang Logo">
<div>
<h1>BENDERANG POWER MONITOR</h1>
<small>4-Channel Smart Power Dashboard</small>
</div>
</div>
<div id="connection" class="pill">● Menghubungkan...</div>
</header>

<main>
<div class="summary">
<div class="card"><div class="label">Total Power</div><div class="metric" id="totalW">0 W</div></div>
<div class="card"><div class="label">Total Current</div><div class="metric" id="totalA">0 A</div></div>
<div class="card"><div class="label">Average Voltage</div><div class="metric" id="totalV">0 V</div></div>
<div class="card"><div class="label">Active Channels</div><div class="metric" id="active">0 / 4</div></div>
</div>
<div id="devices" class="devices"><div class="empty">Mengambil data Sonoff...</div></div>
</main>

<footer>Benderang • FastAPI • eWeLink • MQTT</footer>

<script>
const $=id=>document.getElementById(id);
const fmt=(n,d=2)=>Number(n||0).toFixed(d);

function setConn(ok,msg){
    $('connection').textContent='● '+msg;
    $('connection').style.color=ok?'#22c55e':'#ef4444';
}

async function load(){
    try{
        const r=await fetch('/api/devices');
        if(!r.ok)throw new Error(await r.text());
        const data=await r.json();
        render(data.switches||[]);
        setConn(true,'ONLINE');
    }catch(e){
        setConn(false,'API ERROR');
        console.error(e);
    }
}

function render(devs){
    let W=0,A=0,V=[],active=0,total=0;
    devs.forEach(d=>(d.channels||[]).forEach(c=>{
        W+=+c.power_w||0;
        A+=+c.current_a||0;
        if(+c.voltage_v)V.push(+c.voltage_v);
        total++;
        if(c.state==='on')active++;
    }));

    $('totalW').textContent=fmt(W)+' W';
    $('totalA').textContent=fmt(A)+' A';
    $('totalV').textContent=fmt(V.length?V.reduce((a,b)=>a+b,0)/V.length:0)+' V';
    $('active').textContent=active+' / '+total;

    const root=$('devices');
    root.innerHTML='';
    if(!devs.length){
        root.innerHTML='<div class="empty">Tidak ada perangkat ditemukan.</div>';
        return;
    }

    devs.forEach(d=>{
        const box=document.createElement('section');
        box.className='device';
        box.innerHTML=`
            <div class="devicehead">
                <h2>${esc(d.name||'Sonoff')}</h2>
                <span class="pill"><i class="dot ${d.online?'online':'offline'}"></i>${d.online?'Online':'Offline'}</span>
            </div>
            <small>${esc(d.product_model||'')} • ${esc(d.device_id||'')}</small>
        `;

        (d.channels||[]).forEach(c=>{
            const on=c.state==='on';
            const ch=document.createElement('div');
            ch.className='channel';
            ch.innerHTML=`
                <div class="chrow">
                    <div>
                        <div class="chname">Channel ${c.outlet+1}</div>
                        <div class="state">${on?'● ON':'○ OFF'} • Outlet ${c.outlet}</div>
                    </div>
                    <button class="btn ${on?'on':'off'}" ${!d.online?'disabled':''}>${on?'TURN OFF':'TURN ON'}</button>
                </div>
                <div class="stats">
                    <div class="stat"><span>POWER</span><b>${fmt(c.power_w)} W</b></div>
                    <div class="stat"><span>VOLTAGE</span><b>${fmt(c.voltage_v)} V</b></div>
                    <div class="stat"><span>CURRENT</span><b>${fmt(c.current_a,3)} A</b></div>
                    <div class="stat"><span>ENERGY</span><b>${fmt(c.energy_kwh,3)} kWh</b></div>
                    <div class="stat"><span>APPARENT</span><b>${fmt(c.apparent_power_va)} VA</b></div>
                    <div class="stat"><span>REACTIVE</span><b>${fmt(c.reactive_power_var)} var</b></div>
                </div>
            `;
            ch.querySelector('button').onclick=()=>control(d.device_id,c.outlet,on?'off':'on');
            box.appendChild(ch);
        });
        root.appendChild(box);
    });
}

async function control(id,outlet,state){
    if(!confirm(`Yakin ${state.toUpperCase()} Channel ${outlet+1}?`))return;
    try{
        setConn(true,'MENGIRIM COMMAND...');
        const r=await fetch('/api/switch',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({device_id:id,outlet,state})
        });
        const j=await r.json();
        if(!r.ok)throw new Error(j.detail||'Command gagal');
        render(j.switches||[]);
        setConn(true,'ONLINE');
    }catch(e){
        alert('Kontrol gagal: '+e.message);
        setConn(false,'CONTROL ERROR');
    }
}

function esc(s){
    return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

load();
setInterval(load,2500);
</script>
</body>
</html>
"""


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )