/* Linked only into the unprivileged regression executable. No real PAM or
 * virtual-terminal operation is performed. */
#include "physlock.h"
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <security/pam_misc.h>

static void event(const char *name) {
    const char *path = getenv("TEST_EVENTS");
    int out = open(path, O_WRONLY | O_APPEND | O_CREAT, 0600);
    if (out < 0) _exit(90);
    if (write(out, name, strlen(name)) < 0 || write(out, "\n", 1) != 1) _exit(91);
    close(out);
}

uid_t __wrap_geteuid(void) { return 0; }
unsigned int __wrap_sleep(unsigned int seconds) { (void)seconds; return 0; }
pid_t __wrap_setsid(void) { return getpid(); }

int pam_start(const char *service, const char *user, const struct pam_conv *conv,
              pam_handle_t **handle) {
    (void)service; (void)user; (void)conv;
    if (!getenv("LC_ALL") || strcmp(getenv("LC_ALL"), "C") != 0) _exit(93);
    *handle = (pam_handle_t *)malloc(1);
    return *handle ? PAM_SUCCESS : PAM_BUF_ERR;
}
int pam_end(pam_handle_t *handle, int status) {
    (void)status; free(handle); return PAM_SUCCESS;
}
int pam_authenticate(pam_handle_t *handle, int flags) {
    static int attempts;
    (void)handle; (void)flags;
    event("auth");
    usleep(300000);
    fputs("Password: \n", stdout);
    fflush(stdout);
    if (strcmp(getenv("TEST_MODE"), "retry") == 0) {
        if (attempts++ < 2) {
            event("rejected");
            return attempts == 1 ? PAM_AUTH_ERR : PAM_MAXTRIES;
        }
    }
    return strcmp(getenv("TEST_MODE"), "denied") == 0 ? PAM_ABORT : PAM_SUCCESS;
}
int pam_setcred(pam_handle_t *handle, int flags) {
    (void)handle; (void)flags; return PAM_SUCCESS;
}
const char *pam_strerror(pam_handle_t *handle, int status) {
    (void)handle; (void)status; return "simulated denial";
}
int misc_conv(int count, const struct pam_message **messages,
              struct pam_response **responses, void *data) {
    (void)count; (void)messages; (void)responses; (void)data;
    return PAM_CONV_ERR;
}
int get_user_logind(userinfo_t *user, int vt) { (void)user; (void)vt; return -1; }
int get_user_utmp(userinfo_t *user, int vt) { (void)user; (void)vt; return -1; }
void vt_init(void) {}
void vt_destroy(void) {}
void vt_get_current(int *number, uid_t *owner) { *number = 1; *owner = getuid(); }
int vt_lock_switch(int lock) { event(lock ? "lock" : "unlock"); return 0; }
void vt_acquire(vt_t *vt) {
    const char *console = getenv("TEST_CONSOLE");
    vt->nr = 2; vt->ios = console ? fopen(console, "w+") : tmpfile();
    if (!vt->ios) _exit(92);
    vt->fd = fileno(vt->ios);
}
void vt_reopen(vt_t *vt) { (void)vt; }
void vt_secure(vt_t *vt) {
    (void)vt;
    usleep(200000);
    if (strcmp(getenv("TEST_MODE"), "setup-failure") == 0)
        error(EXIT_FAILURE, 0, "simulated secure-input failure");
    event("secure");
}
void vt_blank_until_key(vt_t *vt) { (void)vt; event("blank"); }
void vt_reset(vt_t *vt) { (void)vt; event("reset"); }
int vt_release(vt_t *vt, int number) {
    (void)number;
    event("release");
    if (strcmp(getenv("TEST_MODE"), "return-failure") == 0)
        return -1;
    if (vt->ios) fclose(vt->ios);
    vt->ios = NULL; vt->fd = -1;
    return 0;
}
