import docker
import secrets
import os

try:
    client = docker.from_env()
except Exception as e:
    client = None
    print(f"Warning: Docker unavailable in orchestrator: {e}")
NETWORK_NAME = "openplanetracker_sdr_network"


def _read_shared_volume_file(filename: str) -> str | None:
    """Read a file from the shared opt_config_data volume via a short-lived container."""
    if client is None:
        return None
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


def _write_shared_volume_file(filename: str, value: str) -> None:
    """Write a file into the shared opt_config_data volume via a short-lived container."""
    if client is None:
        raise RuntimeError("Docker unavailable")
    safe_value = value.replace("'", "'\\''")
    client.containers.run(
        "alpine:3.18",
        command=["sh", "-c", f"mkdir -p /config && printf '%s' '{safe_value}' > /config/{filename}"],
        volumes={"opt_config_data": {"bind": "/config", "mode": "rw"}},
        remove=True,
    )


def ensure_shared_psk() -> str:
    """Ensure a shared pre-shared key exists for Sentinel/Server coordination."""
    existing = _read_shared_volume_file("shared_psk.txt")
    if existing:
        return existing
    shared_psk = secrets.token_urlsafe(32)
    try:
        _write_shared_volume_file("shared_psk.txt", shared_psk)
    except Exception as e:
        print(f"Warning writing shared_psk: {e}")
    return shared_psk

def init_infrastructure():
    print("Initializing Infrastructure...")
    if client is None:
        print("Docker unavailable; skipping infrastructure initialization.")
        return
    try:
        client.networks.get(NETWORK_NAME)
    except docker.errors.NotFound:
        print(f"Creating network: {NETWORK_NAME}")
        client.networks.create(NETWORK_NAME, driver="bridge")

    for vol in ["opt_postgres_data", "opt_influx_data", "opt_config_data"]:
        try:
            client.volumes.get(vol)
        except docker.errors.NotFound:
            print(f"Creating volume: {vol}")
            client.volumes.create(vol)

    try:
        client.containers.get("db")
    except docker.errors.NotFound:
        print("Deploying Postgres DB...")
        client.containers.run(
            "postgres:15",
            name="db",
            detach=True,
            network=NETWORK_NAME,
            volumes={"opt_postgres_data": {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
            environment={"POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": "postgrespw", "POSTGRES_DB": "planetracker"},
            restart_policy={"Name": "unless-stopped"}
        )

    # Setup Admin authentication for external data operations
    admin_token = secrets.token_urlsafe(32)
    try:
        if not _read_shared_volume_file("admin_token.txt"):
            _write_shared_volume_file("admin_token.txt", admin_token)
    except Exception as e:
        print(f"Warning writing admin_token: {e}")

    ensure_shared_psk()

    try:
        client.containers.get("influxdb")
    except docker.errors.NotFound:
        print("Deploying InfluxDB...")
        token = secrets.token_urlsafe(32)
        # Write the token directly into the shared configuration volume
        client.containers.run(
            "alpine",
            command=f"sh -c 'echo {token} > /config/influx_token.txt'",
            volumes={"opt_config_data": {"bind": "/config", "mode": "rw"}},
            remove=True
        )
        
        client.containers.run(
            "influxdb:2",
            name="influxdb",
            detach=True,
            network=NETWORK_NAME,
            volumes={"opt_influx_data": {"bind": "/var/lib/influxdb2", "mode": "rw"}},
            environment=[
                "DOCKER_INFLUXDB_INIT_MODE=setup",
                "DOCKER_INFLUXDB_INIT_USERNAME=admin",
                "DOCKER_INFLUXDB_INIT_PASSWORD=adminpassword12345",
                "DOCKER_INFLUXDB_INIT_ORG=planetracker",
                "DOCKER_INFLUXDB_INIT_BUCKET=flights",
                "DOCKER_INFLUXDB_INIT_RETENTION=0",
                f"DOCKER_INFLUXDB_INIT_ADMIN_TOKEN={token}"
            ],
            restart_policy={"Name": "unless-stopped"}
        )
    # Ensure a reverse proxy is deployed to route / -> UI and /api/ -> server
    try:
        deploy_proxy()
    except Exception as e:
        print(f"Warning deploying proxy: {e}")

def deploy_ui():
    print("Checking UI Container...")
    if client is None:
        print("Docker unavailable; cannot deploy UI.")
        return
    try:
        print("Pulling latest mmatvoz/openplanetracker-ui:latest...")
        client.images.pull("mmatvoz/openplanetracker-ui:latest")
    except Exception as e:
        print(f"Warning: Could not pull UI image (has it been uploaded to Docker Hub yet?): {e}")

    try:
        c = client.containers.get("live-viewer")
        if c.status != "running":
            c.start()
    except docker.errors.NotFound:
        print("Starting live-viewer container on port 80...")
        try:
            client.containers.run(
                "mmatvoz/openplanetracker-ui:latest",
                name="live-viewer",
                detach=True,
                ports={"8000/tcp": 80}, # Binding container 8000 to host 80
                network=NETWORK_NAME,
                volumes={"opt_config_data": {"bind": "/config", "mode": "ro"}},
                restart_policy={"Name": "unless-stopped"}
            )
        except Exception as e:
            print(f"Local fallback or failure deploying UI: {e}")


def deploy_sentinel():
    """Deploy the Sentinel container itself."""
    print("Checking Sentinel Container...")
    if client is None:
        print("Docker unavailable; cannot deploy Sentinel.")
        return
    try:
        print("Pulling latest mmatvoz/openplanetracker-sentinel:latest...")
        client.images.pull("mmatvoz/openplanetracker-sentinel:latest")
    except Exception as e:
        print(f"Warning: Could not pull Sentinel image: {e}")

    try:
        c = client.containers.get("sentinel")
        if c.status != "running":
            c.start()
        return
    except docker.errors.NotFound:
        pass

    try:
        client.containers.run(
            "mmatvoz/openplanetracker-sentinel:latest",
            name="sentinel",
            detach=True,
            privileged=True,
            ports={"8001/tcp": 8001},
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
                "/dev/bus/usb": {"bind": "/dev/bus/usb", "mode": "rw"},
                "opt_config_data": {"bind": "/config", "mode": "rw"},
            },
            network=NETWORK_NAME,
            restart_policy={"Name": "unless-stopped"}
        )
        print("Started sentinel container")
    except Exception as e:
        print(f"Failed to start sentinel container: {e}")


def redeploy_sentinel():
    """Pull and recreate the Sentinel container."""
    if client is None:
        print("Docker unavailable; cannot redeploy Sentinel.")
        return
    try:
        client.images.pull("mmatvoz/openplanetracker-sentinel:latest")
    except Exception as e:
        print(f"Warning: Could not pull Sentinel image: {e}")

    try:
        c = client.containers.get("sentinel")
        c.stop(timeout=3)
        c.remove()
    except docker.errors.NotFound:
        pass
    except Exception as e:
        print(f"Warning stopping sentinel: {e}")

    deploy_sentinel()


def redeploy_server(expose_port: int | None = None):
    """Pull and recreate the Server container."""
    if client is None:
        print("Docker unavailable; cannot redeploy Server.")
        return
    try:
        client.images.pull("mmatvoz/openplanetracker-server:latest")
    except Exception as e:
        print(f"Warning: Could not pull Server image: {e}")

    try:
        c = client.containers.get("openplanetracker-server")
        c.stop(timeout=3)
        c.remove()
    except docker.errors.NotFound:
        pass
    except Exception as e:
        print(f"Warning stopping server: {e}")

    deploy_server(expose_port=expose_port)


def deploy_proxy():
    """Deploy an nginx reverse proxy that forwards / to the UI and /api/ to the server.
    The nginx config is written into the shared opt_config_data volume as default.conf
    and then an nginx container is started using that config.
    """
    if client is None:
        print("Docker unavailable; cannot deploy proxy.")
        return
    # Decide whether TLS certs are present in the shared volume.
    # Expected layout:
    #   /config/tls/fullchain.pem
    #   /config/tls/privkey.pem
    tls_enabled = False
    try:
        client.containers.run(
            "alpine:3.18",
            command=["sh", "-c", "test -f /config/tls/fullchain.pem -a -f /config/tls/privkey.pem"],
            volumes={"opt_config_data": {"bind": "/config", "mode": "ro"}},
            remove=True,
        )
        tls_enabled = True
    except Exception:
        tls_enabled = False

    # Prepare the nginx configuration
    if tls_enabled:
        conf = '''server {
    listen 80;
    server_name _;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name _;

    ssl_certificate /etc/nginx/conf.d/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/conf.d/tls/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    location /api/ {
        proxy_pass http://openplanetracker-server:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    location / {
        proxy_pass http://live-viewer:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
'''
    else:
        conf = '''server {
    listen 80;
    server_name _;

    location /api/ {
            proxy_pass http://openplanetracker-server:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    location / {
        proxy_pass http://live-viewer:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
'''

    # Write the config into the opt_config_data volume via a short alpine container
    try:
        client.containers.run(
            "alpine:3.18",
            command=["sh", "-c", f"mkdir -p /config && cat > /config/default.conf <<'NGINXCONF'\n{conf}\nNGINXCONF"],
            volumes={"opt_config_data": {"bind": "/config", "mode": "rw"}},
            remove=True
        )
    except Exception as e:
        # If writing failed, continue — maybe file already exists
        print(f"Warning: could not write proxy config into volume: {e}")

    # Start or ensure the proxy container is running
    try:
        c = client.containers.get("openplanetracker-proxy")
        if c.status != "running":
            c.start()
        return
    except docker.errors.NotFound:
        pass

    try:
        ports = {"80/tcp": 80, "443/tcp": 443} if tls_enabled else {"80/tcp": 80}
        client.containers.run(
            "nginx:stable",
            name="openplanetracker-proxy",
            detach=True,
            network=NETWORK_NAME,
            ports=ports,
            volumes={"opt_config_data": {"bind": "/etc/nginx/conf.d", "mode": "ro"}},
            restart_policy={"Name": "unless-stopped"}
        )
        print("Started openplanetracker-proxy (nginx)")
    except Exception as e:
        print(f"Failed to start proxy container: {e}")

def deploy_server(expose_port: int | None = None):
    """Deploy the optional remote ingest server. If expose_port is provided
    the container's port 8080 will be published to the host on that port.
    """
    print("Checking server container...")
    try:
        print("Pulling latest mmatvoz/openplanetracker-server:latest...")
        client.images.pull("mmatvoz/openplanetracker-server:latest")
    except Exception as e:
        print(f"Warning: Could not pull Server image: {e}")

    try:
        c = client.containers.get("openplanetracker-server")
        if c.status != "running":
            c.start()
        return
    except docker.errors.NotFound:
        pass

    try:
        print("Starting openplanetracker-server container...")
        # If expose_port is provided, use it; otherwise expose admin endpoints to LAN on port 8089
        if expose_port:
            ports = {"8080/tcp": int(expose_port)}
        else:
            ports = {"8080/tcp": 8089}  # Admin endpoints accessible from LAN on 8089
        shared_psk = ensure_shared_psk()
        client.containers.run(
            "mmatvoz/openplanetracker-server:latest",
            name="openplanetracker-server",
            detach=True,
            network=NETWORK_NAME,
            volumes={"opt_config_data": {"bind": "/config", "mode": "ro"}},
            environment=[f"OPT_SHARED_PSK={shared_psk}"],
            ports=ports,
            restart_policy={"Name": "unless-stopped"}
        )
    except Exception as e:
        print(f"Failed to start server container: {e}")

if __name__ == "__main__":
    init_infrastructure()
    deploy_server()
    deploy_ui()
