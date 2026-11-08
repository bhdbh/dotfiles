#define _POSIX_C_SOURCE 200809L
#include "audio-client.h"
#include "pulse-abi.h"
#include "volume-curve.h"
#include <stdlib.h>
#include <string.h>

extern unsigned int g_idle_add(int (*)(void *), void *);
extern int g_source_remove(unsigned int);
extern const char *g_get_user_config_dir(void);
extern char *g_build_filename(const char *, ...);
extern void g_free(void *);
extern void *g_key_file_new(void);
extern int g_key_file_load_from_file(void *, const char *, int, void **);
extern double g_key_file_get_double(void *, const char *, const char *, void **);
extern void g_key_file_unref(void *);

struct AudioClient {
    pa_threaded_mainloop *loop;
    pa_context *context;
    char *sink_name;
    void *curves;
    double volume_floor;
    double volume_ceiling;
    double volume_pivot;
    double volume_pivot_level;
    double volume_pivot_slope;
    double volume_start_slope;
    double volume_stretch_start;
    double volume_stretch_end;
    pa_cvolume channels;
    uint32_t sink_index;
    AudioState state;
    AudioChanged changed;
    void *userdata;
    unsigned int idle;
    unsigned int serial;
    unsigned int query_serial;
    unsigned int pending;
    int querying;
    int refresh_needed;
};

static void refresh(AudioClient *);

/* All server callbacks hold the PulseAudio loop lock. GTK runs on the main thread. */
static int deliver(void *data) {
    AudioClient *client = data;
    pa_threaded_mainloop_lock(client->loop);
    client->idle = 0;
    AudioState state = client->state;
    pa_threaded_mainloop_unlock(client->loop);
    if (client->changed) client->changed(client->userdata, state);
    return 0;
}

static void notify(AudioClient *client) {
    if (!client->idle) client->idle = g_idle_add(deliver, client);
}

static void sink_info(pa_context *context, const pa_sink_info *info, int eol, void *data) {
    (void)context;
    AudioClient *client = data;
    if (!eol && info && client->query_serial == client->serial && !client->pending) {
        client->sink_index = info->index;
        client->channels = info->volume;
        double floor = g_key_file_get_double(client->curves, info->name, "floor", NULL);
        client->volume_floor = floor >= 0 && floor < 100 ? floor : 0;
        double ceiling = g_key_file_get_double(client->curves, info->name, "ceiling", NULL);
        client->volume_ceiling = ceiling > client->volume_floor && ceiling <= 200 ? ceiling : 100;
        client->volume_pivot = g_key_file_get_double(client->curves, info->name, "pivot", NULL);
        client->volume_pivot_level = g_key_file_get_double(client->curves, info->name, "pivot-volume", NULL);
        client->volume_pivot_slope = g_key_file_get_double(client->curves, info->name, "pivot-slope", NULL);
        client->volume_start_slope = g_key_file_get_double(client->curves, info->name, "start-slope", NULL);
        client->volume_stretch_start = g_key_file_get_double(client->curves, info->name, "stretch-start", NULL);
        client->volume_stretch_end = g_key_file_get_double(client->curves, info->name, "stretch-end", NULL);
        double raw = 100.0 * pa_cvolume_max(&info->volume) / PA_VOLUME_NORM;
        double percent = volume_curve_display(raw, client->volume_floor, client->volume_ceiling, client->volume_pivot, client->volume_pivot_level, client->volume_pivot_slope, client->volume_start_slope);
        client->state = (AudioState){volume_curve_stretch_display(percent, client->volume_stretch_start, client->volume_stretch_end), info->mute, 1};
        notify(client);
    }
    if (eol) {
        client->querying = 0;
        if (eol < 0 && client->query_serial == client->serial && !client->pending) {
            client->state.available = 0;
            notify(client);
        }
        if (client->refresh_needed) refresh(client);
    }
}

static void refresh(AudioClient *client) {
    if (!client->context || pa_context_get_state(client->context) != PA_CONTEXT_READY) return;
    if (client->pending || client->querying) {
        client->refresh_needed = 1;
        return;
    }
    client->refresh_needed = 0;
    client->querying = 1;
    client->query_serial = client->serial;
    pa_operation *operation = pa_context_get_sink_info_by_name(client->context, client->sink_name, sink_info, client);
    if (operation) pa_operation_unref(operation);
    else client->querying = 0;
}

static void subscribed(pa_context *context, int event, uint32_t index, void *data) {
    (void)context; (void)event; (void)index;
    refresh(data);
}

static void context_state(pa_context *context, void *data) {
    AudioClient *client = data;
    int state = pa_context_get_state(context);
    if (state == PA_CONTEXT_READY) {
        pa_context_set_subscribe_callback(context, subscribed, client);
        pa_operation *operation = pa_context_subscribe(context, PA_SUBSCRIPTION_MASK_SINK | PA_SUBSCRIPTION_MASK_SERVER, NULL, NULL);
        if (operation) pa_operation_unref(operation);
        refresh(client);
    } else if (state == PA_CONTEXT_FAILED || state == PA_CONTEXT_TERMINATED) {
        client->state.available = 0;
        notify(client);
    }
}

static void connect_audio(AudioClient *client) {
    if (client->context) {
        int state = pa_context_get_state(client->context);
        if (state != PA_CONTEXT_FAILED && state != PA_CONTEXT_TERMINATED) return;
        pa_context_set_state_callback(client->context, NULL, NULL);
        pa_context_disconnect(client->context);
        pa_context_unref(client->context);
    }
    client->pending = 0;
    client->querying = 0;
    client->refresh_needed = 0;
    client->serial++;
    client->context = pa_context_new(pa_threaded_mainloop_get_api(client->loop), "Waybar volume");
    if (!client->context) return;
    pa_context_set_state_callback(client->context, context_state, client);
    if (pa_context_connect(client->context, NULL, PA_CONTEXT_NOAUTOSPAWN, NULL) < 0) {
        client->state.available = 0;
        notify(client);
    }
}

AudioClient *audio_client_new(const char *sink_name, AudioChanged changed, void *userdata) {
    AudioClient *client = calloc(1, sizeof(*client));
    if (!client) return NULL;
    client->loop = pa_threaded_mainloop_new();
    if (!client->loop) { free(client); return NULL; }
    client->sink_name = sink_name ? strdup(sink_name) : NULL;
    client->changed = changed;
    client->userdata = userdata;
    if (pa_threaded_mainloop_start(client->loop) < 0) {
        pa_threaded_mainloop_free(client->loop);
        free(client->sink_name);
        free(client);
        return NULL;
    }
    client->curves = g_key_file_new();
    char *curves_path = g_build_filename(g_get_user_config_dir(), "waybar", "scripts", "volume-curves.ini", NULL);
    g_key_file_load_from_file(client->curves, curves_path, 0, NULL);
    g_free(curves_path);
    audio_client_retry(client);
    return client;
}

void audio_client_retry(AudioClient *client) {
    if (!client) return;
    pa_threaded_mainloop_lock(client->loop);
    connect_audio(client);
    pa_threaded_mainloop_unlock(client->loop);
}

AudioState audio_client_state(AudioClient *client) {
    AudioState state = {0};
    if (!client) return state;
    pa_threaded_mainloop_lock(client->loop);
    state = client->state;
    pa_threaded_mainloop_unlock(client->loop);
    return state;
}

double audio_client_raw_volume(AudioClient *client) {
    if (!client) return 0;
    pa_threaded_mainloop_lock(client->loop);
    double raw = (double)pa_cvolume_max(&client->channels) / PA_VOLUME_NORM;
    pa_threaded_mainloop_unlock(client->loop);
    return raw;
}

static void applied(pa_context *context, int success, void *data) {
    (void)context; (void)success;
    AudioClient *client = data;
    if (client->pending) client->pending--;
    if (!client->pending) refresh(client);
}

int audio_client_step(AudioClient *client, double percent) {
    if (!client) return 0;
    pa_threaded_mainloop_lock(client->loop);
    if (!client->state.available || pa_context_get_state(client->context) != PA_CONTEXT_READY) {
        pa_threaded_mainloop_unlock(client->loop);
        return 0;
    }
    /* Use the displayed integer so server quantization cannot leave a tiny
     * positive target after scrolling down to zero. */
    double target = (int)(client->state.percent + .5) + percent;
    if (target < 0) target = 0;
    if (target > 100) target = 100;
    pa_cvolume channels = client->channels;
    double reference = volume_curve_stretch_server(target, client->volume_stretch_start, client->volume_stretch_end);
    double raw = volume_curve_server(reference, client->volume_floor, client->volume_ceiling, client->volume_pivot, client->volume_pivot_level, client->volume_pivot_slope, client->volume_start_slope);
    pa_cvolume_scale(&channels, (uint32_t)(raw * PA_VOLUME_NORM / 100.0 + .5));
    pa_operation *operation = pa_context_set_sink_volume_by_index(client->context, client->sink_index, &channels, applied, client);
    if (operation) {
        client->channels = channels;
        client->state.percent = target;
        client->serial++;
        client->pending++;
        pa_operation_unref(operation);
    }
    pa_threaded_mainloop_unlock(client->loop);
    return operation != NULL;
}

int audio_client_toggle_mute(AudioClient *client) {
    if (!client) return 0;
    pa_threaded_mainloop_lock(client->loop);
    if (!client->state.available || pa_context_get_state(client->context) != PA_CONTEXT_READY) {
        pa_threaded_mainloop_unlock(client->loop);
        return 0;
    }
    int muted = !client->state.muted;
    pa_operation *operation = pa_context_set_sink_mute_by_index(client->context, client->sink_index, muted, applied, client);
    if (operation) {
        client->state.muted = muted;
        client->serial++;
        client->pending++;
        pa_operation_unref(operation);
    }
    pa_threaded_mainloop_unlock(client->loop);
    return operation != NULL;
}

void audio_client_free(AudioClient *client) {
    if (!client) return;
    pa_threaded_mainloop_stop(client->loop);
    if (client->idle) g_source_remove(client->idle);
    if (client->context) {
        pa_context_set_state_callback(client->context, NULL, NULL);
        pa_context_disconnect(client->context);
        pa_context_unref(client->context);
    }
    pa_threaded_mainloop_free(client->loop);
    free(client->sink_name);
    g_key_file_unref(client->curves);
    free(client);
}
