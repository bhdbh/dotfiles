// Test input client; connect only to the isolated compositor's WAYLAND_DISPLAY.
#define _POSIX_C_SOURCE 200809L
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <wayland-client.h>
#include "virtual-pointer-client-protocol.h"

static struct zwlr_virtual_pointer_manager_v1 *manager;

static void global(void *data, struct wl_registry *registry, uint32_t name,
        const char *interface, uint32_t version) {
    if (strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name) == 0) {
        manager = wl_registry_bind(registry, name,
            &zwlr_virtual_pointer_manager_v1_interface, version < 2 ? version : 2);
    }
}

static void global_remove(void *data, struct wl_registry *registry, uint32_t name) {
}

static uint32_t milliseconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

int main(int argc, char **argv) {
    if (argc != 3) return 1;
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 2;
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener listener = {global, global_remove};
    wl_registry_add_listener(registry, &listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !manager) return 3;
    struct zwlr_virtual_pointer_v1 *pointer =
        zwlr_virtual_pointer_manager_v1_create_virtual_pointer(manager, NULL);
    wl_display_roundtrip(display);
    setvbuf(stdout, NULL, _IOLBF, 0);
    puts("READY");
    char action;
    unsigned int first, second;
    while (scanf(" %c %u %u", &action, &first, &second) == 3) {
        if (action == 'm') {
            zwlr_virtual_pointer_v1_motion_absolute(pointer, milliseconds(),
                first, second, atoi(argv[1]), atoi(argv[2]));
        } else if (action == 'b') {
            zwlr_virtual_pointer_v1_button(pointer, milliseconds(), first, second);
        } else {
            return 4;
        }
        zwlr_virtual_pointer_v1_frame(pointer);
        if (wl_display_roundtrip(display) < 0) return 5;
        puts("OK");
    }
    zwlr_virtual_pointer_v1_destroy(pointer);
    zwlr_virtual_pointer_manager_v1_destroy(manager);
    wl_registry_destroy(registry);
    wl_display_flush(display);
    wl_display_disconnect(display);
    return 0;
}
