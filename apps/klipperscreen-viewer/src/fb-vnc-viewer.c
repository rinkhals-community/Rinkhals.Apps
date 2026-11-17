/*
 * fb-vnc-viewer - Framebuffer VNC viewer for embedded Linux
 *
 * Connects to a remote VNC server and renders the display to /dev/fb0.
 * Reads touch events from /dev/input/event0 and forwards them as VNC
 * pointer events. Designed for running KlipperScreen on a remote RPi
 * and displaying it on the printer's touchscreen.
 *
 * SPDX-License-Identifier: GPL-2.0+
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <arpa/inet.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <rfb/rfbclient.h>

static volatile int running = 1;

/* Framebuffer state */
struct fb_state {
    int fd;
    uint8_t *mem;
    uint8_t *backbuf;  /* shadow buffer to avoid tearing */
    uint8_t *draw_mem; /* active render target: backbuf or fb mmap */
    int use_backbuf;   /* 1 = render to backbuf + flip, 0 = render direct */
    struct fb_var_screeninfo vinfo;
    struct fb_fix_screeninfo finfo;
    int width;
    int height;
    int bpp;       /* bytes per pixel */
    int stride;    /* line length in bytes */
    int dirty_min; /* first dirty row (inclusive), -1 = clean */
    int dirty_max; /* last dirty row (inclusive) */
};

/* Touch input state */
struct touch_state {
    int fd;
    int x;
    int y;
    int pressed;
    int slot;          /* current MT slot being tracked */
    int use_fb_scale;  /* use framebuffer-sized coords instead of advertised abs range */
    /* Raw coordinate range from evdev */
    int abs_min_x;
    int abs_max_x;
    int abs_min_y;
    int abs_max_y;
};

/* Display transformation */
struct transform {
    int rotation;        /* render rotation: 0, 90, 180, 270 */
    int touch_rotation;  /* touch rotation: 0, 90, 180, 270 */
    int touch_swap_xy;   /* swap normalized touch axes before rotation */
    int fb_width;
    int fb_height;
    int vnc_width;
    int vnc_height;
};

/* Per-connection context stored via rfbClientSetClientData */
struct viewer_ctx {
    struct fb_state fb;
    struct touch_state touch;
    struct transform xform;
    int client_bpp;             /* requested VNC client bpp: 16 or 32 */
    int update_interval_ms;     /* throttle incremental update requests */
    int update_request_pending; /* TRUE after request sent, FALSE after reply */
    int got_fb_update;          /* 1 when vnc_finished fires (real frame data) */
    int exit_hold_ms;           /* 0 disables local hold-to-exit */
    int exit_corner_px;         /* physical top-left trigger size, 0 = auto */
    int exit_move_tol_px;       /* allowed finger drift, 0 = auto */
    int exit_hold_active;       /* touch sequence still eligible to trigger */
    int exit_swallow_touch;     /* suppress forwarding this touch sequence */
    long long exit_hold_started_ms;
    int exit_hold_start_x;
    int exit_hold_start_y;
};

static rfbClient *vnc_client;

static void signal_handler(int sig)
{
    (void)sig;
    running = 0;
}

static long long monotonic_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000LL + (long long)ts.tv_nsec / 1000000LL;
}

static uint32_t rgb565_to_bgra_lut[65536];
static int rgb565_to_bgra_lut_ready = 0;

static void init_rgb565_lut(void)
{
    if (rgb565_to_bgra_lut_ready)
        return;

    for (int c = 0; c < 65536; c++) {
        uint8_t r = (uint8_t)(((c >> 11) & 0x1F) << 3);
        uint8_t g = (uint8_t)(((c >> 5) & 0x3F) << 2);
        uint8_t b = (uint8_t)((c & 0x1F) << 3);
        rgb565_to_bgra_lut[c] = (uint32_t)b | ((uint32_t)g << 8)
                              | ((uint32_t)r << 16) | 0xFF000000u;
    }

    rgb565_to_bgra_lut_ready = 1;
}

/* ── Framebuffer ─────────────────────────────────────────────── */

static int fb_open(struct fb_state *fb, const char *device)
{
    fb->fd = open(device, O_RDWR);
    if (fb->fd < 0) {
        perror("open framebuffer");
        return -1;
    }

    if (ioctl(fb->fd, FBIOGET_VSCREENINFO, &fb->vinfo) < 0) {
        perror("FBIOGET_VSCREENINFO");
        close(fb->fd);
        return -1;
    }

    /* Claim the display, not just the buffer: on DRM fbdev emulation the
     * panel keeps scanning out the bootloader splash until something does a
     * modeset (normally a compositor, or fbcon - absent on kiosk kernels).
     * Writing the vinfo back forces set_par and with it the modeset;
     * FB_ACTIVATE_FORCE because the vinfo is unchanged and the fb core
     * would otherwise skip set_par entirely. Non-fatal: displays already
     * scanning out fb memory don't need it. */
    fb->vinfo.activate = FB_ACTIVATE_NOW | FB_ACTIVATE_FORCE;
    if (ioctl(fb->fd, FBIOPUT_VSCREENINFO, &fb->vinfo) < 0)
        perror("FBIOPUT_VSCREENINFO (continuing)");

    if (ioctl(fb->fd, FBIOGET_FSCREENINFO, &fb->finfo) < 0) {
        perror("FBIOGET_FSCREENINFO");
        close(fb->fd);
        return -1;
    }

    fb->width = fb->vinfo.xres;
    fb->height = fb->vinfo.yres;
    fb->bpp = fb->vinfo.bits_per_pixel / 8;
    fb->stride = fb->finfo.line_length;

    size_t size = fb->stride * fb->height;
    fb->mem = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fb->fd, 0);
    if (fb->mem == MAP_FAILED) {
        perror("mmap framebuffer");
        close(fb->fd);
        return -1;
    }

    if (fb->use_backbuf) {
        /* Allocate back buffer for tear-free rendering */
        fb->backbuf = calloc(1, size);
        if (!fb->backbuf) {
            perror("calloc backbuf");
            munmap(fb->mem, size);
            close(fb->fd);
            return -1;
        }
        fb->draw_mem = fb->backbuf;
    } else {
        fb->backbuf = NULL;
        fb->draw_mem = fb->mem;
    }

    fb->dirty_min = -1;
    fb->dirty_max = 0;

    fprintf(stderr, "fb: %dx%d %dbpp stride=%d\n",
            fb->width, fb->height, fb->bpp * 8, fb->stride);
    return 0;
}

static void fb_close(struct fb_state *fb)
{
    free(fb->backbuf);
    if (fb->mem && fb->mem != MAP_FAILED)
        munmap(fb->mem, fb->stride * fb->height);
    if (fb->fd >= 0)
        close(fb->fd);
}

/* Mark rows [y0..y1] dirty. */
static inline void fb_dirty(struct fb_state *fb, int y0, int y1)
{
    if (fb->dirty_min < 0 || y0 < fb->dirty_min)
        fb->dirty_min = y0;
    if (y1 > fb->dirty_max)
        fb->dirty_max = y1;
}

/* Forward declaration needed by fb_draw_status_lines */
static void fb_flip(struct fb_state *fb);

/* ── Embedded 5x7 bitmap font ────────────────────────────────── */

/*
 * Minimal bitmap font for on-screen status messages.
 * Each glyph is 5 pixels wide x 7 pixels tall, stored as 7 bytes
 * (one byte per row, MSB = leftmost pixel, only bits 7..3 used).
 * Covers ASCII 32 (' ') through 126 ('~') -- 95 glyphs.
 */

#define FONT_W 5
#define FONT_H 7
#define FONT_FIRST 32
#define FONT_LAST  126
#define FONT_GLYPH_COUNT (FONT_LAST - FONT_FIRST + 1)

static const uint8_t font5x7[FONT_GLYPH_COUNT][FONT_H] = {
    /* 32 ' ' */ {0x00,0x00,0x00,0x00,0x00,0x00,0x00},
    /* 33 '!' */ {0x20,0x20,0x20,0x20,0x00,0x20,0x00},
    /* 34 '"' */ {0x50,0x50,0x00,0x00,0x00,0x00,0x00},
    /* 35 '#' */ {0x50,0xF8,0x50,0x50,0xF8,0x50,0x00},
    /* 36 '$' */ {0x20,0x78,0xA0,0x70,0x28,0xF0,0x20},
    /* 37 '%' */ {0xC8,0xD0,0x20,0x40,0x58,0x98,0x00},
    /* 38 '&' */ {0x40,0xA0,0x40,0xA8,0x90,0x68,0x00},
    /* 39 ''' */ {0x20,0x20,0x00,0x00,0x00,0x00,0x00},
    /* 40 '(' */ {0x10,0x20,0x40,0x40,0x20,0x10,0x00},
    /* 41 ')' */ {0x40,0x20,0x10,0x10,0x20,0x40,0x00},
    /* 42 '*' */ {0x20,0xA8,0x70,0x20,0x70,0xA8,0x20},
    /* 43 '+' */ {0x00,0x20,0x20,0xF8,0x20,0x20,0x00},
    /* 44 ',' */ {0x00,0x00,0x00,0x00,0x20,0x20,0x40},
    /* 45 '-' */ {0x00,0x00,0x00,0xF8,0x00,0x00,0x00},
    /* 46 '.' */ {0x00,0x00,0x00,0x00,0x00,0x20,0x00},
    /* 47 '/' */ {0x08,0x10,0x20,0x40,0x80,0x00,0x00},
    /* 48 '0' */ {0x70,0x88,0x98,0xA8,0xC8,0x70,0x00},
    /* 49 '1' */ {0x20,0x60,0x20,0x20,0x20,0x70,0x00},
    /* 50 '2' */ {0x70,0x88,0x08,0x30,0x40,0xF8,0x00},
    /* 51 '3' */ {0x70,0x88,0x30,0x08,0x88,0x70,0x00},
    /* 52 '4' */ {0x10,0x30,0x50,0x90,0xF8,0x10,0x00},
    /* 53 '5' */ {0xF8,0x80,0xF0,0x08,0x08,0xF0,0x00},
    /* 54 '6' */ {0x30,0x40,0xF0,0x88,0x88,0x70,0x00},
    /* 55 '7' */ {0xF8,0x08,0x10,0x20,0x40,0x40,0x00},
    /* 56 '8' */ {0x70,0x88,0x70,0x88,0x88,0x70,0x00},
    /* 57 '9' */ {0x70,0x88,0x88,0x78,0x10,0x60,0x00},
    /* 58 ':' */ {0x00,0x00,0x20,0x00,0x20,0x00,0x00},
    /* 59 ';' */ {0x00,0x00,0x20,0x00,0x20,0x20,0x40},
    /* 60 '<' */ {0x08,0x10,0x20,0x40,0x20,0x10,0x08},
    /* 61 '=' */ {0x00,0x00,0xF8,0x00,0xF8,0x00,0x00},
    /* 62 '>' */ {0x80,0x40,0x20,0x10,0x20,0x40,0x80},
    /* 63 '?' */ {0x70,0x88,0x10,0x20,0x00,0x20,0x00},
    /* 64 '@' */ {0x70,0x88,0xB8,0xB8,0x80,0x70,0x00},
    /* 65 'A' */ {0x70,0x88,0x88,0xF8,0x88,0x88,0x00},
    /* 66 'B' */ {0xF0,0x88,0xF0,0x88,0x88,0xF0,0x00},
    /* 67 'C' */ {0x70,0x88,0x80,0x80,0x88,0x70,0x00},
    /* 68 'D' */ {0xF0,0x88,0x88,0x88,0x88,0xF0,0x00},
    /* 69 'E' */ {0xF8,0x80,0xF0,0x80,0x80,0xF8,0x00},
    /* 70 'F' */ {0xF8,0x80,0xF0,0x80,0x80,0x80,0x00},
    /* 71 'G' */ {0x70,0x88,0x80,0xB8,0x88,0x70,0x00},
    /* 72 'H' */ {0x88,0x88,0xF8,0x88,0x88,0x88,0x00},
    /* 73 'I' */ {0x70,0x20,0x20,0x20,0x20,0x70,0x00},
    /* 74 'J' */ {0x08,0x08,0x08,0x08,0x88,0x70,0x00},
    /* 75 'K' */ {0x88,0x90,0xA0,0xC0,0xA0,0x90,0x88},
    /* 76 'L' */ {0x80,0x80,0x80,0x80,0x80,0xF8,0x00},
    /* 77 'M' */ {0x88,0xD8,0xA8,0x88,0x88,0x88,0x00},
    /* 78 'N' */ {0x88,0xC8,0xA8,0x98,0x88,0x88,0x00},
    /* 79 'O' */ {0x70,0x88,0x88,0x88,0x88,0x70,0x00},
    /* 80 'P' */ {0xF0,0x88,0x88,0xF0,0x80,0x80,0x00},
    /* 81 'Q' */ {0x70,0x88,0x88,0xA8,0x90,0x68,0x00},
    /* 82 'R' */ {0xF0,0x88,0x88,0xF0,0xA0,0x90,0x00},
    /* 83 'S' */ {0x70,0x80,0x70,0x08,0x08,0xF0,0x00},
    /* 84 'T' */ {0xF8,0x20,0x20,0x20,0x20,0x20,0x00},
    /* 85 'U' */ {0x88,0x88,0x88,0x88,0x88,0x70,0x00},
    /* 86 'V' */ {0x88,0x88,0x88,0x50,0x50,0x20,0x00},
    /* 87 'W' */ {0x88,0x88,0x88,0xA8,0xA8,0x50,0x00},
    /* 88 'X' */ {0x88,0x50,0x20,0x20,0x50,0x88,0x00},
    /* 89 'Y' */ {0x88,0x88,0x50,0x20,0x20,0x20,0x00},
    /* 90 'Z' */ {0xF8,0x10,0x20,0x40,0x80,0xF8,0x00},
    /* 91 '[' */ {0x70,0x40,0x40,0x40,0x40,0x70,0x00},
    /* 92 '\' */ {0x80,0x40,0x20,0x10,0x08,0x00,0x00},
    /* 93 ']' */ {0x70,0x10,0x10,0x10,0x10,0x70,0x00},
    /* 94 '^' */ {0x20,0x50,0x88,0x00,0x00,0x00,0x00},
    /* 95 '_' */ {0x00,0x00,0x00,0x00,0x00,0xF8,0x00},
    /* 96 '`' */ {0x40,0x20,0x00,0x00,0x00,0x00,0x00},
    /* 97 'a' */ {0x00,0x00,0x70,0x08,0x78,0x78,0x00},
    /* 98 'b' */ {0x80,0x80,0xF0,0x88,0x88,0xF0,0x00},
    /* 99 'c' */ {0x00,0x00,0x70,0x80,0x80,0x70,0x00},
    /*100 'd' */ {0x08,0x08,0x78,0x88,0x88,0x78,0x00},
    /*101 'e' */ {0x00,0x00,0x70,0x88,0xF0,0x70,0x00},
    /*102 'f' */ {0x30,0x40,0xF0,0x40,0x40,0x40,0x00},
    /*103 'g' */ {0x00,0x00,0x78,0x88,0x78,0x08,0x70},
    /*104 'h' */ {0x80,0x80,0xF0,0x88,0x88,0x88,0x00},
    /*105 'i' */ {0x20,0x00,0x60,0x20,0x20,0x70,0x00},
    /*106 'j' */ {0x10,0x00,0x10,0x10,0x10,0x90,0x60},
    /*107 'k' */ {0x80,0x80,0x90,0xA0,0xC0,0xA0,0x90},
    /*108 'l' */ {0x60,0x20,0x20,0x20,0x20,0x70,0x00},
    /*109 'm' */ {0x00,0x00,0xD0,0xA8,0xA8,0x88,0x00},
    /*110 'n' */ {0x00,0x00,0xF0,0x88,0x88,0x88,0x00},
    /*111 'o' */ {0x00,0x00,0x70,0x88,0x88,0x70,0x00},
    /*112 'p' */ {0x00,0x00,0xF0,0x88,0xF0,0x80,0x80},
    /*113 'q' */ {0x00,0x00,0x78,0x88,0x78,0x08,0x08},
    /*114 'r' */ {0x00,0x00,0xB0,0xC8,0x80,0x80,0x00},
    /*115 's' */ {0x00,0x00,0x78,0xC0,0x38,0xF0,0x00},
    /*116 't' */ {0x40,0x40,0xF0,0x40,0x40,0x30,0x00},
    /*117 'u' */ {0x00,0x00,0x88,0x88,0x88,0x78,0x00},
    /*118 'v' */ {0x00,0x00,0x88,0x88,0x50,0x20,0x00},
    /*119 'w' */ {0x00,0x00,0x88,0xA8,0xA8,0x50,0x00},
    /*120 'x' */ {0x00,0x00,0x88,0x50,0x20,0x50,0x88},
    /*121 'y' */ {0x00,0x00,0x88,0x88,0x78,0x08,0x70},
    /*122 'z' */ {0x00,0x00,0xF8,0x10,0x20,0x40,0xF8},
    /*123 '{' */ {0x10,0x20,0x20,0x40,0x20,0x20,0x10},
    /*124 '|' */ {0x20,0x20,0x20,0x20,0x20,0x20,0x20},
    /*125 '}' */ {0x40,0x20,0x20,0x10,0x20,0x20,0x40},
    /*126 '~' */ {0x00,0x40,0xA8,0x10,0x00,0x00,0x00},
};

/* Write a single pixel to the framebuffer draw buffer. */
static inline void fb_put_pixel(struct fb_state *fb, int x, int y,
                                uint8_t r, uint8_t g, uint8_t b)
{
    if (x < 0 || x >= fb->width || y < 0 || y >= fb->height)
        return;
    uint8_t *p = fb->draw_mem + y * fb->stride + x * fb->bpp;
    if (fb->bpp == 4) {
        p[0] = b;
        p[1] = g;
        p[2] = r;
        p[3] = 0xFF;
    } else if (fb->bpp == 2) {
        uint16_t c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
        p[0] = c & 0xFF;
        p[1] = c >> 8;
    }
}

/*
 * Map a logical status-canvas pixel to framebuffer pixel coordinates
 * using the same rotation model as VNC rendering.
 */
static inline void status_to_fb(int rotation, int status_w, int status_h,
                                int fb_w, int fb_h, int sx, int sy,
                                int *fx, int *fy)
{
    int fw = fb_w - 1;
    int fh = fb_h - 1;

    switch (rotation) {
    case 90:
        *fx = ((status_h - sy) * fw) / status_h;
        *fy = (sx * fh) / status_w;
        break;
    case 180:
        *fx = ((status_w - sx) * fw) / status_w;
        *fy = ((status_h - sy) * fh) / status_h;
        break;
    case 270:
        *fx = (sy * fw) / status_h;
        *fy = ((status_w - sx) * fh) / status_w;
        break;
    default:
        *fx = (sx * fw) / status_w;
        *fy = (sy * fh) / status_h;
        break;
    }
}

static inline void fb_put_pixel_rotated(struct fb_state *fb, int rotation,
                                        int status_w, int status_h, int x, int y,
                                        uint8_t r, uint8_t g, uint8_t b)
{
    int fx, fy;

    if (x < 0 || x >= status_w || y < 0 || y >= status_h)
        return;

    status_to_fb(rotation, status_w, status_h,
                 fb->width, fb->height, x, y, &fx, &fy);
    fb_put_pixel(fb, fx, fy, r, g, b);
}

/*
 * Measure the pixel width of a string at a given scale.
 * Each glyph is FONT_W*scale pixels, with 1*scale pixel gap between chars.
 */
static int text_width(const char *s, int scale)
{
    int len = 0;
    while (*s++)
        len++;
    if (len == 0)
        return 0;
    return len * FONT_W * scale + (len - 1) * scale;
}

static int text_height(int scale)
{
    return FONT_H * scale;
}

/*
 * Draw a string at (x0, y0) in framebuffer coordinates.
 * scale enlarges each font pixel to a scale x scale block.
 */
static void fb_draw_text(struct fb_state *fb, int rotation,
                         int status_w, int status_h,
                         int x0, int y0, int scale,
                         uint8_t r, uint8_t g, uint8_t b, const char *s)
{
    int cx = x0;
    for (; *s; s++) {
        int ch = (unsigned char)*s;
        if (ch < FONT_FIRST || ch > FONT_LAST)
            ch = '?';
        const uint8_t *glyph = font5x7[ch - FONT_FIRST];

        for (int row = 0; row < FONT_H; row++) {
            uint8_t bits = glyph[row];
            for (int col = 0; col < FONT_W; col++) {
                if (bits & (0x80 >> col)) {
                    /* Fill a scale x scale block */
                    for (int sy = 0; sy < scale; sy++)
                        for (int sx = 0; sx < scale; sx++)
                            fb_put_pixel_rotated(fb, rotation, status_w, status_h,
                                                 cx + col * scale + sx,
                                                 y0 + row * scale + sy, r, g, b);
                }
            }
        }
        cx += (FONT_W + 1) * scale; /* advance + 1-pixel gap (scaled) */
    }
}

/*
 * Clear the screen and draw centered status lines.
 *
 * lines[]  = array of string pointers (NULL-terminated or bounded by nlines).
 * nlines   = number of lines to draw.
 *
 * Chooses a scale factor so the text block is readable on any resolution
 * from 272x480 portrait up to 800x480 landscape.  Each line is
 * individually centered horizontally; the block is centered vertically.
 */
static void fb_draw_status_lines(struct fb_state *fb, int rotation,
                                 const char **lines, int nlines)
{
    int status_w = fb->width;
    int status_h = fb->height;

    if (rotation == 90 || rotation == 270) {
        status_w = fb->height;
        status_h = fb->width;
    }

    /* Clear to black */
    memset(fb->draw_mem, 0, fb->stride * fb->height);

    if (!lines || nlines <= 0)
        goto flip;

    {
        /* Pick scale based on the shortest screen dimension */
        int min_dim = status_w < status_h ? status_w : status_h;
        int scale = min_dim / (FONT_H * 10);
        if (scale < 1)
            scale = 1;

        /* Shrink until the widest line fits within 90% of screen width */
        for (int i = 0; i < nlines; i++)
            while (scale > 1 && text_width(lines[i], scale) > status_w * 9 / 10)
                scale--;

        int line_h = text_height(scale);
        int gap = scale * 2;  /* vertical gap between lines */
        int total_h = nlines * line_h + (nlines - 1) * gap;
        int y = (status_h - total_h) / 2;

        for (int i = 0; i < nlines; i++) {
            int tw = text_width(lines[i], scale);
            int x = (status_w - tw) / 2;
            /* First line (status) brighter, detail lines dimmer */
            if (i == 0)
                fb_draw_text(fb, rotation, status_w, status_h,
                             x, y, scale, 0xAA, 0xAA, 0xAA, lines[i]);
            else
                fb_draw_text(fb, rotation, status_w, status_h,
                             x, y, scale, 0x66, 0x66, 0x66, lines[i]);
            y += line_h + gap;
        }
    }

flip:
    fb_dirty(fb, 0, fb->height - 1);
    fb_flip(fb);
}

/*
 * Enumerate local non-loopback IPv4 addresses with their interface names.
 * Writes lines like "wlan0: 192.168.1.42" into buf[0..max_entries-1].
 * Each buf[i] must point to a char array of at least buf_sz bytes.
 * Returns the number of entries written.
 */
static int get_local_ips(char buf[][64], int max_entries)
{
    struct ifaddrs *ifap = NULL, *ifa;
    int count = 0;

    if (getifaddrs(&ifap) != 0)
        return 0;

    for (int pass = 0; pass < 2 && count < max_entries; pass++) {
        int want_preferred = (pass == 0);

        for (ifa = ifap; ifa && count < max_entries; ifa = ifa->ifa_next) {
            int is_preferred = 0;

            if (!ifa->ifa_addr)
                continue;
            if (ifa->ifa_addr->sa_family != AF_INET)
                continue;
            if (ifa->ifa_flags & IFF_LOOPBACK)
                continue;

            if (ifa->ifa_name &&
                (strncmp(ifa->ifa_name, "eth", 3) == 0 ||
                 strncmp(ifa->ifa_name, "en", 2) == 0 ||
                 strncmp(ifa->ifa_name, "wlan", 4) == 0 ||
                 strncmp(ifa->ifa_name, "wl", 2) == 0))
                is_preferred = 1;

            if (is_preferred != want_preferred)
                continue;

            struct sockaddr_in *sin = (struct sockaddr_in *)ifa->ifa_addr;
            char addr[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &sin->sin_addr, addr, sizeof(addr));

            snprintf(buf[count], 64, "%s: %s", ifa->ifa_name, addr);
            count++;
        }
    }

    freeifaddrs(ifap);
    return count;
}

#define STATUS_MAX_LINES 12

/*
 * Draw a full status screen: status message, target host, and local IPs.
 * Designed to be called from the reconnect / connecting paths.
 */
static void fb_draw_status_screen(struct fb_state *fb, int rotation,
                                  const char *status_msg,
                                  const char *vnc_host, int vnc_port)
{
    const char *lines[STATUS_MAX_LINES];
    int n = 0;

    /* Line 0: main status */
    lines[n++] = status_msg;

    /* Line 1: blank separator (empty string = blank line) */
    lines[n++] = "";

    /* Line 2: target host */
    char target_buf[80];
    snprintf(target_buf, sizeof(target_buf), "Target: %s:%d", vnc_host, vnc_port);
    lines[n++] = target_buf;

    /* Lines 3+: local IPs */
    char ip_bufs[8][64];
    int ip_count = get_local_ips(ip_bufs, 8);

    if (ip_count == 0) {
        lines[n++] = "No network interfaces found";
    } else {
        for (int i = 0; i < ip_count && n < STATUS_MAX_LINES; i++)
            lines[n++] = ip_bufs[i];
    }

    fb_draw_status_lines(fb, rotation, lines, n);
}

/* ── Touch input ─────────────────────────────────────────────── */

static int touch_open(struct touch_state *ts, const char *device)
{
    ts->fd = open(device, O_RDONLY | O_NONBLOCK);
    if (ts->fd < 0) {
        perror("open touch device");
        return -1;
    }

    /* Query the absolute axis ranges */
    struct input_absinfo abs;
    if (ioctl(ts->fd, EVIOCGABS(ABS_X), &abs) == 0) {
        ts->abs_min_x = abs.minimum;
        ts->abs_max_x = abs.maximum;
    } else if (ioctl(ts->fd, EVIOCGABS(ABS_MT_POSITION_X), &abs) == 0) {
        ts->abs_min_x = abs.minimum;
        ts->abs_max_x = abs.maximum;
    }

    if (ioctl(ts->fd, EVIOCGABS(ABS_Y), &abs) == 0) {
        ts->abs_min_y = abs.minimum;
        ts->abs_max_y = abs.maximum;
    } else if (ioctl(ts->fd, EVIOCGABS(ABS_MT_POSITION_Y), &abs) == 0) {
        ts->abs_min_y = abs.minimum;
        ts->abs_max_y = abs.maximum;
    }

    /* Grab the device to prevent conflict with LVGL */
    if (ioctl(ts->fd, EVIOCGRAB, 1) < 0)
        fprintf(stderr, "warning: could not grab touch device\n");

    fprintf(stderr, "touch: abs_x=[%d..%d] abs_y=[%d..%d]\n",
            ts->abs_min_x, ts->abs_max_x, ts->abs_min_y, ts->abs_max_y);
    ts->slot = 0;
    ts->use_fb_scale = 0;
    return 0;
}

static void touch_close(struct touch_state *ts)
{
    if (ts->fd >= 0) {
        ioctl(ts->fd, EVIOCGRAB, 0);
        close(ts->fd);
    }
}

static void touch_maybe_use_fb_scale(struct viewer_ctx *ctx)
{
    struct touch_state *ts = &ctx->touch;

    if (ts->use_fb_scale)
        return;

    /* Some single-touch controllers advertise a wide raw range (for example
     * 0..4095) but actually report coordinates already scaled close to the
     * framebuffer size. If we trust the advertised range, all touches collapse
     * into the VNC top-left corner. Switch to framebuffer-sized scaling once
     * we see in-range samples that clearly match screen coordinates. */
    if (ctx->fb.width <= 0 || ctx->fb.height <= 0)
        return;
    if (ts->x < 0 || ts->y < 0)
        return;
    if (ts->x >= ctx->fb.width || ts->y >= ctx->fb.height)
        return;
    if (ts->abs_max_x < ctx->fb.width * 2 || ts->abs_max_y < ctx->fb.height * 2)
        return;

    ts->abs_min_x = 0;
    ts->abs_max_x = ctx->fb.width - 1;
    ts->abs_min_y = 0;
    ts->abs_max_y = ctx->fb.height - 1;
    ts->use_fb_scale = 1;

    fprintf(stderr,
            "touch: using framebuffer-sized coordinate scaling %dx%d instead of advertised abs range\n",
            ctx->fb.width, ctx->fb.height);
}

/* ── Coordinate transforms ───────────────────────────────────── */

/*
 * Map raw evdev coordinates → VNC server coordinates, accounting for
 * display rotation. The VNC server (KlipperScreen) sees an unrotated
 * coordinate space of vnc_width × vnc_height.
 */
static void touch_to_vnc(struct transform *xf, struct touch_state *ts,
                          int raw_x, int raw_y, int *vnc_x, int *vnc_y)
{
    /* Normalize raw touch to 0.0–1.0 range */
    float nx = 0, ny = 0;
    if (ts->abs_max_x != ts->abs_min_x)
        nx = (float)(raw_x - ts->abs_min_x) / (ts->abs_max_x - ts->abs_min_x);
    if (ts->abs_max_y != ts->abs_min_y)
        ny = (float)(raw_y - ts->abs_min_y) / (ts->abs_max_y - ts->abs_min_y);

    /* Clamp */
    if (nx < 0) nx = 0;
    if (nx > 1) nx = 1;
    if (ny < 0) ny = 0;
    if (ny > 1) ny = 1;

    if (xf->touch_swap_xy) {
        float tmp = nx;
        nx = ny;
        ny = tmp;
    }

    /* Apply rotation — maps touch position to VNC coordinate space */
    float fx, fy;
    switch (xf->touch_rotation) {
    case 90:
        fx = ny;
        fy = 1.0f - nx;
        break;
    case 180:
        fx = 1.0f - nx;
        fy = 1.0f - ny;
        break;
    case 270:
        fx = 1.0f - ny;
        fy = nx;
        break;
    default: /* 0 */
        fx = nx;
        fy = ny;
        break;
    }

    *vnc_x = (int)(fx * xf->vnc_width);
    *vnc_y = (int)(fy * xf->vnc_height);
}

static void touch_to_view(struct transform *xf, struct touch_state *ts,
                          int raw_x, int raw_y, int *view_x, int *view_y)
{
    float nx = 0, ny = 0;
    if (ts->abs_max_x != ts->abs_min_x)
        nx = (float)(raw_x - ts->abs_min_x) / (ts->abs_max_x - ts->abs_min_x);
    if (ts->abs_max_y != ts->abs_min_y)
        ny = (float)(raw_y - ts->abs_min_y) / (ts->abs_max_y - ts->abs_min_y);

    if (nx < 0) nx = 0;
    if (nx > 1) nx = 1;
    if (ny < 0) ny = 0;
    if (ny > 1) ny = 1;

    if (xf->touch_swap_xy) {
        float tmp = nx;
        nx = ny;
        ny = tmp;
    }

    switch (xf->touch_rotation) {
    case 90:
        *view_x = (int)(ny * (xf->fb_width - 1));
        *view_y = (int)((1.0f - nx) * (xf->fb_height - 1));
        break;
    case 180:
        *view_x = (int)((1.0f - nx) * (xf->fb_width - 1));
        *view_y = (int)((1.0f - ny) * (xf->fb_height - 1));
        break;
    case 270:
        *view_x = (int)((1.0f - ny) * (xf->fb_width - 1));
        *view_y = (int)(nx * (xf->fb_height - 1));
        break;
    default:
        *view_x = (int)(nx * (xf->fb_width - 1));
        *view_y = (int)(ny * (xf->fb_height - 1));
        break;
    }
}

static int clamp_int(int value, int min_value, int max_value)
{
    if (value < min_value)
        return min_value;
    if (value > max_value)
        return max_value;
    return value;
}

static void resolve_exit_defaults(struct viewer_ctx *ctx)
{
    int min_dim = ctx->fb.width;
    if (ctx->fb.height < min_dim)
        min_dim = ctx->fb.height;

    if (ctx->exit_corner_px <= 0)
        ctx->exit_corner_px = clamp_int(min_dim / 8, 32, 96);
    if (ctx->exit_move_tol_px <= 0)
        ctx->exit_move_tol_px = clamp_int(ctx->exit_corner_px, 48, 120);
}

static void run_hold_exit_cmd(void)
{
    const char *cmd = getenv("VIEWER_HOLD_EXIT_CMD");

    if (!cmd || !*cmd)
        return;

    fprintf(stderr, "touch: running VIEWER_HOLD_EXIT_CMD\n");
    if (system(cmd) == -1)
        perror("system VIEWER_HOLD_EXIT_CMD");
}

static int maybe_trigger_hold_exit(struct viewer_ctx *ctx)
{
    long long now_ms;

    if (ctx->exit_hold_ms <= 0 || !ctx->exit_hold_active || !ctx->touch.pressed)
        return 0;

    now_ms = monotonic_ms();
    if ((now_ms - ctx->exit_hold_started_ms) < ctx->exit_hold_ms)
        return 0;

    fprintf(stderr, "touch: hold-to-exit triggered\n");
    run_hold_exit_cmd();
    running = 0;
    return 1;
}

static void process_touch_events(struct viewer_ctx *ctx, int allow_pointer_forward)
{
    struct input_event ev;

    while (read(ctx->touch.fd, &ev, sizeof(ev)) == sizeof(ev)) {
        if (ev.type == EV_ABS) {
            if (ev.code == ABS_MT_SLOT) {
                /* Protocol B: subsequent MT events belong to this slot.
                 * We only track slot 0 (first finger). */
                ctx->touch.slot = ev.value;
            } else if (ctx->touch.slot == 0) {
                /* Only process events for slot 0 */
                if (ev.code == ABS_X || ev.code == ABS_MT_POSITION_X)
                    ctx->touch.x = ev.value;
                else if (ev.code == ABS_Y || ev.code == ABS_MT_POSITION_Y)
                    ctx->touch.y = ev.value;
                else if (ev.code == ABS_MT_TRACKING_ID) {
                    /* Protocol B: tracking_id >= 0 means finger down,
                     * -1 means finger lifted. */
                    ctx->touch.pressed = (ev.value != -1) ? 1 : 0;
                }
            }
        } else if (ev.type == EV_KEY && ev.code == BTN_TOUCH) {
            /* Protocol A fallback: some drivers still use BTN_TOUCH */
            ctx->touch.pressed = ev.value;
        } else if (ev.type == EV_SYN && ev.code == SYN_REPORT) {
            int suppress_pointer = FALSE;

            if (ctx->touch.pressed)
                touch_maybe_use_fb_scale(ctx);

            if (ctx->exit_hold_ms > 0) {
                if (!ctx->touch.pressed) {
                    if (ctx->exit_hold_active)
                        fprintf(stderr, "touch: hold-to-exit canceled (release)\n");
                    ctx->exit_hold_active = FALSE;
                    ctx->exit_swallow_touch = FALSE;
                    ctx->exit_hold_started_ms = -1;
                } else if (!ctx->exit_swallow_touch) {
                    int sx, sy;
                    touch_to_view(&ctx->xform, &ctx->touch, ctx->touch.x, ctx->touch.y, &sx, &sy);
                    if (sx >= 0 && sy >= 0 &&
                        sx < ctx->exit_corner_px && sy < ctx->exit_corner_px) {
                        ctx->exit_hold_active = TRUE;
                        ctx->exit_swallow_touch = TRUE;
                        ctx->exit_hold_started_ms = monotonic_ms();
                        ctx->exit_hold_start_x = sx;
                        ctx->exit_hold_start_y = sy;
                        suppress_pointer = TRUE;
                        fprintf(stderr,
                                "touch: hold-to-exit tracking from (%d,%d), wait %dms\n",
                                sx, sy, ctx->exit_hold_ms);
                    }
                } else if (ctx->exit_swallow_touch) {
                    suppress_pointer = TRUE;
                    if (ctx->exit_hold_active) {
                        int sx, sy;
                        int limit_px;
                        touch_to_view(&ctx->xform, &ctx->touch, ctx->touch.x, ctx->touch.y, &sx, &sy);
                        limit_px = ctx->exit_corner_px + ctx->exit_move_tol_px;
                        if (sx < 0 || sy < 0 || sx >= limit_px || sy >= limit_px) {
                            fprintf(stderr,
                                    "touch: hold-to-exit canceled (moved to %d,%d)\n",
                                    sx, sy);
                            ctx->exit_hold_active = FALSE;
                        }
                    }
                }
            }

            if (!suppress_pointer && allow_pointer_forward && vnc_client) {
                int vx, vy;
                touch_to_vnc(&ctx->xform, &ctx->touch,
                             ctx->touch.x, ctx->touch.y, &vx, &vy);
                SendPointerEvent(vnc_client, vx, vy,
                                 ctx->touch.pressed ? rfbButton1Mask : 0);
            }
        }
    }
}

static void sleep_ms_interruptible_with_touch(struct viewer_ctx *ctx, int have_touch, int ms)
{
    while (running && ms > 0) {
        int step_ms = (ms > 50) ? 50 : ms;

        if (have_touch && ctx->touch.fd >= 0) {
            fd_set fds;
            struct timeval tv;
            int ret;

            FD_ZERO(&fds);
            FD_SET(ctx->touch.fd, &fds);
            tv.tv_sec = 0;
            tv.tv_usec = step_ms * 1000;

            ret = select(ctx->touch.fd + 1, &fds, NULL, NULL, &tv);
            if (ret > 0 && FD_ISSET(ctx->touch.fd, &fds))
                process_touch_events(ctx, FALSE);
        } else {
            usleep((unsigned int)step_ms * 1000u);
        }

        if (maybe_trigger_hold_exit(ctx))
            return;

        ms -= step_ms;
    }
}

/*
 * Map VNC server pixel (sx, sy) → framebuffer pixel (fx, fy),
 * applying scaling and rotation.
 */
static void vnc_to_fb(struct transform *xf, int sx, int sy,
                       int *fx, int *fy)
{
    /* Scale from VNC resolution to fb "logical" size */
    float nx = (float)sx / xf->vnc_width;
    float ny = (float)sy / xf->vnc_height;

    int fw = xf->fb_width - 1;
    int fh = xf->fb_height - 1;

    switch (xf->rotation) {
    case 90:
        *fx = (int)((1.0f - ny) * fw);
        *fy = (int)(nx * fh);
        break;
    case 180:
        *fx = (int)((1.0f - nx) * fw);
        *fy = (int)((1.0f - ny) * fh);
        break;
    case 270:
        *fx = (int)(ny * fw);
        *fy = (int)((1.0f - nx) * fh);
        break;
    default: /* 0 */
        *fx = (int)(nx * fw);
        *fy = (int)(ny * fh);
        break;
    }
}

/* ── libvncclient callbacks ──────────────────────────────────── */

static rfbBool vnc_resize(rfbClient *cl)
{
    struct viewer_ctx *ctx = rfbClientGetClientData(cl, vnc_client);
    int w = cl->width;
    int h = cl->height;
    int src_bpp = (ctx->client_bpp == 16) ? 2 : 4;

    ctx->xform.vnc_width = w;
    ctx->xform.vnc_height = h;

    fprintf(stderr, "vnc: resize %dx%d\n", w, h);

    if (ctx->client_bpp == 16) {
        cl->format.bitsPerPixel = 16;
        cl->format.depth = 16;
        cl->format.redShift = 11;
        cl->format.greenShift = 5;
        cl->format.blueShift = 0;
        cl->format.redMax = 31;
        cl->format.greenMax = 63;
        cl->format.blueMax = 31;
    } else {
        cl->format.bitsPerPixel = 32;
        cl->format.depth = 24;
        cl->format.redShift = 0;
        cl->format.greenShift = 8;
        cl->format.blueShift = 16;
        cl->format.redMax = 255;
        cl->format.greenMax = 255;
        cl->format.blueMax = 255;
    }
    cl->format.bigEndian = FALSE;
    /* Don't call SetFormatAndEncodings here — the library calls it
     * automatically after MallocFrameBuffer returns. Calling it twice
     * sends duplicate SetPixelFormat/SetEncodings, which can confuse
     * some VNC servers into not responding to FramebufferUpdateRequests. */

    if (cl->frameBuffer)
        free(cl->frameBuffer);
    cl->frameBuffer = malloc(w * h * src_bpp);
    if (!cl->frameBuffer) {
        fprintf(stderr, "out of memory for VNC framebuffer\n");
        return FALSE;
    }
    memset(cl->frameBuffer, 0, w * h * src_bpp);

    return TRUE;
}

static inline void unpack_rgb(const uint8_t *src, int src_bpp,
                              uint8_t *r, uint8_t *g, uint8_t *b)
{
    if (src_bpp == 2) {
        uint16_t c = (uint16_t)src[0] | ((uint16_t)src[1] << 8);
        *r = (uint8_t)(((c >> 11) & 0x1F) << 3);
        *g = (uint8_t)(((c >> 5) & 0x3F) << 2);
        *b = (uint8_t)((c & 0x1F) << 3);
    } else {
        *r = src[0];
        *g = src[1];
        *b = src[2];
    }
}

static void vnc_update(rfbClient *cl, int x, int y, int w, int h)
{
    struct viewer_ctx *ctx = rfbClientGetClientData(cl, vnc_client);
    struct fb_state *fb = &ctx->fb;
    struct transform *xf = &ctx->xform;
    int vnc_w = cl->width;
    int fb_w = fb->width;
    int fb_h = fb->height;
    int bpp = fb->bpp;
    int stride = fb->stride;
    int src_bpp = (ctx->client_bpp == 16) ? 2 : 4;

    if (xf->rotation == 180 && xf->vnc_width == fb_w && xf->vnc_height == fb_h && bpp == 4) {
        /* Fast path: rotation=180, matching resolution, 32bpp framebuffer.
         * VNC row sy maps to fb row (fb_h-1-sy), pixels reversed. */
        for (int sy = y; sy < y + h; sy++) {
            int fy = fb_h - 1 - sy;
            uint32_t *dst = (uint32_t *)(fb->draw_mem + fy * stride);
            int fx_start = fb_w - 1 - x;

            if (src_bpp == 4) {
                uint8_t *src_row = (uint8_t *)cl->frameBuffer + (sy * vnc_w + x) * 4;
                for (int i = 0; i < w; i++) {
                    uint8_t *s = src_row + i * 4;
                    /* R=s[0], G=s[1], B=s[2] → BGRA = B,G,R,0xFF */
                    dst[fx_start - i] = (uint32_t)s[2] | ((uint32_t)s[1] << 8)
                                      | ((uint32_t)s[0] << 16) | 0xFF000000u;
                }
            } else {
                uint8_t *src_row = (uint8_t *)cl->frameBuffer + (sy * vnc_w + x) * 2;
                for (int i = 0; i < w; i++) {
                    uint16_t c = (uint16_t)src_row[i * 2] | ((uint16_t)src_row[i * 2 + 1] << 8);
                    dst[fx_start - i] = rgb565_to_bgra_lut[c];
                }
            }

            if (fb->use_backbuf)
                fb_dirty(fb, fy, fy);
        }
    } else {
        /* Generic path for all rotations / resolutions.
         * Use integer math for coordinate mapping and only dirty-touch rows
         * once per source row to reduce callback overhead during heavy motion. */
        int vnc_h = xf->vnc_height;
        int fw = fb_w - 1;
        int fh = fb_h - 1;

        for (int sy = y; sy < y + h; sy++) {
            int row_dirty_min = -1;
            int row_dirty_max = -1;

            for (int sx = x; sx < x + w; sx++) {
                int fx, fy;

                switch (xf->rotation) {
                case 90:
                    fx = ((vnc_h - sy) * fw) / vnc_h;
                    fy = (sx * fh) / vnc_w;
                    break;
                case 180:
                    fx = ((vnc_w - sx) * fw) / vnc_w;
                    fy = ((vnc_h - sy) * fh) / vnc_h;
                    break;
                case 270:
                    fx = (sy * fw) / vnc_h;
                    fy = ((vnc_w - sx) * fh) / vnc_w;
                    break;
                default:
                    fx = (sx * fw) / vnc_w;
                    fy = (sy * fh) / vnc_h;
                    break;
                }

                if (fx >= 0 && fx < fb_w && fy >= 0 && fy < fb_h) {
                    uint8_t *src = (uint8_t *)cl->frameBuffer + (sy * vnc_w + sx) * src_bpp;
                    uint8_t r, g, b;
                    unpack_rgb(src, src_bpp, &r, &g, &b);
                    uint8_t *p = fb->draw_mem + fy * stride + fx * bpp;

                    if (bpp == 4) {
                        p[0] = b;
                        p[1] = g;
                        p[2] = r;
                        p[3] = 0xFF;
                    } else if (bpp == 2) {
                        uint16_t c = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
                        p[0] = c & 0xFF;
                        p[1] = c >> 8;
                    }
                    if (row_dirty_min < 0 || fy < row_dirty_min)
                        row_dirty_min = fy;
                    if (fy > row_dirty_max)
                        row_dirty_max = fy;
                }
            }

            if (fb->use_backbuf && row_dirty_min >= 0)
                fb_dirty(fb, row_dirty_min, row_dirty_max);
        }
    }
}

/* Flush only dirty rows from back buffer to display */
static void fb_flip(struct fb_state *fb)
{
    if (fb->dirty_min < 0)
        return;  /* nothing changed */

    if (fb->use_backbuf) {
        int y0 = fb->dirty_min;
        int y1 = fb->dirty_max;
        if (y1 >= fb->height)
            y1 = fb->height - 1;

        size_t off = y0 * fb->stride;
        size_t len = (y1 - y0 + 1) * fb->stride;
        memcpy(fb->mem + off, fb->backbuf + off, len);
    }

    fb->dirty_min = -1;
    fb->dirty_max = 0;
}

/* Called once per frame after all rectangles are decoded. */
static void vnc_finished(rfbClient *cl)
{
    struct viewer_ctx *ctx = rfbClientGetClientData(cl, vnc_client);
    ctx->got_fb_update = 1;
    fb_flip(&ctx->fb);
}

/* ── Main ────────────────────────────────────────────────────── */

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [options] host[:port]\n"
        "\n"
        "Options:\n"
        "  -f <device>   Framebuffer device (default: /dev/fb0)\n"
        "  -t <device>   Touch input device (default: /dev/input/event0)\n"
        "  -r <degrees>  Display rotation: 0, 90, 180, 270 (default: 0)\n"
        "  -p <port>     VNC port (default: 5900)\n"
        "  -b <depth>    VNC client color depth: 16 or 32 (default: 32)\n"
        "  -u <ms>       Incremental update interval in ms (default: 66)\n"
        "  -d <0|1>      Direct framebuffer render: 1=on, 0=off (default: 1)\n"
        "  -h            Show this help\n"
        "\n"
        "Environment:\n"
        "  VNC_HOST      VNC server hostname (alternative to positional arg)\n"
        "  VNC_PORT      VNC server port\n"
        "  VNC_PASSWORD  VNC password (if server requires authentication)\n"
        "  ROTATION      Display rotation in degrees\n"
        "  VNC_COLOR_DEPTH        Same as -b (16 or 32)\n"
        "  VNC_UPDATE_INTERVAL_MS Same as -u\n"
        "  VNC_DIRECT_RENDER      Same as -d (0 or 1)\n"
        "  VIEWER_TOUCH_ROTATION  Touch rotation in degrees (default: same as render)\n"
        "  VIEWER_TOUCH_SWAP_XY   Swap touch axes before rotation (0 or 1)\n"
        "  VIEWER_EXIT_HOLD_MS    Hold top-left to stop viewer (default: 5000)\n"
        "  VIEWER_EXIT_CORNER_PX  Trigger size in px, 0=auto\n"
        "  VIEWER_EXIT_MOVE_TOL_PX Allowed drift in px, 0=auto\n"
        "  VIEWER_HOLD_EXIT_CMD   Shell command run when hold-to-exit fires\n",
        prog);
}

static char *vnc_get_password(rfbClient *cl)
{
    (void)cl;
    char *pw = getenv("VNC_PASSWORD");
    if (pw)
        return strdup(pw);
    return strdup("");
}

int main(int argc, char **argv)
{
    const char *fb_device = "/dev/fb0";
    const char *touch_device = "/dev/input/event0";
    const char *vnc_host = NULL;
    int vnc_port = 5900;
    int rotation = 0;
    int client_bpp = 32;
    int update_interval_ms = 66;
    int direct_render = 1;
    int touch_rotation = -1;
    int touch_swap_xy = 0;
    int exit_hold_ms = 5000;
    int exit_corner_px = 0;
    int exit_move_tol_px = 0;
    int opt;

    /* Environment defaults */
    char *env;
    if ((env = getenv("VNC_HOST")) != NULL)
        vnc_host = env;
    if ((env = getenv("VNC_PORT")) != NULL)
        vnc_port = atoi(env);
    if ((env = getenv("ROTATION")) != NULL)
        rotation = atoi(env);
    if ((env = getenv("VNC_COLOR_DEPTH")) != NULL)
        client_bpp = atoi(env);
    if ((env = getenv("VNC_UPDATE_INTERVAL_MS")) != NULL)
        update_interval_ms = atoi(env);
    if ((env = getenv("VNC_DIRECT_RENDER")) != NULL)
        direct_render = atoi(env);
    if ((env = getenv("VIEWER_TOUCH_ROTATION")) != NULL)
        touch_rotation = atoi(env);
    if ((env = getenv("VIEWER_TOUCH_SWAP_XY")) != NULL)
        touch_swap_xy = atoi(env);
    if ((env = getenv("VIEWER_EXIT_HOLD_MS")) != NULL)
        exit_hold_ms = atoi(env);
    if ((env = getenv("VIEWER_EXIT_CORNER_PX")) != NULL)
        exit_corner_px = atoi(env);
    if ((env = getenv("VIEWER_EXIT_MOVE_TOL_PX")) != NULL)
        exit_move_tol_px = atoi(env);

    while ((opt = getopt(argc, argv, "f:t:r:p:b:u:d:h")) != -1) {
        switch (opt) {
        case 'f': fb_device = optarg; break;
        case 't': touch_device = optarg; break;
        case 'r': rotation = atoi(optarg); break;
        case 'p': vnc_port = atoi(optarg); break;
        case 'b': client_bpp = atoi(optarg); break;
        case 'u': update_interval_ms = atoi(optarg); break;
        case 'd': direct_render = atoi(optarg); break;
        case 'h': usage(argv[0]); return 0;
        default: usage(argv[0]); return 1;
        }
    }

    if (optind < argc) {
        vnc_host = argv[optind];
        /* Parse host:port */
        char *colon = strrchr((char *)vnc_host, ':');
        if (colon) {
            *colon = '\0';
            vnc_port = atoi(colon + 1);
        }
    }

    if (!vnc_host) {
        fprintf(stderr, "Error: VNC host not specified\n");
        usage(argv[0]);
        return 1;
    }

    if (vnc_port < 1 || vnc_port > 65535) {
        fprintf(stderr, "Error: invalid port %d\n", vnc_port);
        return 1;
    }
    if (client_bpp != 16 && client_bpp != 32) {
        fprintf(stderr, "Error: VNC color depth must be 16 or 32, got %d\n", client_bpp);
        return 1;
    }
    if (update_interval_ms < 0 || update_interval_ms > 1000) {
        fprintf(stderr, "Error: update interval must be 0..1000 ms, got %d\n", update_interval_ms);
        return 1;
    }
    if (direct_render != 0 && direct_render != 1) {
        fprintf(stderr, "Error: direct render must be 0 or 1, got %d\n", direct_render);
        return 1;
    }
    if (touch_swap_xy != 0 && touch_swap_xy != 1) {
        fprintf(stderr, "Error: touch axis swap must be 0 or 1, got %d\n", touch_swap_xy);
        return 1;
    }
    if (touch_rotation == -1)
        touch_rotation = rotation;
    if (touch_rotation != 0 && touch_rotation != 90 &&
        touch_rotation != 180 && touch_rotation != 270) {
        fprintf(stderr, "Error: touch rotation must be 0, 90, 180, or 270, got %d\n",
                touch_rotation);
        return 1;
    }
    if (exit_hold_ms < 0 || exit_hold_ms > 60000) {
        fprintf(stderr, "Error: hold-to-exit must be 0..60000 ms, got %d\n", exit_hold_ms);
        return 1;
    }
    if (exit_corner_px < 0 || exit_corner_px > 300) {
        fprintf(stderr, "Error: exit corner size must be 0..300 px, got %d\n", exit_corner_px);
        return 1;
    }
    if (exit_move_tol_px < 0 || exit_move_tol_px > 300) {
        fprintf(stderr, "Error: exit move tolerance must be 0..300 px, got %d\n", exit_move_tol_px);
        return 1;
    }
    if (client_bpp == 16)
        init_rgb565_lut();

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Initialize viewer context */
    struct viewer_ctx ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.fb.fd = -1;
    ctx.touch.fd = -1;
    ctx.fb.use_backbuf = direct_render ? 0 : 1;
    ctx.xform.rotation = rotation;
    ctx.xform.touch_rotation = touch_rotation;
    ctx.xform.touch_swap_xy = touch_swap_xy;
    ctx.client_bpp = client_bpp;
    ctx.update_interval_ms = update_interval_ms;
    ctx.update_request_pending = FALSE;
    ctx.exit_hold_ms = exit_hold_ms;
    ctx.exit_corner_px = exit_corner_px;
    ctx.exit_move_tol_px = exit_move_tol_px;
    ctx.exit_hold_active = FALSE;
    ctx.exit_swallow_touch = FALSE;
    ctx.exit_hold_started_ms = -1;
    ctx.exit_hold_start_x = 0;
    ctx.exit_hold_start_y = 0;

    /* Open framebuffer */
    if (fb_open(&ctx.fb, fb_device) < 0)
        return 1;

    ctx.xform.fb_width = ctx.fb.width;
    ctx.xform.fb_height = ctx.fb.height;

    /* Show initial status on screen */
    fb_draw_status_screen(&ctx.fb, ctx.xform.rotation, "Connecting...", vnc_host, vnc_port);

    /* Open touch */
    int have_touch = (touch_open(&ctx.touch, touch_device) == 0);

    /* If ABS ranges are zero (driver quirk or grab failure), fall back to
     * framebuffer dimensions — fts_ts is a direct-touch device so its
     * coordinate space matches the screen resolution exactly. */
    if (have_touch && ctx.touch.abs_max_x == ctx.touch.abs_min_x) {
        ctx.touch.abs_min_x = 0;
        ctx.touch.abs_max_x = ctx.fb.width - 1;
        fprintf(stderr, "touch: ABS_X range was zero, using fb width %d\n",
                ctx.fb.width);
    }
    if (have_touch && ctx.touch.abs_max_y == ctx.touch.abs_min_y) {
        ctx.touch.abs_min_y = 0;
        ctx.touch.abs_max_y = ctx.fb.height - 1;
        fprintf(stderr, "touch: ABS_Y range was zero, using fb height %d\n",
                ctx.fb.height);
    }
    if (ctx.exit_hold_ms > 0) {
        resolve_exit_defaults(&ctx);
        fprintf(stderr,
                "touch: hold top-left %dx%d for %dms to stop viewer (move_tol=%d)\n",
                ctx.exit_corner_px, ctx.exit_corner_px,
                ctx.exit_hold_ms, ctx.exit_move_tol_px);
    }

    long long next_update_request_ms = 0;
    long long update_request_sent_ms = 0;
    int update_timeout_count = 0;
    int last_request_incremental = 0; /* track request type for timeout logic */
    const int update_request_timeout_ms = 2500;
    const int update_timeout_max = 3;  /* force reconnect after 3 consecutive non-incremental timeouts */
    const int reconnect_delay_ms = 5000;
    const int delayed_full_refresh_ms = 3000;
    long long delayed_full_refresh_at_ms = -1;
    int delayed_full_refresh_pending = FALSE;

    /* Main loop */
    while (running) {
        int disconnected = 0;

        if (!vnc_client) {
            /* Create VNC client — 16/32bpp, request Raw + ZRLE encodings */
            if (client_bpp == 16)
                vnc_client = rfbGetClient(5, 3, 2);
            else
                vnc_client = rfbGetClient(8, 3, 4);

            if (!vnc_client) {
                fprintf(stderr, "rfbGetClient failed, retrying in %dms\n", reconnect_delay_ms);
                fb_draw_status_screen(&ctx.fb, ctx.xform.rotation,
                                      "Connection failed - retrying...", vnc_host, vnc_port);
                sleep_ms_interruptible_with_touch(&ctx, have_touch, reconnect_delay_ms);
                continue;
            }

            vnc_client->MallocFrameBuffer = vnc_resize;
            vnc_client->GotFrameBufferUpdate = vnc_update;
            vnc_client->FinishedFrameBufferUpdate = vnc_finished;
            vnc_client->GetPassword = vnc_get_password;
            vnc_client->canHandleNewFBSize = TRUE;

            rfbClientSetClientData(vnc_client, vnc_client, &ctx);

            /* Connect */
            vnc_client->serverHost = strdup(vnc_host);
            vnc_client->serverPort = vnc_port;
            if (!rfbInitClient(vnc_client, NULL, NULL)) {
                fprintf(stderr, "Failed to connect to %s:%d, retrying in %dms\n",
                        vnc_host, vnc_port, reconnect_delay_ms);
                /* rfbInitClient already cleans up / frees the client on failure! */
                vnc_client = NULL;
                fb_draw_status_screen(&ctx.fb, ctx.xform.rotation,
                                      "Connection failed - retrying...", vnc_host, vnc_port);
                sleep_ms_interruptible_with_touch(&ctx, have_touch, reconnect_delay_ms);
                continue;
            }

            /* Set a receive timeout so HandleRFBServerMessage cannot
             * block forever if the server stops sending mid-update. */
            struct timeval recv_timeout;
            recv_timeout.tv_sec = 10;
            recv_timeout.tv_usec = 0;
            if (setsockopt(vnc_client->sock, SOL_SOCKET, SO_RCVTIMEO,
                           &recv_timeout, sizeof(recv_timeout)) < 0)
                perror("setsockopt SO_RCVTIMEO");

            fprintf(stderr, "Connected to %s:%d (%dx%d), rotation=%d, touch_rotation=%d, depth=%d, update_interval=%dms, direct_render=%s\n",
                    vnc_host, vnc_port,
                    vnc_client->width, vnc_client->height, rotation, touch_rotation,
                    client_bpp, update_interval_ms,
                    direct_render ? "on" : "off");

            /* Prime with one full update request. */
            SendFramebufferUpdateRequest(vnc_client, 0, 0,
                                         vnc_client->width, vnc_client->height,
                                         FALSE);
            ctx.update_request_pending = TRUE;
            update_request_sent_ms = monotonic_ms();
            last_request_incremental = 0;
            update_timeout_count = 0;
            next_update_request_ms = monotonic_ms() + ctx.update_interval_ms;
            delayed_full_refresh_pending = TRUE;
            delayed_full_refresh_at_ms = monotonic_ms() + delayed_full_refresh_ms;
            fprintf(stderr, "Scheduled one-time full refresh in %dms\n", delayed_full_refresh_ms);
        }

        fd_set fds;
        struct timeval tv;
        int maxfd = vnc_client->sock;

        FD_ZERO(&fds);
        FD_SET(vnc_client->sock, &fds);

        if (have_touch && ctx.touch.fd >= 0) {
            FD_SET(ctx.touch.fd, &fds);
            if (ctx.touch.fd > maxfd)
                maxfd = ctx.touch.fd;
        }

        tv.tv_sec = 0;
        tv.tv_usec = 50000; /* 50ms polling keeps CPU lower on idle/retry loops */

        int ret = select(maxfd + 1, &fds, NULL, NULL, &tv);
        if (ret < 0) {
            if (errno == EINTR)
                continue;
            perror("select");
            disconnected = 1;
        }

        /* Handle VNC data */
        if (!disconnected && ret > 0 && FD_ISSET(vnc_client->sock, &fds)) {
            if (!HandleRFBServerMessage(vnc_client)) {
                fprintf(stderr, "VNC connection lost\n");
                disconnected = 1;
            }
            ctx.update_request_pending = FALSE;
            /* Only reset timeout counter when actual framebuffer data arrived */
            if (ctx.got_fb_update) {
                update_timeout_count = 0;
                ctx.got_fb_update = 0;
            }
        }

        /* Handle touch events */
        if (have_touch && ctx.touch.fd >= 0 && ret > 0 && FD_ISSET(ctx.touch.fd, &fds))
            process_touch_events(&ctx, !disconnected && vnc_client != NULL);

        if (maybe_trigger_hold_exit(&ctx))
            continue;

        /* Past this point we only do VNC traffic / reconnect bookkeeping. */
        if (!disconnected && ctx.update_request_pending &&
            (monotonic_ms() - update_request_sent_ms) >= update_request_timeout_ms) {
            if (last_request_incremental) {
                /* Incremental timed out — screen probably idle.
                 * Re-send as non-incremental to probe server health. */
                SendFramebufferUpdateRequest(vnc_client, 0, 0,
                                             vnc_client->width, vnc_client->height,
                                             FALSE);
                last_request_incremental = 0;
                update_request_sent_ms = monotonic_ms();
                /* update_request_pending stays TRUE */
            } else {
                /* Non-incremental timed out — server is truly unresponsive */
                update_timeout_count++;
                fprintf(stderr, "Non-incremental request timed out (%d/%d)\n",
                        update_timeout_count, update_timeout_max);
                if (update_timeout_count >= update_timeout_max) {
                    fprintf(stderr, "VNC server unresponsive, forcing reconnect\n");
                    disconnected = 1;
                } else {
                    /* Retry non-incremental */
                    SendFramebufferUpdateRequest(vnc_client, 0, 0,
                                                 vnc_client->width, vnc_client->height,
                                                 FALSE);
                    update_request_sent_ms = monotonic_ms();
                }
            }
        }

        if (!disconnected && !ctx.update_request_pending) {
            long long now_ms = monotonic_ms();

            if (delayed_full_refresh_pending && now_ms >= delayed_full_refresh_at_ms) {
                SendFramebufferUpdateRequest(vnc_client, 0, 0,
                                             vnc_client->width, vnc_client->height,
                                             FALSE);
                ctx.update_request_pending = TRUE;
                update_request_sent_ms = now_ms;
                last_request_incremental = 0;
                delayed_full_refresh_pending = FALSE;
                next_update_request_ms = now_ms + ctx.update_interval_ms;
                fprintf(stderr, "Triggered delayed one-time full refresh\n");
            } else if (now_ms >= next_update_request_ms) {
                SendFramebufferUpdateRequest(vnc_client, 0, 0,
                                             vnc_client->width, vnc_client->height,
                                             TRUE);
                ctx.update_request_pending = TRUE;
                update_request_sent_ms = now_ms;
                last_request_incremental = 1;
                next_update_request_ms = now_ms + ctx.update_interval_ms;
            }
        }

        if (disconnected && vnc_client) {
            rfbClientCleanup(vnc_client);
            vnc_client = NULL;
            ctx.update_request_pending = FALSE;
            update_timeout_count = 0;
            delayed_full_refresh_pending = FALSE;
            delayed_full_refresh_at_ms = -1;
            /* Show reconnecting status instead of stale/squished image */
            if (running) {
                fb_draw_status_screen(&ctx.fb, ctx.xform.rotation,
                                      "Connection lost - reconnecting...", vnc_host, vnc_port);
                fprintf(stderr, "Reconnecting in %dms...\n", reconnect_delay_ms);
                sleep_ms_interruptible_with_touch(&ctx, have_touch, reconnect_delay_ms);
            } else {
                /* Shutting down — just clear to black */
                memset(ctx.fb.mem, 0, ctx.fb.stride * ctx.fb.height);
                if (ctx.fb.use_backbuf)
                    memset(ctx.fb.backbuf, 0, ctx.fb.stride * ctx.fb.height);
            }
        }
    }

    fprintf(stderr, "Shutting down\n");
    if (vnc_client)
        rfbClientCleanup(vnc_client);
    if (have_touch)
        touch_close(&ctx.touch);
    fb_close(&ctx.fb);

    return 0;
}
