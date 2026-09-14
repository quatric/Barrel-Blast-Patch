#!/usr/bin/env python3
"""Poll a handful of fixed Wii memory addresses live over a USB Gecko.

Protocol (confirmed against real USB Gecko client implementations, e.g.
CosmoCortney/MungPlex's USBGecko.cpp):
  - FTDI VCP serial, 115200 8N1.
  - Send 0x04 (cmd_readmem), expect one ack byte back: 0xAA (GCACK).
  - Send 8 bytes: big-endian uint32 start address, big-endian uint32 end
    address (exclusive).
  - Device streams back (end - start) raw bytes.
  - Send 0xAA (GCACK) to close out the read.

No console-side homebrew/hook is required for a plain memory read -- this
is the same "dump" path used for live memory viewing/cheat searching while
a retail game runs normally.
"""
import serial
import struct
import sys
import time

PORT = sys.argv[1] if len(sys.argv) > 1 else "/dev/cu.usbserial-GECKUSB0"
BAUD = 115200

CMD_READMEM = 0x04
GCACK = 0xAA

# (label, address, size in bytes)
WATCHES = [
    ("SIBUSY  0x80331538", 0x80331538, 4),   # si:: global transfer-busy flag; -1 = idle
    ("SIPOLLSH 0x8033153c", 0x8033153c, 4),  # shadow of SIPOLL (SISetXY mirror)
    ("TYPE[0] 0x80331550", 0x80331550, 4),   # per-channel cached SI type, chan 0
    ("TYPE[1] 0x80331554", 0x80331554, 4),
    ("TYPE[2] 0x80331558", 0x80331558, 4),
    ("TYPE[3] 0x8033155c", 0x8033155c, 4),
    ("SICOMCSR 0xcd006434", 0xcd006434, 4),  # hardware SI command/status reg
    ("SISR     0xcd006438", 0xcd006438, 4),  # hardware SI status (per-channel error bits)
    ("PEND[0] 0x803845b0", 0x803845b0, 4),   # per-channel queued-transfer sentinel (-1=none)
]


def read_mem(ser, start, end):
    ser.reset_input_buffer()
    ser.write(bytes([CMD_READMEM]))
    ack = ser.read(1)
    if len(ack) != 1 or ack[0] != GCACK:
        raise IOError(f"no ack for cmd_readmem (got {ack!r})")
    ser.write(struct.pack(">II", start, end))
    data = ser.read(end - start)
    if len(data) != end - start:
        raise IOError(f"short read: wanted {end-start}, got {len(data)}")
    ser.write(bytes([GCACK]))
    return data


def main():
    print(f"Opening {PORT} @ {BAUD} 8N1 ...")
    ser = serial.Serial(PORT, BAUD, timeout=2)
    time.sleep(0.5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    print("Connected. Polling every 0.25s -- Ctrl+C to stop.")
    print("Unplug/replug the GC pad now and watch for changes.\n")

    last = {}
    try:
        while True:
            row = []
            for label, addr, size in WATCHES:
                try:
                    data = read_mem(ser, addr, addr + size)
                    val = int.from_bytes(data, "big")
                    hexval = f"0x{val:0{size*2}x}"
                except IOError as e:
                    hexval = f"ERR({e})"
                changed = last.get(label) != hexval
                marker = " *" if changed and label in last else "  "
                row.append(f"{label}={hexval}{marker}")
                last[label] = hexval
            print(time.strftime("%H:%M:%S"), " | ".join(row))
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()


if __name__ == "__main__":
    main()
