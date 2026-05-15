# Rigol DG4102 EPICS SoftIOC

This repository provides an EPICS SoftIOC for the Rigol DG4102 Arbitrary Waveform Generator, implemented in Python using `pcaspy` and `pyvisa`.

## Features
- **Dual-channel control** (CH1 and CH2).
- **Impedance Control**: Toggle between **50 Ohm** and **High-Z** output loads.
- **Channel Sync & Copy**:
  - One-click **Phase Synchronization** (`PHAS:INIT`).
  - Copy full configuration between channels (**CH1 -> CH2** or **CH2 -> CH1**).
- **Comprehensive Voltage Control**: Support for both Amplitude/Offset and High/Low level settings.
- **Waveform Specifics**: Precise control over Square Duty Cycle, Ramp Symmetry, and Pulse width/edges.
- **Advanced Modes**: Sweep and Burst support for both channels.
- **Modulation**: Support for AM, FM, PM, ASK, FSK, PSK, PWM, etc.
- **Triggering**: Support for Internal, External, and Manual software triggering.

## Installation & Deployment
1. **Install dependencies**: `pip install -r requirements.txt`
2. **Configure environment**: Edit `scripts/.env` (set `RIGOL_IP` and `PROJECT_DIR`).
3. **Deploy**: Run `sudo ./scripts/deploy.sh`.

## PV List (Key Extensions)
| PV | Description |
|---|---|
| `CHx:IMPEDANCE` | Output Load (50 Ohm, High-Z) |
| `PHASE_SYNC` | Align phases of both channels |
| `COPY_1_TO_2` / `COPY_2_TO_1` | Copy settings between channels |
| `CHx:HIGH` / `CHx:LOW` | High/Low voltage levels |
| `CHx:SQU_DCYC` | Square Duty Cycle (%) |
| `CHx:SWEEP_START` / `STOP` | Sweep Frequency boundaries |

## OPI Interface
The OPI file `opi/opi-rigol-dg4102.bob` includes a dedicated **System & Sync Actions** panel at the bottom for global synchronization and channel copying.
