# SD read-error fix (Firmware 1.1.0 / Marlin 2.0.9.2)

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

## Fix

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

## Build

```
pio run -e STM32F103RET6_creality
```

Produces `.pio/build/STM32F103RET6_creality/firmware.bin`
(verified: Flash ~29%, RAM ~14%).

## Related observations (not yet changed)

- **SD card size:** a 64 GB SanDisk (FAT32, 32 KiB clusters) reproduced the
  failure; an 8-16 GB card with 4 KiB clusters (`mkfs.vfat -F 32 -s 8`) printed
  past the previous failure point with zero read errors. Recommend <= 32 GB.
- **Power-Loss Recovery wear:** `PLR_ENABLED_DEFAULT true` rewrites `/PLR` every
  layer (`powerloss.cpp`), wearing/corrupting cards over time.
- **Watchdog gap:** there is no `watchdog_refresh()` in the SD read path
  (`HAL/STM32F1/onboard_sd.cpp`), so a *hard* SD hang would still not reset the
  board. A follow-up fix should feed the watchdog during SD I/O.
- **SPI clock:** `onboard_sd.cpp` uses `SPI_BAUD_PCLK_DIV_4`; lowering to
  `DIV_8`/`DIV_16` improves tolerance of a marginal SD slot at the cost of read
  speed.
