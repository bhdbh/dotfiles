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

#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <pwd.h>
#include <signal.h>
#include <locale.h>
#include <security/pam_misc.h>

static int oldvt;
static vt_t vt;
static int oldsysrq;
static int oldprintk;
static pid_t chpid;
static int locked;
static userinfo_t user;
static int prompt_visible;

/* Keep PAM's password reader; change only the visible prompt and readiness. */
static int password_conv(int count, const struct pam_message **messages,
                         struct pam_response **responses, void *data) {
	int i;
	struct pam_message copies[PAM_MAX_NUM_MSG];
	const struct pam_message *forward[PAM_MAX_NUM_MSG];
	if (count <= 0 || count > PAM_MAX_NUM_MSG)
		return PAM_CONV_ERR;
	for (i = 0; i < count; i++) {
		copies[i] = *messages[i];
		if (copies[i].msg_style == PAM_PROMPT_ECHO_OFF) {
			if (!prompt_visible) {
				fputs("Password: ", vt.ios);
				fflush(vt.ios);
			}
			prompt_visible = 0;
			copies[i].msg = "";
		}
		forward[i] = &copies[i];
	}
	return misc_conv(count, forward, responses, data);
}

static struct pam_conv conv = {
	password_conv,
	NULL
};

static void get_pam(userinfo_t *uinfo) {
	if (pam_start("sway-tty-lock", uinfo->name, &conv, &uinfo->pamh) != PAM_SUCCESS)
		error(EXIT_FAILURE, 0, "No pam for user %s", uinfo->name);
}

void get_user_by_id(userinfo_t *uinfo, uid_t uid) {
	struct passwd *pw;

	while (errno = 0, (pw = getpwuid(uid)) == NULL && errno == EINTR);
	if (pw == NULL)
		error(EXIT_FAILURE, 0, "No password file entry for uid %u found", uid);

	get_user_by_name(uinfo, pw->pw_name);
}

void get_user_by_name(userinfo_t *uinfo, const char *name) {
	uinfo->name = estrdup(name);
	get_pam(uinfo);
}

CLEANUP void free_user(userinfo_t *uinfo) {
	if (uinfo->pamh != NULL)
		pam_end(uinfo->pamh, uinfo->pam_status);
}

void cleanup() {
	if (options->detach && chpid > 0)
		/* No cleanup in parent after successful fork */
		return;
	console_stop();
	free_user(&user);
	if (!locked)
		session_set_locked(0);
	close(0);
	close(1);
	close(2);
	if (oldprintk > 1)
		write_int_to_file(PRINTK_PATH, oldprintk);
	if (locked)
		return;
	if (oldsysrq > 0)
		write_int_to_file(SYSRQ_PATH, oldsysrq);
	if (vt.fd >= 0)
		vt_reset(&vt);
	vt_lock_switch(0);
	vt_release(&vt, oldvt);
	vt_destroy();
}

void sa_handler_exit(int signum) {
	(void)signum;
	exit(0);
}

void setup_signal(int signum, void (*handler)(int)) {
	struct sigaction sigact;

	sigact.sa_flags = signum == SIGUSR1 ? SA_RESTART : 0;
	sigact.sa_handler = handler;
	sigemptyset(&sigact.sa_mask);
	
	if (sigaction(signum, &sigact, NULL) < 0)
		error(0, errno, "signal %d", signum);
}

int main(int argc, char **argv) {
	uid_t owner;
	userinfo_t *u = &user;

	oldvt = oldsysrq = oldprintk = vt.nr = vt.fd = -1;
	vt.ios = NULL;

	error_init(2);
	setlocale(LC_ALL, "C");
	parse_options(argc, argv);

	if (geteuid() != 0)
		error(EXIT_FAILURE, 0, "Must be root!");

	setup_signal(SIGTERM, sa_handler_exit);
	setup_signal(SIGQUIT, sa_handler_exit);
	setup_signal(SIGHUP, SIG_IGN);
	setup_signal(SIGINT, SIG_IGN);
	setup_signal(SIGUSR1, console_wake);
	setup_signal(SIGUSR2, SIG_IGN);

	vt_init();
	vt_get_current(&oldvt, &owner);

	if (options->lock_switch != -1) {
		if (vt_lock_switch(options->lock_switch) == -1)
			exit(EXIT_FAILURE);
		vt_destroy();
		return 0;
	}

	if (get_user_logind(&user, oldvt) == -1 && get_user_utmp(&user, oldvt) == -1)
		get_user_by_id(&user, owner);

	atexit(cleanup);

	if (options->disable_sysrq) {
		oldsysrq = read_int_from_file(SYSRQ_PATH, '\n');
		if (oldsysrq > 0)
			if (write_int_to_file(SYSRQ_PATH, 0) == -1)
				exit(EXIT_FAILURE);
	}

	if (options->mute_kernel_messages) {
		oldprintk = read_int_from_file(PRINTK_PATH, '\t');
		if (oldprintk > 1)
			if (write_int_to_file(PRINTK_PATH, 1) == -1)
				exit(EXIT_FAILURE);
	}

	vt_lock_switch(0);
	vt_acquire(&vt);
	if (vt_lock_switch(1) < 0)
		exit(EXIT_FAILURE);

	if (options->detach) {
		chpid = fork();
		if (chpid < 0) {
			error(EXIT_FAILURE, errno, "fork");
		} else if (chpid > 0) {
			return 0;
		} else {
			setsid();
			sleep(1); /* w/o this, accessing the vt might fail */
			vt_reopen(&vt);
		}
	}
	vt_secure(&vt);

	dup2(vt.fd, 0);
	dup2(vt.fd, 1);
	dup2(vt.fd, 2);
	console_start(&vt);

	if (options->prompt != NULL && options->prompt[0] != '\0') {
		fprintf(vt.ios, "%s\n\n", options->prompt);
	}

	locked = 1;

	while (locked) {
		fputs("Password: ", vt.ios);
		fflush(vt.ios);
		prompt_visible = 1;
		console_ready();
		u->pam_status = pam_authenticate(u->pamh, 0);
		switch (u->pam_status) {
		case PAM_SUCCESS:
			pam_setcred(u->pamh, PAM_REFRESH_CRED);
			locked = 0;
			break;
		case PAM_AUTH_ERR:
		case PAM_MAXTRIES:
			fprintf(vt.ios, "Authentication failed\n\n");
			break;
		case PAM_ABORT:
		case PAM_CRED_INSUFFICIENT:
		case PAM_USER_UNKNOWN:
			fprintf(vt.ios, "%s\n", pam_strerror(u->pamh, u->pam_status));
			sleep(1);
			break;
		default:
			fprintf(vt.ios, "%s\n", pam_strerror(u->pamh, u->pam_status));
			sleep(1);
			break;
		}
	}

	return 0;
}

