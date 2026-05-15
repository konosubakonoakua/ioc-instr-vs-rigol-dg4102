#!/bin/bash

# Deployment script for Rigol DG4102 EPICS SoftIOC
# This script reads configuration from .env and generates a systemd service file.

set -e

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
TEMPLATE_FILE="$SCRIPT_DIR/softioc-rigol-dg4102.service.template"
SERVICE_FILE="/etc/systemd/system/softioc-rigol-dg4102.service"

# Check if .env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found in $SCRIPT_DIR."
    echo "Please copy env.example to .env and configure it first."
    exit 1
fi

# Load configuration from .env
export $(grep -v '^#' "$ENV_FILE" | xargs)

echo "Generating systemd service file from template..."

# Use sed to replace placeholders in the template with values from .env
sed -e "s|{{SERVICE_USER}}|$SERVICE_USER|g" \
    -e "s|{{PROJECT_DIR}}|$PROJECT_DIR|g" \
    -e "s|{{PYTHON_BIN}}|$PYTHON_BIN|g" \
    -e "s|{{EPICS_CA_SERVER_PORT}}|$EPICS_CA_SERVER_PORT|g" \
    -e "s|{{EPICS_CA_REPEATER_PORT}}|$EPICS_CA_REPEATER_PORT|g" \
    -e "s|{{EPICS_CAS_BEACON_ADDR_LIST}}|$EPICS_CAS_BEACON_ADDR_LIST|g" \
    -e "s|{{EPICS_CA_ADDR_LIST}}|$EPICS_CA_ADDR_LIST|g" \
    -e "s|{{EPICS_BASE}}|$EPICS_BASE|g" \
    -e "s|{{RIGOL_IP}}|$RIGOL_IP|g" \
    -e "s|{{RIGOL_PORT}}|$RIGOL_PORT|g" \
    -e "s|{{RIGOL_PREFIX}}|$RIGOL_PREFIX|g" \
    "$TEMPLATE_FILE" >/tmp/softioc-rigol-dg4102.service

echo "Installing service to $SERVICE_FILE..."
sudo mv /tmp/softioc-rigol-dg4102.service "$SERVICE_FILE"
sudo chown root:root "$SERVICE_FILE"
sudo chmod 644 "$SERVICE_FILE"

echo "Reloading systemd and restarting service..."
sudo systemctl daemon-reload
sudo systemctl enable softioc-rigol-dg4102.service
sudo systemctl restart softioc-rigol-dg4102.service

echo "------------------------------------------------------"
echo "Deployment successful!"
echo "You can check the status with: systemctl status softioc-rigol-dg4102.service"
echo "You can view logs with: journalctl -u softioc-rigol-dg4102.service -f"
echo "------------------------------------------------------"
