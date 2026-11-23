/* SPDX-License-Identifier: GPL-2.0-or-later
 * Local console DPMS and systemd readiness integration for physlock.
 */
#include "physlock.h"
#include <errno.h>
#include <glob.h>
#include <linux/tiocl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <systemd/sd-daemon.h>
#include <time.h>
#include <unistd.h>

static vt_t *console;
static pthread_t worker;
static int worker_started, ready;
static atomic_int stopping;
static volatile sig_atomic_t wake_requested;
static volatile sig_atomic_t console_fd = -1;

static int blank_minutes(void) {
	glob_t paths = {0};
	size_t i;
	int online = 0;
	if (glob("/sys/class/power_supply/*/online", 0, NULL, &paths) == 0) {
		for (i = 0; i < paths.gl_pathc && !online; i++) {
			FILE *file = fopen(paths.gl_pathv[i], "r");
			int value;
			if (file != NULL) {
				if (fscanf(file, "%d", &value) == 1 && value == 1)
					online = 1;
				fclose(file);
			}
		}
	}
	globfree(&paths);
	return online ? 30 : 5;
}

static void configure_timeout(int minutes) {
	/* Linux console escape: set its inactivity timeout in minutes. */
	dprintf(console->fd, "\033[9;%d]", minutes);
}

static void *watch_power(void *unused) {
	int previous = blank_minutes(), ticks = 0;
	struct timespec interval = {0, 200000000};
	sigset_t signals;
	(void)unused;
	sigemptyset(&signals);
	sigaddset(&signals, SIGUSR1);
	pthread_sigmask(SIG_BLOCK, &signals, NULL);
	while (!atomic_load(&stopping)) {
		if (++ticks == 5) {
			int current = blank_minutes();
			if (current != previous) {
				configure_timeout(current);
				previous = current;
			}
			ticks = 0;
		}
		nanosleep(&interval, NULL);
	}
	return NULL;
}

void console_start(vt_t *vt) {
	char vesa[] = {TIOCL_SETVESABLANK, 3};
	console = vt;
	console_fd = vt->fd;
	fputs("\033[0m\033[2J\033[H", console->ios);
	fflush(console->ios);
	if (ioctl(console->fd, TIOCLINUX, vesa) < 0)
		error(0, errno, "Could not configure console display power saving");
	configure_timeout(blank_minutes());
	worker_started = pthread_create(&worker, NULL, watch_power, NULL) == 0;
}

void console_wake(int signal) {
	int saved_errno = errno;
	char command = TIOCL_UNBLANKSCREEN;
	(void)signal;
	wake_requested = 1;
	if (console_fd >= 0)
		ioctl(console_fd, TIOCLINUX, &command);
	errno = saved_errno;
}

void console_ready(void) {
	struct termios saved, raw;
	struct pollfd input;
	char command = TIOCL_BLANKSCREEN;
	int blanked = 0;
	if (ready)
		return;
	ready = 1;
	session_set_locked(1);
	if (options->blank && tcgetattr(console->fd, &saved) == 0) {
		raw = saved;
		raw.c_lflag &= ~ICANON;
		raw.c_cc[VMIN] = 1;
		raw.c_cc[VTIME] = 0;
		if (tcsetattr(console->fd, TCSANOW, &raw) == 0) {
			blanked = ioctl(console->fd, TIOCLINUX, &command) == 0;
			if (!blanked)
				tcsetattr(console->fd, TCSANOW, &saved);
		}
		if (!blanked)
			error(0, errno, "Could not power off console displays");
	}
	sd_notify(0, blanked ? "READY=1\nSTATUS=Locked; console displays off"
	                    : "READY=1\nSTATUS=Locked; waiting for password on TTY");
	if (!blanked)
		return;
	input.fd = console->fd;
	input.events = POLLIN;
	while (!wake_requested) {
		if (poll(&input, 1, 250) > 0 && (input.revents & POLLIN)) {
			char key;
			if (read(console->fd, &key, 1) == 1)
				break;
		}
	}
	console_wake(0);
	tcsetattr(console->fd, TCSAFLUSH, &saved);
}

void console_stop(void) {
	if (worker_started) {
		atomic_store(&stopping, 1);
		pthread_join(worker, NULL);
	}
	console_fd = -1;
}
