# Rigol DG4102 EPICS SoftIOC

This project provides a feature-rich EPICS SoftIOC for controlling the Rigol DG4102 Arbitrary Waveform Generator.

## Project Overview
*   **Technology Stack:** Python 3, `pcaspy`, `pyvisa`.
*   **Architecture:** A Python-based IOC (`ioc/ioc.py`) that maps a comprehensive set of EPICS PVs to SCPI commands for the Rigol DG4000 series.
*   **Key Capabilities:**
    *   Dual-channel independent control.
    *   Unified voltage control (High/Low or Ampl/Offset).
    *   Detailed waveform parameter adjustment (Duty Cycle, Pulse Width, etc.).
    *   Full support for Sweep, Burst, and Modulation modes.
    *   Software-based manual triggering.

## Directory Structure
*   `ioc/`: Core IOC implementation.
*   `scripts/`: Deployment scripts and systemd templates.
*   `opi/`: Phoebus OPI screen with extended controls.
*   `.refs/`: Original reference drivers and manuals.

## Development Conventions
*   **PV Naming:** Prefix (e.g., `DG4102:`) followed by `CH1:` or `CH2:` and the parameter suffix.
*   **Command Logic:** The driver handles bidirectional mapping between EPICS enums/floats and instrument-specific SCPI strings.
*   **Robustness:** Includes a heartbeat thread to monitor VISA connection status.

## Key Files
*   `ioc/ioc.py`: The main driver and PV database.
*   `opi/opi-rigol-dg4102.bob`: The graphical user interface.
*   `scripts/deploy.sh`: The automated Linux deployment tool.
