import docker
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import subprocess
from contextlib import asynccontextmanager
import threading
import time
import json
import urllib.request
import urllib.error
import os

NETWORK_NAME = "openplanetracker_sdr_network"

def self_connect_to_network():
    try:
        client = docker.from_env()
        
        # Preverimo, če omrežje že obstaja, sicer ga ustvarimo
        try:
            network = client.networks.get(NETWORK_NAME)
        except docker.errors.NotFound:
            print(f"[Sentinel] Omrežje {NETWORK_NAME} ne obstaja. Ustvarjam...")
            network = client.networks.create(NETWORK_NAME, driver="bridge")
        
        # Poiščemo samega sebe (vsebnik sentinel)
        try:
            self_container = client.containers.get("sentinel")
            
            # Preverimo, če smo že del tega omrežja
            networks = self_container.attrs["NetworkSettings"]["Networks"]
            if NETWORK_NAME not in networks:
                print(f"[Sentinel] Povezujem vsebnik 'sentinel' v omrežje {NETWORK_NAME}...")
                network.connect(self_container)
                print("[Sentinel] Uspešno povezan!")
            else:
                print(f"[Sentinel] Vsebnik je že povezan v omrežje {NETWORK_NAME}.")
        except docker.errors.NotFound:
            print("[Sentinel] Opozorilo: Vsebnika z imenom 'sentinel' ni mogoče najti. Tečeš izven Dockerja?")
            
    except Exception as e:
        print(f"[Sentinel] Napaka pri samodejnem povezovanju v omrežje: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Koda, ki se izvede OB ZAGONU aplikacije
    self_connect_to_network()
    yield
    # Koda, ki se izvede OB USTAVITVI aplikacije (prazno)

app = FastAPI()
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

client = docker.from_env()
SERVER_INTERNAL_URL = os.getenv("OPT_SERVER_INTERNAL_URL", "http://openplanetracker-server:8080")


def read_shared_volume_file(filename: str) -> str | None:
    try:
        output = client.containers.run(
            "alpine:3.18",
            command=["sh", "-c", f"cat /config/{filename}"],
            volumes={"opt_config_data": {"bind": "/config", "mode": "ro"}},
            remove=True,
        )
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="ignore")
        return str(output).strip()
    except Exception:
        return None


def get_admin_token() -> str | None:
    return read_shared_volume_file("admin_token.txt")


def get_shared_psk() -> str | None:
    return read_shared_volume_file("shared_psk.txt")


def _server_admin_post(path: str, payload: dict):
    admin_token = get_admin_token()
    if not admin_token:
        raise HTTPException(status_code=500, detail="Admin token not available")

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SERVER_INTERNAL_URL}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Admin-Token": admin_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"status": "ok"}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=e.code, detail=detail or str(e))


def _server_admin_get(path: str):
    admin_token = get_admin_token()
    if not admin_token:
        raise HTTPException(status_code=500, detail="Admin token not available")

    req = urllib.request.Request(
        f"{SERVER_INTERNAL_URL}{path}",
        headers={"X-Admin-Token": admin_token},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=e.code, detail=detail or str(e))


def _container_version_info(container_name: str, image_ref: str):
    """Return current/latest image metadata for a container image."""
    current = {
        "container": container_name,
        "deployed": False,
        "running": False,
        "current_image_id": None,
        "current_image_tag": None,
        "latest_image_id": None,
        "latest_image_tag": image_ref,
        "update_available": False,
        "error": None,
    }

    try:
        c = client.containers.get(container_name)
        current["deployed"] = True
        current["running"] = c.status == "running"
        current["current_image_id"] = c.image.id
        current["current_image_tag"] = c.image.tags[0] if c.image.tags else c.image.short_id
    except docker.errors.NotFound:
        pass
    except Exception as e:
        current["error"] = str(e)

    try:
        latest = client.images.pull(image_ref)
        current["latest_image_id"] = latest.id
        current["update_available"] = bool(current["current_image_id"] and current["current_image_id"] != latest.id)
    except Exception as e:
        current["error"] = str(e) if not current["error"] else current["error"]

    return current

class SDRConfig(BaseModel):
    name: str
    device_index: int = 0
    ppm: int = 0


class ExternalConnectionsToggle(BaseModel):
    enabled: bool = True


class PeerRegistration(BaseModel):
    peer_name: str
    peer_url: str | None = None


class PushConfig(BaseModel):
    target_url: str
    enabled: bool = True
    interval_seconds: int = 2

@app.get("/")
def get_dashboard():
    with open("/app/static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/usb-devices")
def get_usb_devices():
    try:
        output = subprocess.check_output(["lsusb"], text=True)
        devices = []
        rtlsdr_count = 0
        for line in output.split('\n'):
            if not line.strip():
                continue
            parts = line.split(":", 1)
            name = parts[1].strip() if len(parts) > 1 else line
            
            # Simple heuristic for SDR detection
            is_sdr = "Realtek" in name or "RTL2838" in name or "DVB-T" in name or "SDR" in name
            dev_index = rtlsdr_count if is_sdr else -1
            
            if is_sdr:
                rtlsdr_count += 1
            
            used_indices = get_used_device_indices()
            
            devices.append({
                "label": f"{line.split(':')[0].strip()} - {name}",
                "is_sdr": is_sdr,
                "readsb_index": dev_index,
                "in_use": dev_index in used_indices
            })
        return devices
    except Exception as e:
        return []

def get_used_device_indices():
    used_indices = set()
    try:
        containers = client.containers.list(all=True, filters={"name": "readsb-"})
        for c in containers:
            cmd = c.attrs.get("Config", {}).get("Cmd", [])
            if cmd:
                for arg in cmd:
                    if arg.startswith("--device="):
                        try:
                            used_indices.add(int(arg.split("=")[1]))
                        except ValueError:
                            pass
    except Exception:
        pass
    return used_indices

@app.get("/api/sdrs")
def list_sdrs():
    containers = client.containers.list(all=True, filters={"name": "readsb-"})
    result = []
    for c in containers:
        status = c.status
        ip = "unknown"
        if status == "running":
            networks = c.attrs["NetworkSettings"]["Networks"]
            if "openplanetracker_sdr_network" in networks:
                ip = networks["openplanetracker_sdr_network"]["IPAddress"]
            elif "sdr_network" in networks:
                ip = networks["sdr_network"]["IPAddress"]
        result.append({
            "name": c.name,
            "status": status,
            "ip": ip,
            "id": c.short_id
        })
    return result

@app.post("/api/sdrs")
def create_sdr(config: SDRConfig):
    container_name = f"readsb-{config.name}"
    
    used_indices = get_used_device_indices()
    if config.device_index in used_indices:
        raise HTTPException(status_code=400, detail=f"SDR device index {config.device_index} is already assigned to a container.")

    try:
        # Check if exists
        client.containers.get(container_name)
        raise HTTPException(status_code=400, detail="SDR already exists")
    except docker.errors.NotFound:
        pass

    try:
        c = client.containers.run(
            "mikenye/readsb:latest",
            name=container_name,
            detach=True,
            tty=True,
            privileged=True,
            restart_policy={"Name": "unless-stopped"},
            devices=["/dev/bus/usb:/dev/bus/usb"],
            environment=["TZ=Europe/Ljubljana"],
            network="openplanetracker_sdr_network", # Default compose network name prefix, might need adjustment depending on project name
            command=[
                "--dcfilter",
                f"--device-type=rtlsdr",
                f"--device={config.device_index}", # Pass SDR index correctly using --device
                "--fix",
                "--json-location-accuracy=2",
                "--lat=46.0443",
                "--lon=14.4860",
                "--modeac",
                f"--ppm={config.ppm}",
                "--net",
                "--stats-every=3600",
                "--quiet",
                "--write-json=/run/readsb"
            ]
        )
        return {"status": "created", "id": c.short_id, "name": c.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/sdrs/{name}")
def delete_sdr(name: str):
    try:
        c = client.containers.get(name)
        c.stop(timeout=2)
        c.remove()
        return {"status": "deleted"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="SDR not found")

@app.get("/api/check-update")
def check_update():
    try:
        # Check running container image ID
        c = client.containers.get("live-viewer")
        running_id = c.image.id
        
        # Pull the absolute newest latest from Docker Hub
        print("Polling Docker Hub for UI updates...")
        new_image = client.images.pull("mmatvoz/openplanetracker-ui:latest")
        
        return {"update_available": running_id != new_image.id}
    except Exception as e:
        return {"update_available": False, "error": str(e)}


@app.get("/api/versions")
def get_versions():
    """Report current and latest Sentinel/Server container versions."""
    return {
        "sentinel": _container_version_info("sentinel", "mmatvoz/openplanetracker-sentinel:latest"),
        "server": _container_version_info("openplanetracker-server", "mmatvoz/openplanetracker-server:latest"),
    }

@app.post("/api/update-ui")
def update_ui():
    try:
        c = client.containers.get("live-viewer")
        c.stop()
        c.remove()
    except:
        pass
        
    try:
        import orchestrator
        orchestrator.deploy_ui()
        return {"status": "updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/update-server")
def update_server():
    """One-click pull + redeploy the server container."""
    try:
        import orchestrator

        threading.Thread(target=orchestrator.redeploy_server, daemon=True).start()
        return {"status": "updating"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/update-sentinel")
def update_sentinel():
    """One-click pull + redeploy the Sentinel container itself."""
    try:
        import orchestrator

        def _delayed_update():
            time.sleep(1)
            orchestrator.redeploy_sentinel()

        threading.Thread(target=_delayed_update, daemon=True).start()
        return {"status": "updating"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/state")
def admin_state():
    """Expose server admin state and shared key to the Sentinel admin UI."""
    server_state = _server_admin_get("/admin/state")
    return {
        "shared_psk": get_shared_psk(),
        "server": server_state,
    }


@app.post("/admin/external-connections/enable")
def admin_enable_external_connections(cfg: ExternalConnectionsToggle):
    return _server_admin_post("/admin/external-connections/enable", cfg.model_dump())


@app.post("/admin/peers/register")
def admin_register_peer(peer: PeerRegistration):
    payload = peer.model_dump()
    payload["shared_key"] = get_shared_psk()
    return _server_admin_post("/admin/peers/register", payload)


@app.post("/admin/push-config")
def admin_push_config(cfg: PushConfig):
    payload = cfg.model_dump()
    payload["shared_key"] = get_shared_psk()
    return _server_admin_post("/admin/push-config", payload)


@app.get("/api/server")
def server_status():
    try:
        c = client.containers.get("openplanetracker-server")
        return {"deployed": True, "status": c.status}
    except docker.errors.NotFound:
        return {"deployed": False}


@app.post("/api/server/deploy")
def deploy_server(expose_port: int | None = None):
    try:
        import orchestrator
        orchestrator.deploy_server(expose_port)
        return {"status": "deploying"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/server")
def remove_server():
    try:
        c = client.containers.get("openplanetracker-server")
        c.stop(timeout=2)
        c.remove()
        return {"status": "removed"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Server not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
