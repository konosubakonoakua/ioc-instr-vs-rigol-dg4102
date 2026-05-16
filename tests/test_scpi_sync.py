import pyvisa
import time
import sys

# Configuration - update these to match your setup
VISA_ADDR = "TCPIP::21.21.25.1::INSTR" 
TIMEOUT = 5000

def run_test(delay):
    print(f"\n--- Testing with DELAY = {delay}s ---")
    rm = pyvisa.ResourceManager()
    try:
        instr = rm.open_resource(VISA_ADDR)
        instr.timeout = TIMEOUT
        instr.write_termination = '\n'
        instr.read_termination = '\n'
        
        # 1. Reset and Clear
        print("Clearing buffer...")
        instr.write("*CLS")
        time.sleep(0.1)
        
        queries = [
            ("*IDN?", "string"),
            ("SOUR1:FUNC?", "string"),
            ("SOUR1:VOLT?", "numeric"),
            ("SOUR1:VOLT:OFFS?", "numeric"),
            ("SOUR1:FREQ?", "numeric"),
            ("OUTP1?", "string"),
            ("SOUR2:FUNC?", "string"),
            ("SOUR2:VOLT?", "numeric"),
            ("SOUR2:FREQ?", "numeric"),
            ("OUTP2?", "string"),
        ]
        
        success_count = 0
        for cmd, expected in queries:
            try:
                time.sleep(delay)
                res = instr.query(cmd).strip().replace('"', "")
                print(f"  Q: {cmd:20} -> R: {res}")
                
                # Simple validation
                if expected == "numeric":
                    float(res)
                elif cmd == "*IDN?" and "Rigol" not in res:
                    raise ValueError("IDN Mismatch")
                
                success_count += 1
            except Exception as e:
                print(f"  FAILED Query '{cmd}': {e}")
                instr.clear()
                instr.write("*CLS")
                # Stop this run on failure to prevent cascading
                break
                
        print(f"Result: {success_count}/{len(queries)} queries successful.")
        instr.close()
        return success_count == len(queries)

    except Exception as e:
        print(f"Connection/Global error: {e}")
        return False
    finally:
        rm.close()

if __name__ == "__main__":
    print(f"Starting Rigol DG4102 Communication Test on {VISA_ADDR}")
    
    # Try different delays from fast to slow
    delays = [0.01, 0.05, 0.1, 0.2]
    
    for d in delays:
        if run_test(d):
            print(f"\n>>> SUCCESS! Minimum stable delay found: {d}s")
            #sys.exit(0)
        else:
            print(f"\n>>> FAILED at delay {d}s")
    
    print("\nTests completed.")
