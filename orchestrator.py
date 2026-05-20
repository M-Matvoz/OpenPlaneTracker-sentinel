import docker
import secrets

client = docker.from_env()
NETWORK_NAME = "openplanetracker_sdr_network"

def init_infrastructure():
    print("Initializing Infrastructure...")
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
        client.containers.run(
            "alpine",
            command=f"sh -c 'if [ ! -f /config/admin_token.txt ]; then echo {admin_token} > /config/admin_token.txt; fi'",
            volumes={"opt_config_data": {"bind": "/config", "mode": "rw"}},
            remove=True
        )
    except Exception as e:
        print(f"Warning writing admin_token: {e}")

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

def deploy_ui():
    print("Checking UI Container...")
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
        ports = {"8080/tcp": int(expose_port)} if expose_port else None
        client.containers.run(
            "mmatvoz/openplanetracker-server:latest",
            name="openplanetracker-server",
            detach=True,
            network=NETWORK_NAME,
            volumes={"opt_config_data": {"bind": "/config", "mode": "ro"}},
            ports=ports,
            restart_policy={"Name": "unless-stopped"}
        )
    except Exception as e:
        print(f"Failed to start server container: {e}")

if __name__ == "__main__":
    init_infrastructure()
    deploy_ui()
