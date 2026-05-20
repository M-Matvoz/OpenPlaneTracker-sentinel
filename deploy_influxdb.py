import docker
import secrets
import time

client = docker.from_env()

token = secrets.token_urlsafe(32)
print("Deploying InfluxDB container...")

try:
    existing = client.containers.get("influxdb")
    existing.stop()
    existing.remove()
except docker.errors.NotFound:
    pass

try:
    network_name = "openplanetracker_sdr_network"
    
    container = client.containers.run(
        "influxdb:2",
        name="influxdb",
        detach=True,
        ports={"8086/tcp": 8086},
        volumes={"influxdb_data": {"bind": "/var/lib/influxdb2", "mode": "rw"}},
        environment=[
            "DOCKER_INFLUXDB_INIT_MODE=setup",
            "DOCKER_INFLUXDB_INIT_USERNAME=admin",
            "DOCKER_INFLUXDB_INIT_PASSWORD=adminpassword12345",
            "DOCKER_INFLUXDB_INIT_ORG=planetracker",
            "DOCKER_INFLUXDB_INIT_BUCKET=flights",
            "DOCKER_INFLUXDB_INIT_RETENTION=0",
            f"DOCKER_INFLUXDB_INIT_ADMIN_TOKEN={token}",
        ],
        network=network_name,
        restart_policy={"Name": "unless-stopped"}
    )
    
    print("\n✅ InfluxDB successfully deployed!")
    print(f"TOKEN={token}")

    with open('/app/shared/influx_token.txt', 'w') as f:
        f.write(token)

except Exception as e:
    print(f"Error deploying InfluxDB: {e}")
