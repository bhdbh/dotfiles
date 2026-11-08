/* Input-only shapes keep GTK's clickable windows strictly inside each frame. */
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

typedef struct { void *obj; const char *version; void *(*get_root_widget)(void *); void (*queue_update)(void *); } InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;
typedef struct { int x, y, width, height; } Rectangle;
typedef struct { short left, right, top, bottom; } Border;
extern unsigned long gtk_event_box_get_type(void);
extern unsigned long gtk_button_get_type(void);
extern unsigned long gtk_container_get_type(void);
extern unsigned long gtk_widget_get_type(void);
extern int g_type_check_instance_is_a(void *, unsigned long);
extern void *gtk_widget_get_parent(void *);
extern void *gtk_widget_get_toplevel(void *);
extern const char *gtk_widget_get_name(void *);
extern void *gtk_widget_get_window(void *);
extern void *gtk_button_get_event_window(void *);
extern int gtk_widget_get_realized(void *);
extern int gtk_widget_get_visible(void *);
extern void gtk_widget_get_allocation(void *, Rectangle *);
extern int gtk_widget_translate_coordinates(void *, void *, int, int, int *, int *);
extern void *gtk_widget_get_style_context(void *);
extern unsigned int gtk_style_context_get_state(void *);
extern void gtk_style_context_get_border(void *, unsigned int, Border *);
extern void gtk_widget_set_no_show_all(void *, int);
extern void gtk_widget_hide(void *);
extern void gtk_container_foreach(void *, void (*)(void *, void *), void *);
extern void g_object_unref(void *);
extern void *gdk_window_get_display(void *);
extern void *gdk_cursor_new_for_display(void *, int);
extern void gdk_window_set_cursor(void *, void *);
extern void gdk_window_input_shape_combine_region(void *, const void *, int, int);
extern void *cairo_region_create_rectangle(const Rectangle *);
extern void cairo_region_destroy(void *);
extern unsigned long g_signal_connect_data(void *, const char *, void (*)(void), void *, void *, unsigned int);
extern void g_signal_handler_disconnect(void *, unsigned long);
extern unsigned int g_signal_lookup(const char *, unsigned long);
extern unsigned long g_signal_add_emission_hook(unsigned int, unsigned int, int (*)(void *, unsigned int, const void *, void *), void *, void *);
extern void g_signal_remove_emission_hook(unsigned int, unsigned long);
extern void *g_value_get_object(const void *);
extern unsigned int g_idle_add(int (*)(void *), void *);
extern unsigned int g_timeout_add(unsigned int, int (*)(void *), void *);
extern int g_source_remove(unsigned int);

typedef struct Binding Binding;
typedef struct { void *root; Binding *bindings; unsigned int idle, timer, hierarchy_signal, show_signal; unsigned long hook, show_hook; } Areas;
struct Binding {
    Areas *owner;
    void *widget, *frame;
    int button;
    unsigned long allocate, realize, enter, destroy;
    Binding *next;
};
size_t wbcffi_version = 1;

static void hide_internal_workspace(Areas *areas, void *widget) {
    const char prefix[] = "sway-workspace-__secondary_";
    if (strncmp(gtk_widget_get_name(widget), prefix, sizeof(prefix) - 1) != 0) return;
    void *parent = gtk_widget_get_parent(widget);
    if (!parent || strcmp(gtk_widget_get_name(parent), "workspaces") != 0) return;
    if (gtk_widget_get_toplevel(widget) != gtk_widget_get_toplevel(areas->root)) return;
    if (gtk_widget_get_visible(widget)) gtk_widget_hide(widget);
}

static int workspace_shown(void *hint, unsigned int count, const void *values, void *data) {
    (void)hint; (void)count;
    /* GtkWidget::show runs its default handler before emission hooks. Hide the
     * internal button synchronously, before GTK can paint or allocate it.
     * Waybar explicitly shows each button on every workspace update. */
    hide_internal_workspace(data, g_value_get_object(values));
    return 1;
}

static void shape(Binding *binding) {
    if (!gtk_widget_get_realized(binding->widget)) return;
    void *window = binding->button ? gtk_button_get_event_window(binding->widget) : gtk_widget_get_window(binding->widget);
    if (!window) return;
    Rectangle allocation;
    gtk_widget_get_allocation(binding->frame, &allocation);
    int x = 0, y = 0;
    if (!gtk_widget_translate_coordinates(binding->frame, binding->widget, 0, 0, &x, &y)) return;
    Border border;
    void *style = gtk_widget_get_style_context(binding->frame);
    gtk_style_context_get_border(style, gtk_style_context_get_state(style), &border);
    Rectangle interior = {x + border.left, y + border.top,
        allocation.width - border.left - border.right,
        allocation.height - border.top - border.bottom};
    if (interior.width < 0) interior.width = 0;
    if (interior.height < 0) interior.height = 0;
    void *region = cairo_region_create_rectangle(&interior);
    gdk_window_input_shape_combine_region(window, region, 0, 0);
    cairo_region_destroy(region);
    void *cursor = gdk_cursor_new_for_display(gdk_window_get_display(window), 60); /* GDK_HAND2 */
    gdk_window_set_cursor(window, cursor);
    if (cursor) g_object_unref(cursor);
}

static void allocated(void *widget, void *allocation, void *data) {
    (void)widget; (void)allocation;
    shape(data);
}
static void realized(void *widget, void *data) { (void)widget; shape(data); }
static int entered(void *widget, void *event, void *data) {
    (void)widget; (void)event; shape(data); return 0;
}
static void destroyed(void *widget, void *data) {
    (void)widget;
    Binding *binding = data;
    Binding **link = &binding->owner->bindings;
    while (*link && *link != binding) link = &(*link)->next;
    if (*link) *link = binding->next;
    free(binding);
}
static Binding *bind_area(Areas *areas, void *widget, void *frame, int button) {
    for (Binding *b = areas->bindings; b; b = b->next)
        if (b->widget == widget || b->frame == frame) return NULL;
    Binding *binding = calloc(1, sizeof(*binding));
    binding->owner = areas;
    binding->widget = widget;
    binding->frame = frame;
    binding->button = button;
    binding->next = areas->bindings;
    areas->bindings = binding;
    binding->allocate = g_signal_connect_data(widget, "size-allocate", (void (*)(void))allocated, binding, NULL, 1);
    binding->realize = g_signal_connect_data(widget, "realize", (void (*)(void))realized, binding, NULL, 1);
    binding->enter = g_signal_connect_data(widget, "enter-notify-event", (void (*)(void))entered, binding, NULL, 1);
    binding->destroy = g_signal_connect_data(widget, "destroy", (void (*)(void))destroyed, binding, NULL, 0);
    shape(binding);
    return binding;
}

static void *event_parent(void *widget) {
    for (void *p = gtk_widget_get_parent(widget); p; p = gtk_widget_get_parent(p))
        if (g_type_check_instance_is_a(p, gtk_event_box_get_type())) return p;
    return NULL;
}
static void scan(void *widget, void *data) {
    Areas *areas = data;
    hide_internal_workspace(areas, widget);
    const char *name = gtk_widget_get_name(widget);
    if (g_type_check_instance_is_a(widget, gtk_button_get_type())) {
        void *parent = gtk_widget_get_parent(widget);
        if (parent && strcmp(gtk_widget_get_name(parent), "workspaces") == 0)
            bind_area(areas, widget, widget, 1);
    } else if (strcmp(name, "network") == 0) {
        void *target = event_parent(widget);
        if (target) bind_area(areas, target, widget, 0);
    } else if (strcmp(name, "battery-profile") == 0 || strcmp(name, "battery-charge-toggle") == 0 ||
               strcmp(name, "volume-toggle") == 0 || strcmp(name, "audio-output-toggle") == 0) {
        bind_area(areas, widget, widget, 0);
    }
    if (g_type_check_instance_is_a(widget, gtk_container_get_type()))
        gtk_container_foreach(widget, scan, areas);
}
static int refresh(void *data) {
    Areas *areas = data;
    void *top = gtk_widget_get_toplevel(areas->root);
    if (top != areas->root) {
        scan(top, areas);
        void *window = gtk_widget_get_window(top);
        if (window) {
            void *cursor = gdk_cursor_new_for_display(gdk_window_get_display(window), 68); /* GDK_LEFT_PTR */
            gdk_window_set_cursor(window, cursor);
            if (cursor) g_object_unref(cursor);
        }
    }
    for (Binding *b = areas->bindings; b; b = b->next) shape(b);
    return 1;
}
static int ready(void *data) {
    Areas *areas = data;
    areas->idle = 0;
    refresh(data);
    return 0;
}
static int hierarchy_changed(void *hint, unsigned int count, const void *values, void *data) {
    (void)hint; (void)count; (void)values;
    Areas *areas = data;
    if (!areas->idle) areas->idle = g_idle_add(ready, areas);
    return 1;
}
void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    (void)entries; (void)count;
    Areas *areas = calloc(1, sizeof(*areas));
    areas->root = info->get_root_widget(info->obj);
    areas->show_signal = g_signal_lookup("show", gtk_widget_get_type());
    areas->show_hook = g_signal_add_emission_hook(areas->show_signal, 0, workspace_shown, areas, NULL);
    gtk_widget_set_no_show_all(areas->root, 1);
    gtk_widget_hide(areas->root);
    areas->hierarchy_signal = g_signal_lookup("hierarchy-changed", gtk_widget_get_type());
    areas->hook = g_signal_add_emission_hook(areas->hierarchy_signal, 0, hierarchy_changed, areas, NULL);
    areas->idle = g_idle_add(ready, areas);
    areas->timer = g_timeout_add(1000, refresh, areas);
    return areas;
}
void wbcffi_deinit(void *data) {
    Areas *areas = data;
    g_signal_remove_emission_hook(areas->show_signal, areas->show_hook);
    g_signal_remove_emission_hook(areas->hierarchy_signal, areas->hook);
    if (areas->idle) g_source_remove(areas->idle);
    g_source_remove(areas->timer);
    while (areas->bindings) {
        Binding *b = areas->bindings;
        areas->bindings = b->next;
        unsigned long handlers[] = {b->allocate, b->realize, b->enter, b->destroy};
        for (size_t i = 0; i < sizeof(handlers) / sizeof(*handlers); ++i)
            if (handlers[i]) g_signal_handler_disconnect(b->widget, handlers[i]);
        free(b);
    }
    free(areas);
}
void wbcffi_refresh(void *data, int signal) { (void)signal; refresh(data); }
