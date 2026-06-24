/*************************************************************
 * File  :  test_rtsa_multi.c
 * Module:  RTSA Lite test application.
 *
 * This is a part of the Agora IoT SDK.
 * Copyright (C) 2022 Agora IO
 * All rights reserved.
 *
 *************************************************************/

#include <stdio.h>
#include <unistd.h>
#include <string.h>
#include <signal.h>
#include <pthread.h>
#include <getopt.h>

#include "agora_rtc_api.h"
#include "file_parser.h"
#include "utility.h"
#include "pacer.h"
#include "log.h"

#define DEFAULT_CHANNEL_NAME "hello_demo"
#define DEFAULT_SEND_VIDEO_FILENAME "send_video.h264"
#define DEFAULT_SEND_AUDIO_FILENAME "send_audio_16k_1ch.pcm"

#define DEFAULT_RECV_AUDIO_BASENAME "recv_audio.bin"
#define DEFAULT_RECV_VIDEO_BASENAME "recv_video.bin"
#define DEFAULT_SEND_VIDEO_FRAME_RATE (25)
#define DEFAULT_BANDWIDTH_ESTIMATE_MIN_BITRATE (100000)
#define DEFAULT_BANDWIDTH_ESTIMATE_MAX_BITRATE (1000000)
#define DEFAULT_BANDWIDTH_ESTIMATE_START_BITRATE (500000)
#define DEFAULT_SEND_AUDIO_FRAME_PERIOD_MS (20)
#define DEFAULT_PCM_SAMPLE_RATE (16000)
#define DEFAULT_PCM_CHANNEL_NUM (1)

typedef struct {
  const char *p_sdk_log_dir;

  const char *p_appid;
  const char *p_token;
  const char *p_license_value;
  const char *p_channel;
  uint32_t uid;
  uint32_t area;

  // video related config
  video_data_type_e video_data_type;
  int send_video_frame_rate;
  const char *send_video_file_path;

  // audio related config
  audio_data_type_e audio_data_type;
  audio_codec_type_e audio_codec_type;
  const char *send_audio_file_path;

  uint32_t pcm_sample_rate;
  uint32_t pcm_channel_num;

  // advanced config
  bool send_video_generic_flag;
  bool enable_audio_mixer;
  bool receive_data_only;
} app_config_t;

typedef struct {
  app_config_t config;

  void *video_file_parser;
  void *video_file_writer;

  void *audio_file_parser;
  void *audio_file_writer;

  uint32_t conn_cnt;
  connection_id_t conn_ids[10];
  connection_info_t conn_infos[10];
  bool conn_flags[10];
  bool b_connected_flag;
  bool b_stop_flag;
} app_t;

static app_t g_app = {
    .config = {
        // common config
        .p_sdk_log_dir              = "io.agora.rtc_sdk",
        .p_appid                    = "",
        .p_channel                  = DEFAULT_CHANNEL_NAME,
        .p_token                    = "",
        .uid                        = 0,
        .area                       = AREA_CODE_GLOB,

        // video related config
        .video_data_type            = VIDEO_DATA_TYPE_H264,
        .send_video_frame_rate      = DEFAULT_SEND_VIDEO_FRAME_RATE,
        .send_video_file_path       = DEFAULT_SEND_VIDEO_FILENAME,

        // audio related config
        .audio_data_type            = AUDIO_DATA_TYPE_PCM,
        .audio_codec_type           = AUDIO_CODEC_TYPE_OPUS,
        .send_audio_file_path       = DEFAULT_SEND_AUDIO_FILENAME,

        // pcm related config
        .pcm_sample_rate            = DEFAULT_PCM_SAMPLE_RATE,
        .pcm_channel_num            = DEFAULT_PCM_CHANNEL_NUM,

        // advanced config
        .send_video_generic_flag    = false,
        .enable_audio_mixer         = false,
        .receive_data_only          = false,
    },

    .video_file_parser      = NULL,
    .video_file_writer      = NULL,

    .audio_file_parser      = NULL,
    .audio_file_writer      = NULL,

    .b_stop_flag            = false,
    .b_connected_flag       = false,
};

const char *certificate_for_test =
        "eyJzaWduIjoiU2dpNS9VUjZOcXV0YmlTTXFTb3cyRm0rc1RDRE5KeHZ6NGQ3eEtaN1F2QzlmVWd0UHRDRmdLSk1ndWZMQ"
        "0V3eEZoNnRFdythME1Ia0RDSVBmOVRUc05ZbWZQSVB5UXVJM0pIR25FVmd5MUVLNlNKem4zMHE2azlBMnZHTmJKQzdQaH"
        "dMbVVHTmc0UmNnSGZJZlE1b2FGdTlRbjh5NHZMWURrOWoyZlFqZkVFd0pyRXA5MjlTTDZtR2VKcWdlOXZCbHBLQm9kWjR"
        "mTTU1Y3dGR3JTL1RKOHVPb1R1MXZJWFJjUTdOTHNaaDlLeW8xUEdKN29PdWsxYXQyNldBcGRsVko3Q1BJcnZ3VndPU3Uy"
        "ejVzeHZXdXVkeTlvZFVRd1phd3BrM0x6dzVnc2ZBVUpmS2E4U2d2NVl6VVlWOExWRlQxNCtWNkJWT20wcWlpcTZJd0J3Y"
        "0NBPT0iLCJjdXN0b20iOiIxNTM3NDMiLCJjcmVkZW50aWFsIjoiNGE1ZmEyZWVkZGQyYjJjOWM3YzQxOTlhYWQ0ODIwNW"
        "I5YjBjOWJiMTllYWVmZGVhNDk5NmFhOTlmNmRkOTI1MiIsImR1ZSI6IjIwMjIwNDE3In0=";

static media_file_type_e video_data_type_to_file_type(video_data_type_e type)
{
  media_file_type_e file_type;
  switch (type) {
  case VIDEO_DATA_TYPE_H264:
    file_type = MEDIA_FILE_TYPE_H264;
    break;
  default:
    file_type = MEDIA_FILE_TYPE_H264;
    break;
  }
  return file_type;
}

static media_file_type_e audio_data_type_to_file_type(audio_data_type_e type)
{
  media_file_type_e file_type;
  switch (type) {
  case AUDIO_DATA_TYPE_PCM:
    file_type = MEDIA_FILE_TYPE_PCM;
    break;
  case AUDIO_DATA_TYPE_OPUS:
    file_type = MEDIA_FILE_TYPE_OPUS;
    break;
  case AUDIO_DATA_TYPE_AACLC:
    file_type = MEDIA_FILE_TYPE_AACLC;
    break;
  case AUDIO_DATA_TYPE_HEAAC:
    file_type = MEDIA_FILE_TYPE_HEAAC;
    break;
  case AUDIO_DATA_TYPE_G722:
    file_type = MEDIA_FILE_TYPE_G722;
    break;
  case AUDIO_DATA_TYPE_PCMA:
  case AUDIO_DATA_TYPE_PCMU:
    file_type = MEDIA_FILE_TYPE_G711;
    break;
  default:
    file_type = MEDIA_FILE_TYPE_PCM;
    break;
  }
  return file_type;
}

static void app_signal_handler(int sig)
{
  switch (sig) {
  case SIGINT:
    g_app.b_stop_flag = true;
    break;
  default:
    LOGW("no handler, sig=%d", sig);
  }
}

void app_print_usage(int argc, char **argv)
{
  LOGS("\nUsage: %s [OPTION]", argv[0]);
  LOGS(" -h, --help                : show help info");
  LOGS(" -i, --app-id              : application id; either app-id OR token MUST be set");
  LOGS(" -t, --token               : token for authentication");
  LOGS(" -l, --license-value       : license value for authentication");
  LOGS(" -c, --channel-id          : channel name; default is 'demo'");
  LOGS(" -u, --user-id             : user id; default is 0");
  LOGS(" -v, --video-type          : video data type for the input video file; default is 2");
  LOGS("                             support: 2=H264, 20=JPEG");
  LOGS(" -a, --audio-type          : audio data type for the input audio file; default is 100");
  LOGS("                             support: 1=OPUS, 5=G722, 8=AACLC, 9=HEAAC, 100=PCM");
  LOGS(" -C, --audio-codec         : audio codec type; only valid when audio type is PCM; default is 1");
  LOGS("                             support: 1=OPUS, 2=G722");
  LOGS(" -f  --fps                 : video frame rate; default is 30");
  LOGS(" -s, --send-video-file     : send video file path; default is './%s'", DEFAULT_SEND_VIDEO_FILENAME);
  LOGS(" -S, --send-audio-file     : send audio file path; default is './%s'", DEFAULT_SEND_AUDIO_FILENAME);
  LOGS(" -r, --pcm-sample-rate     : sample rate for the input PCM data; only valid when audio type is PCM");
  LOGS(" -n, --pcm-channel-num     : channel number for the input PCM data; only valid when audio type is PCM");
  LOGS(" -A, --area                : hex format with 0x header, supported area_code list:");
  LOGS("                             CN (Mainland China) : 0x00000001");
  LOGS("                             NA (North America)  : 0x00000002");
  LOGS("                             EU (Europe)         : 0x00000004");
  LOGS("                             AS (Asia)           : 0x00000008");
  LOGS("                             JP (Japan)          : 0x00000010");
  LOGS("                             IN (India)          : 0x00000020");
  LOGS("                             OC (Oceania)        : 0x00000040");
  LOGS("                             SA (South-American) : 0x00000080");
  LOGS("                             AF (Africa)         : 0x00000100");
  LOGS("                             KR (South Korea)    : 0x00000200");
  LOGS("                             OVS (Global except China): 0xFFFFFFFE");
  LOGS("                             GLOB (Global)       : 0xFFFFFFFF");
  LOGS(" -g, --send-video-generic  : enable generic codec flag for sending video");
  LOGS(" -m, --audio-mixer         : enable audio mixer to mix multiple incoming audio streams");
  LOGS(" -R, --recv-only           : do not send video and audio data");
  LOGS("\nExample:");
  LOGS("    %s --app-id xxx [--token xxx] --channel-id xxx --send-video-file ./video.h264 --fps 15 \
--send-audio-file ./audio.pcm",
           argv[0]);
}

int app_parse_args(int argc, char **argv)
{
  app_config_t *config = &g_app.config;
  const char *av_short_option = "hi:t:l:c:u:v:a:C:f:S:s:r:n:A:gmR";
  const struct option av_long_option[] = { { "help", 0, NULL, 'h' },
                                           { "app-id", 1, NULL, 'i' },
                                           { "token", 1, NULL, 't' },
                                           { "license-value", 1, NULL, 'l' },
                                           { "channel-id", 1, NULL, 'c' },
                                           { "user-id", 1, NULL, 'u' },
                                           { "video-type", 1, NULL, 'v' },
                                           { "audio-type", 1, NULL, 'a' },
                                           { "audio-codec", 1, NULL, 'C' },
                                           { "fps", 1, NULL, 'f' },
                                           { "send-audio-file", 1, NULL, 'S' },
                                           { "send-video-file", 1, NULL, 's' },
                                           { "pcm-sample-rate", 1, NULL, 'r' },
                                           { "pcm-channel-num", 1, NULL, 'n' },
                                           { "area", 0, NULL, 'A' },
                                           { "send-video-generic", 0, NULL, 'g' },
                                           { "audio-mixer", 0, NULL, 'm' },
                                           { "recv-only", 0, NULL, 'R' },
                                           { 0, 0, 0, 0 } };

  int ch = -1;
  int optidx = 0;

  while (1) {
    ch = getopt_long(argc, argv, av_short_option, av_long_option, &optidx);
    if (ch == -1) {
      break;
    }

    switch (ch) {
    case 'h':
      return -1;
    case 'i':
      config->p_appid = optarg;
      break;
    case 't':
      config->p_token = optarg;
      break;
    case 'l':
      config->p_license_value = optarg;
      break;
    case 'c':
      config->p_channel = optarg;
      break;
    case 'u':
      config->uid = strtol(optarg, NULL, 10);
      break;
    case 'v':
      config->video_data_type = strtol(optarg, NULL, 10);
      break;
    case 'a':
      config->audio_data_type = strtol(optarg, NULL, 10);
      break;
    case 'C':
      config->audio_codec_type = strtol(optarg, NULL, 10);
      break;
    case 'f':
      config->send_video_frame_rate = strtol(optarg, NULL, 10);
      break;
    case 'S':
      config->send_audio_file_path = optarg;
      break;
    case 's':
      config->send_video_file_path = optarg;
      break;
    case 'r':
      config->pcm_sample_rate = atoi(optarg);
      break;
    case 'n':
      config->pcm_channel_num = atoi(optarg);
      break;
    case 'A':
      config->area = strtol(optarg, NULL, 16);
      break;
    case 'g':
      config->send_video_generic_flag = true;
      break;
    case 'm':
      config->enable_audio_mixer = true;
      break;
    case 'R':
      config->receive_data_only = true;
      break;
    default:
      LOGS("Unknown cmd param %s", av_long_option[optidx].name);
      return -1;
    }
  }

  // check parameter sanity
  if (strcmp(config->p_appid, "") == 0) {
    LOGE("MUST provide App ID");
    return -1;
  }

  if (strcmp(config->p_channel, "") == 0) {
    LOGE("MUST provide channel name");
    return -1;
  }

  if (config->p_license_value && strlen(config->p_license_value) > AGORA_LICENSE_VALUE_LEN) {
    LOGE("MUST provide license value less than %d length", AGORA_LICENSE_VALUE_LEN);
    return -1;
  }

  if (config->send_video_frame_rate <= 0) {
    LOGE("Invalid video frame rate: %d", config->send_video_frame_rate);
    return -1;
  }

  return 0;
}

static int app_init(void)
{
  app_config_t *config = &g_app.config;
  parser_cfg_t parser_cfg = { 0 };

  signal(SIGINT, app_signal_handler);

  parser_cfg.u.audio_cfg.framePeriodMs = DEFAULT_SEND_AUDIO_FRAME_PERIOD_MS;
  if (config->audio_data_type == AUDIO_DATA_TYPE_PCM) {
    parser_cfg.u.audio_cfg.sampleRateHz = config->pcm_sample_rate;
    parser_cfg.u.audio_cfg.numberOfChannels = config->pcm_channel_num;
  }

#ifndef CONFIG_AUDIO_ONLY
  g_app.video_file_parser =
          create_file_parser(video_data_type_to_file_type(config->video_data_type), config->send_video_file_path, NULL);
  if (!g_app.video_file_parser) {
    return -1;
  }

  //g_app.video_file_writer = create_file_writer(DEFAULT_RECV_VIDEO_BASENAME);

  if (config->send_video_generic_flag) {
    config->video_data_type = VIDEO_DATA_TYPE_GENERIC;
  }
#endif

  g_app.audio_file_parser = create_file_parser(audio_data_type_to_file_type(config->audio_data_type),
                                               config->send_audio_file_path, &parser_cfg);
  if (!g_app.audio_file_parser) {
    return -1;
  }

  //g_app.audio_file_writer = create_file_writer(DEFAULT_RECV_AUDIO_BASENAME);

  return 0;
}

#ifndef CONFIG_AUDIO_ONLY
static int app_send_video(void)
{
  app_config_t *config = &g_app.config;
  int i = 0;

  frame_t frame;
  if (file_parser_obtain_frame(g_app.video_file_parser, &frame) < 0) {
    LOGE("The file parser failed to obtain audio frame");
    return -1;
  }

  // API: send vido data
  video_frame_info_t info;
  info.frame_type = frame.u.video.is_key_frame ? VIDEO_FRAME_KEY : VIDEO_FRAME_DELTA;
  info.frame_rate = config->send_video_frame_rate;
  info.data_type = config->video_data_type;
  info.stream_type = VIDEO_STREAM_HIGH;

  for (i = 0; i < g_app.conn_cnt; i++) {
    if (!g_app.conn_flags[i]) {
      continue;
    }
    int rval = agora_rtc_send_video_data(g_app.conn_ids[i], frame.ptr, frame.len, &info);
    if (rval < 0) {
      LOGE("Failed to send video data, reason: %s", agora_rtc_err_2_str(rval));
    }
  }

  file_parser_release_frame(g_app.video_file_parser, &frame);
  return 0;
}
#endif

static int app_send_audio(void)
{
  app_config_t *config = &g_app.config;
  int i = 0;

  frame_t frame;
  if (file_parser_obtain_frame(g_app.audio_file_parser, &frame) < 0) {
    LOGE("The file parser failed to obtain audio frame");
    return -1;
  }

  // API: send audio data
  audio_frame_info_t info = { 0 };
  info.data_type = config->audio_data_type;

  for (i = 0; i < g_app.conn_cnt; i++) {
    if (!g_app.conn_flags[i]) {
      continue;
    }
    int rval = agora_rtc_send_audio_data(g_app.conn_ids[i], frame.ptr, frame.len, &info);
    if (rval < 0) {
      LOGE("Failed to send audio data, reason: %s", agora_rtc_err_2_str(rval));
    }
  }

  file_parser_release_frame(g_app.audio_file_parser, &frame);
  return 0;
}

#ifndef CONFIG_AUDIO_ONLY
static void *video_send_thread(void *threadid)
{
  int video_send_interval_ms = 1000 / g_app.config.send_video_frame_rate;
  void *pacer = pacer_create((uint32_t)-1, video_send_interval_ms);

  while (!g_app.b_stop_flag) {
    if (g_app.b_connected_flag && is_time_to_send_video(pacer)) {
      app_send_video();
    }
    // sleep and wait until time is up for next send
    wait_before_next_send(pacer);
  }
  pacer_destroy(pacer);
  return NULL;
}
#endif

static void *audio_send_thread(void *threadid)
{
  int audio_send_interval_ms = DEFAULT_SEND_AUDIO_FRAME_PERIOD_MS;
  void *pacer = pacer_create(audio_send_interval_ms, (uint32_t)-1);

  while (!g_app.b_stop_flag) {
    if (g_app.b_connected_flag && is_time_to_send_audio(pacer)) {
      app_send_audio();
    }
    // sleep and wait until time is up for next send
    wait_before_next_send(pacer);
  }
  pacer_destroy(pacer);
  return NULL;
}

static void __on_join_channel_success(connection_id_t conn_id, uint32_t uid, int elapsed)
{
  int i = 0;
  for (i = 0; i < g_app.conn_cnt; i++) {
    if (conn_id == g_app.conn_ids[i]) {
      g_app.conn_flags[i] = true;
    }
  }
  g_app.b_connected_flag = true;
  LOGI("[conn-%u] Join the channel successfully, uid %u elapsed %d ms", conn_id, uid, elapsed);

  connection_info_t conn_info = { 0 };
  if (agora_rtc_get_connection_info(conn_id, &conn_info) == 0) {
    LOGI("conn_id=%u uid=%u cname=%s", conn_info.conn_id, conn_info.uid, conn_info.channel_name);
  }
}

static void __on_connection_lost(connection_id_t conn_id)
{
  int i = 0;
  for (i = 0; i < g_app.conn_cnt; i++) {
    if (conn_id == g_app.conn_ids[i]) {
      g_app.conn_flags[i] = true;
    }
  }
  g_app.b_connected_flag = false;
  LOGW("[conn-%u] Lost connection from the channel", conn_id);

  connection_info_t conn_info = { 0 };
  if (agora_rtc_get_connection_info(conn_id, &conn_info) == 0) {
    LOGI("conn_id=%u uid=%u cname=%s", conn_info.conn_id, conn_info.uid, conn_info.channel_name);
  }
}

static void __on_rejoin_channel_success(connection_id_t conn_id, uint32_t uid, int elapsed_ms)
{
  int i = 0;
  for (i = 0; i < g_app.conn_cnt; i++) {
    if (conn_id == g_app.conn_ids[i]) {
      g_app.conn_flags[i] = true;
    }
  }
  g_app.b_connected_flag = true;
  LOGI("[conn-%u] Rejoin the channel successfully, uid %u elapsed %d ms", conn_id, uid, elapsed_ms);

  connection_info_t conn_info = { 0 };
  if (agora_rtc_get_connection_info(conn_id, &conn_info) == 0) {
    LOGI("conn_id=%u uid=%u cname=%s", conn_info.conn_id, conn_info.uid, conn_info.channel_name);
  }
}

static void __on_user_joined(connection_id_t conn_id, uint32_t uid, int elapsed_ms)
{
  LOGI("[conn-%u] Remote user \"%u\" has joined the channel, elapsed %d ms", conn_id, uid, elapsed_ms);
  if (uid == 1 || uid == 2) {
    LOGI("dddd");
  }
}

static void __on_user_offline(connection_id_t conn_id, uint32_t uid, int reason)
{
  LOGI("[conn-%u] Remote user \"%u\" has left the channel, reason %d", conn_id, uid, reason);
}

static void __on_user_mute_audio(connection_id_t conn_id, uint32_t uid, bool muted)
{
  LOGI("[conn-%u] audio: uid=%u muted=%d", conn_id, uid, muted);
}

static void __on_user_mute_video(connection_id_t conn_id, uint32_t uid, bool muted)
{
  LOGI("[conn-%u] video: uid=%u muted=%d", conn_id, uid, muted);
}

static void __on_error(connection_id_t conn_id, int code, const char *msg)
{
  if (code == ERR_SEND_VIDEO_OVER_BANDWIDTH_LIMIT) {
    LOGE("Not enough uplink bandwdith. Error msg \"%s\"", msg);
    return;
  }

  if (code == ERR_INVALID_APP_ID) {
    LOGE("Invalid App ID. Please double check. Error msg \"%s\"", msg);
  } else if (code == ERR_INVALID_CHANNEL_NAME) {
    LOGE("Invalid channel name for conn_id %u. Please double check. Error msg \"%s\"", conn_id, msg);
  } else if (code == ERR_INVALID_TOKEN || code == ERR_TOKEN_EXPIRED) {
    LOGE("Invalid token. Please double check. Error msg \"%s\"", msg);
  } else if (code == ERR_DYNAMIC_TOKEN_BUT_USE_STATIC_KEY) {
    LOGE("Dynamic token is enabled but is not provided. Error msg \"%s\"", msg);
  } else {
    LOGW("Error %d is captured. Error msg \"%s\"", code, msg);
  }

  g_app.b_stop_flag = true;
}

static void __on_audio_data(connection_id_t conn_id, const uint32_t uid, uint16_t sent_ts, const void *data, size_t len,
                            const audio_frame_info_t *info_ptr)
{
  LOGI("[conn-%u] on_audio_data, uid %u sent_ts %u data_type %d, len %zu", conn_id, uid, sent_ts,
           info_ptr->data_type, len);
  //write_file(g_app.audio_file_writer, data_type, data, len);
}

static void __on_mixed_audio_data(connection_id_t conn_id, const void *data, size_t len,
                                  const audio_frame_info_t *info_ptr)
{
  //LOGD(TAG, "[conn-%u] on_mixed_audio_data, data_type %d, len %zu", conn_id, info_ptr->data_type, len);
  //write_file(g_app.audio_file_writer, data_type, data, len);
}

#ifndef CONFIG_AUDIO_ONLY
static void __on_video_data(connection_id_t conn_id, const uint32_t uid, uint16_t sent_ts, const void *data, size_t len,
                            const video_frame_info_t *info_ptr)
{
  LOGI("[conn-%u] on_video_data: uid %u sent_ts %u data_type %d frame_type %d stream_type %d len %zu", conn_id,
           uid, sent_ts, info_ptr->data_type, info_ptr->frame_type, info_ptr->stream_type, len);
  //write_file(g_app.video_file_writer, data_type, data, len);
#if 1 // dump file
  int ret = 0;
  static FILE *pFile = NULL;
  if (!pFile) {
    pFile = fopen("dump.h264", "wb");
  }

  ret = fwrite(data, 1, len, pFile);
#endif
}

static void __on_target_bitrate_changed(connection_id_t conn_id, uint32_t target_bps)
{
  LOGI("[conn-%u] Bandwidth change detected. Please adjust encoder bitrate to %u kbps", conn_id,
           target_bps / 1000);
}

static void __on_key_frame_gen_req(connection_id_t conn_id, uint32_t uid, video_stream_type_e stream_type)
{
  LOGW("[conn-%u] Frame loss detected. Please notify the encoder to generate key frame immediately", conn_id);
}
#endif

static void app_init_event_handler(agora_rtc_event_handler_t *event_handler, app_config_t *config)
{
  event_handler->on_join_channel_success = __on_join_channel_success;
  event_handler->on_connection_lost = __on_connection_lost;
  event_handler->on_rejoin_channel_success = __on_rejoin_channel_success;
  event_handler->on_user_joined = __on_user_joined;
  event_handler->on_user_offline = __on_user_offline;
  event_handler->on_user_mute_audio = __on_user_mute_audio;
#ifndef CONFIG_AUDIO_ONLY
  event_handler->on_user_mute_video = __on_user_mute_video;
  event_handler->on_target_bitrate_changed = __on_target_bitrate_changed;
  event_handler->on_key_frame_gen_req = __on_key_frame_gen_req;
  event_handler->on_video_data = __on_video_data;
#endif
  event_handler->on_error = __on_error;

  if (config->enable_audio_mixer) {
    event_handler->on_mixed_audio_data = __on_mixed_audio_data;
  } else {
    event_handler->on_audio_data = __on_audio_data;
  }
}

int main(int argc, char **argv)
{
  app_config_t *config = &g_app.config;
  int rval = 0;
  int i = 0;

  // 1. app parse args
  rval = app_parse_args(argc, argv);
  if (rval < 0) {
    app_print_usage(argc, argv);
    return -1;
  }

  LOGI("Welcome to RTSA SDK v%s", agora_rtc_get_version());

  // 2. app init
  rval = app_init();
  if (rval < 0) {
    LOGE("Failed to initialize application");
    return -1;
  }

  // 3. API: verify license
#ifdef CONFIG_LICENSE
  rval = agora_rtc_license_verify(certificate_for_test, strlen(certificate_for_test), NULL, 0);
  if (rval < 0) {
    printf("Failed to verify license, reason: %s\n", agora_rtc_err_2_str(rval));
    return -1;
  }
  printf("Verify license successfully\n");

#endif

  // 4. API: init agora rtc sdk
  int appid_len = strlen(config->p_appid);
  void *p_appid = (void *)(appid_len == 0 ? NULL : config->p_appid);

  agora_rtc_event_handler_t event_handler = { 0 };
  app_init_event_handler(&event_handler, config);

  rtc_service_option_t service_opt = { 0 };
  service_opt.area_code = config->area;
  service_opt.log_cfg.log_level = RTC_LOG_INFO;
  service_opt.log_cfg.log_path = config->p_sdk_log_dir;
  if (config->p_license_value) {
    strncpy(service_opt.license_value, config->p_license_value, sizeof(service_opt.license_value));
  }

  rval = agora_rtc_init(p_appid, &event_handler, &service_opt);
  if (rval < 0) {
    LOGE("Failed to initialize Agora sdk, reason: %s", agora_rtc_err_2_str(rval));
    return -1;
  }

#ifndef CONFIG_AUDIO_ONLY
  agora_rtc_set_bwe_param(CONNECTION_ID_ALL, DEFAULT_BANDWIDTH_ESTIMATE_MIN_BITRATE,
                          DEFAULT_BANDWIDTH_ESTIMATE_MAX_BITRATE, DEFAULT_BANDWIDTH_ESTIMATE_START_BITRATE);
#endif

  g_app.conn_cnt = 3;
  for (i = 0; i < g_app.conn_cnt; i++) {
    // 5. API: Create connection
    rval = agora_rtc_create_connection(&g_app.conn_ids[i]);
    if (rval < 0) {
      LOGE("Failed to create connection, reason: %s", agora_rtc_err_2_str(rval));
      return -1;
    }
  }

  // 6. API: join channel
  rtc_channel_options_t channel_options;
  memset(&channel_options, 0, sizeof(channel_options));
  channel_options.auto_subscribe_audio = true;
  channel_options.auto_subscribe_video = true;
  channel_options.subscribe_local_user = false;

  if (config->audio_data_type == AUDIO_DATA_TYPE_PCM) {
    /* If we want to send PCM data instead of encoded audio like AAC or Opus, here please enable
     * audio codec, as well as configure the PCM sample rate and number of channels
     */
    channel_options.audio_codec_opt.audio_codec_type = config->audio_codec_type;
    channel_options.audio_codec_opt.pcm_sample_rate = config->pcm_sample_rate;
    channel_options.audio_codec_opt.pcm_channel_num = config->pcm_channel_num;
  }

  for (i = 0; i < g_app.conn_cnt; i++) {
    rval = agora_rtc_join_channel(g_app.conn_ids[i], config->p_channel, config->uid + i + 1, config->p_token,
                                  &channel_options);
    if (rval < 0) {
      LOGE("Failed to join channel \"%s\", reason: %s", config->p_channel, agora_rtc_err_2_str(rval));
      return -1;
    }
  }

  // 7. wait until we join channel successfully
  while (!g_app.b_connected_flag && !g_app.b_stop_flag) {
    usleep(100 * 1000);
  }

  for (i = 0; i < g_app.conn_cnt; i++) {
    agora_rtc_get_connection_info(g_app.conn_ids[i], &g_app.conn_infos[i]);
    LOGI("joined-after: conn_id=%u uid=%u cname=%s", g_app.conn_infos[i].conn_id, g_app.conn_infos[i].uid,
             g_app.conn_infos[i].channel_name);
  }

  // 8. create tasks sending video and audio frames
#ifndef CONFIG_AUDIO_ONLY
  pthread_t video_thread_id;
#endif
  pthread_t audio_thread_id;

#ifndef CONFIG_AUDIO_ONLY
  rval = pthread_create(&video_thread_id, NULL, video_send_thread, 0);
  if (rval < 0) {
    printf("Unable to create video push thread\n");
    return -1;
  }
#endif

  rval = pthread_create(&audio_thread_id, NULL, audio_send_thread, 0);
  if (rval < 0) {
    printf("Unable to create audio push thread\n");
    return -1;
  }

#ifndef CONFIG_AUDIO_ONLY
  pthread_join(video_thread_id, NULL);
#endif
  pthread_join(audio_thread_id, NULL);

  for (i = 0; i < g_app.conn_cnt; i++) {
    // 9. API: leave channel
    agora_rtc_leave_channel(g_app.conn_ids[i]);
    // 10: API: Destroy connection
    agora_rtc_destroy_connection(g_app.conn_ids[i]);
  }

  // 11. API: fini rtc sdk
  agora_rtc_fini();

  return 0;
}
