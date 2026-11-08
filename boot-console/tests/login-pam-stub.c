/* Test-only PAM replacement and PTY ownership shim. Reject every password,
 * and stop if any account/session setup is attempted. Never install this. */
#define _POSIX_C_SOURCE 200809L
#include <security/pam_appl.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <utmpx.h>

struct pam_handle {
    struct pam_conv conversation;
    char *user;
    char *prompt;
    unsigned int attempts;
};

__attribute__((constructor)) static void unprivileged_only(void) {
    if (getuid() == 0 || geteuid() == 0)
        _exit(94);
}

static void event(const char *name) {
    const char *path = getenv("TEST_LOGIN_EVENTS");
    if (!path) _exit(95);
    int fd = open(path, O_WRONLY | O_APPEND | O_CREAT, 0600);
    if (fd < 0 || write(fd, name, strlen(name)) < 0 || write(fd, "\n", 1) != 1)
        _exit(96);
    close(fd);
}

int pam_start(const char *service, const char *user, const struct pam_conv *conv,
              pam_handle_t **handle) {
    if (strcmp(service, "login") != 0) _exit(97);
    *handle = calloc(1, sizeof(**handle));
    if (!*handle) return PAM_BUF_ERR;
    (*handle)->conversation = *conv;
    (*handle)->user = user ? strdup(user) : NULL;
    event("start");
    return PAM_SUCCESS;
}

int pam_set_item(pam_handle_t *handle, int item, const void *value) {
    char **target;
    if (item == PAM_USER) target = &handle->user;
    else if (item == PAM_USER_PROMPT) target = &handle->prompt;
    else return PAM_SUCCESS;
    free(*target);
    *target = value ? strdup(value) : NULL;
    return value && !*target ? PAM_BUF_ERR : PAM_SUCCESS;
}

int pam_get_item(const pam_handle_t *handle, int item, const void **value) {
    if (item != PAM_USER) _exit(98);
    *value = handle->user;
    return PAM_SUCCESS;
}

static int prompt(pam_handle_t *handle, int style, const char *text, char **answer) {
    const struct pam_message message = { style, text };
    const struct pam_message *messages[] = { &message };
    struct pam_response *response = NULL;
    int result = handle->conversation.conv(1, messages, &response,
                                            handle->conversation.appdata_ptr);
    if (result == PAM_SUCCESS && response) {
        *answer = response[0].resp;
        free(response);
    }
    return result;
}

int pam_authenticate(pam_handle_t *handle, int flags) {
    (void)flags;
    if (!handle->user) {
        if (!handle->prompt) _exit(99);
        int result = prompt(handle, PAM_PROMPT_ECHO_ON, handle->prompt, &handle->user);
        if (result != PAM_SUCCESS) return result;
    }
    char *password = NULL;
    int result = prompt(handle, PAM_PROMPT_ECHO_OFF, "Password: ", &password);
    if (result != PAM_SUCCESS) return result;
    if (password) {
        volatile char *cursor = password;
        size_t length = strlen(password);
        while (length--) *cursor++ = 0;
        free(password);
    }
    event("rejected");
    handle->attempts++;
    if (getenv("TEST_LOGIN_MAXTRIES")) return PAM_MAXTRIES;
    return handle->attempts == 2 ? PAM_USER_UNKNOWN : PAM_AUTH_ERR;
}

int pam_end(pam_handle_t *handle, int status) {
    (void)status;
    event("end");
    free(handle->user);
    free(handle->prompt);
    free(handle);
    return PAM_SUCCESS;
}

const char *pam_strerror(pam_handle_t *handle, int status) {
    (void)handle; (void)status;
    return "simulated authentication rejection";
}

unsigned int sleep(unsigned int seconds) { (void)seconds; return 0; }
/* Native login normally takes ownership of the terminal as root. Keep this
 * disposable PTY owned by the test user and skip host accounting/audit. */
int fchown(int fd, uid_t owner, gid_t group) {
    (void)owner; (void)group;
    if (!isatty(fd)) _exit(103);
    return 0;
}
void updwtmpx(const char *file, const struct utmpx *record) { (void)file; (void)record; }
int audit_open(void) { return -1; }
int pam_acct_mgmt(pam_handle_t *handle, int flags) {
    (void)handle; (void)flags; _exit(100);
}
int pam_setcred(pam_handle_t *handle, int flags) {
    (void)handle; (void)flags; _exit(101);
}
int pam_open_session(pam_handle_t *handle, int flags) {
    (void)handle; (void)flags; _exit(102);
}
