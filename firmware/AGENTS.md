# AiPi-CAM-D200 project notes for the coding agent

Read this before changing code. Facts below were observed on the owner's real board unless marked "inferred".

## Hardware
- Board: Ai-Thinker AiPi-CAM-D200. Module: Ai-M61-32S (Bouffalo BL616/BL618). Camera: GC2145 (2 MP). 8 MB flash (JEDEC C84017).
- Owner connects through a CP2102 USB-to-TTL adapter on `uart0`: adapter TXD -> board RXD, adapter RXD -> board TXD, GND to GND, VCC to 5V.
- Wi-Fi is 2.4 GHz only.
- The README says the application was compile-checked, not hardware-validated. Camera streaming has since been seen working on this board; SD card, buttons and long-run stability were not tested. No SD card was inserted (boot log shows `fail to mount filesystem, error= 3`).

## Repository layout (Windows only toolchain)
- `AiPi-CAM-D200/` application. `main.c` initialises the board and creates FreeRTOS tasks.
- `AiPi-CAM-D200/d200_camera/` sensor, CAM, MJPEG, frame buffers.
- `AiPi-CAM-D200/bl618_wifi/` Wi-Fi init and callbacks.
- `AiPi-CAM-D200/ap_web/` HTTP capture and stream service (`mlwip_https.c`), port 80.
- `AiPi-CAM-D200/bl616_uart/` UART AT parser (`bl616_uart.c`, `qyq_frame_at.c/.h`).
- `AiPi-CAM-D200/filesystem/` FatFs SD card.
- `bouffalo_sdk/` vendored BouffaloSDK 2.0.0. Do not edit it, with one exception below.
- Boot log shows the sensor at 1280x720 (`dvp x: 1280`, `dvp y: 720`).

## Build and flash
- Never use a path with spaces or non-English characters anywhere in it. A folder called `G12 Capstone` made the final link step fail (the linker split the path at the space). Use names like `G12_Capstone`.
- Build: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1` (add `-Clean` for a clean build). Success ends with `Built target combine`; output is `AiPi-CAM-D200/build/build_out/AiPi-CAM-D200_bl616.bin`.
- About 20 SDK warnings are normal. Report only errors and new warnings in application files.
- Flash: `scripts\flash.ps1 -Port COMxx`. It calls `BLFlashCommand.exe` at 460800 baud, not the SDK's `make flash` (2000000 baud, too fast for a CP2102).
- **The board must be put in download mode by hand** (hold Burn, tap RST, keep holding Burn 2 s). Do not run the flash script unless the user has said they are ready. Never run it in the background.
- Only one program can hold a COM port. Never leave a serial session open; close it before flashing.

## Console and AT commands
- Console speed was changed from 2000000 to 115200 at `bouffalo_sdk/bsp/board/bl616dk/board.c` line 247 (`cfg.baudrate`). This is the only allowed edit inside `bouffalo_sdk/`. If this line is back at 2000000, a CP2102 shows garbled text.
- Use `scripts\at.ps1` to talk to the board. The AT parser only accepts a line that ends in exactly `\r\n` and arrives as one burst, and answers `ERROR` otherwise (typing in PuTTY failed this way).
- Commands: `AT+WJAP=ssid,password` (connect; no commas inside values), `AT+WJAP?` (print IPv4), `AT+LED=n` (capture LED flag).
- The command table is `qyq_frame_at_list[]` in `bl616_uart.c`. Entry order (inferred from usage): name, length, test_cb, query_cb, setup_cb, exe_cb. A callback returns 0 for `OK` and -1 for `ERROR`. `qyq_frame_at_init` rejects a table where `length != strlen(name)` or where two entries share the same name or the same callback function, so every new command needs its own callback.
- The receive buffer size is defined in `qyq_frame_at.h`/`bl616_uart.c` (`uart1_read_buf`); its size was not checked.

## Editing rules
- Many source comments are Chinese in UTF-8. Keep files UTF-8 and do not re-encode them. Do not translate or delete comments you are not changing.
- Keep application changes inside `AiPi-CAM-D200/`. Keep each change small, then run the build and show the result.
- Do not claim a feature works on hardware until the user has tested it. Say "builds" for compile-only results.
- Wi-Fi names and passwords belong to the user. Never write them into source files or commit them.
