#ifndef WAYBAR_PULSE_ABI_H
#define WAYBAR_PULSE_ABI_H

/* Public libpulse ABI, used without requiring development packages.
 * Layouts: https://www.freedesktop.org/software/pulseaudio/doxygen/introspect_8h_source.html
 * Only the prefix of pa_sink_info through mute is accessed. */
#include <stdint.h>

typedef struct pa_threaded_mainloop pa_threaded_mainloop;
typedef struct pa_mainloop_api pa_mainloop_api;
typedef struct pa_context pa_context;
typedef struct pa_operation pa_operation;
typedef struct { int format; uint32_t rate; uint8_t channels; } pa_sample_spec;
typedef struct { uint8_t channels; int map[32]; } pa_channel_map;
typedef struct { uint8_t channels; uint32_t values[32]; } pa_cvolume;
typedef struct {
    const char *name;
    uint32_t index;
    const char *description;
    pa_sample_spec sample_spec;
    pa_channel_map channel_map;
    uint32_t owner_module;
    pa_cvolume volume;
    int mute;
} pa_sink_info;

enum { PA_CONTEXT_READY = 4, PA_CONTEXT_FAILED = 5, PA_CONTEXT_TERMINATED = 6 };
enum { PA_CONTEXT_NOAUTOSPAWN = 1, PA_VOLUME_NORM = 65536 };
enum { PA_SUBSCRIPTION_MASK_SINK = 1, PA_SUBSCRIPTION_MASK_SERVER = 128 };

pa_threaded_mainloop *pa_threaded_mainloop_new(void);
void pa_threaded_mainloop_free(pa_threaded_mainloop *);
int pa_threaded_mainloop_start(pa_threaded_mainloop *);
void pa_threaded_mainloop_stop(pa_threaded_mainloop *);
void pa_threaded_mainloop_lock(pa_threaded_mainloop *);
void pa_threaded_mainloop_unlock(pa_threaded_mainloop *);
pa_mainloop_api *pa_threaded_mainloop_get_api(pa_threaded_mainloop *);
pa_context *pa_context_new(pa_mainloop_api *, const char *);
void pa_context_unref(pa_context *);
int pa_context_connect(pa_context *, const char *, int, const void *);
void pa_context_disconnect(pa_context *);
int pa_context_get_state(const pa_context *);
void pa_context_set_state_callback(pa_context *, void (*)(pa_context *, void *), void *);
void pa_context_set_subscribe_callback(pa_context *, void (*)(pa_context *, int, uint32_t, void *), void *);
pa_operation *pa_context_subscribe(pa_context *, int, void (*)(pa_context *, int, void *), void *);
pa_operation *pa_context_get_sink_info_by_name(pa_context *, const char *, void (*)(pa_context *, const pa_sink_info *, int, void *), void *);
pa_operation *pa_context_set_sink_volume_by_index(pa_context *, uint32_t, const pa_cvolume *, void (*)(pa_context *, int, void *), void *);
pa_operation *pa_context_set_sink_mute_by_index(pa_context *, uint32_t, int, void (*)(pa_context *, int, void *), void *);
void pa_operation_unref(pa_operation *);
uint32_t pa_cvolume_max(const pa_cvolume *);
pa_cvolume *pa_cvolume_scale(pa_cvolume *, uint32_t);

#endif
