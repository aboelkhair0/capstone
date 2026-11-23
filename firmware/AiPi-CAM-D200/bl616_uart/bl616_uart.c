#define __BL616_UART_C_
#include "bl616_uart.h"
#include "bl618_wifi.h"
#include "d200_audio.h"
#include "qyq_frame_at.h"

qyq_frame_at_type_t qyq_frame_at;
qyq_frame_at_config_t qyq_frame_at_config;
static TaskHandle_t bl618_uart_handle;
static TaskHandle_t bl618_uart_tick_handle;
static TaskHandle_t bl618_uart_rec_handle;
struct bflb_device_s *uartx;
static uint8_t uart1_read_buf[1024];

uint8_t s_led_status = 0;

char *repeat_string(const char *str, int length)
{
    int str_len = strlen(str);
    int repeat_count = length / str_len + 1;

    char *result = (char *)malloc((length + 1) * sizeof(char));
    if (result == NULL)
    {
        return NULL; // 内存分配失败
    }

    int pos = 0;
    for (int i = 0; i < repeat_count; i++)
    {
        for (int j = 0; j < str_len; j++)
        {
            if (pos < length)
            {
                result[pos++] = str[j];
            }
            else
            {
                break;
            }
        }
    }
    result[length] = '\0'; // 确保字符串以空字符结尾

    return result;
}

void remove_trailing_newline(char *str)
{
    int len = strlen(str);
    if (len > 0 && str[len - 1] == '\n')
    {
        str[len - 1] = '\0';
        len--;
    }
    if (len > 0 && str[len - 1] == '\r')
    {
        str[len - 1] = '\0';
    }
}

int8_t bl616_sta_connect(unsigned int argc, const char **argv)
{
    printf("%s\r\n", argv[0]);
    // 检查参数个数是否正确
    if (argc != 2)
    {
        return -1; // 返回错误码
    }

    char *ssid_str = repeat_string(argv[0], strlen(argv[0]));
    // printf("pss length:%d",strlen(argv[1]));
    char *pwd_str = repeat_string(argv[1], strlen(argv[1]));

    if ((ssid_str == NULL) || (pwd_str == NULL))
    {
        free(ssid_str);
        free(pwd_str);
        return -1;
    }

    remove_trailing_newline(ssid_str);
    remove_trailing_newline(pwd_str);

    if (bl618_wifi_connect(ssid_str, pwd_str) != 0)
    {
        printf("bl618 wifi connecct fail!\r\n");
        free(ssid_str);
        free(pwd_str);
        return -1;
    }

    free(ssid_str);
    free(pwd_str);
    return 0; // 成功返回0
}

int8_t bl616_led_set(unsigned int argc, const char **argv)
{
    uint8_t write_value;
    // printf("%s\r\n", argv[0]);
    // 检查参数个数是否正确
    if (argc != 1)
    {
        return -1; // 返回错误码
    }

    char *led_str = repeat_string(argv[0], strlen(argv[0]));

    if (led_str == NULL)
    {
        return -1;
    }

    remove_trailing_newline(led_str);

    // 解析AT指令参数

    // 检查输入参数是否为有效的整数
    if (sscanf(led_str, "%hhu", &write_value) != 1)
    {
        free(led_str);
        return -1; // 返回参数错误码
    }

    s_led_status = write_value;
    free(led_str);

    // printf("led status :%d", s_led_status);

    return 0; // 成功返回0
}

void aiio_wifi_sta_ip_get(uint32_t *ip, uint32_t *gw, uint32_t *mask)
{
    uint32_t dns = 0;
    if (wifi_mgmr_sta_state_get())
    {
        wifi_sta_ip4_addr_get(ip, mask, gw, &dns);
    }
    else
    {
        *ip = 0;
        *gw = 0;
        *mask = 0;
    }
}

void print_ip(uint32_t ip)
{
    printf("%d.%d.%d.%d\r\n",
           ip & 0xFF,
           (ip >> 8) & 0xFF,
           (ip >> 16) & 0xFF,
           (ip >> 24) & 0xFF);
}

int8_t bl616_sta_find_ip(unsigned int argc, const char **argv)
{
    uint32_t ip, gw, mask;
    aiio_wifi_sta_ip_get(&ip, &gw, &mask);
    print_ip(ip);
    return 0;
}

int8_t bl616_host_query(unsigned int argc, const char **argv)
{
    (void)argc;
    (void)argv;
    char host[64];
    uint16_t port = 0;
    d200_audio_get_host(host, sizeof(host), &port);
    printf("%s:%u\r\n", host, (unsigned int)port);
    return 0;
}

int8_t bl616_host_set(unsigned int argc, const char **argv)
{
    if (argc < 1 || argc > 2)
    {
        return -1;
    }

    char *host_str = repeat_string(argv[0], strlen(argv[0]));
    if (host_str == NULL)
    {
        return -1;
    }
    remove_trailing_newline(host_str);

    uint16_t port = EDUVOICE_DEFAULT_PORT;
    if (argc == 2)
    {
        char *port_str = repeat_string(argv[1], strlen(argv[1]));
        if (port_str == NULL)
        {
            free(host_str);
            return -1;
        }
        remove_trailing_newline(port_str);
        unsigned int parsed = 0;
        if (sscanf(port_str, "%u", &parsed) != 1 || parsed == 0 || parsed > 65535)
        {
            free(host_str);
            free(port_str);
            return -1;
        }
        port = (uint16_t)parsed;
        free(port_str);
    }

    d200_audio_set_host(host_str, port);
    free(host_str);
    return 0;
}

int8_t bl616_beep_exe(unsigned int argc, const char **argv)
{
    (void)argc;
    (void)argv;
    d200_audio_play_beep(880, 300);
    return 0;
}

int8_t bl616_mic_exe(unsigned int argc, const char **argv)
{
    (void)argc;
    (void)argv;
    d200_audio_loopback_test(5000);
    return 0;
}

int8_t bl616_mic_set(unsigned int argc, const char **argv)
{
    if (argc != 1)
    {
        return -1;
    }
    char *s_str = repeat_string(argv[0], strlen(argv[0]));
    if (s_str == NULL)
    {
        return -1;
    }
    remove_trailing_newline(s_str);
    unsigned int sec = 0;
    if (sscanf(s_str, "%u", &sec) != 1 || sec < 1 || sec > AUDIO_MAX_REC_SEC)
    {
        free(s_str);
        return -1;
    }
    free(s_str);
    d200_audio_loopback_test(sec * 1000);
    return 0;
}

int8_t bl616_ask_exe(unsigned int argc, const char **argv)
{
    (void)argc;
    (void)argv;
    d200_eduvoice_ask_fixed(4000);
    return 0;
}

int8_t bl616_vol_query(unsigned int argc, const char **argv)
{
    (void)argc;
    (void)argv;
    printf("%u\r\n", (unsigned int)d200_audio_get_volume());
    return 0;
}

int8_t bl616_vol_set(unsigned int argc, const char **argv)
{
    if (argc != 1)
    {
        return -1;
    }
    char *v_str = repeat_string(argv[0], strlen(argv[0]));
    if (v_str == NULL)
    {
        return -1;
    }
    remove_trailing_newline(v_str);
    unsigned int val = 0;
    if (sscanf(v_str, "%u", &val) != 1 || val < 5 || val > 100)
    {
        free(v_str);
        return -1;
    }
    free(v_str);
    d200_audio_set_volume((uint8_t)val);
    return 0;
}

qyq_frame_at_list_t qyq_frame_at_list[] = {
    {"+WJAP", 5, null, bl616_sta_find_ip, bl616_sta_connect, null},
    {"+LED", 4, null, null, bl616_led_set, null},
    {"+HOST", 5, null, bl616_host_query, bl616_host_set, null},
    {"+BEEP", 5, null, null, null, bl616_beep_exe},
    {"+MIC", 4, null, null, bl616_mic_set, bl616_mic_exe},
    {"+ASK", 4, null, null, null, bl616_ask_exe},
    {"+VOL", 4, null, bl616_vol_query, bl616_vol_set, null},
};

void qyq_frame_at_write(const char *buf, uint16_t length)
{
    uint16_t i = 0;
    for (i = 0; i < length; i++)
    {
        bflb_uart_putchar(uartx, buf[i]);
    }
}

// 1ms定时时钟
void bl616_uart1_tick_task(void *msg)
{
    while (1)
    {
        qyq_frame_at.qyq_frame_at_tick(&qyq_frame_at);
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

void bl616_uart1_at_rec_task(void *msg)
{
    int ch;

    while (1)
    {
        ch = bflb_uart_getchar(uartx);
        if (ch != -1)
        {
            // printf("0x%02x ", ch);
            qyq_frame_at.qyq_frame_at_recv(&qyq_frame_at, ch);
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

void bl616_at_init(void)
{
    qyq_frame_at_config.qyq_frame_at_list = qyq_frame_at_list;
    qyq_frame_at_config.qyq_frame_at_list_size = (sizeof(qyq_frame_at_list) / sizeof(qyq_frame_at_list_t));

    qyq_frame_at_config.qyq_frame_at_rx_buf = uart1_read_buf;
    qyq_frame_at_config.qyq_frame_at_rx_size = sizeof(uart1_read_buf);
    qyq_frame_at_config.qyq_frame_at_write = qyq_frame_at_write;

    if (qyq_frame_at_create(&qyq_frame_at, &qyq_frame_at_config) != 0)
    {
        printf("qyq_frame_at_create is fail!\r\n");
    }

    if (qyq_frame_at.qyq_frame_at_init(&qyq_frame_at) != 0)
    {
        printf("qyq_frame_at_init is fail!\r\n");
    }

    xTaskCreate(bl616_uart1_tick_task, (char *)"bl616_uart1_tick_task", 1152, NULL, 15, &bl618_uart_tick_handle);
    xTaskCreate(bl616_uart1_at_rec_task, (char *)"bl616_uart1_at_rec_task", 1152, NULL, 14, &bl618_uart_rec_handle);
}


void bl616_uart0_init(void)
{
    uartx = bflb_device_get_by_name("uart0");
}

// 串口运行任务
void bl616_uart_task(void *msg)
{
    (void)msg;
    // AT 框架初始化
    bl616_at_init();

    // 串口初始化
    bl616_uart0_init();

    while (1)
    {
        qyq_frame_at.qyq_frame_at_run(&qyq_frame_at);
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

void bl616_uart_init(void)
{
    xTaskCreate(bl616_uart_task, (char *)"bl616_uart_task", 1536, NULL, 20, &bl618_uart_handle);
}
