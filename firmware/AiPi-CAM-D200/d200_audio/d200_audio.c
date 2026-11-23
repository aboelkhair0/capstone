#define __D200_AUDIO_C_
#include "d200_audio.h"
#include "d200_camera.h"
#include "bflb_i2s.h"
#include "bflb_dma.h"
#include "bflb_l1c.h"
#include "semphr.h"

extern uint8_t *pic_addr;
extern uint32_t pic_size;
extern void *camera_muxsem_handle;

/* DMA chunk: 512 stereo 32-bit frames = 1024 uint32_t = 4096 bytes = 32 ms at 16 kHz */
#define AUDIO_CHUNK_FRAMES   512
#define AUDIO_CHUNK_WORDS    (AUDIO_CHUNK_FRAMES * 2)
#define AUDIO_CHUNK_BYTES    (AUDIO_CHUNK_WORDS * sizeof(int32_t))

#define AUDIO_MAX_REC_SAMPLES   (AUDIO_SAMPLE_RATE * AUDIO_MAX_REC_SEC)
#define AUDIO_MAX_PLAY_SAMPLES  (AUDIO_SAMPLE_RATE * AUDIO_MAX_PLAY_SEC)
#define SNAP_MAX_JPG_BYTES      (160 * 1024)

/* Non-cached SRAM DMA ping-pong buffers (8 KB total) */
static ATTR_NOCACHE_NOINIT_RAM_SECTION int32_t s_dma_buf[2][AUDIO_CHUNK_WORDS] __ALIGNED(4);

/* PSRAM buffers for recording (256 KB), playback (512 KB), and camera snapshot (160 KB) */
static int16_t __attribute__((section(".psram_noinit"), aligned(32))) s_mic_pcm[AUDIO_MAX_REC_SAMPLES];
static int16_t __attribute__((section(".psram_noinit"), aligned(32))) s_spk_pcm[AUDIO_MAX_PLAY_SAMPLES];
static uint8_t __attribute__((section(".psram_noinit"), aligned(32))) s_snap_jpg[SNAP_MAX_JPG_BYTES];

static struct bflb_device_s *s_i2s0 = NULL;
static struct bflb_device_s *s_dma_tx = NULL;
static struct bflb_device_s *s_dma_rx = NULL;
static struct bflb_dma_channel_lli_pool_s s_tx_lli[2];
static struct bflb_dma_channel_lli_pool_s s_rx_lli[2];

static SemaphoreHandle_t s_tx_done_sem = NULL;
static SemaphoreHandle_t s_rx_done_sem = NULL;
static SemaphoreHandle_t s_audio_mutex = NULL;
static volatile int s_rx_ready_idx = 0;
static volatile int s_rx_next_idx = 0;
static uint8_t s_audio_ready = 0;

static char s_host_ip[64] = EDUVOICE_DEFAULT_HOST;
static uint16_t s_host_port = EDUVOICE_DEFAULT_PORT;
static uint8_t s_spk_volume = 80; /* 5..100% — adjustable live via AT+VOL=<5..100> */

static void i2s_dma_tx_isr(void *arg)
{
    (void)arg;
    BaseType_t woken = pdFALSE;
    if (s_tx_done_sem != NULL) {
        xSemaphoreGiveFromISR(s_tx_done_sem, &woken);
        portYIELD_FROM_ISR(woken);
    }
}

static void i2s_dma_rx_isr(void *arg)
{
    (void)arg;
    BaseType_t woken = pdFALSE;
    s_rx_ready_idx = s_rx_next_idx;
    s_rx_next_idx ^= 1;
    if (s_rx_done_sem != NULL) {
        xSemaphoreGiveFromISR(s_rx_done_sem, &woken);
        portYIELD_FROM_ISR(woken);
    }
}

void d200_audio_set_host(const char *host, uint16_t port)
{
    if (host != NULL && host[0] != '\0') {
        snprintf(s_host_ip, sizeof(s_host_ip), "%s", host);
    }
    if (port > 0) {
        s_host_port = port;
    }
    printf("EduVoice host set to %s:%u\r\n", s_host_ip, (unsigned int)s_host_port);
}

void d200_audio_get_host(char *host_buf, size_t buf_len, uint16_t *port_out)
{
    if (host_buf != NULL && buf_len > 0) {
        snprintf(host_buf, buf_len, "%s", s_host_ip);
    }
    if (port_out != NULL) {
        *port_out = s_host_port;
    }
}

void d200_audio_set_volume(uint8_t percent)
{
    if (percent < 5) percent = 5;
    if (percent > 100) percent = 100;
    s_spk_volume = percent;
    printf("Speaker volume set to %u%%\r\n", (unsigned int)s_spk_volume);
}

uint8_t d200_audio_get_volume(void)
{
    return s_spk_volume;
}

void d200_audio_init(void)
{
    struct bflb_device_s *gpio;

    if (s_audio_ready) {
        return;
    }

    s_tx_done_sem = xSemaphoreCreateBinary();
    s_rx_done_sem = xSemaphoreCreateBinary();
    s_audio_mutex = xSemaphoreCreateMutex();

    /* Configure Audio PLL to 491.52 MHz for exact 16 kHz I2S clock (0% drift) */
    GLB_Config_AUDIO_PLL_To_491P52M();
    GLB_Set_I2S_CLK(ENABLE, 0, GLB_I2S_DI_SEL_I2S_DI_INPUT, GLB_I2S_DO_SEL_I2S_DO_OUTPT);

    gpio = bflb_device_get_by_name("gpio");
    bflb_gpio_init(gpio, AUDIO_PIN_BCLK, GPIO_FUNC_I2S | GPIO_ALTERNATE | GPIO_PULLUP   | GPIO_SMT_EN | GPIO_DRV_2);
    bflb_gpio_init(gpio, AUDIO_PIN_FS,   GPIO_FUNC_I2S | GPIO_ALTERNATE | GPIO_PULLUP   | GPIO_SMT_EN | GPIO_DRV_2);
    bflb_gpio_init(gpio, AUDIO_PIN_DI,   GPIO_FUNC_I2S | GPIO_ALTERNATE | GPIO_PULLDOWN | GPIO_SMT_EN | GPIO_DRV_2);
    bflb_gpio_init(gpio, AUDIO_PIN_DO,   GPIO_FUNC_I2S | GPIO_ALTERNATE | GPIO_PULLUP   | GPIO_SMT_EN | GPIO_DRV_2);

    /* Standard Philips I2S: 16 kHz, 32-bit stereo frame (64 BCLKs/frame = 1.024 MHz), 1-BCLK offset.
     * Works natively with both INMP441 (24-bit in 32-bit slot) and MAX98357A (32-bit slot).
     */
    struct bflb_i2s_config_s i2s_cfg = {
        .bclk_freq_hz = AUDIO_SAMPLE_RATE * 32 * 2,
        .role = I2S_ROLE_MASTER,
        .format_mode = I2S_MODE_LEFT_JUSTIFIED,
        .channel_mode = I2S_CHANNEL_MODE_NUM_2,
        .frame_width = I2S_SLOT_WIDTH_32,
        .data_width = I2S_SLOT_WIDTH_32,
        .fs_offset_cycle = 1,
        .tx_fifo_threshold = 4,
        .rx_fifo_threshold = 4,
    };

    s_i2s0 = bflb_device_get_by_name("i2s0");
    bflb_i2s_init(s_i2s0, &i2s_cfg);
    bflb_i2s_link_txdma(s_i2s0, true);
    bflb_i2s_link_rxdma(s_i2s0, true);

    struct bflb_dma_channel_config_s tx_cfg = {
        .direction = DMA_MEMORY_TO_PERIPH,
        .src_req = DMA_REQUEST_NONE,
        .dst_req = DMA_REQUEST_I2S_TX,
        .src_addr_inc = DMA_ADDR_INCREMENT_ENABLE,
        .dst_addr_inc = DMA_ADDR_INCREMENT_DISABLE,
        .src_burst_count = DMA_BURST_INCR1,
        .dst_burst_count = DMA_BURST_INCR1,
        .src_width = DMA_DATA_WIDTH_32BIT,
        .dst_width = DMA_DATA_WIDTH_32BIT,
    };

    struct bflb_dma_channel_config_s rx_cfg = {
        .direction = DMA_PERIPH_TO_MEMORY,
        .src_req = DMA_REQUEST_I2S_RX,
        .dst_req = DMA_REQUEST_NONE,
        .src_addr_inc = DMA_ADDR_INCREMENT_DISABLE,
        .dst_addr_inc = DMA_ADDR_INCREMENT_ENABLE,
        .src_burst_count = DMA_BURST_INCR1,
        .dst_burst_count = DMA_BURST_INCR1,
        .src_width = DMA_DATA_WIDTH_32BIT,
        .dst_width = DMA_DATA_WIDTH_32BIT,
    };

    s_dma_tx = bflb_device_get_by_name("dma0_ch0");
    s_dma_rx = bflb_device_get_by_name("dma0_ch1");
    bflb_dma_channel_init(s_dma_tx, &tx_cfg);
    bflb_dma_channel_init(s_dma_rx, &rx_cfg);
    bflb_dma_channel_irq_attach(s_dma_tx, i2s_dma_tx_isr, NULL);
    bflb_dma_channel_irq_attach(s_dma_rx, i2s_dma_rx_isr, NULL);

    /* Keep I2S data paths idle until recording or playback starts */
    bflb_i2s_feature_control(s_i2s0, I2S_CMD_DATA_ENABLE, 0);

    s_audio_ready = 1;
    printf("d200_audio init OK (16kHz I2S: BCLK=12 FS=13 DI=14 DO=15, host=%s:%u)\r\n",
           s_host_ip, (unsigned int)s_host_port);
}

static inline int16_t inmp441_word_to_pcm16(int32_t raw_word)
{
    /* INMP441 puts 24-bit signed audio in bits [31:8].
     * Shift right by 12 (clean natural voice gain without clipping).
     */
    int32_t v = raw_word >> 12;
    if (v > 32767) {
        return 32767;
    }
    if (v < -32768) {
        return -32768;
    }
    return (int16_t)v;
}

static void start_rx_dma_ring(void)
{
    /* Continuous 2-buffer hardware circular ping-pong DMA ring (zero gap between 32ms chunks) */
    struct bflb_dma_channel_lli_transfer_s xfers[2];
    xfers[0].src_addr = (uint32_t)DMA_ADDR_I2S_RDR;
    xfers[0].dst_addr = (uint32_t)(uintptr_t)s_dma_buf[0];
    xfers[0].nbytes   = AUDIO_CHUNK_BYTES;
    xfers[1].src_addr = (uint32_t)DMA_ADDR_I2S_RDR;
    xfers[1].dst_addr = (uint32_t)(uintptr_t)s_dma_buf[1];
    xfers[1].nbytes   = AUDIO_CHUNK_BYTES;

    s_rx_next_idx = 0;
    s_rx_ready_idx = 0;
    int used = bflb_dma_channel_lli_reload(s_dma_rx, s_rx_lli, 2, xfers, 2);
    if (used > 0) {
        bflb_dma_channel_lli_link_head(s_dma_rx, s_rx_lli, (uint32_t)used);
        bflb_dma_channel_start(s_dma_rx);
    }
}

static void start_tx_dma(int buf_idx, uint32_t nbytes)
{
    struct bflb_dma_channel_lli_transfer_s xfer;
    xfer.src_addr = (uint32_t)(uintptr_t)s_dma_buf[buf_idx];
    xfer.dst_addr = (uint32_t)DMA_ADDR_I2S_TDR;
    xfer.nbytes = nbytes;
    bflb_dma_channel_lli_reload(s_dma_tx, s_tx_lli, 2, &xfer, 1);
    bflb_dma_channel_start(s_dma_tx);
}

static void fill_tx_stereo_buf(int buf_idx, const int16_t *mono_pcm, uint32_t frames)
{
    int32_t *dst = s_dma_buf[buf_idx];
    int32_t vol = (int32_t)s_spk_volume;
    for (uint32_t i = 0; i < frames; i++) {
        int32_t scaled = ((int32_t)mono_pcm[i] * vol) / 100;
        if (scaled > 32767) scaled = 32767;
        if (scaled < -32768) scaled = -32768;
        int32_t s = scaled << 16;
        dst[2 * i]     = s; /* Left */
        dst[2 * i + 1] = s; /* Right (MAX98357A mixes (L+R)/2 by default) */
    }
}

static void play_pcm_buffer_locked(const int16_t *pcm, uint32_t num_samples)
{
    if (!s_audio_ready || pcm == NULL || num_samples == 0) {
        return;
    }

    bflb_i2s_feature_control(s_i2s0, I2S_CMD_CLEAR_TX_FIFO, true);
    xSemaphoreTake(s_tx_done_sem, 0);

    uint32_t pos = 0;
    int cur = 0;

    uint32_t first_frames = (num_samples - pos > AUDIO_CHUNK_FRAMES) ? AUDIO_CHUNK_FRAMES : (num_samples - pos);
    fill_tx_stereo_buf(cur, &pcm[pos], first_frames);
    pos += first_frames;

    bflb_i2s_feature_control(s_i2s0, I2S_CMD_DATA_ENABLE, I2S_CMD_DATA_ENABLE_TX);
    start_tx_dma(cur, first_frames * 2 * sizeof(int32_t));

    while (pos < num_samples) {
        int next = cur ^ 1;
        uint32_t next_frames = (num_samples - pos > AUDIO_CHUNK_FRAMES) ? AUDIO_CHUNK_FRAMES : (num_samples - pos);
        fill_tx_stereo_buf(next, &pcm[pos], next_frames);
        pos += next_frames;

        if (xSemaphoreTake(s_tx_done_sem, pdMS_TO_TICKS(500)) != pdTRUE) {
            printf("I2S TX DMA timeout\r\n");
            break;
        }
        start_tx_dma(next, next_frames * 2 * sizeof(int32_t));
        cur = next;
    }

    /* Wait for final chunk + tiny flush of silence so speaker doesn't pop */
    xSemaphoreTake(s_tx_done_sem, pdMS_TO_TICKS(500));
    memset(s_dma_buf[0], 0, AUDIO_CHUNK_BYTES);
    start_tx_dma(0, 64 * 2 * sizeof(int32_t));
    xSemaphoreTake(s_tx_done_sem, pdMS_TO_TICKS(200));

    bflb_dma_channel_stop(s_dma_tx);
    bflb_i2s_feature_control(s_i2s0, I2S_CMD_DATA_ENABLE, 0);
}

static uint32_t record_pcm_locked(uint8_t (*hold_check_fn)(void), uint32_t max_ms)
{
    if (!s_audio_ready) {
        return 0;
    }

    uint32_t max_samples = (AUDIO_SAMPLE_RATE * max_ms) / 1000;
    if (max_samples > AUDIO_MAX_REC_SAMPLES) {
        max_samples = AUDIO_MAX_REC_SAMPLES;
    }

    bflb_dma_channel_stop(s_dma_rx);
    bflb_i2s_feature_control(s_i2s0, I2S_CMD_CLEAR_RX_FIFO, true);
    xSemaphoreTake(s_rx_done_sem, 0);

    /* Start continuous circular ping-pong DMA BEFORE enabling I2S RX so zero words are dropped */
    start_rx_dma_ring();
    bflb_i2s_feature_control(s_i2s0, I2S_CMD_DATA_ENABLE, I2S_CMD_DATA_ENABLE_RX);

    /* Discard first 4 chunks (~128ms) while INMP441 wakes up (>83ms datasheet spec) */
    for (int w = 0; w < 4; w++) {
        if (xSemaphoreTake(s_rx_done_sem, pdMS_TO_TICKS(300)) != pdTRUE) {
            break;
        }
    }

    uint32_t total = 0;
    int32_t dc_acc = 0;
    int32_t peak_l = 0;
    int32_t peak_r = 0;

    while (total + AUDIO_CHUNK_FRAMES <= max_samples) {
        if (hold_check_fn != NULL && !hold_check_fn()) {
            break;
        }
        if (xSemaphoreTake(s_rx_done_sem, pdMS_TO_TICKS(300)) != pdTRUE) {
            printf("I2S RX DMA timeout\r\n");
            break;
        }

        const int32_t *src = s_dma_buf[s_rx_ready_idx];
        /* Combine L + R slots so INMP441 works whether L/R pin is tied to GND or 3.3V */
        for (uint32_t i = 0; i < AUDIO_CHUNK_FRAMES; i++) {
            int32_t sl = inmp441_word_to_pcm16(src[2 * i]);
            int32_t sr = inmp441_word_to_pcm16(src[2 * i + 1]);
            int32_t al = sl < 0 ? -sl : sl;
            int32_t ar = sr < 0 ? -sr : sr;
            if (al > peak_l) peak_l = al;
            if (ar > peak_r) peak_r = ar;

            int32_t mixed = sl + sr;
            if (mixed > 32767) mixed = 32767;
            if (mixed < -32768) mixed = -32768;

            /* Clean fixed-point DC blocker (no integer-division stuck offset) */
            dc_acc += mixed - (dc_acc >> 7);
            int32_t centered = mixed - (dc_acc >> 7);
            if (centered > 32767) centered = 32767;
            if (centered < -32768) centered = -32768;
            s_mic_pcm[total++] = (int16_t)centered;
        }
    }

    bflb_dma_channel_stop(s_dma_rx);
    bflb_i2s_feature_control(s_i2s0, I2S_CMD_DATA_ENABLE, 0);
    printf("Mic capture: %lu samples (peak_L=%ld, peak_R=%ld)\r\n",
           (unsigned long)total, (long)peak_l, (long)peak_r);
    return total;
}

void d200_audio_play_beep(uint32_t freq_hz, uint32_t duration_ms)
{
    if (!s_audio_ready) {
        return;
    }
    if (freq_hz < 100) freq_hz = 440;
    uint32_t samples = (AUDIO_SAMPLE_RATE * duration_ms) / 1000;
    if (samples > AUDIO_SAMPLE_RATE * 2) {
        samples = AUDIO_SAMPLE_RATE * 2;
    }

    xSemaphoreTake(s_audio_mutex, portMAX_DELAY);
    /* Simple triangle wave (no math.h sinf needed, clean pleasant tone) */
    uint32_t period = AUDIO_SAMPLE_RATE / freq_hz;
    if (period < 4) period = 4;
    uint32_t half = period / 2;
    for (uint32_t i = 0; i < samples; i++) {
        uint32_t phase = i % period;
        int32_t tri = (phase < half)
            ? ((int32_t)(phase * 30000 / half) - 15000)
            : (15000 - (int32_t)((phase - half) * 30000 / half));
        s_spk_pcm[i] = (int16_t)tri;
    }
    play_pcm_buffer_locked(s_spk_pcm, samples);
    xSemaphoreGive(s_audio_mutex);
}

void d200_audio_loopback_test(uint32_t duration_ms)
{
    if (!s_audio_ready) {
        return;
    }
    if (duration_ms < 500) duration_ms = 500;
    if (duration_ms > AUDIO_MAX_REC_SEC * 1000) duration_ms = AUDIO_MAX_REC_SEC * 1000;

    struct bflb_device_s *gpio = bflb_device_get_by_name("gpio");
    xSemaphoreTake(s_audio_mutex, portMAX_DELAY);
    printf("Mic loopback: recording %lu ms from INMP441...\r\n", (unsigned long)duration_ms);
    bflb_gpio_set(gpio, GPIO_PIN_16);
    uint32_t n = record_pcm_locked(NULL, duration_ms);
    bflb_gpio_reset(gpio, GPIO_PIN_16);

    int32_t peak = 0;
    for (uint32_t i = 0; i < n; i++) {
        int32_t a = s_mic_pcm[i] < 0 ? -s_mic_pcm[i] : s_mic_pcm[i];
        if (a > peak) peak = a;
    }
    printf("Mic loopback: recorded %lu samples, peak=%ld. Playing back on MAX98357A...\r\n",
           (unsigned long)n, (long)peak);
    play_pcm_buffer_locked(s_mic_pcm, n);
    printf("Mic loopback done.\r\n");
    xSemaphoreGive(s_audio_mutex);
}

static uint32_t snapshot_camera_jpeg(void)
{
    uint32_t len = 0;
    if (camera_muxsem_handle == NULL || pic_addr == NULL) {
        return 0;
    }
    xSemaphoreTake(camera_muxsem_handle, portMAX_DELAY);
    if (pic_size > 0 && pic_size <= SNAP_MAX_JPG_BYTES) {
        memcpy(s_snap_jpg, pic_addr, pic_size);
        len = pic_size;
    }
    xSemaphoreGive(camera_muxsem_handle);
    return len;
}

static int send_all(int sock, const uint8_t *buf, uint32_t len)
{
    uint32_t sent = 0;
    while (sent < len) {
        uint32_t chunk = (len - sent > 4096) ? 4096 : (len - sent);
        int ret = write(sock, &buf[sent], chunk);
        if (ret <= 0) {
            return -1;
        }
        sent += (uint32_t)ret;
    }
    return 0;
}

static int post_board_ask(uint32_t jpg_len, uint32_t pcm_bytes, uint32_t *out_pcm_samples)
{
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) {
        printf("EduVoice: socket failed\r\n");
        return -1;
    }

    struct timeval tv;
    tv.tv_sec = 45;
    tv.tv_usec = 0;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(s_host_port);
    if (inet_aton(s_host_ip, &addr.sin_addr) == 0) {
        printf("EduVoice: invalid host IP %s\r\n", s_host_ip);
        close(sock);
        return -1;
    }

    printf("EduVoice: connecting to %s:%u (jpg=%lu B, pcm=%lu B)...\r\n",
           s_host_ip, (unsigned int)s_host_port, (unsigned long)jpg_len, (unsigned long)pcm_bytes);
    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        printf("EduVoice: connect failed (is bridge.py running on %s:%u?)\r\n",
               s_host_ip, (unsigned int)s_host_port);
        close(sock);
        return -1;
    }

    uint32_t body_len = 12 + jpg_len + pcm_bytes;
    char req_hdr[256];
    int hdr_len = snprintf(
        req_hdr, sizeof(req_hdr),
        "POST /board/ask HTTP/1.1\r\n"
        "Host: %s:%u\r\n"
        "Content-Type: application/octet-stream\r\n"
        "Content-Length: %lu\r\n"
        "Connection: close\r\n\r\n",
        s_host_ip, (unsigned int)s_host_port, (unsigned long)body_len
    );

    uint8_t bin_hdr[12];
    bin_hdr[0] = 'E';
    bin_hdr[1] = 'D';
    bin_hdr[2] = 'U';
    bin_hdr[3] = 'V';
    bin_hdr[4] = (uint8_t)(jpg_len & 0xFF);
    bin_hdr[5] = (uint8_t)((jpg_len >> 8) & 0xFF);
    bin_hdr[6] = (uint8_t)((jpg_len >> 16) & 0xFF);
    bin_hdr[7] = (uint8_t)((jpg_len >> 24) & 0xFF);
    bin_hdr[8] = (uint8_t)(pcm_bytes & 0xFF);
    bin_hdr[9] = (uint8_t)((pcm_bytes >> 8) & 0xFF);
    bin_hdr[10] = (uint8_t)((pcm_bytes >> 16) & 0xFF);
    bin_hdr[11] = (uint8_t)((pcm_bytes >> 24) & 0xFF);

    if (send_all(sock, (const uint8_t *)req_hdr, (uint32_t)hdr_len) != 0 ||
        send_all(sock, bin_hdr, sizeof(bin_hdr)) != 0 ||
        (jpg_len > 0 && send_all(sock, s_snap_jpg, jpg_len) != 0) ||
        send_all(sock, (const uint8_t *)s_mic_pcm, pcm_bytes) != 0) {
        printf("EduVoice: send failed\r\n");
        close(sock);
        return -1;
    }

    /* Read HTTP response headers + raw 16kHz 16-bit mono PCM body into s_spk_pcm */
    uint8_t rx_tmp[1024];
    char hdr_buf[1024];
    uint32_t hdr_pos = 0;
    uint8_t header_done = 0;
    uint8_t *spk_bytes = (uint8_t *)s_spk_pcm;
    uint32_t max_spk_bytes = AUDIO_MAX_PLAY_SAMPLES * sizeof(int16_t);
    uint32_t spk_rx_bytes = 0;

    while (1) {
        int r = read(sock, rx_tmp, sizeof(rx_tmp));
        if (r <= 0) {
            break;
        }
        if (!header_done) {
            for (int i = 0; i < r; i++) {
                if (hdr_pos < sizeof(hdr_buf) - 1) {
                    hdr_buf[hdr_pos++] = (char)rx_tmp[i];
                    hdr_buf[hdr_pos] = '\0';
                }
                if (hdr_pos >= 4 &&
                    hdr_buf[hdr_pos - 4] == '\r' && hdr_buf[hdr_pos - 3] == '\n' &&
                    hdr_buf[hdr_pos - 2] == '\r' && hdr_buf[hdr_pos - 1] == '\n') {
                    header_done = 1;
                    if (strstr(hdr_buf, "200 OK") == NULL) {
                        printf("EduVoice: server error:\r\n%s\r\n", hdr_buf);
                        close(sock);
                        return -1;
                    }
                    uint32_t rem = (uint32_t)(r - (i + 1));
                    if (rem > 0) {
                        uint32_t copy_len = (rem > max_spk_bytes) ? max_spk_bytes : rem;
                        memcpy(&spk_bytes[spk_rx_bytes], &rx_tmp[i + 1], copy_len);
                        spk_rx_bytes += copy_len;
                    }
                    break;
                }
            }
        } else {
            uint32_t space = max_spk_bytes - spk_rx_bytes;
            if (space > 0) {
                uint32_t copy_len = ((uint32_t)r > space) ? space : (uint32_t)r;
                memcpy(&spk_bytes[spk_rx_bytes], rx_tmp, copy_len);
                spk_rx_bytes += copy_len;
            }
        }
    }

    close(sock);
    if (!header_done || spk_rx_bytes < 2) {
        printf("EduVoice: empty audio response\r\n");
        return -1;
    }

    *out_pcm_samples = spk_rx_bytes / sizeof(int16_t);
    printf("EduVoice: received %lu audio samples (%.2f s)\r\n",
           (unsigned long)(*out_pcm_samples),
           (float)(*out_pcm_samples) / (float)AUDIO_SAMPLE_RATE);
    return 0;
}

void d200_eduvoice_hold_and_ask(uint8_t (*hold_check_fn)(void))
{
    if (!s_audio_ready) {
        return;
    }

    struct bflb_device_s *gpio = bflb_device_get_by_name("gpio");
    xSemaphoreTake(s_audio_mutex, portMAX_DELAY);

    /* Brief 60ms LED blink on start/end (avoids holding high-current flash LED ON during mic capture) */
    bflb_gpio_set(gpio, GPIO_PIN_16);
    vTaskDelay(pdMS_TO_TICKS(60));
    bflb_gpio_reset(gpio, GPIO_PIN_16);
    uint32_t jpg_len = snapshot_camera_jpeg();
    printf("EduVoice: recording while button held (snapshot=%lu B)...\r\n", (unsigned long)jpg_len);

    uint32_t rec_samples = record_pcm_locked(hold_check_fn, AUDIO_MAX_REC_SEC * 1000);
    bflb_gpio_set(gpio, GPIO_PIN_16);
    vTaskDelay(pdMS_TO_TICKS(60));
    bflb_gpio_reset(gpio, GPIO_PIN_16);

    if (rec_samples < (AUDIO_SAMPLE_RATE / 4)) {
        printf("EduVoice: recording too short (%lu samples), ignored.\r\n", (unsigned long)rec_samples);
        xSemaphoreGive(s_audio_mutex);
        return;
    }

    printf("EduVoice: recorded %.2f s, sending to laptop...\r\n",
           (float)rec_samples / (float)AUDIO_SAMPLE_RATE);

    uint32_t spk_samples = 0;
    if (post_board_ask(jpg_len, rec_samples * sizeof(int16_t), &spk_samples) == 0 && spk_samples > 0) {
        play_pcm_buffer_locked(s_spk_pcm, spk_samples);
    } else {
        /* Error double-beep */
        xSemaphoreGive(s_audio_mutex);
        d200_audio_play_beep(330, 150);
        vTaskDelay(pdMS_TO_TICKS(80));
        d200_audio_play_beep(260, 200);
        return;
    }

    xSemaphoreGive(s_audio_mutex);
}

void d200_eduvoice_ask_fixed(uint32_t duration_ms)
{
    if (!s_audio_ready) {
        return;
    }
    if (duration_ms < 500) duration_ms = 500;
    if (duration_ms > AUDIO_MAX_REC_SEC * 1000) duration_ms = AUDIO_MAX_REC_SEC * 1000;

    struct bflb_device_s *gpio = bflb_device_get_by_name("gpio");
    xSemaphoreTake(s_audio_mutex, portMAX_DELAY);

    bflb_gpio_set(gpio, GPIO_PIN_16);
    uint32_t jpg_len = snapshot_camera_jpeg();
    printf("EduVoice: recording %lu ms (snapshot=%lu B)...\r\n",
           (unsigned long)duration_ms, (unsigned long)jpg_len);

    uint32_t rec_samples = record_pcm_locked(NULL, duration_ms);
    bflb_gpio_reset(gpio, GPIO_PIN_16);

    uint32_t spk_samples = 0;
    if (post_board_ask(jpg_len, rec_samples * sizeof(int16_t), &spk_samples) == 0 && spk_samples > 0) {
        play_pcm_buffer_locked(s_spk_pcm, spk_samples);
    } else {
        xSemaphoreGive(s_audio_mutex);
        d200_audio_play_beep(330, 150);
        vTaskDelay(pdMS_TO_TICKS(80));
        d200_audio_play_beep(260, 200);
        return;
    }

    xSemaphoreGive(s_audio_mutex);
}
