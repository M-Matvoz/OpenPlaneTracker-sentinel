import sys

with open("docker-compose.deploy.yml", "r") as f:
    text = f.read()

text = text.replace(
    "- /dev/bus/usb:/dev/bus/usb",
    "- /dev/bus/usb:/dev/bus/usb\n      - opt_config_data:/config"
)
text += "\n\nvolumes:\n  opt_config_data:\n    external: true\n"

with open("docker-compose.deploy.yml", "w") as f:
    f.write(text)
