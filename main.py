import docker
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import subprocess

app = FastAPI()
app.mount("/static", StaticFiles(directory="/app/static"), name="static")

client = docker.from_env()

class SDRConfig(BaseModel):
    name: str
    device_index: int = 0
    ppm: int = 0

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
