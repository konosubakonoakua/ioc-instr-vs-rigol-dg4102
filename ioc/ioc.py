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
        self.lock = threading.RLock()
        self.connected = False
        self.instr = None
        self.rm = pyvisa.ResourceManager()
        self.last_full_sync = 0

        # Heartbeat/Polling thread
        self.heartbeat_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.heartbeat_thread.start()

    def _connect_instrument(self):
        with self.lock:
            try:
                logging.info(f"Attempting to connect to {self.visa_addr}...")
                self.instr = self.rm.open_resource(self.visa_addr)
                self.instr.write_termination = self.terminator
                self.instr.read_termination = self.terminator
                self.instr.timeout = 5000

                self.instr.write("*CLS")
                self.instr.query("*OPC?")

                # Use safe query for identification
                idn = self._safe_query("*IDN?", expected_type="string")
                if idn:
                    logging.info(f"Connected to: {idn}")
                    self.connected = True
                    self.setParam("COMM_STATUS", 1)
                    logging.info("Performing startup state synchronization...")
                    self._sync_state()
                    self.updatePVs()
                    return True
                return False
            except Exception as e:
                logging.error(f"Connection failed: {e}")
                self.connected = False
                self.setParam("COMM_STATUS", 0)
                self.updatePVs()
                if self.instr:
                    try:
                        self.instr.clear()
                        self.instr.close()
                    except:
                        pass
                return False

    def _safe_query(self, cmd, expected_type=None):
        """Perform a query with pre-delay and type verification to prevent buffer shift."""
        # Note: calling internal check instead of self.connected to allow IDN check during connect
        if self.instr is None:
            return None

        with self.lock:
            try:
                time.sleep(0.04)  # Slightly longer delay for stability
                val = self.instr.query(cmd).strip().replace('"', "")

                if expected_type == "numeric":
                    try:
                        float(val)
                    except ValueError:
                        logging.error(
                            f"Sync error: Query '{cmd}' expected number but got '{val}'. Flushing."
                        )
                        self.instr.write("*CLS")
                        self.instr.clear()
                        return None
                return val
            except Exception as e:
                logging.error(f"Query '{cmd}' failed: {e}")
                try:
                    self.instr.write("*CLS")
                    self.instr.clear()
                except:
                    pass
                return None

    def _sync_state(self, target_channel=None):
        if not self.connected:
            return
        skip_suffixes = [
            "SYSTEM_PRESET",
            "COPY_1_TO_2",
            "COPY_2_TO_1",
            "PHASE_SYNC",
            "TRIG_MANUAL",
            "COMM_STATUS",
            "IDN",
        ]
        with self.lock:
            for pv_name in self.pvdb:
                if any(pv_name.endswith(s) for s in skip_suffixes):
                    continue
                if target_channel is not None:
                    ch, suffix = self._parse_reason(pv_name)
                    if ch != target_channel:
                        continue
                self.read(pv_name)
                time.sleep(0.01)
            self.updatePVs()
            self.last_full_sync = time.time()

    def _polling_loop(self):
        while True:
            if not self.connected:
                self._connect_instrument()
            else:
                with self.lock:
                    try:
                        # Use safe query for check
                        res = self._safe_query("*OPC?", expected_type="numeric")
                        if res is None:
                            raise Exception("OPC sync failed")

                        if (
                            time.time() - self.last_full_sync > 20
                        ):  # Slower polling for stability
                            self._sync_state()
                    except Exception as e:
                        logging.error(f"Polling loop failure: {e}")
                        self.connected = False
                        self.setParam("COMM_STATUS", 0)
                        self.updatePVs()
                        try:
                            self.instr.write("*CLS")
                            self.instr.clear()
                        except:
                            pass
            time.sleep(4)

    def close(self):
        with self.lock:
            if self.instr:
                try:
                    self.instr.clear()
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
            "OUTPUT": ("OUTP{ch}", ""),
            "IMPEDANCE": ("OUTP{ch}:IMP", ""),
            "POLARITY": ("OUTP{ch}:POL", ""),
            "FUNC": ("SOUR{ch}:FUNC", ""),
            "FREQ": ("SOUR{ch}:FREQ", ""),
            "AMPL": ("SOUR{ch}:VOLT", ""),
            "OFFSET": ("SOUR{ch}:VOLT:OFFS", ""),
            "HIGH": ("SOUR{ch}:VOLT:HIGH", ""),
            "LOW": ("SOUR{ch}:VOLT:LOW", ""),
            "PHASE": ("SOUR{ch}:PHAS", ""),
            "SQU_DCYC": ("SOUR{ch}:FUNC:SQU:DCYC", ""),
            "RAMP_SYMM": ("SOUR{ch}:FUNC:RAMP:SYMM", ""),
            "PULS_WIDT": ("SOUR{ch}:PULS:WIDT", ""),
            "PULS_LEAD": ("SOUR{ch}:PULS:TRAN:LEAD", ""),
            "PULS_TRAI": ("SOUR{ch}:PULS:TRAN:TRA", ""),
            "SWEEP_STAT": ("SOUR{ch}:SWE:STAT", ""),
            "SWEEP_TIME": ("SOUR{ch}:SWE:TIME", ""),
            "SWEEP_START": ("SOUR{ch}:FREQ:STAR", ""),
            "SWEEP_STOP": ("SOUR{ch}:FREQ:STOP", ""),
            "BURST_STAT": ("SOUR{ch}:BURS:STAT", ""),
            "BURST_MODE": ("SOUR{ch}:BURS:MODE", ""),
            "BURST_CYCLES": ("SOUR{ch}:BURS:NCYC", ""),
            "BURST_PERIOD": ("SOUR{ch}:BURS:INT:PER", ""),
            "MOD_STAT": ("SOUR{ch}:MOD:STAT", ""),
            "MOD_TYP": ("SOUR{ch}:MOD:TYP", ""),
            "TRIG_SOUR": ("SOUR{ch}:BURS:TRIG:SOUR", ""),
        }
        global_action_mapping = {
            "COPY_1_TO_2": "SYST:CSC CH1,CH2",
            "COPY_2_TO_1": "SYST:CSC CH2,CH1",
            "PHASE_SYNC": "PHAS:INIT",
        }
        if suffix in mapping and channel is not None:
            cmd_base, _ = mapping[suffix]
            cmd = cmd_base.format(ch=channel)
            return f"{cmd}?" if is_query else cmd
        elif not is_query and suffix in {"TRIG_MANUAL"}:
            return "*TRG"
        elif not is_query and suffix in global_action_mapping:
            return global_action_mapping[suffix]
        return None

    @log_exceptions
    def write(self, reason, value):
        if reason == "COMM_STATUS":
            return False
        epics_value = value
        scpi_value = value
        channel, suffix = self._parse_reason(reason)

        if reason in self.pvdb and self.pvdb[reason].get("type") == "enum":
            enums = self.pvdb[reason].get("enums", [])
            try:
                if isinstance(value, str):
                    if "item " in value:
                        value = int(value.replace("item ", ""))
                    elif value.upper() in enums:
                        value = enums.index(value.upper())
                epics_value = int(value)
                scpi_value = enums[epics_value]
            except:
                return False
            if suffix == "IMPEDANCE":
                scpi_value = "50" if scpi_value == "50 Ohm" else "INF"

        if reason in ["SYSTEM_PRESET", "COPY_1_TO_2", "COPY_2_TO_1", "PHASE_SYNC"]:
            cmd = (
                "*RST"
                if reason == "SYSTEM_PRESET"
                else self._get_scpi_cmd(None, reason)
            )
            if cmd:
                with self.lock:
                    if not self.connected:
                        return False
                    try:
                        self.instr.write(cmd)
                        self.instr.query("*OPC?")
                        self.setParam(reason, 1)
                        self.updatePVs()
                        time.sleep(0.1)
                        self._sync_state()  # Full sync after global action
                        self.setParam(reason, 0)
                        self.updatePVs()
                        return True
                    except:
                        try:
                            self.instr.clear()
                        except:
                            pass
                        return False

        if channel:
            cmd = self._get_scpi_cmd(channel, suffix)
            if cmd:
                full_cmd = cmd if suffix == "TRIG_MANUAL" else f"{cmd} {scpi_value}"
                with self.lock:
                    if not self.connected:
                        return False
                    try:
                        self.instr.write(full_cmd)
                        self.instr.query("*OPC?")
                        if suffix != "TRIG_MANUAL":
                            self.setParam(reason, epics_value)
                        return True
                    except:
                        try:
                            self.instr.clear()
                        except:
                            pass
                        return False
        return False

    @log_exceptions
    def read(self, reason):
        if reason == "COMM_STATUS":
            return self.connected
        if reason == "IDN":
            val = self._safe_query("*IDN?", expected_type="string")
            if val:
                self.setParam(reason, val)
                return val
            return self.getParam(reason)

        channel, suffix = self._parse_reason(reason)
        if channel:
            expected = "numeric"
            if reason in self.pvdb and self.pvdb[reason].get("type") == "enum":
                expected = "enum"
            cmd = self._get_scpi_cmd(channel, suffix, is_query=True)
            if cmd:
                val = self._safe_query(cmd, expected_type=expected)
                if val is None:
                    return self.getParam(reason)
                if suffix == "IMPEDANCE":
                    val = (
                        "High-Z" if ("INF" in val.upper() or "9.9" in val) else "50 Ohm"
                    )
                if reason in self.pvdb and self.pvdb[reason].get("type") == "enum":
                    enums = self.pvdb[reason].get("enums", [])
                    try:
                        index = (
                            enums.index(val)
                            if val in enums
                            else enums.index(val.upper())
                        )
                        self.setParam(reason, index)
                        return index
                    except:
                        return self.getParam(reason)
                else:
                    try:
                        if "." in val or "E" in val.upper():
                            val = float(val)
                        else:
                            val = int(val)
                        self.setParam(reason, val)
                        return val
                    except:
                        return self.getParam(reason)
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
                f"{ch}:IMPEDANCE": {"type": "enum", "enums": ["50 Ohm", "High-Z"]},
                f"{ch}:POLARITY": {"type": "enum", "enums": ["NORMAL", "INVERTED"]},
                f"{ch}:FUNC": {
                    "type": "enum",
                    "enums": [
                        "SIN",
                        "SQU",
                        "RAMP",
                        "PULS",
                        "NOIS",
                        "DC",
                        "USER",
                        "HARM",
                    ],
                },
                f"{ch}:FREQ": {
                    "type": "float",
                    "unit": "Hz",
                    "prec": 3,
                    "lolim": 0,
                    "hilim": 100000000,
                },
                f"{ch}:AMPL": {
                    "type": "float",
                    "unit": "V",
                    "prec": 3,
                    "lolim": -20,
                    "hilim": 20,
                },
                f"{ch}:OFFSET": {
                    "type": "float",
                    "unit": "V",
                    "prec": 3,
                    "lolim": -10,
                    "hilim": 10,
                },
                f"{ch}:HIGH": {
                    "type": "float",
                    "unit": "V",
                    "prec": 3,
                    "lolim": -10,
                    "hilim": 10,
                },
                f"{ch}:LOW": {
                    "type": "float",
                    "unit": "V",
                    "prec": 3,
                    "lolim": -10,
                    "hilim": 10,
                },
                f"{ch}:PHASE": {
                    "type": "float",
                    "unit": "deg",
                    "prec": 2,
                    "lolim": 0,
                    "hilim": 360,
                },
                f"{ch}:SQU_DCYC": {
                    "type": "float",
                    "unit": "%",
                    "prec": 2,
                    "lolim": 20,
                    "hilim": 80,
                },
                f"{ch}:RAMP_SYMM": {
                    "type": "float",
                    "unit": "%",
                    "prec": 2,
                    "lolim": 0,
                    "hilim": 100,
                },
                f"{ch}:PULS_WIDT": {
                    "type": "float",
                    "unit": "s",
                    "prec": 9,
                    "lolim": 0,
                    "hilim": 1,
                },
                f"{ch}:PULS_LEAD": {
                    "type": "float",
                    "unit": "s",
                    "prec": 9,
                    "lolim": 0,
                    "hilim": 1,
                },
                f"{ch}:PULS_TRAI": {
                    "type": "float",
                    "unit": "s",
                    "prec": 9,
                    "lolim": 0,
                    "hilim": 1,
                },
                f"{ch}:SWEEP_STAT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:SWEEP_TIME": {
                    "type": "float",
                    "unit": "s",
                    "prec": 3,
                    "lolim": 0,
                    "hilim": 300,
                },
                f"{ch}:SWEEP_START": {
                    "type": "float",
                    "unit": "Hz",
                    "prec": 3,
                    "lolim": 0,
                    "hilim": 100000000,
                },
                f"{ch}:SWEEP_STOP": {
                    "type": "float",
                    "unit": "Hz",
                    "prec": 3,
                    "lolim": 0,
                    "hilim": 100000000,
                },
                f"{ch}:BURST_STAT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:BURST_MODE": {"type": "enum", "enums": ["TRIG", "GAT", "INF"]},
                f"{ch}:BURST_CYCLES": {"type": "int", "lolim": 1, "hilim": 1000000},
                f"{ch}:BURST_PERIOD": {
                    "type": "float",
                    "unit": "s",
                    "prec": 6,
                    "lolim": 0,
                    "hilim": 1000,
                },
                f"{ch}:MOD_STAT": {"type": "enum", "enums": ["OFF", "ON"]},
                f"{ch}:MOD_TYP": {
                    "type": "enum",
                    "enums": [
                        "AM",
                        "FM",
                        "PM",
                        "ASK",
                        "FSK",
                        "PSK",
                        "BPSK",
                        "QPSK",
                        "OSK",
                        "PWM",
                    ],
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
            "COPY_1_TO_2": {"type": "int"},
            "COPY_2_TO_1": {"type": "int"},
            "PHASE_SYNC": {"type": "int"},
        }
    )

    server = SimpleServer()
    server.createPV(args.prefix, RIGOL_PVDB)
    visa_addr = (
        f"TCPIP0::{args.ip}::{args.port}::SOCKET"
        if args.port
        else f"TCPIP0::{args.ip}::INSTR"
    )
    driver = RigolDG4102Driver(visa_addr, args.terminator, RIGOL_PVDB)

    def shutdown_handler(signum, frame):
        logging.info("Shutting down...")
        driver.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info(f"Rigol DG4102 IOC started with prefix {args.prefix}")
    while True:
        server.process(0.1)
