#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "virtual-keyboard.h"

static struct wl_seat *seat;
static struct zwp_virtual_keyboard_manager_v1 *manager;
static unsigned format;

static void global(void *data, struct wl_registry *registry, uint32_t name,
        const char *interface, uint32_t version) {
    if (!strcmp(interface, "wl_seat"))
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
    if (!strcmp(interface, "zwp_virtual_keyboard_manager_v1"))
        manager = wl_registry_bind(registry, name,
            &zwp_virtual_keyboard_manager_v1_interface, 1);
}
static void removed(void *d, struct wl_registry *r, uint32_t name) {}
static void keymap(void *d, struct wl_keyboard *k, uint32_t f, int32_t fd, uint32_t size) {
    format = f;
    close(fd);
}
static void enter(void *d, struct wl_keyboard *k, uint32_t s, struct wl_surface *w, struct wl_array *a) {}
static void leave(void *d, struct wl_keyboard *k, uint32_t s, struct wl_surface *w) {}
static void key(void *d, struct wl_keyboard *k, uint32_t s, uint32_t t, uint32_t c, uint32_t v) {}
static void modifiers(void *d, struct wl_keyboard *k, uint32_t s, uint32_t a, uint32_t b, uint32_t c, uint32_t g) {}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) return 1;
    struct wl_registry *registry = wl_display_get_registry(display);
    const struct wl_registry_listener listener = {global, removed};
    wl_registry_add_listener(registry, &listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !seat || !manager) return 2;
    if (argc > 1 && !strcmp(argv[1], "probe")) {
        struct wl_keyboard *keyboard = wl_seat_get_keyboard(seat);
        const struct wl_keyboard_listener kl = {.keymap=keymap, .enter=enter,
            .leave=leave, .key=key, .modifiers=modifiers};
        wl_keyboard_add_listener(keyboard, &kl, NULL);
        if (wl_display_roundtrip(display) < 0) return 3;
        printf("%u\n", format);
    } else if (argc > 1 && !strcmp(argv[1], "empty")) {
        struct zwp_virtual_keyboard_v1 *keyboard =
            zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager, seat);
        if (wl_display_roundtrip(display) < 0) return 8;
        puts("READY");
        char buffer[16];
        (void)fgets(buffer, sizeof(buffer), stdin);
        zwp_virtual_keyboard_v1_destroy(keyboard);
        if (wl_display_roundtrip(display) < 0) return 9;
    } else {
        struct xkb_context *context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
        struct xkb_keymap *map = xkb_keymap_new_from_names(context, NULL, XKB_KEYMAP_COMPILE_NO_FLAGS);
        if (!map) return 4;
        char *text = xkb_keymap_get_as_string(map, XKB_KEYMAP_FORMAT_TEXT_V1);
        size_t size = strlen(text) + 1;
        int fd = memfd_create("test-keymap", MFD_CLOEXEC);
        if (fd < 0 || write(fd, text, size) != (ssize_t)size) return 5;
        struct zwp_virtual_keyboard_v1 *keyboard =
            zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(manager, seat);
        zwp_virtual_keyboard_v1_keymap(keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, size);
        zwp_virtual_keyboard_v1_key(keyboard, 0, 42, WL_KEYBOARD_KEY_STATE_PRESSED);
        zwp_virtual_keyboard_v1_key(keyboard, 1, 42, WL_KEYBOARD_KEY_STATE_RELEASED);
        if (wl_display_roundtrip(display) < 0) return 6;
        close(fd);
        free(text);
        xkb_keymap_unref(map);
        xkb_context_unref(context);
        puts("READY");
        char buffer[16];
        (void)fgets(buffer, sizeof(buffer), stdin);
        zwp_virtual_keyboard_v1_destroy(keyboard);
        if (wl_display_roundtrip(display) < 0) return 7;
    }
    wl_display_disconnect(display);
    return 0;
}
