# SD reliability fixes (Sovol SV04 / Marlin 2.0.9.2)

Target: Sovol SV04 (IDEX) mainboard, `STM32F103RET6_creality` build.
Tracked against upstream Sovol3d/SV04-IDEX-3D-Printer-Mainboard-Source-code.

## Symptom

Printing from the SD card stops mid-print (observed reproducibly at ~51% of a
calicat). The machine does **not** pause or stop: motion freezes, the touchscreen
shows the last progress, and the mainboard keeps spamming this over the host
serial (USART1 / CH340):

```
Error:SD read error
Error:SD read error
...
```

No error is surfaced and the mainboard never auto-resets, so it stays frozen with
the heaters potentially stuck at their last PWM duty. After a manual power-cycle,
Marlin's Power-Loss Recovery finds `/PLR` on the SD card and offers to resume.

## Root cause

`Marlin/src/gcode/queue.cpp`, `GCodeQueue::get_sdcard_commands()`:

```cpp
while (!ring_buffer.full() && !card.eof()) {
  const int16_t n = card.get();
  const bool card_eof = card.eof();
  if (n < 0 && !card_eof) { SERIAL_ERROR_MSG(STR_SD_ERR_READ); continue; }  // <-- spins forever
  ...
}
```

When the SD read fails, `SdBaseFile::read()` returns `-1` **without advancing the
file position** (`Marlin/src/sd/SdBaseFile.cpp`), so `card.eof()` (`sdpos >= filesize`)
never becomes true. The `continue` therefore retries forever, printing
`Error:SD read error` on every iteration.

Because `get_sdcard_commands()` runs inside the main loop
(`idle()` -> `manage_inactivity()` -> `get_available_commands()`), the whole loop
is wedged: no new moves are fetched, status stops reaching the display, and
`thermalManager.manage_heater()` (PID + thermal-runaway protection) never runs.
The `USE_WATCHDOG` feed lives in `manage_heater()`, so the hardware watchdog is
never refreshed either — hence no auto-reset.

## Fixes

### Fix 1 — abort on SD read error (was: infinite loop)

Treat a mid-file read error as a fatal print error and abort gracefully instead
of retrying:

```cpp
if (n < 0 && !card_eof) {
  SERIAL_ERROR_MSG(STR_SD_ERR_READ);
  card.abortFilePrintSoon();
  break;
}
```

`card.abortFilePrintSoon()` sets `flag.abort_sd_printing`. On the next main-loop
pass (`MarlinCore.cpp`), `abortSDPrinting()` runs and does the safe teardown:
close the file, clear the queue, `quickstop_stepper()`, abort the job timer,
`thermalManager.disable_all_heaters()` (heaters off), and `recovery.purge()`.
This converts "freeze with heaters stuck on" into a clean stop.

### Fix 2 — feed the watchdog during SD I/O

`Marlin/src/HAL/STM32F1/onboard_sd.cpp` polls the card with blocking loops
(`wait_ready()` and `rcvr_datablock()`) that previously never fed the watchdog.
Added `watchdog_refresh()` inside both loops. This prevents false watchdog resets
during legitimate (bounded) card waits while still allowing a genuine hardware
hang (inside `dmaTransfer`) to trip the IWDG and reset safely.

### Fix 3 — slower SD SPI clock

`onboard_sd.cpp`: `SPI_CLOCK_MAX` changed from `SPI_BAUD_PCLK_DIV_4` to
`SPI_BAUD_PCLK_DIV_8`. Halves the SPI clock, trading a little read throughput
for much better tolerance of a marginal SD slot / electrical noise.

### Fix 4 — Power-Loss Recovery off by default

`Configuration_adv.h`: `PLR_ENABLED_DEFAULT` changed from `true` to `false`.
The `/PLR` file is no longer rewritten every layer by default, avoiding the
constant small writes that wear/corrupt SD cards over time. Can be re-enabled
per-print with `M413 S1` + `M500`.

> Note: `M413` state is stored in EEPROM, so an existing board that already has
> PLR saved as enabled will keep it enabled until reset (`M502` + `M500`) or
> explicitly set `M413 S0` + `M500`.

## Version scheme

| Firmware | Contents |
|----------|----------|
| `V1.1.1` | Fix 1 |
| `V1.1.2` | Fixes 2, 3, 4 |

`SHORT_BUILD_VERSION` lives in `Marlin/src/inc/Version.h`.

## Build

```
pio run -e STM32F103RET6_creality
```

Produces `.pio/build/STM32F103RET6_creality/firmware.bin`
(verified: Flash ~29%, RAM ~14%). Flash by placing `firmware.bin` at the root of a
FAT32 SD card with 4 KiB clusters (`mkfs.vfat -F 32 -s 8`) and powering on; the
bootloader renames it to `firmware.CUR` on success.

## Remaining notes (not firmware changes)

- **SD card size:** a 64 GB SanDisk (FAT32, 32 KiB clusters) reproduced the
  failure; an 8-16 GB card with 4 KiB clusters printed past the previous failure
  point with zero read errors. Use <= 32 GB, ideally 8-16 GB.
- **Long filenames:** the touchscreen UI (RTS protocol) only lists `.gcode`
  files and truncates display names at ~20 chars; Sovol documents a 16-char /
  no-special-character limit. Keep filenames short and plain.
