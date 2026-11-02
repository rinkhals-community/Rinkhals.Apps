#ifndef __AGORA_RTC_API_H__
#define __AGORA_RTC_API_H__

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AGORA_RTC_CHANNEL_NAME_MAX_LEN 64
#define AGORA_RTC_PRODUCT_ID_MAX_LEN 63
#define AGORA_LICENSE_VALUE_LEN 32

typedef uint32_t connection_id_t;

#define CONNECTION_ID_ALL ((connection_id_t)0)
#define CONNECTION_ID_INVALID ((connection_id_t)-1)

typedef enum {
  VIDEO_DATA_TYPE_H264 = 2,
} video_data_type_e;

typedef enum {
  VIDEO_STREAM_HIGH = 0,
} video_stream_type_e;

typedef enum {
  VIDEO_FRAME_KEY = 3,
  VIDEO_FRAME_DELTA = 4,
} video_frame_type_e;

typedef enum {
  VIDEO_FRAME_RATE_FPS_1 = 1,
  VIDEO_FRAME_RATE_FPS_7 = 7,
  VIDEO_FRAME_RATE_FPS_10 = 10,
  VIDEO_FRAME_RATE_FPS_15 = 15,
  VIDEO_FRAME_RATE_FPS_24 = 24,
  VIDEO_FRAME_RATE_FPS_30 = 30,
  VIDEO_FRAME_RATE_FPS_60 = 60,
} video_frame_rate_e;

typedef struct {
  video_data_type_e data_type;
  video_stream_type_e stream_type;
  video_frame_type_e frame_type;
  video_frame_rate_e frame_rate;
} video_frame_info_t;

typedef struct audio_frame_info_t audio_frame_info_t;
typedef struct agora_rtc_event_handler_t agora_rtc_event_handler_t;

typedef int (*agora_rtc_init_t)(const char *app_id,
                                const agora_rtc_event_handler_t *event_handler,
                                const void *option);
typedef const char *(*agora_rtc_err_2_str_t)(int err);
typedef int (*agora_rtc_create_connection_t)(connection_id_t *conn_id);
typedef int (*agora_rtc_leave_channel_t)(connection_id_t conn_id);
typedef int (*agora_rtc_join_channel_t)(connection_id_t conn_id,
                                        const char *channel,
                                        uint32_t uid,
                                        const char *token,
                                        void *options);
typedef int (*agora_rtc_send_video_data_t)(connection_id_t conn_id,
                                           const void *data_ptr,
                                           size_t data_len,
                                           video_frame_info_t *info_ptr);

extern const char *agora_rtc_get_version(void);
extern const char *agora_rtc_err_2_str(int err);
extern int agora_rtc_init(const char *app_id,
                          const agora_rtc_event_handler_t *event_handler,
                          const void *option);
extern int agora_rtc_fini(void);
extern int agora_rtc_create_connection(connection_id_t *conn_id);
extern int agora_rtc_leave_channel(connection_id_t conn_id);
extern int agora_rtc_join_channel(connection_id_t conn_id,
                                  const char *channel_name,
                                  uint32_t uid,
                                  const char *token,
                                  void *options);
extern int agora_rtc_send_video_data(connection_id_t conn_id,
                                     const void *data_ptr,
                                     size_t data_len,
                                     video_frame_info_t *info_ptr);

#ifdef __cplusplus
}
#endif

#endif /* __AGORA_RTC_API_H__ */
