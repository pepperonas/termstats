/*
 * termstats_core.h - the C interface of the termstats dashboard core.
 *
 * This header is the contract: native/src/ffi.rs implements exactly these types and
 * functions, and an app that links the static library copies this file verbatim.
 * Change both together and raise TS_ABI when a layout or a meaning changes.
 *
 * Return values of the int32_t functions: 0 ok, -1 caught panic or null pointer,
 * -2 bad argument. Pointers inside a TsFrame stay valid until the next call on the
 * same core. A core is not thread-safe: call it from one thread at a time - with ONE
 * exception, ts_feed_audio, which may be called from any thread (the audio thread) while
 * another thread draws: it only copies the samples into a guarded ring of 2 seconds.
 */
#ifndef TERMSTATS_CORE_H
#define TERMSTATS_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TS_ABI 2

/* Colours are 0xRRGGBB; TS_COLOR_DEFAULT means "the surface's own colour". */
#define TS_COLOR_DEFAULT 0xFF000000u

/* TsOptions.views: which sections are drawn (a section that is off is not read). */
#define TS_VIEW_CPU        1u
#define TS_VIEW_MEMORY     2u
#define TS_VIEW_NETWORK    4u
#define TS_VIEW_DISK       8u
#define TS_VIEW_CHARTS     16u
#define TS_VIEW_PROCESSES  32u
#define TS_VIEW_DASHBOARD  63u
#define TS_VIEW_EQ         64u   /* microphone: 28-band equalizer */
#define TS_VIEW_BPM        128u  /* microphone: tempo */
#define TS_VIEW_LEVEL      256u  /* microphone: level in dB */
#define TS_VIEW_AUDIO      448u

/* ts_draw regions: which parts of the frame are drawn anew; a part not asked for keeps its
 * cells from the previous ts_draw. 0 means both. A new size or new options draw both. */
#define TS_REGION_DASHBOARD 1u
#define TS_REGION_AUDIO     2u

/* ts_audio_state: shown as text in the audio panels when not running. */
#define TS_AUDIO_RUNNING       0
#define TS_AUDIO_NO_PERMISSION 1
#define TS_AUDIO_NO_DEVICE     2

/* TsOptions.chrome: the header and footer lines and what they carry. */
#define TS_CHROME_HEADER   1u
#define TS_CHROME_CLOCK    2u
#define TS_CHROME_VERSION  4u
#define TS_CHROME_FOOTER   8u
#define TS_CHROME_HINT     16u
#define TS_CHROME_BRAND    32u
#define TS_CHROME_ALL      63u

typedef struct TsCore TsCore;

/* One cell of the grid. ch is a Unicode scalar value; 16 bytes. */
typedef struct {
    uint32_t ch, fg, bg;   /* fg/bg 0xRRGGBB, 0xFF000000 = default */
    uint8_t bold, _pad[3];
} TsCell;

/* interval: seconds between samples (> 0, finite) - the chart titles are computed from it.
 * theme: NUL-terminated name inside the 32 bytes (see ts_theme_name). */
typedef struct {
    uint32_t views, chrome;
    double interval;
    uint8_t compact, no_border, _pad[6];
    char theme[32];
} TsOptions;

/* cells: w*h cells, row-major. dirty_rows: the rows that differ from the previous
 * ts_draw (every row after a size or options change), ascending. */
typedef struct {
    const TsCell *cells;
    uint32_t w, h;
    const uint32_t *dirty_rows;
    uint32_t dirty_len;
    uint32_t theme_bg, theme_text;
} TsFrame;

uint32_t ts_abi_version(void);
const char *ts_version(void);
uint32_t ts_theme_count(void);
const char *ts_theme_name(uint32_t index);                 /* NULL past the end */
TsCore *ts_create(const TsOptions *opts);                  /* NULL on bad theme */
void ts_free(TsCore *core);
int32_t ts_set_options(TsCore *core, const TsOptions *opts);
int32_t ts_sample(TsCore *core, double mono);              /* read the machine */
int32_t ts_draw(TsCore *core, uint32_t w, uint32_t h, double mono, uint32_t regions, TsFrame *out);
/* ts_draw clamps w to 40...1000 and h to 8...500; out->w/out->h are the sizes used. It also
 * analyses the samples fed since the last call. With dashboard and audio views both on, the
 * audio is a column on the right from 190 columns, else rows at the bottom of the body. */
int32_t ts_feed_audio(TsCore *core, const float *mono, size_t n, double sample_rate);
/* mono samples in -1...1 at sample_rate (4000...768000 Hz, else -2), ANY thread. A new rate
 * drops what is buffered and starts the analysis afresh. */
int32_t ts_audio_state(TsCore *core, int32_t state);       /* TS_AUDIO_*, else -2 */

#ifdef __cplusplus
}
#endif

#endif /* TERMSTATS_CORE_H */
