import sys

with open("orchestrator.py", "r") as f:
    text = f.read()

old_code = """
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
"""

new_code = """
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
"""
text = text.replace(old_code.strip(), new_code.strip())

with open("orchestrator.py", "w") as f:
    f.write(text)
