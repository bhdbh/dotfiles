/* Independent profile and full-charge controls inside one battery frame. */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void (*AsyncCallback)(void *, void *, void *);
typedef struct {
    void *obj;
    const char *version;
    void *(*get_root_widget)(void *);
    void (*queue_update)(void *);
} InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;

extern void *gtk_label_new(const char *);
extern void gtk_label_set_markup(void *, const char *);
extern void *gtk_widget_get_style_context(void *);
extern void gtk_style_context_add_class(void *, const char *);
extern void gtk_style_context_remove_class(void *, const char *);
extern void *gtk_box_new(int, int);
extern void gtk_box_pack_start(void *, void *, int, int, unsigned int);
extern void *gtk_event_box_new(void);
extern void *gtk_separator_new(int);
extern void gtk_widget_set_valign(void *, int);
extern void gtk_widget_set_no_show_all(void *, int);
extern void gtk_widget_set_visible(void *, int);
extern int gtk_widget_get_visible(void *);
extern void gtk_container_add(void *, void *);
extern void gtk_widget_add_events(void *, int);
extern void gtk_widget_set_name(void *, const char *);
extern void gtk_widget_set_has_tooltip(void *, int);
extern void gtk_widget_show_all(void *);
extern int gdk_event_get_button(const void *, unsigned int *);
extern unsigned long g_signal_connect_data(void *, const char *, void (*)(void), void *, void *, unsigned int);
extern void g_signal_handler_disconnect(void *, unsigned long);
extern unsigned int g_timeout_add_seconds(unsigned int, int (*)(void *), void *);
extern unsigned int g_timeout_add(unsigned int, int (*)(void *), void *);
extern int64_t g_get_monotonic_time(void);
extern char *g_strdup(const char *);
extern int g_source_remove(unsigned int);
extern void *g_subprocess_newv(const char * const *, unsigned int, void **);
extern void g_subprocess_communicate_utf8_async(void *, const char *, void *, AsyncCallback, void *);
extern int g_subprocess_communicate_utf8_finish(void *, void *, char **, char **, void **);
extern int g_subprocess_get_successful(void *);
extern void g_object_unref(void *);
extern void g_error_free(void *);
extern void g_free(void *);

enum ChargeSync { CHARGE_SETTLED, CHARGE_STARTING, CHARGE_STOPPING };
enum { SYNC_INTERVAL_MS = 200, SYNC_TIMEOUT_MS = 5000 };

typedef struct {
    void *label;
    void *title;
    void *clickbox;
    void *charge;
    void *divider;
    void *circle;
    unsigned int timer;
    unsigned long handler;
    unsigned long charge_handler;
    int references;
    int alive;
    int polling;
    int acting;
    unsigned int revision;
    enum ChargeSync sync;
    unsigned int sync_timer;
    int64_t sync_deadline;
    int plugged, full, capacity, sync_full;
    char status[24];
    char *value_markup;
} Battery;
typedef struct { Battery *battery; int action; unsigned int revision; } Job;

size_t wbcffi_version = 1;
static int poll_battery(void *);
static void run(Battery *, const char *, int);
static int sync_tick(void *);

static void release(Battery *battery) {
    if (--battery->references == 0) {
        g_free(battery->value_markup);
        free(battery);
    }
}

static void render_value(Battery *battery) {
    void *style = gtk_widget_get_style_context(battery->label);
    if (battery->sync == CHARGE_STARTING) {
        char markup[96];
        snprintf(markup, sizeof(markup), "<span foreground=\"#22c55e\">%d%%</span>", battery->capacity);
        gtk_label_set_markup(battery->label, markup);
    } else if (battery->value_markup) {
        gtk_label_set_markup(battery->label, battery->value_markup);
    }
    if (battery->sync != CHARGE_SETTLED) gtk_style_context_add_class(style, "syncing");
    else gtk_style_context_remove_class(style, "syncing");
}

static void stop_sync(Battery *battery) {
    if (battery->sync_timer) {
        g_source_remove(battery->sync_timer);
        battery->sync_timer = 0;
    }
    battery->sync = CHARGE_SETTLED;
}

static void update_sync(Battery *battery, int charge_action) {
    int charging = strcmp(battery->status, "Charging") == 0;
    int idle = strcmp(battery->status, "Not charging") == 0;
    int valid = battery->plugged && battery->capacity >= 0 && battery->capacity < 100 &&
                (charging || idle);
    if (charge_action) {
        stop_sync(battery);
        battery->sync_full = battery->full;
        /* The helper has already confirmed the mode. Bridge only a stale ACPI
           status, never an unplug, a full battery, a failed action or discharge. */
        if (valid && battery->full && idle) battery->sync = CHARGE_STARTING;
        else if (valid && !battery->full && battery->capacity >= 80 && charging)
            battery->sync = CHARGE_STOPPING;
        if (battery->sync != CHARGE_SETTLED) {
            battery->sync_deadline = g_get_monotonic_time() + (int64_t)SYNC_TIMEOUT_MS * 1000;
            battery->sync_timer = g_timeout_add(SYNC_INTERVAL_MS, sync_tick, battery);
        }
    } else if (battery->sync != CHARGE_SETTLED &&
               (!valid || battery->full != battery->sync_full ||
                g_get_monotonic_time() >= battery->sync_deadline ||
                (battery->sync == CHARGE_STARTING && charging) ||
                (battery->sync == CHARGE_STOPPING && !charging))) {
        stop_sync(battery);
    }
}

static void completed(void *process, void *result, void *data) {
    Job *job = data;
    Battery *battery = job->battery;
    char *output = NULL;
    void *error = NULL;
    int success = g_subprocess_communicate_utf8_finish(process, result, &output, NULL, &error);
    success = success && g_subprocess_get_successful(process);
    if (job->action) {
        battery->acting = 0;
    } else {
        battery->polling = 0;
    }
    if (battery->alive && success && output && *output && job->revision == battery->revision) {
        output[strcspn(output, "\r\n")] = '\0';
        char *value = strchr(output, '\t');
        if (value) {
            *value++ = '\0';
            char *plugged = strchr(value, '\t');
            char *full = plugged ? strchr(plugged + 1, '\t') : NULL;
            char *status = full ? strchr(full + 1, '\t') : NULL;
            char *capacity = status ? strchr(status + 1, '\t') : NULL;
            if (plugged && full && status && capacity) {
                *plugged++ = '\0';
                *full++ = '\0';
                *status++ = '\0';
                *capacity++ = '\0';
                battery->plugged = *plugged == '1';
                battery->full = *full == '1';
                char *end = NULL;
                long level = strtol(capacity, &end, 10);
                battery->capacity = end != capacity && *end == '\0' && level >= 0 && level <= 100 ? (int)level : -1;
                snprintf(battery->status, sizeof(battery->status), "%s", status);
                g_free(battery->value_markup);
                battery->value_markup = g_strdup(value);
                update_sync(battery, job->action == 2);
                void *style = gtk_widget_get_style_context(battery->circle);
                if (battery->full) gtk_style_context_add_class(style, "enabled");
                else gtk_style_context_remove_class(style, "enabled");
                gtk_widget_set_visible(battery->charge, battery->plugged);
                gtk_widget_set_visible(battery->divider, battery->plugged);
                gtk_label_set_markup(battery->title, output);
                render_value(battery);
            }
        }
    }
    if (error) g_error_free(error);
    g_free(output);
    g_object_unref(process);
    release(battery);
    free(job);
}

static void run(Battery *battery, const char *action, int is_action) {
    const char *argv[] = {"/usr/bin/python3", "/home/user/Projects/waybar/validation/fake-battery.py", action, "--widget", NULL};
    void *error = NULL;
    /* G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_SILENCE */
    void *process = g_subprocess_newv(argv, 4 | 32, &error);
    if (!process) {
        if (error) g_error_free(error);
        return;
    }
    Job *job = calloc(1, sizeof(*job));
    job->battery = battery;
    job->action = is_action ? (strcmp(action, "toggle-charge") == 0 ? 2 : 1) : 0;
    battery->references++;
    if (is_action) {
        battery->acting = 1;
        battery->revision++;
        if (job->action == 2) {
            stop_sync(battery);
            render_value(battery);
        }
    }
    else battery->polling = 1;
    job->revision = battery->revision;
    g_subprocess_communicate_utf8_async(process, NULL, NULL, completed, job);
}

static int poll_battery(void *data) {
    Battery *battery = data;
    if (battery->alive && !battery->polling && !battery->acting) run(battery, "status", 0);
    return battery->alive;
}

static int sync_tick(void *data) {
    Battery *battery = data;
    if (g_get_monotonic_time() >= battery->sync_deadline) {
        /* Do not retain the provisional color when firmware never settles. */
        battery->sync_timer = 0;
        battery->sync = CHARGE_SETTLED;
        render_value(battery);
        return 0;
    }
    poll_battery(battery); /* Existing in-flight guards prevent a polling backlog. */
    return 1;
}

static int clicked(void *widget, void *event, void *data) {
    (void)widget;
    Battery *battery = data;
    unsigned int button = 0;
    if (!gdk_event_get_button(event, &button) || button != 1) return 0;
    if (!battery->acting) run(battery, "toggle-profile", 1);
    return 1;
}

static int charge_clicked(void *widget, void *event, void *data) {
    (void)widget;
    Battery *battery = data;
    unsigned int button = 0;
    if (!gdk_event_get_button(event, &button) || button != 1) return 0;
    if (!battery->acting && gtk_widget_get_visible(battery->charge))
        run(battery, "toggle-charge", 1);
    return 1;
}

void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    (void)entries;
    (void)count;
    Battery *battery = calloc(1, sizeof(*battery));
    battery->alive = battery->references = 1;
    void *root = info->get_root_widget(info->obj);
    battery->clickbox = gtk_event_box_new();
    gtk_widget_set_name(battery->clickbox, "battery-profile");
    void *frame = gtk_box_new(0, 0);
    gtk_widget_set_name(frame, "battery-control");
    void *box = gtk_box_new(0, 0);
    gtk_widget_set_name(box, "battery-info");
    gtk_style_context_add_class(gtk_widget_get_style_context(box), "metric");
    battery->title = gtk_label_new("Battery:");
    battery->label = gtk_label_new("--%");
    gtk_widget_set_name(battery->title, "battery-label");
    gtk_widget_set_name(battery->label, "battery-value");
    gtk_style_context_add_class(gtk_widget_get_style_context(battery->title), "metric-label");
    gtk_style_context_add_class(gtk_widget_get_style_context(battery->label), "metric-value");
    gtk_box_pack_start(box, battery->title, 0, 0, 0);
    gtk_box_pack_start(box, battery->label, 0, 0, 0);
    gtk_container_add(battery->clickbox, box);
    gtk_box_pack_start(frame, battery->clickbox, 0, 0, 0);
    battery->divider = gtk_box_new(0, 0);
    gtk_widget_set_name(battery->divider, "battery-charge-divider");
    void *separator = gtk_separator_new(1);
    gtk_widget_set_name(separator, "item-divider");
    gtk_box_pack_start(battery->divider, separator, 0, 0, 0);
    gtk_box_pack_start(frame, battery->divider, 0, 0, 0);
    battery->charge = gtk_event_box_new();
    gtk_widget_set_name(battery->charge, "battery-charge-toggle");
    void *charge_content = gtk_box_new(0, 0);
    gtk_widget_set_name(charge_content, "battery-charge-content");
    battery->circle = gtk_box_new(0, 0);
    gtk_widget_set_name(battery->circle, "battery-full");
    gtk_widget_set_valign(battery->circle, 3); /* GTK_ALIGN_CENTER, like recording. */
    gtk_box_pack_start(charge_content, battery->circle, 0, 0, 0);
    gtk_container_add(battery->charge, charge_content);
    gtk_box_pack_start(frame, battery->charge, 0, 0, 0);
    gtk_container_add(root, frame);
    gtk_widget_set_no_show_all(battery->divider, 1);
    gtk_widget_set_no_show_all(battery->charge, 1);
    gtk_widget_set_has_tooltip(root, 0);
    gtk_widget_set_has_tooltip(battery->clickbox, 0);
    gtk_widget_add_events(battery->clickbox, 1 << 8);
    battery->handler = g_signal_connect_data(battery->clickbox, "button-press-event", (void (*)(void))clicked, battery, NULL, 0);
    gtk_widget_add_events(battery->charge, 1 << 8);
    battery->charge_handler = g_signal_connect_data(battery->charge, "button-press-event", (void (*)(void))charge_clicked, battery, NULL, 0);
    gtk_widget_show_all(root);
    /* Prepare the children while keeping both optional sections hidden. */
    gtk_widget_show_all(separator);
    gtk_widget_show_all(charge_content);
    gtk_widget_set_visible(battery->divider, 0);
    gtk_widget_set_visible(battery->charge, 0);
    battery->timer = g_timeout_add_seconds(2, poll_battery, battery);
    poll_battery(battery);
    return battery;
}

void wbcffi_deinit(void *data) {
    Battery *battery = data;
    battery->alive = 0;
    stop_sync(battery);
    g_source_remove(battery->timer);
    g_signal_handler_disconnect(battery->clickbox, battery->handler);
    g_signal_handler_disconnect(battery->charge, battery->charge_handler);
    release(battery);
}

void wbcffi_refresh(void *data, int signal) {
    (void)signal;
    poll_battery(data);
}
