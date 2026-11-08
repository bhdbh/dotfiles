/* Power down the console's active DRM connectors while waiting for a key.
 * Only called after the lock VT is secured and Sway has released DRM master.
 * No compositor IPC, environment paths or external programs run as root. */
#define _GNU_SOURCE
#include "physlock.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/stat.h>
#include <syslog.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#define MAX_CARDS 64
#define MAX_CONNECTORS 32

struct saved_connector {
	uint32_t connector, property;
	uint64_t value;
};

struct saved_card {
	int fd, count;
	struct saved_connector connectors[MAX_CONNECTORS];
};

static struct saved_card cards[MAX_CARDS];
static int card_count;

void display_power_restore(void) {
	int i, j;
	for (i = 0; i < card_count; i++) {
		struct saved_card *card = &cards[i];
		for (j = 0; j < card->count; j++) {
			struct saved_connector *saved = &card->connectors[j];
			if (drmModeConnectorSetProperty(card->fd, saved->connector,
			                               saved->property, saved->value) < 0)
				syslog(LOG_ERR, "Could not restore DRM connector %u: %s",
				       saved->connector, strerror(errno));
		}
		drmDropMaster(card->fd);
		close(card->fd);
	}
	if (card_count)
		syslog(LOG_NOTICE, "Restored monitor power and released DRM devices");
	card_count = 0;
}

int display_power_down(void) {
	int number, total = 0;
	/* Do not overwrite the saved state if called twice before waking. */
	if (card_count)
		return 0;
	for (number = 0; number < MAX_CARDS; number++) {
		char path[64];
		int device, i;
		struct stat info;
		drmModeRes *resources;
		struct saved_card *card = &cards[card_count];

		snprintf(path, sizeof(path), "/dev/dri/card%d", number);
		device = open(path, O_RDWR | O_CLOEXEC | O_NOFOLLOW);
		if (device < 0)
			continue;
		if (fstat(device, &info) < 0 || !S_ISCHR(info.st_mode) ||
		    drmSetMaster(device) < 0) {
			syslog(LOG_WARNING, "Could not acquire console DRM device %s: %s",
			       path, strerror(errno));
			close(device);
			continue;
		}
		resources = drmModeGetResources(device);
		if (!resources) {
			drmDropMaster(device);
			close(device);
			continue;
		}
		card->fd = device;
		card->count = 0;
		for (i = 0; i < resources->count_connectors && card->count < MAX_CONNECTORS; i++) {
			drmModeConnector *connector = drmModeGetConnectorCurrent(device, resources->connectors[i]);
			drmModeEncoder *encoder;
			uint32_t property = 0;
			uint64_t value = DRM_MODE_DPMS_OFF;
			int j, desktop = 1, enabled;
			if (!connector)
				continue;
			if (connector->connection != DRM_MODE_CONNECTED || !connector->encoder_id) {
				drmModeFreeConnector(connector);
				continue;
			}
			encoder = drmModeGetEncoder(device, connector->encoder_id);
			enabled = encoder && encoder->crtc_id;
			if (encoder)
				drmModeFreeEncoder(encoder);
			for (j = 0; enabled && j < connector->count_props; j++) {
				drmModePropertyRes *p = drmModeGetProperty(device, connector->props[j]);
				if (!p)
					continue;
				if (!strcmp(p->name, "DPMS")) {
					property = p->prop_id;
					value = connector->prop_values[j];
				} else if (!strcmp(p->name, "non-desktop") && connector->prop_values[j]) {
					desktop = 0;
				}
				drmModeFreeProperty(p);
			}
			if (desktop && property && value == DRM_MODE_DPMS_ON) {
				if (drmModeConnectorSetProperty(device, connector->connector_id,
				                               property, DRM_MODE_DPMS_OFF) == 0) {
					card->connectors[card->count++] = (struct saved_connector){
						connector->connector_id, property, value };
					total++;
					syslog(LOG_NOTICE, "Powered off DRM connector %u on %s",
					       connector->connector_id, path);
				} else {
					syslog(LOG_WARNING, "Could not power off DRM connector %u: %s",
					       connector->connector_id, strerror(errno));
				}
			}
			drmModeFreeConnector(connector);
		}
		drmModeFreeResources(resources);
		if (card->count) {
			card_count++;
		} else {
			drmDropMaster(device);
			close(device);
		}
	}
	if (!total)
		syslog(LOG_WARNING, "No active DRM connector was powered off; console blanking only");
	return total;
}
