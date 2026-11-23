#define __MAIN_C_
#include "chip_include.h"
#include "app_include.h"

static TaskHandle_t bl618_camera_task_handle;
static TaskHandle_t bl618_wifi_task_handle;
static TaskHandle_t bl618_button_task_handle;

qyq_module_button_type_t qyq_module_button;
qyq_module_button_config_t qyq_module_button_config;
uint16_t qyq_module_button_contimer_value[8] = {20, 20, 20, 20, 20, 20, 20, 20}; // 短按与双击的间隔时间
uint16_t qyq_module_button_longtimer_value[1] = {200};                           // 长按时间值s
struct bflb_device_s *gpio;

volatile static uint8_t wifi_status = 0;
extern uint8_t *pic_addr;
extern uint32_t pic_size;
extern uint8_t s_led_status;

void bl618_wifi_init_process(void)
{
    printf("bl618_wifi_init_process\r\n");
}

void bl618_wifi_got_ip_process(void)
{
    printf("bl618_wifi_got_ip_process\r\n");
    wifi_status = 1;
}

void bl618_wifi_disconnect_process(void)
{
    printf("bl618_wifi_disconnect_process\r\n");
    wifi_status = 0;
}

static void bl618_wifi_task(void *pvParameters)
{
    (void)pvParameters;
    vTaskDelay(pdMS_TO_TICKS(3000));
    bl618_wifi_callback_register(bl618_wifi_got_ip_process, CODE_WIFI_ON_GOT_IP);
    bl618_wifi_callback_register(bl618_wifi_init_process, CODE_WIFI_ON_INIT_DONE);
    bl618_wifi_callback_register(bl618_wifi_disconnect_process, CODE_WIFI_ON_DISCONNECT);

    // vTaskDelay(pdMS_TO_TICKS(3000));
    printf("bl618 wifi connecct\r\n");

    while (1)
    {
        if (wifi_status)
        {
            mhttp_server_init();
        }

        vTaskDelay(pdMS_TO_TICKS(3000));
        // d200_semap_lock();
        // take_photo(pic_addr, pic_size);
        // d200_semap_uplock();
    }
}

void bl618_freeheap_task(void *msg)
{
    size_t freeHeapSize;

    vTaskDelay(pdMS_TO_TICKS(3000));

    while (1)
    {
        freeHeapSize = kfree_size();
        printf("Free heap size: %u bytes\n", freeHeapSize);
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

#define EDUVOICE_BTN_PIN GPIO_PIN_10 /* Wire button between GPIO10 and GND (active LOW) */

uint8_t qyq_module_button_statusin(void) // 需要适配，按键输入引脚
{
    if (bflb_gpio_read(gpio, EDUVOICE_BTN_PIN) == 0)
    {
        return 1;
    }

    return 0;
}

void bl618_button_task(void *msg)
{
    (void)msg;

    gpio = bflb_device_get_by_name("gpio");

    bflb_gpio_init(gpio, EDUVOICE_BTN_PIN, GPIO_INPUT | GPIO_PULLUP | GPIO_SMT_EN | GPIO_DRV_0);
    bflb_gpio_init(gpio, GPIO_PIN_16, GPIO_OUTPUT | GPIO_PULLDOWN | GPIO_SMT_EN | GPIO_DRV_1);

    printf("bl618_button_task (hold GPIO10->GND button to ask EduVoice AI)\r\n");

    while (1)
    {
        if (qyq_module_button_statusin())
        {
            /* Simple 40ms debounce before starting recording */
            vTaskDelay(pdMS_TO_TICKS(40));
            if (qyq_module_button_statusin())
            {
                if (!wifi_status)
                {
                    printf("EduVoice: Wi-Fi not connected yet (send AT+WJAP=ssid,pass)\r\n");
                    d200_audio_play_beep(300, 200);
                    while (qyq_module_button_statusin())
                    {
                        vTaskDelay(pdMS_TO_TICKS(20));
                    }
                }
                else
                {
                    d200_eduvoice_hold_and_ask(qyq_module_button_statusin);
                    while (qyq_module_button_statusin())
                    {
                        vTaskDelay(pdMS_TO_TICKS(20));
                    }
                }
            }
        }

        vTaskDelay(pdMS_TO_TICKS(15));
    }
}

int main(void)
{
    // 板卡初始化
    board_init();

    bl618_wifi_init();

    /* SD card init skipped so GPIO12-15 remain free for I2S (INMP441 + MAX98357A) */
    d200_audio_init();

    bl616_uart_init();

    xTaskCreate(bl618_wifi_task, (char *)"bl618_wifi_task", 4096, NULL, 22, &bl618_wifi_task_handle);
    xTaskCreate(bl618_button_task, (char *)"bl618_button_task", 4096, NULL, 23, &bl618_button_task_handle);
    // xTaskCreate(bl618_freeheap_task, (char *)"bl618_freeheap_task", 4096, NULL, 21, &bl618_freeheap_task_handle);
    xTaskCreate(bl618_camera_task, (char *)"bl618_camera_task", 4096, NULL, 20, &bl618_camera_task_handle);

    vTaskStartScheduler();

    while (1)
    {
    }
}
