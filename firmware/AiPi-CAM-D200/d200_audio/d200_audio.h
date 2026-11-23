#ifndef __D200_AUDIO_H_
#define __D200_AUDIO_H_

#include "chip_include.h"

/* Verified BL616/BL618 I2S Pinout:
 *   GPIO12 = I2S_BCLK  -> INMP441 SCK  + MAX98357A BCLK
 *   GPIO13 = I2S_FS    -> INMP441 WS   + MAX98357A LRC
 *   GPIO14 = I2S_DI    -> INMP441 SD   (tie INMP441 L/R to GND)
 *   GPIO15 = I2S_DO    -> MAX98357A DIN
 */
#define AUDIO_PIN_BCLK       GPIO_PIN_12
#define AUDIO_PIN_FS         GPIO_PIN_13
#define AUDIO_PIN_DI         GPIO_PIN_14
#define AUDIO_PIN_DO         GPIO_PIN_15

#define AUDIO_SAMPLE_RATE    16000
#define AUDIO_MAX_REC_SEC    8
#define AUDIO_MAX_PLAY_SEC   28

#define EDUVOICE_DEFAULT_HOST "192.168.0.121"
#define EDUVOICE_DEFAULT_PORT 8000

void d200_audio_init(void);
void d200_audio_set_host(const char *host, uint16_t port);
void d200_audio_get_host(char *host_buf, size_t buf_len, uint16_t *port_out);
void d200_audio_set_volume(uint8_t percent);
uint8_t d200_audio_get_volume(void);

/* Plays a short test tone through MAX98357A */
void d200_audio_play_beep(uint32_t freq_hz, uint32_t duration_ms);

/* Records from INMP441 for fixed duration and plays it back on MAX98357A (hardware loopback test) */
void d200_audio_loopback_test(uint32_t duration_ms);

/* Records while hold_check_fn() returns non-zero (up to AUDIO_MAX_REC_SEC),
 * snapshots the camera JPEG, sends both to laptop POST /board/ask,
 * and plays the returned 16 kHz PCM answer on MAX98357A.
 */
void d200_eduvoice_hold_and_ask(uint8_t (*hold_check_fn)(void));

/* Records for a fixed duration (ms), snapshots camera, sends to laptop, plays answer */
void d200_eduvoice_ask_fixed(uint32_t duration_ms);

#endif
