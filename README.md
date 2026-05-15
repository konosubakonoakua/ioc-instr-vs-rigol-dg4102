# Rigol DG4102 EPICS SoftIOC

This repository provides an EPICS SoftIOC for the Rigol DG4102 Arbitrary Waveform Generator, implemented in Python using `pcaspy` and `pyvisa`.

## Features
- **Dual-channel control** (CH1 and CH2).
- **Comprehensive Voltage Control**: Support for both Amplitude/Offset and High/Low level settings.
- **Waveform Specifics**: Precise control over Square Duty Cycle, Ramp Symmetry, and Pulse width/edges.
- **Standard Waveforms**: Sin, Square, Ramp, Pulse, Noise, DC, ARB, Harmonic.
- **Advanced Modes**: Sweep and Burst support for both channels.
- **Modulation**: Support for AM, FM, PM, ASK, FSK, PSK, PWM, etc.
- **Triggering**: Support for Internal, External, and Manual software triggering.
- **Automated deployment** via `systemd`.
- **Heartbeat monitoring** and connection status reporting.

## Prerequisites
- Python 3.x
- EPICS Base
- NI-VISA or PyVISA-py backend

## Installation
1. **Clone the repository**
2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

## Deployment
1. **Configure the environment**
   Copy the example environment file and edit it with your specific settings:
   ```bash
   cp scripts/env.example scripts/.env
   vim scripts/.env
   ```
2. **Run the deployment script**
   ```bash
   chmod +x scripts/deploy.sh
   sudo ./scripts/deploy.sh
   ```

## PV List
The following PVs are available for both `CH1:` and `CH2:`:

| PV Suffix | Description |
|---|---|
| `OUTPUT` | Output ON/OFF |
| `HIGH` / `LOW` | High/Low voltage levels |
| `AMPL` / `OFFSET`| Amplitude/Offset voltage |
| `FREQ` / `PHASE` | Frequency and Phase |
| `SQU_DCYC` | Square Duty Cycle (%) |
| `RAMP_SYMM` | Ramp Symmetry (%) |
| `PULS_WIDT` | Pulse Width (s) |
| `SWEEP_STAT` | Sweep mode toggle |
| `BURST_STAT` | Burst mode toggle |
| `MOD_STAT` | Modulation toggle |
| `TRIG_SOUR` | Trigger source (INT, EXT, MAN) |
| `TRIG_MANUAL` | Manual trigger action |

## OPI Interface
An extended OPI file for Phoebus is provided in `opi/opi-rigol-dg4102.bob`.
- Set macro `P` to your `RIGOL_PREFIX` (e.g., `DG4102:`).
