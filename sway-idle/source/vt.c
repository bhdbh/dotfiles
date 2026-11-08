/* Copyright 2013 Bert Muennich
 *
 * This file is part of physlock.
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
 */

#include "physlock.h"
#include "config.h"

#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <linux/vt.h>
#include <linux/tiocl.h>
#include <sys/stat.h>
#include <sys/ioctl.h>
#include <errno.h>
#include <time.h>
#include <syslog.h>

static int fd = -1;
static char filename[1024];

static void clear_vt(vt_t *vt) {
	/* Clear visible contents and scrollback before exposing a reused VT. */
	fputs("\033[H\033[2J\033[3J", vt->ios);
	fflush(vt->ios);
}

static long long monotonic_ms(void) {
	struct timespec now;
	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* A VT switch can be lost during resume. Reissue it instead of waiting
 * indefinitely in VT_WAITACTIVE, and leave recovery keys usable on failure. */
static int activate_vt(int nr) {
	struct vt_stat state;
	const struct timespec pause = { 0, 50000000 };
	long long start = monotonic_ms(), now, requested = -1;
	if (start < 0)
		return -1;
	for (;;) {
		now = monotonic_ms();
		if (now < 0)
			return -1;
		if (now - start >= 5000) {
			errno = ETIMEDOUT;
			return -1;
		}
		if (ioctl(fd, VT_GETSTATE, &state) == 0) {
			if (state.v_active == nr)
				return 0;
		} else if (errno != EINTR) {
			return -1;
		}
		if (requested < 0 || now - requested >= 500) {
			if (ioctl(fd, VT_ACTIVATE, nr) < 0 && errno != EINTR)
				return -1;
			requested = now;
		}
		nanosleep(&pause, NULL);
	}
}

void vt_init() {
	while ((fd = open(CONSOLE_DEVICE, O_RDWR)) == -1 && errno == EINTR);
	if (fd < 0)
		error(EXIT_FAILURE, errno, "%s", CONSOLE_DEVICE);
}

CLEANUP void vt_destroy() {
	if (fd >= 0) {
		close(fd);
		fd = -1;
	}
}

void vt_get_current(int *nr, uid_t *owner) {
	int ret;
	struct stat fstat;
	struct vt_stat vtstat;

	while ((ret = ioctl(fd, VT_GETSTATE, &vtstat)) == -1 && errno == EINTR);
	if (ret == -1)
		error(EXIT_FAILURE, errno, "%s: VT_GETSTATE", CONSOLE_DEVICE);
	*nr = vtstat.v_active;
	snprintf(filename, sizeof(filename), "%s%d", TTY_DEVICE_BASE, *nr);
	*owner = stat(filename, &fstat) == 0 ? fstat.st_uid : (uid_t)-1;
}

CLEANUP int vt_lock_switch(int set) {
	int ret;
	
	if (set) {
		while ((ret = ioctl(fd, VT_LOCKSWITCH, 1)) == -1 && errno == EINTR);
		if (ret == -1)
			error(0, errno, "%s: VT_LOCKSWITCH", CONSOLE_DEVICE);
	} else {
		while ((ret = ioctl(fd, VT_UNLOCKSWITCH, 1)) == -1 && errno == EINTR);
		if (ret == -1)
			error(0, errno, "%s: VT_UNLOCKSWITCH", CONSOLE_DEVICE);
	}
	return ret;
}

void vt_acquire(vt_t *vt) {
	int ret;

	vt->nr = -1;
	vt->ios = NULL;
	vt->fd = -1;

	while ((ret = ioctl(fd, VT_OPENQRY, &vt->nr)) == -1 && errno == EINTR);
	if (ret == -1)
		error(EXIT_FAILURE, errno, "%s: VT_OPENQRY", CONSOLE_DEVICE);

	snprintf(filename, sizeof(filename), "%s%d", TTY_DEVICE_BASE, vt->nr);
	while ((vt->ios = fopen(filename, "r+")) == NULL && errno == EINTR);
	if (vt->ios == NULL)
		error(EXIT_FAILURE, errno, "%s", filename);
	vt->fd = fileno(vt->ios);

	clear_vt(vt);
	if (activate_vt(vt->nr) == -1)
		error(EXIT_FAILURE, errno, "%s: activate lock VT", CONSOLE_DEVICE);

	if (tcgetattr(vt->fd, &vt->term) < 0)
		error(EXIT_FAILURE, errno, "read console input mode");
	memcpy(&vt->term_orig, &vt->term, sizeof(vt->term_orig));
}

void vt_reopen(vt_t *vt) {
	FILE *ios = vt->ios;

	vt->fd = -1;
	while ((vt->ios = freopen(filename, "r+", ios)) == NULL && errno == EINTR);
	if (vt->ios == NULL)
		error(EXIT_FAILURE, errno, "%s", filename);
	vt->fd = fileno(vt->ios);
}

CLEANUP int vt_release(vt_t *vt, int nr) {
	int ret;

	syslog(LOG_NOTICE, "Returning from tty%d to tty%d", vt->nr, nr);
	if (activate_vt(nr) == -1) {
		int saved_errno = errno;
		syslog(LOG_ERR, "Could not return to tty%d: %s; VT switching remains unlocked",
		       nr, strerror(saved_errno));
		errno = saved_errno;
		return -1;
	}
	syslog(LOG_NOTICE, "Session tty%d is active", nr);

	if (vt->ios != NULL) {
		fclose(vt->ios);
		vt->ios = NULL;
		vt->fd = -1;
	}

	if (vt->nr > 0) {
		while ((ret = ioctl(fd, VT_DISALLOCATE, vt->nr)) == -1 && errno == EINTR);
		if (ret == -1) {
			syslog(LOG_ERR, "Could not deallocate tty%d: %s", vt->nr, strerror(errno));
			return -1;
		}
		vt->nr = -1;
	}
	return 0;
}

void vt_secure(vt_t *vt) {
	vt->term.c_iflag &= ~IXON;
	vt->term.c_lflag &= ~(ECHO | ISIG);
	if (tcsetattr(vt->fd, TCSANOW, &vt->term) < 0)
		error(EXIT_FAILURE, errno, "secure console input");
}

/* Blank only after VT switching has been locked. The wake key is consumed. */
void vt_blank_until_key(vt_t *vt) {
	struct termios raw = vt->term;
	/* Request monitor power-down, then immediately reset the global mode.
	 * The current blank keeps its power state until explicitly unblanked;
	 * suspend and the handoff back to Sway must not inherit power-down. */
	char vesa[2] = { TIOCL_SETVESABLANK, 3 };
	char command = TIOCL_BLANKSCREEN;
	unsigned char key;
	ssize_t count;
	int blanked, powered;
	raw.c_lflag &= ~ICANON;
	raw.c_cc[VMIN] = 1;
	raw.c_cc[VTIME] = 0;
	fflush(vt->ios);
	tcsetattr(vt->fd, TCSANOW, &raw);
	if (ioctl(vt->fd, TIOCLINUX, vesa) < 0)
		syslog(LOG_WARNING, "Could not select monitor power-down: %s", strerror(errno));
	blanked = ioctl(vt->fd, TIOCLINUX, &command);
	if (blanked < 0)
		syslog(LOG_WARNING, "Could not power down the console: %s", strerror(errno));
	vesa[1] = 0;
	if (ioctl(vt->fd, TIOCLINUX, vesa) < 0)
		syslog(LOG_WARNING, "Could not reset console power-down mode: %s", strerror(errno));
	/* A successful console blank ioctl can leave the signal and backlight on.
	 * Keep DRM master until the wake key, restoring only outputs we changed. */
	powered = display_power_down();
	if (blanked == 0 || powered > 0) {
		do {
			count = read(vt->fd, &key, 1);
		} while (count < 0 && errno == EINTR);
	}
	display_power_restore();
	command = TIOCL_UNBLANKSCREEN;
	ioctl(vt->fd, TIOCLINUX, &command);
	tcsetattr(vt->fd, TCSAFLUSH, &vt->term);
}

CLEANUP void vt_reset(vt_t *vt) {
	display_power_restore();
	clear_vt(vt);
	tcsetattr(vt->fd, TCSANOW, &vt->term_orig);
}

