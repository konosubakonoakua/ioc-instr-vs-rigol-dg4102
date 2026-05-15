import pyvisa
from pcaspy import Driver, SimpleServer
from rich.pretty import pprint
import argparse
import logging
import signal
import sys
import threading
import time


def log_exceptions(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            _ = f"Error in {func.__name__}: {e}"
            logging.error(_)
            pprint(_)

    return wrapper


class RigolDG4102Driver(Driver):
    def __init__(self, visa_addr, terminator, pvdb):
        super().__init__()
        self.pvdb = pvdb
        self.visa_addr = visa_addr
        self.terminator = terminator
        self.lock = threading.Lock()
        self.connected = False
        self.instr = None
        self.rm = pyvisa.ResourceManager()

        # Heartbeat thread
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self.heartbeat_thread.start()

    def _connect_instrument(self):
        with self.lock:
            try:
                logging.info(f"Attempting to connect to {self.visa_addr}...")
                self.instr = self.rm.open_resource(self.visa_addr)
                self.instr.write_termination = self.terminator
                self.instr.read_termination = self.terminator
                self.instr.timeout = 2000

                idn = self.instr.query("*IDN?")
                logging.info(f"Connected to: {idn.strip()}")
                self.connected = True
                self.setParam("COMM_STATUS", 1)
                self.updatePVs()
                return True
            except Exception as e:
                logging.error(f"Connection failed: {e}")
                self.connected = False
                self.setParam("COMM_STATUS", 0)
                self.updatePVs()
                return False

    def _heartbeat_loop(self):
        while True:
            if not self.connected:
                self._connect_instrument()
            else:
                with self.lock:
                    try:
                        self.instr.query("*STB?")
                    except Exception as e:
                        logging.error(f"Heartbeat failed: {e}")
                        self.connected = False
                        self.setParam("COMM_STATUS", 0)
                        self.updatePVs()
            time.sleep(3)

    def close(self):
        with self.lock:
            if self.instr:
                try:
                    self.instr.close()
                except:
                    pass
            self.rm.close()

    def _parse_reason(self, reason):
        if reason.startswith("CH1:"):
            return 1, reason[4:]
        elif reason.startswith("CH2:"):
            return 2, reason[4:]
        return None, reason

    def _get_scpi_cmd(self, channel, suffix, is_query=False):
        mapping = {
            # Basic
            "OUTPUT": ("OUTP{ch}", ""),
            "IMPEDANCE": ("OUTP{ch}:IMP", ""),
            "POLARITY": ("OUTP{ch}:POL", ""),
            # Waveform General
            "FUNC": ("SOUR{ch}:FUNC", ""),
            "FREQ": ("SOUR{ch}:FREQ", ""),
            "AMPL": ("SOUR{ch}:VOLT", ""),
            "OFFSET": ("SOUR{ch}:VOLT:OFFS", ""),
            "HIGH": ("SOUR{ch}:VOLT:HIGH", ""),
            "LOW": ("SOUR{ch}:VOLT:LOW", ""),
            "PHASE": ("SOUR{ch}:PHAS", ""),
            # Waveform Specific
            "SQU_DCYC": ("SOUR{ch}:FUNC:SQU:DCYC", ""),
            "RAMP_SYMM": ("SOUR{ch}:FUNC:RAMP:SYMM", ""),
            "PULS_WIDT": ("SOUR{ch}:PULS:WIDT", ""),
            "PULS_LEAD": ("SOUR{ch}:PULS:TRAN:LEAD", ""),
            "PULS_TRAI": ("SOUR{ch}:PULS:TRAN:TRA", ""),
            # Sweep
            "SWEEP_STAT": ("SOUR{ch}:SWE:STAT", ""),
            "SWEEP_TIME": ("SOUR{ch}:SWE:TIME", ""),
            "SWEEP_START": ("SOUR{ch}:FREQ:STAR", ""),
            "SWEEP_STOP": ("SOUR{ch}:FREQ:STOP", ""),
            # Burst
            "BURST_STAT": ("SOUR{ch}:BURS:STAT", ""),
            "BURST_MODE": ("SOUR{ch}:BURS:MODE", ""),
            "BURST_CYCLES": ("SOUR{ch}:BURS:NCYC", ""),
            "BURST_PERIOD": ("SOUR{ch}:BURS:INT:PER", ""),
            # Modulation
            "MOD_STAT": ("SOUR{ch}:MOD:STAT", ""),
            "MOD_TYP": ("SOUR{ch}:MOD:TYP", ""),
            # Trigger
            "TRIG_SOUR": ("SOUR{ch}:BURS:TRIG:SOUR", ""),
        }

        # Write-only actions
        action_mapping = {
            "TRIG_MANUAL": "*TRG",
        }

        if suffix in mapping:
            cmd_base, _ = mapping[suffix]
            cmd = cmd_base.format(ch=channel)
            return f"{cmd}?" if is_query else cmd
        elif not is_query and suffix in action_mapping:
            return action_mapping[suffix]
        return None

    @log_exceptions
    def write(self, reason, value):
        if reason == "COMM_STATUS":
            return False

        channel, suffix = self._parse_reason(reason)

        if reason in self.pvdb and self.pvdb[reason].get("type") == "enum":
            enums = self.pvdb[reason].get("enums", [])
            try:
                value = enums[int(value)]
            except:
                pass

        if channel:
            cmd = self._get_scpi_cmd(channel, suffix)
            if cmd:
                if suffix == "TRIG_MANUAL":
                    full_cmd = cmd
                else:
                    full_cmd = f"{cmd} {value}"

                with self.lock:
                    if not self.connected:
                        return False
                    self.instr.write(full_cmd)
                    logging.info(f"Write {reason}: {full_cmd}")
                    if suffix != "TRIG_MANUAL":
                        # CRITICAL FIX: Update state with original index to prevent type errors
                        self.setParam(reason, epics_value)
                    return True

        if reason == "SYSTEM_PRESET":
            with self.lock:
                self.instr.write("*RST")
                return True

        return False

    @log_exceptions
    def read(self, reason):
        if reason == "COMM_STATUS":
            return self.connected
        if reason == "IDN":
            with self.lock:
                return self.instr.query("*IDN?") if self.connected else "Disconnected"

        channel, suffix = self._parse_reason(reason)
        if channel:
            cmd = self._get_scpi_cmd(channel, suffix, is_query=True)
            if cmd:
                with self.lock:
                    if not self.connected:
                        return self.getParam(reason)
                    val = self.instr.query(cmd).strip().replace('"', "")

                    if reason in self.pvdb and self.pvdb[reason].get("type") == "enum":
                        enums = self.pvdb[reason].get("enums", [])
                        try:
                            # Try exact match first, then upper
                            if val in enums:
                                val = enums.index(val)
                            else:
                                val = enums.index(val.upper())
                        except ValueError:
                            # Fallback if instrument returns something unexpected
                            pass
                    else:
                        try:
                            val = (
                                float(val)
                                if "." in val or "E" in val.upper()
                                else int(val)
                            )
                        except:
                            pass
                    self.setParam(reason, val)
                    return val

        return self.getParam(reason)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rigol DG4102 IOC")
    parser.add_argument("--ip", type=str, default="192.168.1.100")
    parser.add_argument("--port", type=str, default="5555")
    parser.add_argument("--prefix", type=str, default="DG4102:")
    parser.add_argument("--terminator", type=str, default="\n")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    RIGOL_PVDB = {}
    channels = ["CH1", "CH2"]

    for ch in channels:
        RIGOL_PVDB.update(
            {
                f"{ch}:OUTPUT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:IMPEDANCE": {"type": "string"},
                f"{ch}:POLARITY": {"type": "enum", "enums": ["NORMAL", "INVERTED"]},
                f"{ch}:FUNC": {
                    "type": "enum",
                    "enums": ["SIN", "SQU", "RAMP", "PULS", "NOIS", "DC", "ARB", "HARM"],
                },
                f"{ch}:FREQ": {"type": "float", "unit": "Hz"},
                f"{ch}:AMPL": {"type": "float", "unit": "V"},
                f"{ch}:OFFSET": {"type": "float", "unit": "V"},
                f"{ch}:HIGH": {"type": "float", "unit": "V"},
                f"{ch}:LOW": {"type": "float", "unit": "V"},
                f"{ch}:PHASE": {"type": "float", "unit": "deg"},
                f"{ch}:SQU_DCYC": {"type": "float", "unit": "%"},
                f"{ch}:RAMP_SYMM": {"type": "float", "unit": "%"},
                f"{ch}:PULS_WIDT": {"type": "float", "unit": "s"},
                f"{ch}:PULS_LEAD": {"type": "float", "unit": "s"},
                f"{ch}:PULS_TRAI": {"type": "float", "unit": "s"},
                f"{ch}:SWEEP_STAT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:SWEEP_TIME": {"type": "float", "unit": "s"},
                f"{ch}:SWEEP_START": {"type": "float", "unit": "Hz"},
                f"{ch}:SWEEP_STOP": {"type": "float", "unit": "Hz"},
                f"{ch}:BURST_STAT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:BURST_MODE": {"type": "enum", "enums": ["TRIG", "GAT", "INF"]},
                f"{ch}:BURST_CYCLES": {"type": "int"},
                f"{ch}:BURST_PERIOD": {"type": "float", "unit": "s"},
                f"{ch}:MOD_STAT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:MOD_TYP": {
                    "type": "enum",
                    "enums": ["AM", "FM", "PM", "ASK", "FSK", "PSK", "BPSK", "QPSK", "OSK", "PWM"],
                },
                f"{ch}:TRIG_SOUR": {"type": "enum", "enums": ["INT", "EXT", "MAN"]},
                f"{ch}:TRIG_MANUAL": {"type": "int"},
            }
        )

    RIGOL_PVDB.update(
        {
            "IDN": {"type": "string"},
            "COMM_STATUS": {"type": "enum", "enums": ["Disconnected", "Connected"]},
            "SYSTEM_PRESET": {"type": "int"},
        }
    )

    server = SimpleServer()
    server.createPV(args.prefix, RIGOL_PVDB)

    # Use IP or full VISA string appropriately based on configuration
    # If port is provided and not empty, use SOCKET, else use INSTR
    visa_addr = f"TCPIP0::{args.ip}::{args.port}::SOCKET"
    if not args.port or args.port == "0":
        visa_addr = f"TCPIP0::{args.ip}::INSTR"
        
    driver = RigolDG4102Driver(visa_addr, args.terminator, RIGOL_PVDB)

    def shutdown_handler(signum, frame):
        logging.info("Shutting down...")
        driver.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info(f"Rigol DG4102 Extended IOC started with prefix {args.prefix}")
    while True:
        server.process(0.1)
