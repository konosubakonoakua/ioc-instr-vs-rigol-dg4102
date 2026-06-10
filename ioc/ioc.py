import pyvisa
from pcaspy import Driver, SimpleServer
from rich.pretty import pprint
import argparse
import logging
import signal
import socket
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

        # Lazy reconnect with exponential backoff
        self._last_attempt = 0
        self._backoff = 1
        self._consecutive_errors = 0

        # Read cache and rate limiting
        self._read_cache = {}  # {pv_name: (timestamp, value)}
        self._last_query_time = 0

        # Lightweight heartbeat
        self.heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self.heartbeat_thread.start()
        self._needs_sync = True

    def _ensure_connected(self):
        """Lazy connect with exponential backoff. Returns True if connected."""
        if self.connected and self.instr:
            return True

        now = time.time()
        if now - self._last_attempt < self._backoff:
            return False

        with self.lock:
            # Re-check after acquiring lock — another thread may have connected
            if self.connected and self.instr:
                return True
            self._last_attempt = now

            try:
                logging.info(f"Attempting to connect to {self.visa_addr}...")

                # Set socket timeout to avoid blocking on OS TCP timeout (up to 127s)
                old_timeout = socket.getdefaulttimeout()
                socket.setdefaulttimeout(5)
                try:
                    self.instr = self.rm.open_resource(self.visa_addr)
                finally:
                    socket.setdefaulttimeout(old_timeout)

                self.instr.write_termination = self.terminator
                self.instr.read_termination = self.terminator
                self.instr.timeout = 3000

                # Initial cleanup
                self.instr.write("*CLS")
                time.sleep(0.1)

                # Check IDN
                idn = self.instr.query("*IDN?").strip()
                if idn:
                    logging.info(f"Connected to: {idn}")
                    self.connected = True
                    self._backoff = 1
                    self._consecutive_errors = 0
                    # One-shot background sync after fresh connection
                    if self._needs_sync:
                        self._needs_sync = False
                        threading.Thread(
                            target=self._trigger_sync, daemon=True
                        ).start()
                    else:
                        self.setParam("COMM_STATUS", 2)  # Connected
                        self.updatePVs()
                    return True
                return False
            except Exception as e:
                logging.error(f"Connection failed: {e}")
                self._backoff = min(self._backoff * 2, 60)
                self._force_reconnect()
                return False

    def _force_reconnect(self):
        """Force a disconnect and recreate ResourceManager to clear all state."""
        with self.lock:
            if self.connected or self.instr:
                logging.warning("Forcing disconnect to reset communication state...")
            self.connected = False
            self.setParam("COMM_STATUS", 0)
            self.updatePVs()
            if self.instr:
                try:
                    self.instr.timeout = 500
                    self.instr.close()
                except:
                    pass
            self.instr = None
            try:
                self.rm.close()
            except:
                pass
            self.rm = pyvisa.ResourceManager()
            self._read_cache.clear()
            self._needs_sync = True

    def _safe_query(self, cmd, expected_type=None):
        """Perform a query with tolerance for transient errors."""
        if not self.instr or not self.connected:
            return None

        with self.lock:
            # Re-check after acquiring lock — close() may have set instr to None
            if not self.instr or not self.connected:
                return None
            try:
                # Dynamic minimum interval between queries
                elapsed = time.time() - self._last_query_time
                if elapsed < 0.03:
                    time.sleep(0.03 - elapsed)
                val = self.instr.query(cmd).strip().replace('"', "")
                self._last_query_time = time.time()

                if not val:
                    self._consecutive_errors += 1
                    if self._consecutive_errors >= 3:
                        self._force_reconnect()
                        self._consecutive_errors = 0
                    return None

                if expected_type == "numeric":
                    try:
                        float(val)
                    except ValueError:
                        logging.error(
                            f"Sync loss detected: Query '{cmd}' expected number but got '{val}'."
                        )
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= 3:
                            self._force_reconnect()
                            self._consecutive_errors = 0
                        return None

                self._consecutive_errors = 0
                return val
            except Exception as e:
                logging.error(f"Query '{cmd}' failed: {e}")
                self._consecutive_errors += 1
                if self._consecutive_errors >= 3:
                    self._force_reconnect()
                    self._consecutive_errors = 0
                return None

    def _heartbeat_loop(self):
        """Lightweight heartbeat — *OPC? every 10s to detect silent disconnects."""
        while True:
            time.sleep(10)
            if not self.connected:
                continue
            try:
                with self.lock:
                    if not self.connected:
                        continue
                    res = self.instr.query("*OPC?").strip()
                    if res != "1":
                        self._consecutive_errors += 1
                        logging.warning(
                            f"Heartbeat: OPC sync lost got '{res}' "
                            f"({self._consecutive_errors}/3)"
                        )
                        if self._consecutive_errors >= 3:
                            self._force_reconnect()
                    else:
                        self._consecutive_errors = 0
            except Exception as e:
                logging.error(f"Heartbeat failed: {e}")
                self._consecutive_errors += 1
                if self._consecutive_errors >= 3:
                    self._force_reconnect()

    def _sync_all_pvs(self):
        """Read all PVs from device. Returns count of successful reads."""
        skip = {"IDN", "COMM_STATUS", "SYNC_ALL"}
        count = 0
        for pv_name in list(self.pvdb):
            if pv_name in skip:
                continue
            try:
                self.read(pv_name)
                count += 1
            except Exception as e:
                logging.error(f"Sync error for {pv_name}: {e}")
        return count

    def _trigger_sync(self):
        """Full sync: set Syncing, read all PVs, then set Connected."""
        if not self.connected:
            return
        self.setParam("COMM_STATUS", 1)  # Syncing
        self.updatePVs()
        count = self._sync_all_pvs()
        logging.info(f"Sync complete: {count} PVs updated")
        self.setParam("COMM_STATUS", 2)  # Connected
        self.updatePVs()

    def close(self):
        """Clean shutdown of instrument and resource manager."""
        with self.lock:
            self.connected = False
            if self.instr:
                try:
                    self.instr.timeout = 500
                    self.instr.close()
                except:
                    pass
            self.instr = None
        try:
            self.rm.close()
        except:
            pass

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
            "BURST_GATE_POL": ("SOUR{ch}:BURS:GATE:POL", ""),
            "BURST_PHASE": ("SOUR{ch}:BURS:PHAS", ""),
            "BURST_TDELAY": ("SOUR{ch}:BURS:TDEL", ""),
            "BURST_TRIG_SLOP": ("SOUR{ch}:BURS:TRIG:SLOP", ""),
            "BURST_TRIG_OUT": ("SOUR{ch}:BURS:TRIG:TRIGO", ""),
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
        if reason in {"COMM_STATUS", "SYNC_ALL"}:
            if reason == "SYNC_ALL":
                self.setParam("SYNC_ALL", 1)
                self.updatePVs()
                threading.Thread(target=self._trigger_sync, daemon=True).start()
                time.sleep(0.05)
                self.setParam("SYNC_ALL", 0)
                self.updatePVs()
            return True

        if not self.connected:
            if not self._ensure_connected():
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
                        time.sleep(0.05)
                        self.setParam(reason, 1)
                        self.updatePVs()
                        time.sleep(0.1)
                        self.setParam(reason, 0)
                        self.updatePVs()
                        self._read_cache.clear()
                        return True
                    except:
                        self._force_reconnect()
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
                        # Don't query *OPC? here to avoid socket collision
                        time.sleep(0.02)
                        if suffix != "TRIG_MANUAL":
                            self.setParam(reason, epics_value)
                        self._read_cache.pop(reason, None)
                        return True
                    except:
                        self._force_reconnect()
                        return False
        return False

    @log_exceptions
    def read(self, reason):
        if reason == "COMM_STATUS":
            return self.getParam(reason)
        if reason == "IDN":
            if not self.connected:
                if not self._ensure_connected():
                    return "Disconnected"
            with self.lock:
                try:
                    val = self.instr.query("*IDN?").strip()
                    self.setParam(reason, val)
                    return val
                except:
                    self._force_reconnect()
                    return self.getParam(reason)

        if not self.connected:
            if not self._ensure_connected():
                return self.getParam(reason)

        channel, suffix = self._parse_reason(reason)
        if channel:
            expected = "numeric"
            if reason in self.pvdb and self.pvdb[reason].get("type") == "enum":
                expected = "enum"
            cmd = self._get_scpi_cmd(channel, suffix, is_query=True)
            if cmd:
                # Check read cache (2s TTL)
                cached = self._read_cache.get(reason)
                if cached and time.time() - cached[0] < 2.0:
                    return cached[1]

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
                        self._read_cache[reason] = (time.time(), index)
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
                        self._read_cache[reason] = (time.time(), val)
                        return val
                    except:
                        return self.getParam(reason)
        return self.getParam(reason)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rigol DG4102 IOC")
    parser.add_argument("--ip", type=str, default="192.168.1.100")
    parser.add_argument("--port", type=str, default="")
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
                f"{ch}:BURST_GATE_POL": {"type": "enum", "enums": ["NORM", "INV"]},
                f"{ch}:BURST_PHASE": {
                    "type": "float",
                    "unit": "deg",
                    "prec": 2,
                    "lolim": 0,
                    "hilim": 360,
                },
                f"{ch}:BURST_TDELAY": {
                    "type": "float",
                    "unit": "s",
                    "prec": 9,
                    "lolim": 0,
                    "hilim": 1000,
                },
                f"{ch}:BURST_TRIG_SLOP": {"type": "enum", "enums": ["POS", "NEG"]},
                f"{ch}:BURST_TRIG_OUT": {"type": "enum", "enums": ["OFF", "POS", "NEG"]},
            }
        )

    RIGOL_PVDB.update(
        {
            "IDN": {"type": "string"},
            "COMM_STATUS": {"type": "enum", "enums": ["Disconnected", "Syncing", "Connected"]},
            "SYNC_ALL": {"type": "int"},
            "SYSTEM_PRESET": {"type": "int"},
            "COPY_1_TO_2": {"type": "int"},
            "COPY_2_TO_1": {"type": "int"},
            "PHASE_SYNC": {"type": "int"},
        }
    )

    server = SimpleServer()
    server.createPV(args.prefix, RIGOL_PVDB)
    if args.port in ["", "0"]:
        visa_addr = f"TCPIP0::{args.ip}::INSTR"
    else:
        visa_addr = f"TCPIP0::{args.ip}::{args.port}::SOCKET"
    driver = RigolDG4102Driver(visa_addr, args.terminator, RIGOL_PVDB)

    # One-shot background connection attempt — OPI opens with connection ready
    threading.Timer(0.5, lambda: driver._ensure_connected()).start()

    def shutdown_handler(signum, frame):
        logging.info("Shutting down...")
        driver.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    logging.info(f"Rigol DG4102 IOC started with prefix {args.prefix}")
    while True:
        server.process(0.1)
