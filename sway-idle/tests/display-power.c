/* No device is opened: all DRM and device operations are simulated. */
#define _GNU_SOURCE
#include <assert.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <syslog.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

static int fake_open(const char *, int, ...);
static int fake_close(int);
static int fake_stat(int, struct stat *);
static int fake_master(int);
static int fake_drop(int);
static void fake_log(int, const char *, ...);
static drmModeRes *fake_resources(int);
static drmModeConnector *fake_connector(int, uint32_t);
static drmModeEncoder *fake_encoder(int, uint32_t);
static drmModePropertyRes *fake_property(int, uint32_t);
static int fake_set(int, uint32_t, uint32_t, uint64_t);
static void fake_free(void *);
#define open fake_open
#define close fake_close
#define fstat fake_stat
#define syslog fake_log
#define drmSetMaster fake_master
#define drmDropMaster fake_drop
#define drmModeGetResources fake_resources
#define drmModeGetConnectorCurrent fake_connector
#define drmModeGetEncoder fake_encoder
#define drmModeGetProperty fake_property
#define drmModeConnectorSetProperty fake_set
#define drmModeFreeResources fake_free
#define drmModeFreeConnector fake_free
#define drmModeFreeEncoder fake_free
#define drmModeFreeProperty fake_free
#include "../source/display.c"
#undef open
#undef close
#undef fstat
#undef syslog

static drmModeRes resources;
static drmModeConnector connectors[6];
static drmModeEncoder encoders[6];
static drmModePropertyRes properties[2];
static uint32_t ids[6], property_ids[2];
static uint64_t values[6][2];
static int acquired, dropped, closed, fail_master, fail_off, fail_on, no_resources;
static int changes, changed_ids[16];
static uint64_t changed_values[16];

static int fake_open(const char *path, int flags, ...) {
    assert((flags & (O_NOFOLLOW | O_CLOEXEC)) == (O_NOFOLLOW | O_CLOEXEC));
    if (!strcmp(path, "/dev/dri/card1")) return 90;
    errno = ENOENT;
    return -1;
}
static int fake_close(int fd) { assert(fd == 90); closed++; return 0; }
static int fake_stat(int fd, struct stat *info) {
    assert(fd == 90); info->st_mode = S_IFCHR; return 0;
}
static int fake_master(int fd) {
    assert(fd == 90);
    if (fail_master) { errno = EBUSY; return -1; }
    acquired++; return 0;
}
static int fake_drop(int fd) { assert(fd == 90); dropped++; return 0; }
static void fake_log(int priority, const char *text, ...) { (void)priority; (void)text; }
static drmModeRes *fake_resources(int fd) { assert(fd == 90); return no_resources ? NULL : &resources; }
static drmModeConnector *fake_connector(int fd, uint32_t id) {
    assert(fd == 90 && id >= 1 && id <= 6); return &connectors[id-1];
}
static drmModeEncoder *fake_encoder(int fd, uint32_t id) {
    assert(fd == 90 && id >= 1 && id <= 6); return &encoders[id-1];
}
static drmModePropertyRes *fake_property(int fd, uint32_t id) {
    assert(fd == 90 && id >= 1 && id <= 2); return &properties[id-1];
}
static int fake_set(int fd, uint32_t id, uint32_t property, uint64_t value) {
    assert(fd == 90 && property == 1);
    changed_ids[changes] = (int)id;
    changed_values[changes++] = value;
    if ((int)id == fail_off && value == DRM_MODE_DPMS_OFF) { errno=EIO; return -1; }
    if ((int)id == fail_on && value == DRM_MODE_DPMS_ON) { errno=EIO; return -1; }
    values[id-1][0] = value;
    return 0;
}
static void fake_free(void *pointer) { assert(pointer); }

static void reset(void) {
    assert(card_count == 0);
    acquired = dropped = closed = fail_master = fail_off = fail_on = no_resources = changes = 0;
    memset(connectors, 0, sizeof(connectors));
    memset(encoders, 0, sizeof(encoders));
    memset(values, 0, sizeof(values));
    resources = (drmModeRes){ .count_connectors=6, .connectors=ids };
    property_ids[0]=1; property_ids[1]=2;
    properties[0].prop_id=1; strcpy(properties[0].name,"DPMS");
    properties[1].prop_id=2; strcpy(properties[1].name,"non-desktop");
    for (int i=0; i<6; i++) {
        ids[i]=(uint32_t)i+1;
        connectors[i]=(drmModeConnector){ .connector_id=ids[i], .encoder_id=ids[i],
            .connection=DRM_MODE_CONNECTED, .count_props=2, .props=property_ids,
            .prop_values=values[i] };
        encoders[i].crtc_id=ids[i];
    }
    /* Two active desktop monitors, a disconnected output, an output disabled
     * by the lid, an already powered-off output, and a non-desktop device. */
    connectors[2].connection=DRM_MODE_DISCONNECTED;
    encoders[3].crtc_id=0;
    values[4][0]=DRM_MODE_DPMS_OFF;
    values[5][1]=1;
}

int main(void) {
    reset();
    assert(display_power_down()==2 && changes==2 && card_count==1);
    assert(changed_ids[0]==1 && changed_ids[1]==2);
    assert(changed_values[0]==DRM_MODE_DPMS_OFF && changed_values[1]==DRM_MODE_DPMS_OFF);
    assert(display_power_down()==0 && changes==2); /* Preserve the snapshot. */
    display_power_restore();
    assert(changes==4 && changed_ids[2]==1 && changed_ids[3]==2);
    assert(changed_values[2]==DRM_MODE_DPMS_ON && changed_values[3]==DRM_MODE_DPMS_ON);
    assert(acquired==1 && dropped==1 && closed==1 && !card_count);
    display_power_restore();
    assert(changes==4 && closed==1); /* Cleanup is idempotent. */

    reset(); fail_off=1;
    assert(display_power_down()==1 && changes==2);
    display_power_restore();
    assert(changes==3 && changed_ids[2]==2 && changed_values[2]==DRM_MODE_DPMS_ON);

    reset(); fail_on=1;
    assert(display_power_down()==2);
    display_power_restore();
    assert(changes==4 && changed_ids[3]==2 && dropped==1 && closed==1);

    reset(); fail_master=1;
    assert(display_power_down()==0 && changes==0 && acquired==0 && closed==1);
    display_power_restore();
    assert(dropped==0); /* Never steal a compositor's DRM master. */

    reset(); no_resources=1;
    assert(display_power_down()==0 && acquired==1 && dropped==1 && closed==1);
    display_power_restore();
    puts("DRM power-down/restore checks passed: multiple outputs, disabled outputs, partial failures, master ownership and cleanup");
    return 0;
}
