#!/usr/bin/env python3
"""SV04 mainboard serial diagnostics via the onboard CH340 (USB) UART.

Connects to the printer's mainboard serial console (115200 8N1) and either:
  * monitors the stream, polling M27 (SD progress) + M105 (temps) so a freeze
    can be localised, or
  * sends a single G-code and prints the reply.

All raw output is timestamped and appended to a log file.
"""

import argparse
import sys
import time

import serial
import serial.tools.list_ports

DEFAULT_BAUD = 115200
VID = 0x1A86
PID = 0x7523


def find_port():
    hits = [p for p in serial.tools.list_ports.comports() if (p.vid, p.pid) == (VID, PID)]
    if hits:
        return hits[0].device
    for p in serial.tools.list_ports.comports():
        if "ch340" in p.description.lower() or "ch341" in p.description.lower():
            return p.device
    return None


def open_port(device, baud):
    s = serial.Serial()
    s.port = device
    s.baudrate = baud
    s.timeout = 1.0
    s.write_timeout = 1.0
    s.open()
    return s


def send(s, line):
    s.reset_input_buffer()
    s.write((line + "\n").encode())
    return s.read_until(b"ok").decode(errors="replace").strip()


def query(device, baud, gcode):
    s = open_port(device, baud)
    try:
        time.sleep(0.3)
        out = send(s, gcode)
        print(out)
    finally:
        s.close()


def monitor(device, baud, interval, logfile):
    s = open_port(device, baud)
    last_m27 = None
    stuck_count = 0
    with open(logfile, "a", buffering=1) as f:
        s.reset_input_buffer()
        s.write(b"\n")
        s.write(b"M111 S1\n")
        last_poll = 0.0
        while True:
            raw = s.read(4096)
            if raw:
                line = raw.decode(errors="replace")
                stamp = time.strftime("%H:%M:%S")
                f.write(f"[{stamp}] {line}")
                f.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            now = time.time()
            if now - last_poll >= interval:
                last_poll = now
                m27 = send(s, "M27")
                m105 = send(s, "M105")
                stamp = time.strftime("%H:%M:%S")
                record = f"[{stamp}] POLL M27={m27!r} M105={m105!r}\n"
                f.write(record)
                f.flush()
                sys.stdout.write(record)
                sys.stdout.flush()
                if m27 == last_m27:
                    stuck_count += 1
                    if stuck_count >= 3:
                        msg = "[!] SD progress unchanged for 3 polls - possible freeze\n"
                        f.write(msg)
                        sys.stdout.write(msg)
                        sys.stdout.flush()
                else:
                    stuck_count = 0
                last_m27 = m27


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", help="serial device (auto-detected if omitted)")
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--interval", type=float, default=5.0, help="poll interval for monitor mode")
    ap.add_argument("--log", default="sv04_serial.log", help="log file for monitor mode")
    ap.add_argument("--query", metavar="GCODE", help="send one G-code and exit (e.g. M115, M503, M413)")
    args = ap.parse_args()

    device = args.port or find_port()
    if not device:
        sys.exit("No CH340 serial device found. Check the USB connection and /dev/ttyUSB*.")

    print(f"Using serial device: {device} @ {args.baud}")

    if args.query:
        query(device, args.baud, args.query)
    else:
        print(f"Monitoring; logging to {args.log}. Ctrl-C to stop.")
        try:
            monitor(device, args.baud, args.interval, args.log)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
