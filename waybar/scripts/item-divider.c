/* A native separator without any input interception. */
#include <stddef.h>
#include <stdlib.h>

typedef struct {
    void *obj;
    const char *version;
    void *(*get_root_widget)(void *);
    void (*queue_update)(void *);
} InitInfo;
typedef struct { const char *key; const char *value; } ConfigEntry;
typedef struct { void *root; } Divider;

extern void *gtk_separator_new(int);
extern void gtk_container_add(void *, void *);
extern void gtk_widget_set_name(void *, const char *);
extern void gtk_widget_set_has_tooltip(void *, int);
extern void gtk_widget_show_all(void *);

size_t wbcffi_version = 1;

void *wbcffi_init(const InitInfo *info, const ConfigEntry *entries, size_t count) {
    (void)entries;
    (void)count;
    Divider *divider = calloc(1, sizeof(*divider));
    divider->root = info->get_root_widget(info->obj);
    void *separator = gtk_separator_new(1);
    gtk_widget_set_name(separator, "item-divider");
    gtk_widget_set_has_tooltip(divider->root, 0);
    gtk_container_add(divider->root, separator);
    gtk_widget_show_all(divider->root);
    return divider;
}

void wbcffi_deinit(void *data) {
    Divider *divider = data;
    free(divider);
}
