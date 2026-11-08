/* Independent mute and output controls inside one passive volume frame. */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "audio-client.h"

typedef void (*AsyncCallback)(void *, void *, void *);
typedef struct {
    void *obj;
    const char *version;
    void *(*get_root_widget)(void *);
    void (*queue_update)(void *);
} InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;

extern void *gtk_label_new(const char *);
extern void gtk_label_set_text(void *, const char *);
extern void *gtk_box_new(int, int);
extern void gtk_box_pack_start(void *, void *, int, int, unsigned int);
extern void *gtk_separator_new(int);
extern void *gtk_event_box_new(void);
extern void gtk_container_add(void *, void *);
extern void gtk_widget_add_events(void *, int);
extern void gtk_widget_set_name(void *, const char *);
extern void gtk_widget_set_has_tooltip(void *, int);
extern void gtk_widget_set_no_show_all(void *, int);
extern void gtk_widget_set_visible(void *, int);
extern void gtk_widget_show_all(void *);
extern void *gtk_widget_get_style_context(void *);
extern void gtk_style_context_add_class(void *, const char *);
extern void gtk_style_context_remove_class(void *, const char *);
extern int gdk_event_get_button(const void *, unsigned int *);
extern int gdk_event_get_event_type(const void *);
extern int gdk_event_get_scroll_direction(const void *, int *);
extern int gdk_event_get_scroll_deltas(const void *, double *, double *);
extern void *gdk_event_get_source_device(const void *);
extern int gdk_device_get_source(void *);
extern int gdk_event_is_scroll_stop_event(const void *);
extern unsigned long g_signal_connect_data(void *, const char *, void (*)(void), void *, void *, unsigned int);
extern void g_signal_handler_disconnect(void *, unsigned long);
extern unsigned int g_timeout_add_seconds(unsigned int, int (*)(void *), void *);
extern int g_source_remove(unsigned int);
extern void *g_subprocess_newv(const char * const *, unsigned int, void **);
extern void g_subprocess_communicate_utf8_async(void *, const char *, void *, AsyncCallback, void *);
extern int g_subprocess_communicate_utf8_finish(void *, void *, char **, char **, void **);
extern void g_object_unref(void *);
extern void g_error_free(void *);
extern const char *g_get_user_config_dir(void);
extern char *g_build_filename(const char *, ...);
extern void g_free(void *);

typedef struct {
    void *label;
    void *output;
    void *clickbox;
    void *outputbox;
    void *divider;
    unsigned int timer;
    unsigned long click_handler;
    unsigned long output_handler;
    unsigned long scroll_handler;
    int references;
    int alive;
    int polling;
    int acting;
    AudioClient *audio;
    unsigned int revision;
    double touchpad_scroll;
} Volume;
typedef struct { Volume *volume; int action; unsigned int revision; } Job;

size_t wbcffi_version = 1;
static int poll_volume(void *);
static void run(Volume *, const char *, int);

static void audio_changed(void *data, AudioState state) {
    Volume *volume = data;
    if (!volume->alive) return;
    char text[64];
    if (state.available) snprintf(text, sizeof(text), "%.0f%%", state.muted ? 0.0 : state.percent);
    else snprintf(text, sizeof(text), "--%%");
    gtk_label_set_text(volume->label, text);
    void *context = gtk_widget_get_style_context(volume->clickbox);
    if (state.muted) gtk_style_context_add_class(context, "muted");
    else gtk_style_context_remove_class(context, "muted");
}

static void release(Volume *volume) {
    if (--volume->references == 0) free(volume);
}

static void completed(void *process, void *result, void *data) {
    Job *job = data;
    Volume *volume = job->volume;
    char *output = NULL;
    void *error = NULL;
    int success = g_subprocess_communicate_utf8_finish(process, result, &output, NULL, &error);
    if (job->action) volume->acting = 0;
    else volume->polling = 0;
    /* A poll started before an action must not overwrite its newer result. */
    if (volume->alive && success && output && job->revision == volume->revision) {
        char *number = strchr(output, '\n');
        char *state = number ? strchr(number + 1, '\n') : NULL;
        char *count = state ? strchr(state + 1, '\n') : NULL;
        if (number && state && count) {
            *number++ = '\0';
            *state++ = '\0';
            gtk_label_set_text(volume->output, number);
            int multiple = strtoul(count + 1, NULL, 10) > 1;
            gtk_widget_set_visible(volume->divider, multiple);
            gtk_widget_set_visible(volume->outputbox, multiple);
        }
    }
    if (volume->alive && job->action) {
        poll_volume(volume);
    }
    if (error) g_error_free(error);
    g_free(output);
    g_object_unref(process);
    release(volume);
    free(job);
}

static void run(Volume *volume, const char *action, int is_action) {
    char *script = g_build_filename(g_get_user_config_dir(), "waybar", "scripts", "audio-output.py", NULL);
    const char *argv[] = {"/usr/bin/python3", script, action, is_action ? "--widget" : NULL, NULL};
    void *error = NULL;
    void *process = g_subprocess_newv(argv, 4 | 32, &error);
    g_free(script);
    if (!process) {
        if (error) g_error_free(error);
        return;
    }
    Job *job = calloc(1, sizeof(*job));
    job->volume = volume;
    job->action = is_action;
    volume->references++;
    if (is_action) {
        volume->acting = 1;
        volume->revision++;
    }
    else volume->polling = 1;
    job->revision = volume->revision;
    g_subprocess_communicate_utf8_async(process, NULL, NULL, completed, job);
}

static int poll_volume(void *data) {
    Volume *volume = data;
    if (volume->alive) audio_client_retry(volume->audio);
    if (volume->alive && !volume->polling && !volume->acting) run(volume, "--widget", 0);
    return volume->alive;
}

static int clicked(void *widget, void *event, void *data) {
    (void)widget;
    Volume *volume = data;
    unsigned int button = 0;
    if (!gdk_event_get_button(event, &button) || button != 1) return 0;
    /* GTK also emits double/triple-press events after the ordinary press. */
    if (gdk_event_get_event_type(event) != 4) return 1; /* GDK_BUTTON_PRESS */
    if (audio_client_toggle_mute(volume->audio))
        audio_changed(volume, audio_client_state(volume->audio));
    return 1;
}

static int output_clicked(void *widget, void *event, void *data) {
    (void)widget;
    Volume *volume = data;
    unsigned int button = 0;
    if (!gdk_event_get_button(event, &button) || button != 1) return 0;
    if (gdk_event_get_event_type(event) != 4) return 1;
    if (!volume->acting) run(volume, "--next", 1);
    return 1;
}

static int scrolled(void *widget, void *event, void *data) {
    (void)widget;
    Volume *volume = data;
    int direction = -1;
    double dx = 0, dy = 0;
    double step;
    void *source = gdk_event_get_source_device(event);
    int touchpad = source && gdk_device_get_source(source) == 6; /* GDK_SOURCE_TOUCHPAD */
    if (!touchpad || gdk_event_is_scroll_stop_event(event)) {
        volume->touchpad_scroll = 0;
        if (touchpad) return 1;
    }
    /* Smooth events deliberately return FALSE from get_scroll_direction. */
    if (!gdk_event_get_scroll_direction(event, &direction)) {
        if (!gdk_event_get_scroll_deltas(event, &dx, &dy)) return 0;
        if (dy == 0) return 0;
        if (touchpad) {
            /* Honor Sway's scroll factor, with 15% more sensitivity, and
             * apply the accumulated distance in 2% volume steps. */
            double delta = -dy * 1.15;
            if ((delta > 0 && volume->touchpad_scroll < 0) ||
                (delta < 0 && volume->touchpad_scroll > 0))
                volume->touchpad_scroll = 0;
            volume->touchpad_scroll += delta;
            step = (int)(volume->touchpad_scroll / 2.0) * 2.0;
            volume->touchpad_scroll -= step;
            if (step == 0) return 1;
            goto apply;
        }
        direction = dy < 0 ? 0 : 1;
    }
    if (direction != 0 && direction != 1) return 0;
    step = direction == 0 ? 2.0 : -2.0;
apply:
    if (audio_client_step(volume->audio, step))
        audio_changed(volume, audio_client_state(volume->audio));
    return 1;
}

void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    (void)entries;
    (void)count;
    Volume *volume = calloc(1, sizeof(*volume));
    volume->alive = volume->references = 1;
    void *root = info->get_root_widget(info->obj);
    volume->clickbox = gtk_event_box_new();
    gtk_widget_set_name(volume->clickbox, "volume-toggle");
    void *box = gtk_box_new(0, 0);
    gtk_widget_set_name(box, "volume-status");
    void *metric = gtk_box_new(0, 0);
    gtk_widget_set_name(metric, "pulseaudio");
    gtk_style_context_add_class(gtk_widget_get_style_context(metric), "metric");
    void *title = gtk_label_new("Volume:");
    gtk_widget_set_name(title, "volume-label");
    gtk_style_context_add_class(gtk_widget_get_style_context(title), "metric-label");
    volume->label = gtk_label_new("--%");
    gtk_style_context_add_class(gtk_widget_get_style_context(volume->label), "metric-value");
    gtk_box_pack_start(metric, title, 0, 0, 0);
    gtk_box_pack_start(metric, volume->label, 0, 0, 0);
    volume->output = gtk_label_new("--");
    volume->divider = gtk_box_new(0, 0);
    gtk_widget_set_name(volume->divider, "audio-output-divider");
    void *separator = gtk_separator_new(1);
    gtk_widget_set_name(volume->label, "volume-value");
    gtk_widget_set_name(volume->output, "custom-audio-output");
    gtk_widget_set_name(separator, "item-divider");
    gtk_container_add(volume->clickbox, metric);
    gtk_box_pack_start(box, volume->clickbox, 0, 0, 0);
    gtk_box_pack_start(volume->divider, separator, 0, 0, 0);
    gtk_box_pack_start(box, volume->divider, 0, 0, 0);
    volume->outputbox = gtk_event_box_new();
    gtk_widget_set_name(volume->outputbox, "audio-output-toggle");
    void *output_content = gtk_box_new(0, 0);
    gtk_widget_set_name(output_content, "audio-output-content");
    gtk_box_pack_start(output_content, volume->output, 0, 0, 0);
    gtk_container_add(volume->outputbox, output_content);
    gtk_box_pack_start(box, volume->outputbox, 0, 0, 0);
    gtk_container_add(root, box);
    gtk_widget_set_no_show_all(volume->divider, 1);
    gtk_widget_set_no_show_all(volume->outputbox, 1);
    gtk_widget_set_has_tooltip(root, 0);
    gtk_widget_set_has_tooltip(volume->clickbox, 0);
    gtk_widget_set_has_tooltip(volume->outputbox, 0);
    gtk_widget_add_events(volume->clickbox, (1 << 8) | (1 << 21) | (1 << 23));
    volume->click_handler = g_signal_connect_data(volume->clickbox, "button-press-event", (void (*)(void))clicked, volume, NULL, 0);
    volume->scroll_handler = g_signal_connect_data(volume->clickbox, "scroll-event", (void (*)(void))scrolled, volume, NULL, 0);
    gtk_widget_add_events(volume->outputbox, 1 << 8);
    volume->output_handler = g_signal_connect_data(volume->outputbox, "button-press-event", (void (*)(void))output_clicked, volume, NULL, 0);
    gtk_widget_show_all(root);
    gtk_widget_show_all(separator);
    gtk_widget_show_all(output_content);
    gtk_widget_set_visible(volume->divider, 0);
    gtk_widget_set_visible(volume->outputbox, 0);
    volume->audio = audio_client_new(NULL, audio_changed, volume);
    volume->timer = g_timeout_add_seconds(1, poll_volume, volume);
    poll_volume(volume);
    return volume;
}

void wbcffi_deinit(void *data) {
    Volume *volume = data;
    volume->alive = 0;
    audio_client_free(volume->audio);
    g_source_remove(volume->timer);
    g_signal_handler_disconnect(volume->clickbox, volume->click_handler);
    g_signal_handler_disconnect(volume->clickbox, volume->scroll_handler);
    g_signal_handler_disconnect(volume->outputbox, volume->output_handler);
    release(volume);
}

void wbcffi_refresh(void *data, int signal) {
    (void)signal;
    poll_volume(data);
}
