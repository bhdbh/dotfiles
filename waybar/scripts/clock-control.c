/* Keep the frame expanded with date and time anchored to its right edge. */
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    void *obj;
    const char *version;
    void *(*get_root_widget)(void *);
    void (*queue_update)(void *);
} InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;
typedef struct {
    void *date;
    void *time;
    void *zone;
    void *recording;
    void *parser;
    char *state_file;
    unsigned int timer;
    unsigned int blink_timer;
    int dim;
} Clock;

extern void *gtk_label_new(const char *);
extern void gtk_label_set_text(void *, const char *);
extern void *gtk_box_new(int, int);
extern void gtk_box_pack_start(void *, void *, int, int, unsigned int);
extern void *gtk_separator_new(int);
extern void gtk_widget_set_valign(void *, int);
extern void gtk_widget_set_no_show_all(void *, int);
extern void gtk_widget_set_visible(void *, int);
extern int gtk_widget_get_visible(void *);
extern void *gtk_widget_get_style_context(void *);
extern void gtk_style_context_add_class(void *, const char *);
extern void gtk_style_context_remove_class(void *, const char *);
extern void *gtk_alignment_new(float, float, float, float);
extern void gtk_container_add(void *, void *);
extern void gtk_widget_set_name(void *, const char *);
extern void gtk_widget_set_has_tooltip(void *, int);
extern void gtk_widget_set_hexpand(void *, int);
extern void gtk_widget_show_all(void *);
extern unsigned int g_timeout_add_seconds(unsigned int, int (*)(void *), void *);
extern unsigned int g_timeout_add(unsigned int, int (*)(void *), void *);
extern int g_source_remove(unsigned int);
extern void *g_time_zone_new_identifier(const char *);
extern void g_time_zone_unref(void *);
extern void *g_date_time_new_now(void *);
extern char *g_date_time_format(void *, const char *);
extern void g_date_time_unref(void *);
extern void g_free(void *);
extern char *g_compute_checksum_for_string(int, const char *, long);
extern char *g_strdup_printf(const char *, ...);
extern int g_file_get_contents(const char *, char **, size_t *, void **);
extern unsigned long g_type_from_name(const char *);
extern void g_object_unref(void *);
extern void *json_parser_new(void);
extern int json_parser_load_from_file(void *, const char *, void **);
extern void *json_parser_get_root(void *);
extern int json_node_get_node_type(void *);
extern void *json_node_get_object(void *);
extern void *json_object_get_member(void *, const char *);
extern unsigned long json_node_get_value_type(void *);
extern int64_t json_node_get_int(void *);
extern const char *json_node_get_string(void *);

size_t wbcffi_version = 1;

static int recording_active(Clock *clock) {
    if (!clock->state_file ||
        !json_parser_load_from_file(clock->parser, clock->state_file, NULL)) return 0;
    void *root = json_parser_get_root(clock->parser);
    if (!root || json_node_get_node_type(root) != 0) return 0; /* JSON_NODE_OBJECT */
    void *object = json_node_get_object(root);
    void *pid_node = json_object_get_member(object, "pid");
    void *start_node = json_object_get_member(object, "start_time");
    if (!pid_node || !start_node ||
        json_node_get_value_type(pid_node) != g_type_from_name("gint64") ||
        json_node_get_value_type(start_node) != g_type_from_name("gchararray")) return 0;
    int64_t pid = json_node_get_int(pid_node);
    const char *expected = json_node_get_string(start_node);
    if (pid <= 0 || pid > INT32_MAX || !expected || !*expected) return 0;
    char path[64];
    snprintf(path, sizeof(path), "/proc/%ld/stat", (long)pid);
    char *stat = NULL;
    if (!g_file_get_contents(path, &stat, NULL, NULL)) return 0;
    /* Match the recorder's process start time so a stale/reused PID never lights up. */
    char *field = strrchr(stat, ')');
    int active = 0;
    if (field && field[1] == ' ' && field[2] && field[2] != 'Z' && field[2] != 'X') {
        field += 2; /* Field 3: process state, after the parenthesized name. */
        for (int number = 3; field && number < 22; ++number) {
            field = strchr(field, ' ');
            if (field) ++field;
        }
        if (field) {
            size_t length = strcspn(field, " \n");
            active = strlen(expected) == length && !strncmp(field, expected, length);
        }
    }
    g_free(stat);
    return active;
}

static int recording_tick(void *data) {
    Clock *clock = data;
    int active = recording_active(clock);
    clock->dim = active && gtk_widget_get_visible(clock->recording) && !clock->dim;
    void *style = gtk_widget_get_style_context(clock->recording);
    if (clock->dim) gtk_style_context_add_class(style, "dim");
    else gtk_style_context_remove_class(style, "dim");
    gtk_widget_set_visible(clock->recording, active);
    return 1;
}

static int tick(void *data) {
    Clock *clock = data;
    void *now = g_date_time_new_now(clock->zone);
    char *date = g_date_time_format(now, "%d-%m-%Y");
    char *time = g_date_time_format(now, "%H:%M:%S");
    gtk_label_set_text(clock->date, date);
    gtk_label_set_text(clock->time, time);
    g_free(date);
    g_free(time);
    g_date_time_unref(now);
    return 1;
}

void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    (void)entries;
    (void)count;
    Clock *clock = calloc(1, sizeof(*clock));
    clock->zone = g_time_zone_new_identifier("America/Sao_Paulo");
    clock->parser = json_parser_new();
    const char *socket = getenv("SWAYSOCK");
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    if (socket && runtime) {
        char *identity = g_compute_checksum_for_string(2, socket, -1); /* SHA-256 */
        clock->state_file = g_strdup_printf("%s/sway-recording-%.12s/state.json", runtime, identity);
        g_free(identity);
    }
    void *root = info->get_root_widget(info->obj);
    void *frame = gtk_box_new(0, 0);
    gtk_widget_set_name(frame, "clock");
    gtk_widget_set_hexpand(frame, 1);
    void *alignment = gtk_alignment_new(1.0f, 0.5f, 0, 1);
    void *content = gtk_box_new(0, 0);
    clock->recording = gtk_box_new(0, 0);
    gtk_widget_set_name(clock->recording, "clock-recording");
    gtk_widget_set_valign(clock->recording, 3); /* GTK_ALIGN_CENTER */
    gtk_widget_set_no_show_all(clock->recording, 1);
    clock->date = gtk_label_new("");
    clock->time = gtk_label_new("");
    void *separator = gtk_separator_new(1);
    gtk_widget_set_name(clock->date, "clock-date");
    gtk_widget_set_name(clock->time, "clock-time");
    gtk_widget_set_name(separator, "item-divider");
    gtk_box_pack_start(content, clock->recording, 0, 0, 0);
    gtk_box_pack_start(content, clock->time, 0, 0, 0);
    gtk_box_pack_start(content, separator, 0, 0, 0);
    gtk_box_pack_start(content, clock->date, 0, 0, 0);
    gtk_container_add(alignment, content);
    gtk_box_pack_start(frame, alignment, 1, 1, 0);
    gtk_widget_set_has_tooltip(root, 0);
    gtk_container_add(root, frame);
    gtk_widget_show_all(root);
    clock->timer = g_timeout_add_seconds(1, tick, clock);
    clock->blink_timer = g_timeout_add(1000, recording_tick, clock);
    tick(clock);
    recording_tick(clock);
    return clock;
}

void wbcffi_deinit(void *data) {
    Clock *clock = data;
    g_source_remove(clock->timer);
    g_source_remove(clock->blink_timer);
    g_time_zone_unref(clock->zone);
    g_object_unref(clock->parser);
    g_free(clock->state_file);
    free(clock);
}
