# Flashing the SV04 mainboard

The SV04 mainboard is an STM32F103RET6 with a 28 KB Creality/Sovol bootloader at
`0x08000000–0x08006FFF`; Marlin is linked at `0x08007000`
(`buildroot/share/PlatformIO/ldscripts/creality.ld`,
`ini/stm32f1.ini` `board_build.offset = 0x7000`).

> **Key safety property:** every method below writes only the application region
> (`0x08007000` and up). The bootloader itself is never overwritten, so a failed
> or interrupted flash cannot brick the bootloader — you can always retry.

## Method 1 — SD card (official, recommended)

The bootloader reads a plain, unencrypted `firmware.bin` from the SD card and
renames it to `firmware.CUR` on success.

1. **Card:** FAT32, **4096-byte (4 KiB) allocation units**, <= 16 GB (8–16 GB
   ideal). Format with:
   ```bash
   sudo mkfs.vfat -F 32 -s 8 -n PRINTER /dev/sdX1   # -s 8 = 4 KiB clusters
   ```
   This cluster size is mandatory — a card formatted with smaller clusters is
   silently ignored by the bootloader.
2. Copy `firmware.bin` to the **root** of the card (exact lowercase name).
   Delete any existing `firmware.CUR` first.
3. **Correct slot:** insert into the **full-size SD slot on the bottom** of the
   printer (the mainboard slot). The touchscreen's **MicroSD slot is for display
   software only** — `firmware.bin` there does nothing.
4. Power on and wait for the menu. Success = the file becomes `firmware.CUR`.
5. Power-cycle, then confirm the version in *Settings → Printer Info* (or `M115`).

## Method 2 — Serial (STM32 ROM bootloader, no SD)

Uses the chip's built-in system-memory bootloader over **USART1** — the same
pins the CH340 bridges — so it can run over the USB cable. Requires physical
access to set **BOOT0**.

1. Set `BOOT0=1`, `BOOT1=0`, then reset/power-cycle (enters ROM bootloader).
2. `sudo apt install stm32flash`
3. Flash the app only, at its link offset:
   ```bash
   stm32flash -w firmware.bin -v -S 0x08007000 /dev/ttyUSB0
   ```
4. **Do not** mass-erase or write from `0x08000000` — that would clobber the
   bootloader. The `-S 0x08007000` offset is what preserves it.
5. Set `BOOT0=0`, reset.

Caveat: needs a BOOT0 jumper/button; many boards tie BOOT0 to GND with no
exposed pad.

## Method 3 — SWD (ST-Link)

Most robust, no SD and no BOOT0. Needs a cheap ST-Link clone on the SWD header
(SWDIO/SWCLK/GND, and optionally NRST/3.3V), flashed with STM32CubeProgrammer or
OpenOCD, writing `firmware.bin` at `0x08007000`.

Also useful post-install for **crash forensics** (halt the CPU and inspect where
it's stuck) — but SWD is a debug/programming port, not a serial monitor; for
live monitoring use the USB serial (see below).

## Version tracking

`SHORT_BUILD_VERSION` lives in `Marlin/src/inc/Version.h`:

| Firmware | Contents |
|----------|----------|
| `V1.1.1` | SD read-error abort (fix 1) |
| `V1.1.2` | watchdog feed, slower SPI clock, PLR default-off (fixes 2–4) |

## Monitoring (not flashing)

For live status while printing, connect via the CH340 USB port (115200 8N1):

```bash
.venv/bin/python tools/serial_monitor.py --interval 3
```
