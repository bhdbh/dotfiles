/* Numeric metrics use separate GTK labels, so CSS owns every gap. */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { void *obj; const char *version; void *(*get_root_widget)(void *); void (*queue_update)(void *); } InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;
typedef struct { void **pdata; unsigned int len; } PointerArray;
extern void *gtk_box_new(int, int);
extern void *gtk_event_box_new(void);
extern void *gtk_label_new(const char *);
extern void gtk_label_set_text(void *, const char *);
extern void gtk_box_pack_start(void *, void *, int, int, unsigned int);
extern void gtk_container_add(void *, void *);
extern void gtk_widget_set_name(void *, const char *);
extern void gtk_widget_set_has_tooltip(void *, int);
extern void gtk_widget_show_all(void *);
extern void *gtk_widget_get_style_context(void *);
extern void gtk_style_context_add_class(void *, const char *);
extern void gtk_style_context_remove_class(void *, const char *);
extern unsigned int g_timeout_add_seconds(unsigned int, int (*)(void *), void *);
extern int g_source_remove(unsigned int);
extern void g_object_unref(void *);
extern void g_error_free(void *);
extern void *nm_client_new(void *, void **);
extern const PointerArray *nm_client_get_devices(void *);
extern unsigned int nm_device_get_device_type(void *);
extern unsigned int nm_device_get_state(void *);
extern void *nm_device_wifi_get_active_access_point(void *);
extern unsigned char nm_access_point_get_strength(void *);
extern const char *nm_device_get_iface(void *);
extern void *g_subprocess_newv(const char * const *, unsigned int, void **);
extern void g_subprocess_communicate_utf8_async(void *, const char *, void *, void (*)(void *, void *, void *), void *);
extern int g_subprocess_communicate_utf8_finish(void *, void *, char **, char **, void **);
extern int g_subprocess_get_successful(void *);
extern void g_free(void *);

typedef struct {
    void *box, *title, *value, *network;
    unsigned int timer;
    unsigned long long total, idle;
    int kind;
    int alive, references, polling, strength;
    unsigned int network_ticks;
    char interface[32];
} Metric;
typedef struct { Metric *metric; char interface[32]; } SignalJob;
size_t wbcffi_version = 1;

static void release(Metric *metric) {
    if (--metric->references == 0) {
        if (metric->network) g_object_unref(metric->network);
        free(metric);
    }
}

static void signal_ready(void *process, void *result, void *data) {
    SignalJob *job = data;
    Metric *metric = job->metric;
    char *output = NULL;
    void *error = NULL;
    int success = g_subprocess_communicate_utf8_finish(process, result, &output, NULL, &error);
    if (metric->alive && success && g_subprocess_get_successful(process) && output && strcmp(job->interface, metric->interface) == 0) {
        char *signal = strstr(output, "signal:");
        int dbm;
        if (signal && sscanf(signal, "signal: %d", &dbm) == 1) {
            /* Match Waybar 0.15's scale, whose optimum is -45 dBm. */
            int strength = (int)(100.0 - abs(dbm + 45) * 100.0 / 45.0);
            metric->strength = strength < 0 ? 0 : strength > 100 ? 100 : strength;
        }
    }
    metric->polling = 0;
    if (error) g_error_free(error);
    g_free(output);
    g_object_unref(process);
    release(metric);
    free(job);
}

static void poll_signal(Metric *metric) {
    if (metric->polling || !*metric->interface) return;
    const char *argv[] = {"/usr/bin/iw", "dev", metric->interface, "link", NULL};
    void *error = NULL;
    void *process = g_subprocess_newv(argv, 4 | 32, &error);
    if (!process) {
        if (error) g_error_free(error);
        return;
    }
    SignalJob *job = calloc(1, sizeof(*job));
    job->metric = metric;
    snprintf(job->interface, sizeof(job->interface), "%s", metric->interface);
    metric->references++;
    metric->polling = 1;
    g_subprocess_communicate_utf8_async(process, NULL, NULL, signal_ready, job);
}

static int update(void *data) {
    Metric *metric = data;
    char text[32] = "--%";
    if (metric->kind == 0) {
        FILE *stream = fopen("/proc/stat", "r");
        unsigned long long u, n, s, idle, wait, irq, softirq, steal;
        if (stream) {
            if (fscanf(stream, "cpu %llu %llu %llu %llu %llu %llu %llu %llu", &u, &n, &s, &idle, &wait, &irq, &softirq, &steal) == 8) {
                unsigned long long total = u + n + s + idle + wait + irq + softirq + steal;
                unsigned long long inactive = idle + wait;
                if (total > metric->total) {
                    double usage = 100.0 * (1.0 - (double)(inactive - metric->idle) / (double)(total - metric->total));
                    snprintf(text, sizeof(text), "%.0f%%", usage);
                }
                metric->total = total;
                metric->idle = inactive;
            }
            fclose(stream);
        }
    } else if (metric->kind == 1) {
        FILE *stream = fopen("/proc/meminfo", "r");
        unsigned long long total = 0, available = 0;
        char line[256];
        if (stream) {
            while (fgets(line, sizeof(line), stream)) {
                if (sscanf(line, "MemTotal: %llu", &total) == 1) continue;
                if (sscanf(line, "MemAvailable: %llu", &available) == 1) continue;
            }
            fclose(stream);
            if (total && available <= total)
                snprintf(text, sizeof(text), "%.0f%%", 100.0 * (double)(total - available) / (double)total);
        }
    } else {
        int connected = 0;
        const char *interface = "";
        const char *title = "Wi-Fi:";
        snprintf(text, sizeof(text), "Offline");
        const PointerArray *devices = metric->network ? nm_client_get_devices(metric->network) : NULL;
        for (unsigned int i = 0; devices && i < devices->len; i++) {
            void *device = devices->pdata[i];
            if (nm_device_get_state(device) != 100) continue;
            unsigned int type = nm_device_get_device_type(device);
            if (type == 1) {
                title = "Ethernet";
                text[0] = '\0';
                connected = 1;
                break;
            }
            if (type == 2) {
                void *ap = nm_device_wifi_get_active_access_point(device);
                if (ap) {
                    interface = nm_device_get_iface(device);
                    if (strcmp(interface, metric->interface) != 0) {
                        snprintf(metric->interface, sizeof(metric->interface), "%s", interface);
                        metric->strength = -1;
                        metric->network_ticks = 0;
                    }
                    if (metric->strength >= 0) snprintf(text, sizeof(text), "%d%%", metric->strength);
                    else snprintf(text, sizeof(text), "--%%");
                    connected = 1;
                }
            }
        }
        if (!*interface) {
            metric->interface[0] = '\0';
            metric->strength = -1;
        } else if (metric->network_ticks++ % 15 == 0) {
            poll_signal(metric);
        }
        gtk_label_set_text(metric->title, title);
        void *style = gtk_widget_get_style_context(metric->box);
        if (connected) gtk_style_context_remove_class(style, "disconnected");
        else gtk_style_context_add_class(style, "disconnected");
        void *value_style = gtk_widget_get_style_context(metric->value);
        if (*text) gtk_style_context_add_class(value_style, "metric-value");
        else gtk_style_context_remove_class(value_style, "metric-value");
    }
    gtk_label_set_text(metric->value, text);
    return 1;
}

void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    Metric *metric = calloc(1, sizeof(*metric));
    metric->alive = metric->references = 1;
    metric->strength = -1;
    for (size_t i = 0; i < count; i++) {
        if (strcmp(entries[i].key, "kind") == 0) {
            if (strcmp(entries[i].value, "memory") == 0) metric->kind = 1;
            else if (strcmp(entries[i].value, "network") == 0) metric->kind = 2;
        }
    }
    const char *names[] = {"cpu", "memory", "network"};
    const char *titles[] = {"CPU:", "RAM:", "Wi-Fi:"};
    metric->box = gtk_box_new(0, 0);
    gtk_widget_set_name(metric->box, names[metric->kind]);
    gtk_style_context_add_class(gtk_widget_get_style_context(metric->box), "metric");
    metric->title = gtk_label_new(titles[metric->kind]);
    metric->value = gtk_label_new("--%");
    char name[32];
    snprintf(name, sizeof(name), "%s-label", names[metric->kind]);
    gtk_widget_set_name(metric->title, name);
    snprintf(name, sizeof(name), "%s-value", names[metric->kind]);
    gtk_widget_set_name(metric->value, name);
    gtk_style_context_add_class(gtk_widget_get_style_context(metric->title), "metric-label");
    gtk_style_context_add_class(gtk_widget_get_style_context(metric->value), "metric-value");
    gtk_box_pack_start(metric->box, metric->title, 0, 0, 0);
    gtk_box_pack_start(metric->box, metric->value, 0, 0, 0);
    void *root = info->get_root_widget(info->obj);
    gtk_container_add(root, metric->box);
    gtk_widget_set_has_tooltip(root, 0);
    if (metric->kind == 2) {
        void *error = NULL;
        metric->network = nm_client_new(NULL, &error);
        if (error) g_error_free(error);
    }
    update(metric);
    metric->timer = g_timeout_add_seconds(metric->kind == 2 ? 1 : metric->kind == 1 ? 30 : 3, update, metric);
    gtk_widget_show_all(root);
    return metric;
}

void wbcffi_deinit(void *data) {
    Metric *metric = data;
    metric->alive = 0;
    g_source_remove(metric->timer);
    release(metric);
}

void wbcffi_refresh(void *data, int signal) {
    (void)signal;
    update(data);
}
