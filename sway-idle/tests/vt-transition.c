/* Regression checks for VT handoff. All VT ioctls and time are simulated;
 * the blank/wake test uses a disposable PTY, never a physical console. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <time.h>
#include <sys/ioctl.h>
#include <syslog.h>
#include <pty.h>
#include <poll.h>
#include <unistd.h>

static int fake_ioctl(int, unsigned long, ...);
static int fake_clock(clockid_t, struct timespec *);
static int fake_sleep(const struct timespec *, struct timespec *);
static void fake_syslog(int, const char *, ...);
static FILE *fake_fopen(const char *, const char *);
static ssize_t fake_read(int, void *, size_t);
#define ioctl fake_ioctl
#define clock_gettime fake_clock
#define nanosleep fake_sleep
#define syslog fake_syslog
#define fopen fake_fopen
#define read fake_read
#include "../source/vt.c"
#undef ioctl
#undef clock_gettime
#undef nanosleep
#undef syslog
#undef fopen
#undef read

static long long elapsed;
static int active, activate_calls, disallocate_calls, required_attempts;
static int state_interrupts, activation_error, vesa_mode, blank_calls, wake_calls;
static int blank_error, powerdown_error, powerdown_calls, reset_calls;
static int blank_power_mode;
static int drm_off_calls, drm_on_calls, drm_outputs;
static int acquire_master = -1, cleared_before_activate, error_calls;
static const char *acquire_path;

int display_power_down(void) { drm_off_calls++; return drm_outputs; }
void display_power_restore(void) { drm_on_calls++; }

static ssize_t fake_read(int terminal, void *buffer, size_t size) {
    assert(vesa_mode == 0 && reset_calls == 1);
    assert(drm_off_calls == 1 && drm_on_calls == 0);
    return read(terminal, buffer, size);
}

static void read_until(int terminal, char *output, size_t size, const char *marker) {
    size_t used = 0;
    output[0] = '\0';
    while (!strstr(output, marker)) {
        struct pollfd ready = { .fd = terminal, .events = POLLIN };
        assert(poll(&ready, 1, 1000) == 1);
        assert(used < size - 1);
        ssize_t length = read(terminal, output + used, size - used - 1);
        assert(length > 0);
        used += length;
        output[used] = '\0';
    }
}

static FILE *fake_fopen(const char *path, const char *mode) {
    assert(acquire_path && strcmp(path, "/dev/tty2") == 0);
    return fopen(acquire_path, mode);
}

static int fake_clock(clockid_t clock, struct timespec *out) {
    assert(clock == CLOCK_MONOTONIC);
    out->tv_sec = elapsed / 1000;
    out->tv_nsec = (elapsed % 1000) * 1000000;
    return 0;
}

static int fake_sleep(const struct timespec *delay, struct timespec *remaining) {
    (void)remaining;
    elapsed += delay->tv_sec * 1000 + delay->tv_nsec / 1000000;
    return 0;
}

static int fake_ioctl(int console, unsigned long request, ...) {
    va_list args;
    (void)console;
    va_start(args, request);
    switch (request) {
    case VT_OPENQRY:
        *va_arg(args, int *) = 2;
        break;
    case VT_GETSTATE: {
        struct vt_stat *state = va_arg(args, struct vt_stat *);
        if (state_interrupts) {
            state_interrupts--;
            errno = EINTR;
            va_end(args);
            return -1;
        }
        state->v_active = active;
        break;
    }
    case VT_ACTIVATE: {
        int target = va_arg(args, int);
        if (acquire_master >= 0) {
            char output[256];
            read_until(acquire_master, output, sizeof(output), "\033[H\033[2J\033[3J");
            char *stale = strstr(output, "Password accepted.");
            char *clear = strstr(output, "\033[H\033[2J\033[3J");
            assert(stale && clear && clear > stale);
            cleared_before_activate = 1;
            acquire_master = -1;
        }
        activate_calls++;
        if (activation_error) {
            errno = activation_error;
            va_end(args);
            return -1;
        }
        if (required_attempts > 0 && activate_calls >= required_attempts)
            active = target;
        break;
    }
    case VT_DISALLOCATE:
        (void)va_arg(args, int);
        disallocate_calls++;
        break;
    case TIOCLINUX: {
        unsigned char *command = va_arg(args, unsigned char *);
        if (command[0] == TIOCL_SETVESABLANK) {
            if (command[1] == 3) {
                powerdown_calls++;
                if (powerdown_error) {
                    errno = powerdown_error;
                    va_end(args);
                    return -1;
                }
            } else {
                assert(command[1] == 0);
                reset_calls++;
            }
            vesa_mode = command[1];
        } else if (command[0] == TIOCL_BLANKSCREEN) {
            blank_calls++;
            blank_power_mode = vesa_mode;
            if (blank_error) {
                errno = blank_error;
                va_end(args);
                return -1;
            }
        } else if (command[0] == TIOCL_UNBLANKSCREEN) {
            assert(vesa_mode == 0 && reset_calls == 1);
            wake_calls++;
        }
        else assert(!"Unexpected console blanking command");
        break;
    }
    default:
        assert(!"Unexpected ioctl (especially an unbounded VT_WAITACTIVE)");
    }
    va_end(args);
    return 0;
}

static void fake_syslog(int priority, const char *format, ...) {
    (void)priority;
    (void)format;
}

void error(int status, int number, const char *format, ...) {
    error_calls++;
    (void)status;
    (void)number;
    (void)format;
}

static void reset(void) {
    elapsed = 0;
    active = 2;
    activate_calls = disallocate_calls = state_interrupts = activation_error = 0;
    required_attempts = 1;
    acquire_master = -1;
    acquire_path = NULL;
    cleared_before_activate = error_calls = 0;
    vesa_mode = 0;
    blank_error = powerdown_error = powerdown_calls = reset_calls = 0;
    blank_calls = wake_calls = 0;
    blank_power_mode = -1;
    drm_off_calls = drm_on_calls = 0;
    drm_outputs = 2;
    fd = 42;
}

int main(void) {
    vt_t console;
    int master, slave;
    char slave_name[128], output[128];
    struct termios restored;

    reset();
    console = (vt_t){ .nr = 2, .fd = -1 };
    assert(vt_release(&console, 1) == 0);
    assert(active == 1 && activate_calls == 1 && disallocate_calls == 1);

    reset();
    required_attempts = 2; /* First switch request disappears during resume. */
    console = (vt_t){ .nr = 2, .fd = -1 };
    assert(vt_release(&console, 1) == 0);
    assert(active == 1 && activate_calls == 2 && elapsed >= 500);

    reset();
    required_attempts = 0; /* Compositor never acknowledges the handoff. */
    console = (vt_t){ .nr = 2, .ios = tmpfile() };
    assert(console.ios);
    console.fd = fileno(console.ios);
    assert(vt_release(&console, 1) == -1 && errno == ETIMEDOUT);
    assert(elapsed == 5000 && activate_calls == 10);
    assert(error_calls == 0); /* Handoff diagnostics stay in the journal. */
    assert(disallocate_calls == 0 && console.nr == 2 && console.ios);
    fclose(console.ios);

    reset();
    state_interrupts = 200; /* Repeated signals must not defeat the deadline. */
    assert(activate_vt(1) == -1 && errno == ETIMEDOUT && elapsed == 5000);

    reset();
    activation_error = EPERM;
    assert(activate_vt(1) == -1 && errno == EPERM && elapsed == 0);

    reset();
    active = 1;
    assert(activate_vt(1) == 0 && activate_calls == 0);

    /* Power-down applies to this blank only; the global mode is reset before
     * waiting for a wake key, authentication, suspend or a VT handoff. */
    reset();
    assert(openpty(&master, &slave, NULL, NULL, NULL) == 0);
    console = (vt_t){ .fd = slave, .ios = fdopen(slave, "r+") };
    assert(console.ios && tcgetattr(slave, &console.term) == 0);
    console.term.c_lflag &= ~ECHO;
    assert(tcsetattr(slave, TCSANOW, &console.term) == 0);
    assert(write(master, "x", 1) == 1);
    vt_blank_until_key(&console);
    assert(vesa_mode == 0 && blank_calls == 1 && wake_calls == 1);
    assert(blank_power_mode == 3 && powerdown_calls == 1 && reset_calls == 1);
    assert(drm_off_calls == 1 && drm_on_calls == 1);
    assert(tcgetattr(slave, &restored) == 0);
    assert(restored.c_lflag == console.term.c_lflag);
    assert(restored.c_cc[VMIN] == console.term.c_cc[VMIN]);
    fclose(console.ios);
    close(master);

    /* A failed blank must reset the global mode and return to the prompt
     * without waiting for an input byte. */
    reset();
    blank_error = EIO;
    drm_outputs = 0;
    assert(openpty(&master, &slave, NULL, NULL, NULL) == 0);
    console = (vt_t){ .fd = slave, .ios = fdopen(slave, "r+") };
    assert(console.ios && tcgetattr(slave, &console.term) == 0);
    vt_blank_until_key(&console);
    assert(blank_power_mode == 3 && vesa_mode == 0 && reset_calls == 1);
    assert(wake_calls == 1 && tcgetattr(slave, &restored) == 0);
    assert(restored.c_lflag == console.term.c_lflag);
    fclose(console.ios);
    close(master);

    /* A driver that rejects power-down still gets a blank/wake request. */
    reset();
    powerdown_error = EINVAL;
    assert(openpty(&master, &slave, NULL, NULL, NULL) == 0);
    console = (vt_t){ .fd = slave, .ios = fdopen(slave, "r+") };
    assert(console.ios && tcgetattr(slave, &console.term) == 0);
    assert(write(master, "x\n", 2) == 2);
    vt_blank_until_key(&console);
    assert(blank_power_mode == 0 && blank_calls == 1 && wake_calls == 1);
    assert(vesa_mode == 0 && reset_calls == 1);
    fclose(console.ios);
    close(master);

    /* A reused lock console is cleared before it becomes visible. Seed it
     * with the old success notice and inspect output at VT_ACTIVATE. */
    reset();
    active = 1;
    assert(openpty(&master, &slave, slave_name, NULL, NULL) == 0);
    assert(fcntl(master, F_SETFL, O_NONBLOCK) == 0);
    const char stale[] = "Password accepted. Returning to tty1...\n";
    assert(write(slave, stale, sizeof(stale) - 1) == sizeof(stale) - 1);
    acquire_path = slave_name;
    acquire_master = master;
    vt_acquire(&console);
    assert(cleared_before_activate && active == 2);
    vt_reset(&console);
    read_until(master, output, sizeof(output), "\033[H\033[2J\033[3J");
    assert(strcmp(output, "\033[H\033[2J\033[3J") == 0);
    fclose(console.ios);
    close(slave);
    close(master);
    puts("11 VT transition/power-down/clearing regression checks passed (no physical VT accessed)");
    return 0;
}
