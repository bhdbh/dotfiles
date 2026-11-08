/* Trim the font's ink bearings, keeping CSS gaps measured from visible glyphs. */
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { void *obj; const char *version; void *(*get_root_widget)(void *); void (*queue_update)(void *); } InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;
typedef struct { int x, y, width, height; } Rectangle;
typedef struct { short left, right, top, bottom; } Border;
extern unsigned long gtk_label_get_type(void);
extern unsigned long gtk_widget_get_type(void);
extern unsigned long gtk_container_get_type(void);
extern unsigned long gtk_event_box_get_type(void);
extern void *gtk_bin_get_child(void *);
extern void gtk_event_box_set_visible_window(void *, int);
extern int g_type_check_instance_is_a(void *, unsigned long);
extern void *gtk_label_get_layout(void *);
extern void *pango_layout_copy(void *);
extern void pango_layout_set_text(void *, const char *, int);
extern void pango_layout_get_pixel_extents(void *, Rectangle *, Rectangle *);
extern void *gtk_widget_get_toplevel(void *);
extern const char *gtk_widget_get_name(void *);
extern void *gtk_widget_get_style_context(void *);
extern unsigned int gtk_style_context_get_state(void *);
extern void gtk_style_context_get_margin(void *, unsigned int, Border *);
extern void gtk_style_context_add_provider(void *, void *, unsigned int);
extern void gtk_style_context_remove_provider(void *, void *);
extern void gtk_style_context_add_class(void *, const char *);
extern void gtk_style_context_remove_class(void *, const char *);
extern int gtk_style_context_has_class(void *, const char *);
extern void *gtk_css_provider_new(void);
extern int gtk_css_provider_load_from_data(void *, const char *, long, void **);
extern void gtk_widget_set_valign(void *, int);
extern void gtk_label_set_yalign(void *, float);
extern void gtk_widget_set_no_show_all(void *, int);
extern void gtk_widget_hide(void *);
extern void gtk_container_foreach(void *, void (*)(void *, void *), void *);
extern unsigned long g_signal_connect_data(void *, const char *, void (*)(void), void *, void *, unsigned int);
extern void g_signal_handler_disconnect(void *, unsigned long);
extern unsigned int g_signal_lookup(const char *, unsigned long);
extern unsigned long g_signal_add_emission_hook(unsigned int, unsigned int, int (*)(void *, unsigned int, const void *, void *), void *, void *);
extern void g_signal_remove_emission_hook(unsigned int, unsigned long);
extern void *g_value_get_object(const void *);
extern unsigned int g_timeout_add(unsigned int, int (*)(void *), void *);
extern unsigned int g_idle_add(int (*)(void *), void *);
extern int g_source_remove(unsigned int);
extern void g_object_unref(void *);

typedef struct Label Label;
typedef struct { void *root; Label *labels; unsigned int timer, idle, hierarchy_signal; unsigned long hierarchy_hook; } Optical;
struct Label {
    void *widget, *provider;
    Border margin;
    Rectangle ink, logical;
    unsigned long notify, style, destroy;
    int measured, updating;
    Optical *owner;
    Label *next;
};
size_t wbcffi_version = 1;

static void measure(Label *label) {
    if (!label->widget || label->updating) return;
    Rectangle ink, logical;
    void *layout = gtk_label_get_layout(label->widget);
    pango_layout_get_pixel_extents(layout, &ink, &logical);
    /* Use cap height for every label, preserving the common text baseline. */
    void *reference = pango_layout_copy(layout);
    pango_layout_set_text(reference, "H", -1);
    Rectangle cap, cap_logical;
    pango_layout_get_pixel_extents(reference, &cap, &cap_logical);
    g_object_unref(reference);
    ink.y = cap.y;
    ink.height = cap.height;
    logical.y = cap_logical.y;
    logical.height = cap_logical.height;
    if (label->measured && !memcmp(&ink, &label->ink, sizeof(ink)) &&
        !memcmp(&logical, &label->logical, sizeof(logical))) return;
    label->updating = 1;
    label->ink = ink;
    label->logical = logical;
    label->measured = 1;
    Border margin = label->margin;
    if (ink.width > 0) {
        margin.left -= ink.x - logical.x;
        margin.right -= logical.x + logical.width - ink.x - ink.width;
    } else {
        margin.left = margin.right = 0;
    }
    margin.top -= ink.y - logical.y;
    margin.bottom -= logical.y + logical.height - ink.y - ink.height;
    char css[192];
    snprintf(css, sizeof(css), "* { margin: %dpx %dpx %dpx %dpx; }",
             margin.top, margin.right, margin.bottom, margin.left);
    gtk_css_provider_load_from_data(label->provider, css, -1, NULL);
    label->updating = 0;
}

static void text_changed(void *widget, void *property, void *data) {
    (void)widget;
    (void)property;
    measure(data);
}

static void style_changed(void *widget, void *data) {
    (void)widget;
    measure(data);
}

static void label_destroyed(void *widget, void *data) {
    Label *label = data;
    g_signal_handler_disconnect(widget, label->notify);
    g_signal_handler_disconnect(widget, label->style);
    Label **link = &label->owner->labels;
    while (*link != label) link = &(*link)->next;
    *link = label->next;
    g_object_unref(label->provider);
    free(label);
}

static void scan(void *widget, void *data) {
    Optical *optical = data;
    /* A label-only event window clips descenders to the cap-height allocation.
       Input-only event boxes preserve clicks while drawing on the parent window. */
    if (g_type_check_instance_is_a(widget, gtk_event_box_get_type())) {
        void *child = gtk_bin_get_child(widget);
        if (child && g_type_check_instance_is_a(child, gtk_label_get_type()))
            gtk_event_box_set_visible_window(widget, 0);
    }
    if (g_type_check_instance_is_a(widget, gtk_label_get_type())) {
        Label *label;
        for (label = optical->labels; label; label = label->next)
            if (label->widget == widget) break;
        if (!label) {
            label = calloc(1, sizeof(*label));
            label->owner = optical;
            label->widget = widget;
            label->provider = gtk_css_provider_new();
            void *context = gtk_widget_get_style_context(widget);
            /* The network module removes this class while Ethernet has no value. */
            int add_value_class = strcmp(gtk_widget_get_name(widget), "network-value") == 0 &&
                                  !gtk_style_context_has_class(context, "metric-value");
            if (add_value_class)
                gtk_style_context_add_class(context, "metric-value");
            gtk_style_context_get_margin(context, gtk_style_context_get_state(context), &label->margin);
            if (add_value_class)
                gtk_style_context_remove_class(context, "metric-value");
            label->next = optical->labels;
            optical->labels = label;
            gtk_widget_set_valign(widget, 1); /* GTK_ALIGN_START: visible ink starts at the top padding. */
            gtk_label_set_yalign(widget, 0.0f);
            label->notify = g_signal_connect_data(widget, "notify::label", (void (*)(void))text_changed, label, NULL, 0);
            label->style = g_signal_connect_data(widget, "style-updated", (void (*)(void))style_changed, label, NULL, 0);
            label->destroy = g_signal_connect_data(widget, "destroy", (void (*)(void))label_destroyed, label, NULL, 0);
            measure(label);
            gtk_style_context_add_provider(context, label->provider, 801);
        }
        measure(label);
    } else if (strcmp(gtk_widget_get_name(widget), "item-divider") == 0) {
        gtk_widget_set_valign(widget, 1);
    }
    if (g_type_check_instance_is_a(widget, gtk_container_get_type()))
        gtk_container_foreach(widget, scan, optical);
}

static int hierarchy_changed(void *hint, unsigned int count, const void *values, void *data) {
    (void)hint;
    (void)count;
    Optical *optical = data;
    void *widget = g_value_get_object(values);
    void *top = gtk_widget_get_toplevel(optical->root);
    if (top != optical->root && gtk_widget_get_toplevel(widget) == top)
        scan(widget, optical);
    return 1;
}

static int tick(void *data) {
    Optical *optical = data;
    void *top = gtk_widget_get_toplevel(optical->root);
    if (top != optical->root) scan(top, optical);
    return 1;
}

static int ready(void *data) {
    Optical *optical = data;
    optical->idle = 0;
    tick(data);
    return 0;
}

void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    (void)entries;
    (void)count;
    Optical *optical = calloc(1, sizeof(*optical));
    optical->root = info->get_root_widget(info->obj);
    optical->hierarchy_signal = g_signal_lookup("hierarchy-changed", gtk_widget_get_type());
    optical->hierarchy_hook = g_signal_add_emission_hook(optical->hierarchy_signal, 0, hierarchy_changed, optical, NULL);
    gtk_widget_set_no_show_all(optical->root, 1);
    gtk_widget_hide(optical->root);
    optical->idle = g_idle_add(ready, optical);
    optical->timer = g_timeout_add(1000, tick, optical);
    return optical;
}

void wbcffi_deinit(void *data) {
    Optical *optical = data;
    g_signal_remove_emission_hook(optical->hierarchy_signal, optical->hierarchy_hook);
    if (optical->idle) g_source_remove(optical->idle);
    g_source_remove(optical->timer);
    while (optical->labels) {
        Label *label = optical->labels;
        optical->labels = label->next;
        if (label->widget) {
            g_signal_handler_disconnect(label->widget, label->notify);
            g_signal_handler_disconnect(label->widget, label->style);
            g_signal_handler_disconnect(label->widget, label->destroy);
            gtk_style_context_remove_provider(gtk_widget_get_style_context(label->widget), label->provider);
        }
        g_object_unref(label->provider);
        free(label);
    }
    free(optical);
}

void wbcffi_refresh(void *data, int signal) {
    (void)signal;
    tick(data);
}
