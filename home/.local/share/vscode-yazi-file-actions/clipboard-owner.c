/* Restore all clipboard MIME formats without creating a window or taking focus. */
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <wayland-client.h>
#include "ext-data-control-v1.h"

struct entry { char *mime; unsigned char *bytes; size_t length; };
struct state {
    struct ext_data_control_manager_v1 *manager;
    struct wl_seat *seat;
    struct entry *entries;
    size_t count;
    int running;
};
static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version) {
    (void)version;
    struct state *state = data;
    if (!strcmp(interface, "ext_data_control_manager_v1"))
        state->manager = wl_registry_bind(registry, name, &ext_data_control_manager_v1_interface, 1);
    else if (!strcmp(interface, "wl_seat") && !state->seat)
        state->seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
}
static void removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static void send_data(void *data, struct ext_data_control_source_v1 *source,
                      const char *mime, int32_t fd) {
    (void)source;
    struct state *state = data;
    for (size_t i = 0; i < state->count; ++i) {
        struct entry *entry = &state->entries[i];
        if (strcmp(entry->mime, mime)) continue;
        size_t offset = 0;
        while (offset < entry->length) {
            ssize_t written = write(fd, entry->bytes + offset, entry->length - offset);
            if (written < 0 && errno == EINTR) continue;
            if (written <= 0) break;
            offset += (size_t)written;
        }
        break;
    }
    close(fd);
}
static void cancelled(void *data, struct ext_data_control_source_v1 *source) {
    (void)source;
    ((struct state *)data)->running = 0;
}
static void offered(void *data, struct ext_data_control_offer_v1 *offer, const char *mime) {
    (void)data; (void)offer; (void)mime;
}
static const struct ext_data_control_offer_v1_listener offer_listener = { offered };
static void data_offer(void *data, struct ext_data_control_device_v1 *device,
                       struct ext_data_control_offer_v1 *offer) {
    (void)data; (void)device;
    ext_data_control_offer_v1_add_listener(offer, &offer_listener, NULL);
}
static void selection(void *data, struct ext_data_control_device_v1 *device,
                      struct ext_data_control_offer_v1 *offer) {
    (void)data; (void)device;
    if (offer) ext_data_control_offer_v1_destroy(offer);
}
static void finished(void *data, struct ext_data_control_device_v1 *device) {
    (void)device;
    ((struct state *)data)->running = 0;
}
static int load(struct state *state, const char *directory) {
    char *filename = NULL, *mime = NULL;
    size_t capacity = 0;
    if (asprintf(&filename, "%s/types", directory) < 0) return -1;
    FILE *types = fopen(filename, "r");
    if (!types) { free(filename); return -1; }
    unlink(filename);
    free(filename);
    while (getline(&mime, &capacity, types) > 0) {
        mime[strcspn(mime, "\n")] = 0;
        struct entry entry = { .mime = strdup(mime) };
        if (asprintf(&filename, "%s/%zu", directory, state->count) < 0) return -1;
        FILE *content = fopen(filename, "rb");
        if (!content) return -1;
        unlink(filename);
        free(filename);
        if (fseek(content, 0, SEEK_END)) return -1;
        long length = ftell(content);
        if (length < 0 || fseek(content, 0, SEEK_SET)) return -1;
        entry.length = (size_t)length;
        entry.bytes = malloc(entry.length ? entry.length : 1);
        if (!entry.mime || !entry.bytes || fread(entry.bytes, 1, entry.length, content) != entry.length) return -1;
        fclose(content);
        struct entry *entries = realloc(state->entries, (state->count + 1) * sizeof(entry));
        if (!entries) return -1;
        state->entries = entries;
        state->entries[state->count++] = entry;
    }
    free(mime);
    fclose(types);
    rmdir(directory);
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 1;
    signal(SIGPIPE, SIG_IGN);
    struct state state = { .running = 1 };
    if (load(&state, argv[1])) { fputs("Could not preserve the clipboard.\n", stderr); return 1; }
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener registry_listener = { global, removed };
    wl_registry_add_listener(registry, &registry_listener, &state);
    if (wl_display_roundtrip(display) < 0 || !state.manager || !state.seat) {
        fputs("The compositor does not support ext-data-control.\n", stderr);
        return 1;
    }
    struct ext_data_control_device_v1 *device = ext_data_control_manager_v1_get_data_device(state.manager, state.seat);
    const struct ext_data_control_device_v1_listener device_listener = {
        .data_offer = data_offer, .selection = selection,
        .finished = finished, .primary_selection = selection,
    };
    ext_data_control_device_v1_add_listener(device, &device_listener, &state);
    if (wl_display_roundtrip(display) < 0) return 1;
    puts("ready"); fflush(stdout);
    char request[32];
    /* An extension-host exit also restores the snapshot rather than dropping it. */
    if (fgets(request, sizeof(request), stdin) && strcmp(request, "restore\n")) return 0;
    struct ext_data_control_source_v1 *source = NULL;
    if (state.count) {
        source = ext_data_control_manager_v1_create_data_source(state.manager);
        const struct ext_data_control_source_v1_listener listener = { send_data, cancelled };
        ext_data_control_source_v1_add_listener(source, &listener, &state);
        for (size_t i = 0; i < state.count; ++i)
            ext_data_control_source_v1_offer(source, state.entries[i].mime);
        ext_data_control_device_v1_set_selection(device, source);
        if (wl_display_roundtrip(display) < 0) return 1;
        puts("restored"); fflush(stdout);
        freopen("/dev/null", "w", stdout);
        freopen("/dev/null", "w", stderr);
        while (state.running && wl_display_dispatch(display) >= 0) {}
    } else {
        ext_data_control_device_v1_set_selection(device, NULL);
        if (wl_display_roundtrip(display) < 0) return 1;
        puts("restored"); fflush(stdout);
    }
    wl_display_disconnect(display);
    for (size_t i = 0; i < state.count; ++i) { free(state.entries[i].mime); free(state.entries[i].bytes); }
    free(state.entries);
    return 0;
}
