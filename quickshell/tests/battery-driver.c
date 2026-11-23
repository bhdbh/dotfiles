/* Exercise the real driver's WMI parsers/stores in userspace, never hardware. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/mman.h>
#include <unistd.h>

typedef uint8_t u8;
typedef int8_t s8;
typedef uint32_t u32;
typedef int acpi_status;
#define __packed __attribute__((packed))
#define __init
#define __exit
#define AE_OK 0
#define AE_ERROR 1
#define ACPI_TYPE_BUFFER 3
#define ACPI_ALLOCATE_BUFFER ((size_t)-1)
#define ACPI_FAILURE(status) ((status) != AE_OK)
#define U16_MAX UINT16_MAX
#define KERNEL_VERSION(a, b, c) (((a) << 16) + ((b) << 8) + (c))
#define LINUX_VERSION_CODE KERNEL_VERSION(7, 3, 3)
#define S_IRUSR 0400
#define S_IWUSR 0200
#define S_IRGRP 0040
#define S_IWGRP 0020
#define MODULE_DESCRIPTION(...)
#define MODULE_LICENSE(...)
#define MODULE_AUTHOR(...)
#define MODULE_ALIAS(...)
#define MODULE_PARM_DESC(...)
#define module_param(...)
#define module_init(...)
#define module_exit(...)
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define kfree fake_kfree
#define sysfs_emit sprintf
struct acpi_buffer { size_t length; void *pointer; };
union acpi_object {
    unsigned type;
    struct { unsigned type, length; u8 *pointer; } buffer;
};
struct device_driver { const char *name; void **groups; };
struct attribute { int unused; };
struct driver_attribute { struct attribute attr; };
struct wmi_device_id { const char *guid_string; };
struct wmi_driver { struct device_driver driver; };
#define DRIVER_ATTR_RO(name) struct driver_attribute driver_attr_##name
#define DRIVER_ATTR_RW(name) struct driver_attribute driver_attr_##name
#define ATTRIBUTE_GROUPS(name) static void *name##_groups[] = {name##_attrs}
static bool wmi_has_guid(const char *guid) { return guid != NULL; }
static int wmi_driver_register(struct wmi_driver *driver) { return 0; }
static void wmi_driver_unregister(struct wmi_driver *driver) {}
static u32 get_unaligned_le32(const u8 *p) {
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}
static int kstrtobool(const char *s, bool *result) {
    if (*s != '0' && *s != '1') return -EINVAL;
    *result = *s == '1';
    return 0;
}

struct allocation { union acpi_object obj; void *mapping; size_t size; };
static void fake_kfree(void *object) {
    struct allocation *allocation = object;
    assert(munmap(allocation->mapping, allocation->size) == 0);
    free(allocation);
}

struct response { acpi_status status; unsigned type, length; bool null_buffer; u8 bytes[8]; };
static struct response replies[4];
static unsigned next_reply, reply_count;
static void reset(void) { next_reply = reply_count = 0; }
static void enqueue(unsigned length, acpi_status status) {
    assert(reply_count < 4);
    replies[reply_count++] = (struct response){.status = status, .type = ACPI_TYPE_BUFFER, .length = length,
                                             .bytes = {3, 0, 0, 1, 0, 0, 0, 0}};
}
static acpi_status wmi_evaluate_method(const char *guid, int instance, int method,
                                     struct acpi_buffer *input, struct acpi_buffer *output) {
    assert(next_reply < reply_count);
    struct response *r = &replies[next_reply++];
    if (ACPI_FAILURE(r->status)) return r->status;
    /* Put each buffer against an unreadable page; a short-buffer read faults
     * even on machines that do not have sanitizer runtime libraries. */
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    struct allocation *allocation = calloc(1, sizeof(*allocation));
    assert(allocation);
    allocation->size = page * 2;
    allocation->mapping = mmap(NULL, allocation->size, PROT_READ | PROT_WRITE,
                               MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    assert(allocation->mapping != MAP_FAILED);
    assert(mprotect((char *)allocation->mapping + page, page, PROT_NONE) == 0);
    union acpi_object *obj = &allocation->obj;
    obj->type = r->type;
    obj->buffer.length = r->length;
    obj->buffer.pointer = r->null_buffer ? NULL : (u8 *)allocation->mapping + page - r->length;
    if (obj->buffer.pointer) memcpy(obj->buffer.pointer, r->bytes, r->length);
    output->pointer = obj;
    return AE_OK;
}

#include ACER_DRIVER_SOURCE

int main(void) {
    struct battery_info result;
    for (unsigned n = 0; n < 8; ++n) {
        reset(); enqueue(n, AE_OK);
        result = (struct battery_info){-7, -7};
        assert(ACPI_FAILURE(get_battery_health_control_status(&result)));
        assert(result.health_mode == -7 && result.calibration_mode == -7);
    }
    reset(); enqueue(8, AE_OK);
    assert(get_battery_health_control_status(&result) == AE_OK);
    assert(result.health_mode == 1 && result.calibration_mode == 0);
    reset(); enqueue(8, AE_OK); replies[0].type = 99;
    assert(ACPI_FAILURE(get_battery_health_control_status(&result)));
    reset(); enqueue(8, AE_OK); replies[0].null_buffer = true;
    assert(ACPI_FAILURE(get_battery_health_control_status(&result)));

    for (unsigned n = 0; n <= 8; ++n) {
        reset(); enqueue(n, AE_OK);
        assert(ACPI_FAILURE(set_battery_health_control(HEALTH_MODE, true)) == (n != 4));
    }
    reset(); enqueue(4, AE_OK); replies[0].type = 99;
    assert(ACPI_FAILURE(set_battery_health_control(HEALTH_MODE, true)));
    reset(); enqueue(4, AE_OK); replies[0].null_buffer = true;
    assert(ACPI_FAILURE(set_battery_health_control(HEALTH_MODE, true)));

    battery_status = (struct battery_info){0, 0};
    reset(); enqueue(0, AE_ERROR);
    assert(health_mode_store(NULL, "1", 1) == -EIO);
    assert(next_reply == 1 && battery_status.health_mode == 0);
    reset(); enqueue(4, AE_OK); enqueue(0, AE_ERROR);
    assert(health_mode_store(NULL, "1", 1) == -EIO);
    assert(battery_status.health_mode == 0);
    reset(); enqueue(4, AE_OK); enqueue(8, AE_OK);
    assert(health_mode_store(NULL, "1", 1) == 1);
    assert(battery_status.health_mode == 1);

    reset(); enqueue(0, AE_ERROR);
    assert(calibration_mode_store(NULL, "1", 1) == -EIO);
    reset(); enqueue(4, AE_OK); enqueue(0, AE_ERROR);
    assert(calibration_mode_store(NULL, "1", 1) == -EIO);
    reset(); enqueue(4, AE_OK); enqueue(8, AE_OK); replies[1].bytes[4] = 1;
    assert(calibration_mode_store(NULL, "1", 1) == 1);
    assert(battery_status.calibration_mode == 1);
    reset();
    assert(health_mode_store(NULL, "bad", 3) == -EINVAL);
    assert(next_reply == 0);
    puts("PASS actual driver: short/type/null buffers, 8-byte query, 4-byte set, errors and both stores");
}
