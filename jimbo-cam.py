#!/usr/bin/env python3
import os
import time
import uuid
import signal
import logging
import requests
from pathlib import Path
from picamera2 import Picamera2
from libcamera import controls

import subprocess
import getpass
import sys
import argparse

# ======== Config Paths ========
CONFIG_DIR = Path.home() / ".config" / "jimbo-cam"
ENV_PATH = CONFIG_DIR / "jimbo-cam-config.env"
FINGERPRINT_FILE = CONFIG_DIR / "fingerprint.txt"
SERVICE_PATH = Path("/etc/systemd/system/jimbo-cam.service")

# ======== Environment Variables ========
PRUSA_URL = os.getenv("PRUSA_URL", "https://webcam.connect.prusa3d.com/c/snapshot")
PRUSA_TOKEN = os.getenv("PRUSA_TOKEN", "").strip()
PRUSA_FINGERPRINT = os.getenv("PRUSA_FINGERPRINT", "").strip()
INTERVAL_SEC = int(os.getenv("PRUSA_INTERVAL_SEC", "10"))
WIDTH = int(os.getenv("PRUSA_WIDTH", "1280"))
HEIGHT = int(os.getenv("PRUSA_HEIGHT", "720"))
JPEG_QUALITY = int(os.getenv("PRUSA_JPEG_QUALITY", "85"))
TIMEOUT = float(os.getenv("PRUSA_HTTP_TIMEOUT", "10"))

PRUSA_AF_MODE = os.getenv("PRUSA_AF_MODE", "cont").strip().lower()
PRUSA_AF_POSITION = os.getenv("PRUSA_AF_POSITION", "").strip()


# ======== Setup ========
def run_setup():
    print("=== Jimbo-Cam Setup ===")
    if os.geteuid() != 0:
        print("Error: Setup must be run with sudo (root privileges).")
        print("Try:  sudo python3", Path(__file__).name, "--setup")
        sys.exit(1)

    token = input("Prusa Connect Camera Token: ").strip()
    fingerprint = input("Enter Fingerprint (leave blank to auto-generate): ").strip()

    # === Autofocus config ===
    print("\nAutofocus Configuration:")
    print("  [1] Continuous (default)")
    print("  [2] Auto (single autofocus cycle)")
    print("  [3] Manual (requires lens position)")
    af_choice = input("Select autofocus mode [1/2/3]: ").strip() or "1"

    af_mode = "cont"
    af_position = ""

    if af_choice == "2":
        af_mode = "auto"
    elif af_choice == "3":
        af_mode = "man"
        af_position = input("Enter manual lens position (e.g. 1.2): ").strip()
        if not af_position:
            print("Error: Manual mode requires a lens position")
            sys.exit(1)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENV_PATH, "w") as f:
        f.write(f"PRUSA_TOKEN={token}\n")
        if fingerprint:
            f.write(f"PRUSA_FINGERPRINT={fingerprint}\n")
        f.write("PRUSA_INTERVAL_SEC=10\n")
        f.write("PRUSA_WIDTH=1280\n")
        f.write("PRUSA_HEIGHT=720\n")
        f.write("PRUSA_JPEG_QUALITY=85\n")
        f.write("PRUSA_HTTP_TIMEOUT=10\n")
        f.write(f"PRUSA_AF_MODE={af_mode}\n")
        if af_position:
            f.write(f"PRUSA_AF_POSITION={af_position}\n")
    print(f"[+] Saved config to {ENV_PATH}")

    # Create systemd service file
    service_unit = f"""[Unit]
Description=Prusa Connect Picamera Uploader by James Robinson
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={getpass.getuser()}
WorkingDirectory={Path(__file__).parent}
EnvironmentFile={ENV_PATH}
ExecStart=/usr/bin/env python3 {Path(__file__).absolute()}
Restart=on-failure
RestartSec=10
SyslogIdentifier=jimbo-cam

[Install]
WantedBy=multi-user.target
"""
    with open(SERVICE_PATH, "w") as f:
        f.write(service_unit)
    print(f"[+] Installed systemd unit at {SERVICE_PATH}")

    subprocess.run(["systemctl", "daemon-reload"], check=False)
    print("[+] Systemd daemon reloaded")

    choice = (
        input("Do you want to enable and start jimbo-cam.service now? [y/N]: ")
        .strip()
        .lower()
    )
    if choice == "y":
        subprocess.run(
            ["systemctl", "enable", "--now", "jimbo-cam.service"], check=True
        )
        print("[+] jimbo-cam.service enabled and started")
        print("You can check logs with: journalctl -u jimbo-cam.service -f")
    else:
        print("You can enable and start manually with:")
        print("  sudo systemctl enable --now jimbo-cam.service\n")
        print("You can check logs with: journalctl -u jimbo-cam.service -f")


def parse_args():
    parser = argparse.ArgumentParser(description="Jimbo-Cam uploader for Prusa Connect")

    parser.add_argument("--setup", action="store_true", help="Run interactive setup")

    parser.add_argument(
        "--af", nargs="+", help="Autofocus mode. Example: '--af cont' or '--af man 1.2'"
    )

    return parser.parse_args()


# ======== Logging ========
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("prusa-picam")


def get_or_create_fingerprint() -> str:
    FINGERPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    if FINGERPRINT_FILE.exists():
        fp = FINGERPRINT_FILE.read_text().strip()
        if fp:
            logger.info(f"Loaded existing fingerprint: {fp}")
            return fp
    fp = uuid.uuid4().hex
    FINGERPRINT_FILE.write_text(fp)
    logger.info(f"Generated new fingerprint: {fp}")
    return fp


def capture_jpeg(picam: Picamera2) -> bytes:
    tmp = Path("/tmp/prusa_snapshot.jpg")
    logger.debug(f"Configuring camera: {WIDTH}x{HEIGHT}")
    cfg = picam.create_still_configuration(
        main={"size": (WIDTH, HEIGHT), "format": "RGB888"}
    )
    picam.configure(cfg)
    picam.options["quality"] = JPEG_QUALITY
    logger.debug(f"Set JPEG quality to {JPEG_QUALITY}")

    logger.info("Starting camera and capturing image...")
    picam.start()
    time.sleep(0.2)
    picam.capture_file(str(tmp))
    picam.stop()
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    logger.info(f"Captured image ({len(data)} bytes)")
    return data


def upload_snapshot(jpeg_bytes: bytes, token: str, fingerprint: str) -> None:
    headers = {
        "accept": "*/*",
        "content-type": "image/jpeg",
        "token": token,
        "fingerprint": fingerprint,
    }
    logger.info("Uploading snapshot to Prusa Connect...")
    resp = requests.put(PRUSA_URL, headers=headers, data=jpeg_bytes, timeout=TIMEOUT)
    resp.raise_for_status()
    logger.info(f"Upload successful (HTTP {resp.status_code})")


def configure_autofocus(picam, cli_af):
    if cli_af:  # CLI takes priority
        mode = cli_af[0].lower()
        pos = cli_af[1] if len(cli_af) > 1 else None
    else:  # fallback to env
        mode = PRUSA_AF_MODE
        pos = PRUSA_AF_POSITION or None

    logger.info(f"Configuring autofocus: mode={mode}, position={pos}")

    if mode in ["cont", "continuous"]:
        picam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
    elif mode in ["auto", "af"]:
        picam.set_controls({"AfMode": controls.AfModeEnum.Auto})
    elif mode in ["man", "manual"]:
        if not pos:
            raise ValueError("Manual mode requires a lens position, e.g. --af man 1.2")
        try:
            lens_pos = float(pos)
        except ValueError:
            raise ValueError(f"Invalid manual lens position: {pos}")

        picam.set_controls(
            {
                "AfMode": controls.AfModeEnum.Manual,
                "LensPosition": lens_pos,
            }
        )
    else:
        raise ValueError(f"Unknown autofocus mode: {mode}")


running = True


def _stop(signum, frame):
    global running
    logger.info("Termination signal received; stopping...")
    running = False


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def main():
    if not PRUSA_TOKEN:
        logger.error("Missing PRUSA_TOKEN env var")
        raise SystemExit("You must set PRUSA_TOKEN environment variable.")

    fingerprint = PRUSA_FINGERPRINT or get_or_create_fingerprint()
    logger.info(f"Using fingerprint: {fingerprint}")

    picam = Picamera2()
    picam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
    logger.info(f"Uploader initialized: URL={PRUSA_URL}, interval={INTERVAL_SEC}s")

    backoff = INTERVAL_SEC
    while running:
        try:
            jpeg = capture_jpeg(picam)
            upload_snapshot(jpeg, PRUSA_TOKEN, fingerprint)
            backoff = INTERVAL_SEC
        except requests.HTTPError as e:
            try:
                logger.error(f"HTTP error {e.response.status_code}: {e.response.text}")
            except Exception:
                logger.error(f"HTTP error: {e}")
            backoff = min(max(INTERVAL_SEC * 3, 15), 120)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            backoff = min(max(INTERVAL_SEC * 3, 15), 120)

        logger.info(f"Next snapshot in {backoff}s...")
        for _ in range(backoff):
            if not running:
                break
            time.sleep(1)

    logger.info("Exiting...")


if __name__ == "__main__":
    args = parse_args()

    if args.setup:
        run_setup()
    else:
        try:
            picam = Picamera2()
            configure_autofocus(picam, args.af)
            main()
        except Exception as e:
            logger.error(f"Startup error: {e}", exc_info=True)
            sys.exit(1)
