#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

#include "./agora_rtc_api.h"

// Microsecond-accurate diagnostic log helper
void log_time(const char *label) {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  fprintf(stderr, "[TIME_%ld.%06ld] [agora_pusher] %s\n", (long)tv.tv_sec,
          (long)tv.tv_usec, label);
}

// SDK Interface Callbacks
void on_join_success(connection_id_t conn_id, uint32_t uid, int elapsed_ms) {
  (void)conn_id;
  (void)uid;
  (void)elapsed_ms;
  extern volatile sig_atomic_t g_join_ready;
  g_join_ready = 1;
  log_time("[CALLBACK] on_join_channel_success event fired cleanly.");
}

void on_conn_lost(connection_id_t conn_id) {
  (void)conn_id;
  extern volatile sig_atomic_t g_join_ready;
  g_join_ready = 0;
  log_time("[CALLBACK] on_connection_lost event fired.");
}

void on_rejoin(connection_id_t conn_id, uint32_t uid, int elapsed_ms) {
  (void)conn_id;
  (void)uid;
  (void)elapsed_ms;
  extern volatile sig_atomic_t g_join_ready;
  g_join_ready = 1;
  log_time("[CALLBACK] on_rejoin_channel_success event fired.");
}

void on_error(connection_id_t conn_id, int err, const char *msg) {
  (void)conn_id;
  fprintf(stderr, "[CALLBACK] on_error fired. Code: %d, Message: %s\n", err,
          msg ? msg : "NULL");
}

void on_reconnect(connection_id_t conn_id) {
  (void)conn_id;
  log_time("[CALLBACK] on_reconnecting event fired.");
}

void on_user_offline(connection_id_t conn_id, uint32_t uid, int reason) {
  (void)conn_id;
  fprintf(stderr, "[CALLBACK] on_user_offline event fired (uid=%u, reason=%d). Exiting pipeline.\n", uid, reason);
  exit(0);
}

typedef struct {
  void (*on_error)(connection_id_t conn_id, int err,
                   const char *msg); // Index 0 (offset 0)
  void (*on_join_channel_success)(connection_id_t conn_id, uint32_t uid,
                                  int elapsed_ms);     // Index 1 (offset 4)
  void (*on_reconnecting)(connection_id_t conn_id);    // Index 2 (offset 8)
  void (*on_connection_lost)(connection_id_t conn_id); // Index 3 (offset 12)
  void (*on_rejoin_channel_success)(connection_id_t conn_id, uint32_t uid,
                                    int elapsed_ms); // Index 4 (offset 16)
  void *pad1[3]; // Indices 5, 6, 7
  void (*on_user_offline)(connection_id_t conn_id, uint32_t uid,
                          int reason); // Index 8 (offset 32)
  void *pad2[17]; // Indices 9-25
} custom_event_handler_t;

typedef struct {
  void *pMb;          // Offset 0
  uint32_t u32Len;    // Offset 4
  uint64_t u64PTS;    // Offset 8
  uint32_t bFrameEnd; // Offset 16
  uint32_t DataType;  // Offset 20
  uint32_t u32Offset; // Offset 24
} my_venc_pack_t;

typedef struct {
  my_venc_pack_t *pstPack;
  uint32_t u32PackCount;
  uint32_t u32Seq;
} my_venc_stream_t;

typedef void *(*RK_MPI_MB_Handle2VirAddr_t)(void *pMb);
typedef int (*RK_MPI_VENC_GetStream_t)(int s32Chn, void *pstStream,
                                       int s32MilliSec);
typedef int (*RK_MPI_VENC_ReleaseStream_t)(int s32Chn, void *pstStream);

static int is_key_frame(const uint8_t *data, size_t size) {
  if (size < 5)
    return 0;
  size_t offset = 0;
  if (data[0] == 0x00 && data[1] == 0x00) {
    if (data[2] == 0x01) {
      offset = 3;
    } else if (data[2] == 0x00 && data[3] == 0x01) {
      offset = 4;
    }
  }
  if (offset > 0 && offset < size) {
    uint8_t nalu_type = data[offset] & 0x1f;
    if (nalu_type == 5 || nalu_type == 7 || nalu_type == 8) {
      return 1;
    }
  }
  return 0;
}

// 204-byte binary-compatible structure matching gkcam's layout
typedef struct __attribute__((packed)) {
  uint8_t auto_subscribe_audio;       // 0
  uint8_t auto_subscribe_video;       // 1
  uint8_t enable_audio_jitter_buffer; // 2 (aj)
  uint8_t enable_audio_mixer;         // 3 (am)
  uint32_t audio_codec_type;          // 4
  uint32_t pcm_sample_rate;           // 8
  uint32_t pcm_channel_num;           // 12
  uint32_t audio_duration;            // 16
  uint8_t pad_20[8];                  // 20-27
  uint8_t enable_aut_encryption;      // 28 (enable)
  uint8_t pad_29[3];                  // 29-31
  uint32_t encryption_mode;           // 32 (mode)
  char encryption_key[129];           // 36-164 (key)
  uint8_t encryption_salt[32];        // 165-196 (salt)
  uint8_t pad_197[3];                 // 197-199
  uint8_t field_200;                  // 200 (enable_rdt)
  uint8_t field_201;                  // 201 (lan_acce)
  uint8_t pad_202[2];                 // 202-203
} custom_channel_options_t;

// Self-contained Base64 Decoder
static int base64_decode(const char *in, size_t in_len, uint8_t *out,
                         size_t out_max, size_t *out_len) {
  size_t i = 0, j = 0;
  uint32_t val = 0;
  int valb = -8;
  for (i = 0; i < in_len; i++) {
    char c = in[i];
    if (c == '=')
      break;
    int d = -1;
    if (c >= 'A' && c <= 'Z')
      d = c - 'A';
    else if (c >= 'a' && c <= 'z')
      d = c - 'a' + 26;
    else if (c >= '0' && c <= '9')
      d = c - '0' + 52;
    else if (c == '+')
      d = 62;
    else if (c == '/')
      d = 63;
    if (d != -1) {
      val = (val << 6) | d;
      valb += 6;
      if (valb >= 0) {
        if (j >= out_max)
          return -1;
        out[j++] = (val >> valb) & 0xFF;
        valb -= 8;
      }
    }
  }
  *out_len = j;
  return 0;
}

// Maps encryption mode strings to integers used by libagora-rtc-sdk
static uint32_t get_encryption_mode_value(const char *mode_str) {
  if (!mode_str)
    return 8; // Default to AES_256_GCM2
  if (strcmp(mode_str, "AES_128_XTS") == 0)
    return 1;
  if (strcmp(mode_str, "AES_128_ECB") == 0)
    return 2;
  if (strcmp(mode_str, "AES_256_XTS") == 0)
    return 3;
  if (strcmp(mode_str, "SM4_128_ECB") == 0)
    return 4;
  if (strcmp(mode_str, "AES_128_GCM") == 0)
    return 5;
  if (strcmp(mode_str, "AES_256_GCM") == 0)
    return 6;
  if (strcmp(mode_str, "AES_128_GCM2") == 0)
    return 7;
  if (strcmp(mode_str, "AES_256_GCM2") == 0)
    return 8;

  char *endptr;
  unsigned long val = strtoul(mode_str, &endptr, 10);
  if (*endptr == '\0') {
    return (uint32_t)val;
  }
  return 0;
}

typedef int (*agora_rtc_init_t)(const char *app_id,
                                const agora_rtc_event_handler_t *event_handler,
                                const void *option);
typedef const char *(*agora_rtc_err_2_str_t)(int err);
typedef int (*agora_rtc_create_connection_t)(connection_id_t *conn_id);
typedef int (*agora_rtc_leave_channel_t)(connection_id_t conn_id);

// Verified 5-parameter public SDK footprint accepting custom options layout
typedef int (*agora_rtc_join_channel_t)(connection_id_t conn_id,
                                        const char *channel, uint32_t uid,
                                        const char *token, void *options);
typedef int (*agora_rtc_send_video_data_t)(connection_id_t conn_id,
                                           const void *data_ptr,
                                           size_t data_len,
                                           video_frame_info_t *info_ptr);

static uint8_t h264_ring[2 * 1024 * 1024];
static size_t h264_ring_len = 0;
static uint8_t send_bundle[2 * 1024 * 1024];
static uint8_t cached_sps[512];
static size_t cached_sps_len = 0;
static uint8_t cached_pps[256];
static size_t cached_pps_len = 0;
volatile sig_atomic_t g_join_ready = 0;

static int find_start_code(const uint8_t *data, size_t size, size_t *code_len) {
  if (size < 3) {
    return -1;
  }

  for (size_t index = 0; index + 3 < size; ++index) {
    if (data[index] == 0x00 && data[index + 1] == 0x00) {
      if (data[index + 2] == 0x01) {
        if (code_len) {
          *code_len = 3;
        }
        return (int)index;
      }
      if (index + 3 < size && data[index + 2] == 0x00 &&
          data[index + 3] == 0x01) {
        if (code_len) {
          *code_len = 4;
        }
        return (int)index;
      }
    }
  }

  return -1;
}

// Explicitly sized raw options buffer to match 0x7C copy length safely
int main(int argc, char *argv[]) {
  log_time("Program start");
  if (argc < 6) {
    fprintf(stderr,
            "[ERROR] Usage: %s <appid> <channel> <token> <license> <uid> "
            "[venc_channel] [enc_mode] [enc_key] [enc_salt]\n",
            argv[0]);
    return 1;
  }

  const char *appid = argv[1];
  const char *channel = argv[2];
  const char *token = argv[3];
  const char *license = argv[4];
  uint32_t uid = (uint32_t)strtoul(argv[5], NULL, 10);
  int venc_channel = -1;
  if (argc >= 7) {
    venc_channel = (int)strtol(argv[6], NULL, 10);
  }

  const char *enc_mode = NULL;
  const char *enc_key = NULL;
  const char *enc_salt = NULL;
  if (argc >= 10) {
    enc_mode = argv[7];
    enc_key = argv[8];
    enc_salt = argv[9];
  }

  if (enc_mode && strlen(enc_mode) == 0)
    enc_mode = NULL;
  if (enc_key && strlen(enc_key) == 0)
    enc_key = NULL;
  if (enc_salt && strlen(enc_salt) == 0)
    enc_salt = NULL;

  printf("[ARGS] appid='%s'\n", appid);
  printf("[ARGS] channel='%s'\n", channel);
  printf("[ARGS] token='%s'\n", token);
  printf("[ARGS] license='%s'\n", license);
  printf("[ARGS] uid=%u\n", uid);
  printf("[ARGS] venc_channel=%d\n", venc_channel);
  printf("[ARGS] enc_mode='%s'\n", enc_mode ? enc_mode : "NULL");
  printf("[ARGS] enc_key='%s'\n", enc_key ? enc_key : "NULL");
  printf("[ARGS] enc_salt='%s'\n", enc_salt ? enc_salt : "NULL");

  log_time("Loading libagora-rtc-sdk.so");
  void *lib = dlopen("/ac_lib/lib/third_lib/libagora-rtc-sdk.so",
                     RTLD_NOW | RTLD_GLOBAL);
  if (!lib) {
    fprintf(stderr, "[ERROR] dlopen failed: %s\n", dlerror());
    return 1;
  }

  agora_rtc_init_t fn_init = dlsym(lib, "agora_rtc_init");
  agora_rtc_err_2_str_t fn_err_str = dlsym(lib, "agora_rtc_err_2_str");
  agora_rtc_create_connection_t fn_create_conn =
      dlsym(lib, "agora_rtc_create_connection");
  agora_rtc_join_channel_t fn_join = dlsym(lib, "agora_rtc_join_channel");
  agora_rtc_leave_channel_t fn_leave = dlsym(lib, "agora_rtc_leave_channel");
  agora_rtc_send_video_data_t fn_send_video =
      dlsym(lib, "agora_rtc_send_video_data");

  if (!fn_init || !fn_create_conn || !fn_join || !fn_send_video) {
    fprintf(stderr, "[ERROR] Critical symbols could not be resolved.\n");
    dlclose(lib);
    return 1;
  }

  // Set up cleanly cleared event handler mapping aligning with actual SDK
  // structure offsets
  custom_event_handler_t handler;
  memset(&handler, 0, sizeof(handler));
  handler.on_error = on_error;
  handler.on_join_channel_success = on_join_success;
  handler.on_reconnecting = on_reconnect;
  handler.on_connection_lost = on_conn_lost;
  handler.on_rejoin_channel_success = on_rejoin;
  handler.on_user_offline = on_user_offline;
  uint32_t area_code = 0xFFFFFFFF; // Default to AREA_CODE_GLOB
  const char *area_env = getenv("AGORA_AREA_CODE");
  if (area_env) {
    area_code = (uint32_t)strtoul(area_env, NULL, 0);
    printf("[INIT] Area code set from environment: 0x%08X\n", area_code);
  }

  uint8_t service_options[0x7c];
  memset(service_options, 0, sizeof(service_options));
  *(uint32_t *)service_options = area_code;

  bool enable_sdk_log = false;
  if (enable_sdk_log) {
    service_options[68] = 0; // service_options[log_disable] = true
    service_options[69] = 0; // log_disable_desensitize = true
    *(uint32_t *)(service_options + 72) = 8; // log_level = RTC_LOG_DEFAULT
    *(const char **)(service_options + 76) = "/tmp"; // log_path = NULL
  } else {
    service_options[68] = 1;                 // log_disable = true
    service_options[69] = 1;                 // log_disable_desensitize = true
    *(uint32_t *)(service_options + 72) = 0; // log_level = RTC_LOG_DEFAULT
    *(const char **)(service_options + 76) = "/tmp"; // log_path = NULL
  }

  snprintf((char *)(service_options + 0x58), 0x21, "%s", license);

  log_time("Calling agora_rtc_init");
  int init_ret = fn_init(appid, (const agora_rtc_event_handler_t *)&handler,
                         service_options);
  printf("[INIT] %d (%s)\n", init_ret,
         fn_err_str ? fn_err_str(init_ret) : "unknown");
  if (init_ret < 0) {
    dlclose(lib);
    return 1;
  }

  connection_id_t conn_id = 0;
  int conn_ret = fn_create_conn(&conn_id);
  printf("[CONN] %d conn_id=%u\n", conn_ret, conn_id);
  if (conn_ret < 0) {
    dlclose(lib);
    return 1;
  }

  // Setup binary-compatible join options if encryption args are passed
  custom_channel_options_t options;
  custom_channel_options_t *options_ptr = NULL;

  if (enc_mode || enc_key || enc_salt) {
    memset(&options, 0, sizeof(options));
    options.auto_subscribe_audio = 0;
    options.auto_subscribe_video = 0;
    options.enable_audio_jitter_buffer = 0; // aj = 0
    options.enable_audio_mixer = 1; // am = 1 (matching local_e9 in gkcam)
    options.audio_codec_type = 0;
    options.pcm_sample_rate = 0;
    options.pcm_channel_num = 0;
    options.audio_duration = 0;
    options.enable_aut_encryption = 1;

    options.encryption_mode = get_encryption_mode_value(enc_mode);
    if (enc_key) {
      strncpy(options.encryption_key, enc_key,
              sizeof(options.encryption_key) - 1);
    }
    if (enc_salt) {
      size_t decoded_len = 0;
      if (base64_decode(enc_salt, strlen(enc_salt), options.encryption_salt,
                        sizeof(options.encryption_salt), &decoded_len) == 0) {
        printf("[INIT] Successfully decoded %zu salt bytes\n", decoded_len);
      } else {
        fprintf(stderr, "[WARNING] Failed to decode base64 salt\n");
      }
    }
    options.field_200 = 1; // enable_rdt = 1
    options.field_201 = 0; // lan_acce = 0

    options_ptr = &options;
  }

  log_time("Joining channel");
  int join_ret = fn_join(conn_id, channel, uid, token, options_ptr);
  printf("[JOIN] %d (%s)\n", join_ret,
         fn_err_str ? fn_err_str(join_ret) : "unknown");

  if (join_ret < 0) {
    dlclose(lib);
    return 1;
  }

  int wait_loops = 0;
  while (!g_join_ready && wait_loops < 500) {
    usleep(10000);
    ++wait_loops;
  }
  if (!g_join_ready) {
    fprintf(stderr, "[ERROR] channel join callback did not arrive in time\n");
    if (fn_leave) {
      fn_leave(conn_id);
    }
    dlclose(lib);
    return 1;
  }

  uint32_t total_frames = 0;

  // Check if we can and should use direct Rockchip VENC mode
  void *mpp_lib = NULL;
  RK_MPI_VENC_GetStream_t fn_venc_get_stream = NULL;
  RK_MPI_VENC_ReleaseStream_t fn_venc_release_stream = NULL;
  RK_MPI_MB_Handle2VirAddr_t fn_mb_handle2viraddr = NULL;

  if (venc_channel >= 0) {
    const char *mpp_libs[] = {"librkmedia.so",
                              "librockchip_mpp.so",
                              "/usr/lib/librkmedia.so",
                              "/usr/lib/librockchip_mpp.so",
                              "/oem/usr/lib/librkmedia.so",
                              "/oem/usr/lib/librockchip_mpp.so",
                              "/ac_lib/lib/third_lib/librkmedia.so",
                              "/ac_lib/lib/third_lib/librockchip_mpp.so"};
    for (size_t i = 0; i < sizeof(mpp_libs) / sizeof(mpp_libs[0]); i++) {
      mpp_lib = dlopen(mpp_libs[i], RTLD_NOW | RTLD_GLOBAL);
      if (mpp_lib) {
        printf("[INIT] Successfully loaded MPP library: %s\n", mpp_libs[i]);
        fn_venc_get_stream =
            (RK_MPI_VENC_GetStream_t)dlsym(mpp_lib, "RK_MPI_VENC_GetStream");
        fn_venc_release_stream = (RK_MPI_VENC_ReleaseStream_t)dlsym(
            mpp_lib, "RK_MPI_VENC_ReleaseStream");
        fn_mb_handle2viraddr = (RK_MPI_MB_Handle2VirAddr_t)dlsym(
            mpp_lib, "RK_MPI_MB_Handle2VirAddr");
        if (fn_venc_get_stream && fn_venc_release_stream &&
            fn_mb_handle2viraddr) {
          break;
        }
        dlclose(mpp_lib);
        mpp_lib = NULL;
      }
    }
    if (!mpp_lib) {
      fprintf(stderr, "[WARNING] Direct VENC mode failed to load MPP/rkmedia "
                      "libraries. Falling back to STDIN.\n");
      venc_channel = -1;
    }
  }

  if (venc_channel >= 0 && fn_venc_get_stream && fn_venc_release_stream &&
      fn_mb_handle2viraddr) {
    log_time("Entering Rockchip VENC channel loop");
    my_venc_stream_t stream;
    my_venc_pack_t pack_array[10];
    memset(&stream, 0, sizeof(stream));
    stream.pstPack = pack_array;
    stream.u32PackCount = 10;

    while (1) {
      int ret = fn_venc_get_stream(venc_channel, &stream, 10);
      if (ret == 0) {
        if (stream.u32PackCount > 0 && stream.pstPack != NULL) {
          for (uint32_t i = 0; i < stream.u32PackCount; i++) {
            void *vir_addr = fn_mb_handle2viraddr(stream.pstPack[i].pMb);
            uint32_t len = stream.pstPack[i].u32Len;
            if (vir_addr != NULL && len > 0) {
              total_frames++;
              int is_key = is_key_frame((const uint8_t *)vir_addr, len);

              video_frame_info_t video_frame_info;
              memset(&video_frame_info, 0, sizeof(video_frame_info));
              video_frame_info.data_type = VIDEO_DATA_TYPE_H264;
              video_frame_info.stream_type = VIDEO_STREAM_HIGH;
              video_frame_info.frame_rate = 0; // Use real timestamp
              video_frame_info.frame_type =
                  is_key ? VIDEO_FRAME_KEY : VIDEO_FRAME_DELTA;

              int send_ret =
                  fn_send_video(conn_id, vir_addr, len, &video_frame_info);
              if (total_frames % 20 == 1 || send_ret < 0) {
                fprintf(stderr,
                        "[agora_pusher] VENC Transaction -> ID: %u, Bytes "
                        "shipped: %u, Result: %d (%s), Key: %d\n",
                        total_frames, len, send_ret,
                        fn_err_str ? fn_err_str(send_ret) : "unknown", is_key);
              }
            }
          }
        }
        fn_venc_release_stream(venc_channel, &stream);
      } else {
        usleep(10000);
      }
    }
  } else {
    log_time("Entering H.264 Annex-B STDIN send loop");

    uint8_t read_tmp[4096];
    ssize_t n;

    while ((n = read(STDIN_FILENO, read_tmp, sizeof(read_tmp))) > 0) {
      if (h264_ring_len + (size_t)n > sizeof(h264_ring)) {
        h264_ring_len = 0;
      }
      memcpy(h264_ring + h264_ring_len, read_tmp, (size_t)n);
      h264_ring_len += (size_t)n;

      while (1) {
        if (h264_ring_len < 3) {
          break;
        }

        size_t start_code_len = 0;
        int sync_idx =
            find_start_code(h264_ring, h264_ring_len, &start_code_len);
        if (sync_idx < 0) {
          h264_ring_len = 0;
          break;
        }
        if (sync_idx > 0) {
          memmove(h264_ring, h264_ring + sync_idx,
                  h264_ring_len - (size_t)sync_idx);
          h264_ring_len -= (size_t)sync_idx;
          continue;
        }

        size_t next_frame_idx = 0;
        size_t next_start_code_len = 0;
        int next_sync_idx = find_start_code(h264_ring + start_code_len,
                                            h264_ring_len - start_code_len,
                                            &next_start_code_len);
        if (next_sync_idx >= 0) {
          next_frame_idx = start_code_len + (size_t)next_sync_idx;
        }

        if (next_frame_idx == 0) {
          break;
        }

        uint8_t nalu_type = h264_ring[start_code_len] & 0x1f;

        if (nalu_type == 7) {
          if (next_frame_idx <= sizeof(cached_sps)) {
            memcpy(cached_sps, h264_ring, next_frame_idx);
            cached_sps_len = next_frame_idx;
          }
        } else if (nalu_type == 8) {
          if (next_frame_idx <= sizeof(cached_pps)) {
            memcpy(cached_pps, h264_ring, next_frame_idx);
            cached_pps_len = next_frame_idx;
          }
        }

        // If this is a VCL NALU (slice), we send the frame (with SPS/PPS
        // prepended for keyframes)
        if (nalu_type >= 1 && nalu_type <= 5) {
          size_t send_len = 0;
          if (nalu_type == 5) {
            // Prepend cached SPS and PPS to keyframe
            if (cached_sps_len > 0 && cached_pps_len > 0) {
              if (send_len + cached_sps_len + cached_pps_len <
                  sizeof(send_bundle)) {
                memcpy(send_bundle + send_len, cached_sps, cached_sps_len);
                send_len += cached_sps_len;
                memcpy(send_bundle + send_len, cached_pps, cached_pps_len);
                send_len += cached_pps_len;
              }
            }
          }

          if (send_len + next_frame_idx < sizeof(send_bundle)) {
            memcpy(send_bundle + send_len, h264_ring, next_frame_idx);
            send_len += next_frame_idx;

            video_frame_info_t video_frame_info;
            memset(&video_frame_info, 0, sizeof(video_frame_info));
            video_frame_info.data_type = VIDEO_DATA_TYPE_H264;
            video_frame_info.stream_type = VIDEO_STREAM_HIGH;
            video_frame_info.frame_rate = 0; // Use real timestamp
            video_frame_info.frame_type =
                (nalu_type == 5) ? VIDEO_FRAME_KEY : VIDEO_FRAME_DELTA;

            total_frames++;
            int send_ret = fn_send_video(conn_id, send_bundle, send_len,
                                         &video_frame_info);
            if (total_frames % 20 == 1 || send_ret < 0) {
              fprintf(stderr,
                      "[agora_pusher] Packet Transaction -> ID: %u, Bytes "
                      "shipped: %zu, Result: %d (%s), VCL NALU: %u, Key: %d\n",
                      total_frames, send_len, send_ret,
                      fn_err_str ? fn_err_str(send_ret) : "unknown", nalu_type,
                      (nalu_type == 5));
            }
          }
        }

        memmove(h264_ring, h264_ring + next_frame_idx,
                h264_ring_len - next_frame_idx);
        h264_ring_len -= next_frame_idx;
      }
    }
  }

  log_time("Tearing down bridge pipeline components.");
  if (mpp_lib) {
    dlclose(mpp_lib);
  }
  if (fn_leave) {
    fn_leave(conn_id);
  }
  dlclose(lib);
  return 0;
}
