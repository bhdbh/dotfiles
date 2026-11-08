#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include "../scripts/battery-control.c"

typedef struct { char markup[256]; int visible, syncing, enabled, writes; } Widget;
typedef struct { AsyncCallback callback; void *job; const char *output; int success; } Process;
typedef struct { int (*callback)(void *); void *data; int active; } Timer;
static Process *last_process;
static Timer timers[64];
static unsigned int timer_count;
static int spawns;
static int64_t now;

void *gtk_widget_get_style_context(void *widget) { return widget; }
void gtk_style_context_add_class(void *widget, const char *name) {
    if (!strcmp(name,"syncing")) ((Widget *)widget)->syncing = 1;
    if (!strcmp(name,"enabled")) ((Widget *)widget)->enabled = 1;
}
void gtk_style_context_remove_class(void *widget, const char *name) {
    if (!strcmp(name,"syncing")) ((Widget *)widget)->syncing = 0;
    if (!strcmp(name,"enabled")) ((Widget *)widget)->enabled = 0;
}
void gtk_widget_set_visible(void *widget, int visible) { ((Widget *)widget)->visible = visible; }
void gtk_label_set_markup(void *widget, const char *markup) {
    Widget *w = widget;
    snprintf(w->markup,sizeof(w->markup),"%s",markup); w->writes++;
}
int64_t g_get_monotonic_time(void) { return now; }
unsigned int g_timeout_add(unsigned int ms, int (*callback)(void *), void *data) {
    assert(ms == 200); assert(timer_count + 1 < 64);
    timers[++timer_count] = (Timer){callback,data,1}; return timer_count;
}
int g_source_remove(unsigned int id) { if (id) timers[id].active = 0; return 1; }
void g_signal_handler_disconnect(void *widget, unsigned long id) { (void)widget; (void)id; }
void *g_subprocess_newv(const char *const *argv, unsigned int flags, void **error) {
    (void)argv; (void)flags; (void)error;
    last_process = calloc(1,sizeof(*last_process)); last_process->success = 1; spawns++;
    return last_process;
}
void g_subprocess_communicate_utf8_async(void *process, const char *input, void *cancel, AsyncCallback callback, void *job) {
    (void)input; (void)cancel;
    ((Process *)process)->callback = callback; ((Process *)process)->job = job;
}
int g_subprocess_communicate_utf8_finish(void *process, void *result, char **output, char **errors, void **error) {
    (void)result; (void)errors; (void)error;
    *output = strdup(((Process *)process)->output); return 1;
}
int g_subprocess_get_successful(void *process) { return ((Process *)process)->success; }
void g_object_unref(void *process) { free(process); }
static void finish(Process *process, const char *output) {
    process->output = output; process->callback(process,NULL,process->job);
}
static void tick(unsigned int id, unsigned int ms) {
    now += (int64_t)ms * 1000;
    assert(timers[id].active);
    if (!timers[id].callback(timers[id].data)) timers[id].active = 0;
}
#define IDLE_FULL "Battery:\t<span foreground=\"#f4f4f5\">81%</span>\t1\t1\tNot charging\t81\n"
#define CHARGING_FULL "Battery:\t<span foreground=\"#22c55e\">81%</span>\t1\t1\tCharging\t81\n"
#define IDLE_LIMIT "Battery:\t<span foreground=\"#f4f4f5\">81%</span>\t1\t0\tNot charging\t81\n"
#define STALE_CHARGING_LIMIT "Battery:\t<span foreground=\"#f4f4f5\">81%</span>\t1\t0\tCharging\t81\n"
#define UNPLUGGED "Battery:\t<span foreground=\"#f4f4f5\">81%</span>\t0\t1\tDischarging\t81\n"
#define FULL_BATTERY "Battery:\t<span foreground=\"#f4f4f5\">100%</span>\t1\t1\tFull\t100\n"

int main(void) {
    Widget title={0}, value={0}, charge={0}, divider={0}, circle={0};
    Battery b={.alive=1,.references=1,.title=&title,.label=&value,.charge=&charge,.divider=&divider,.circle=&circle};
    poll_battery(&b); Process *old = last_process;
    run(&b,"toggle-charge",1); finish(last_process,IDLE_FULL);
    assert(b.sync == CHARGE_STARTING && value.syncing && circle.enabled);
    assert(strstr(value.markup,"#22c55e"));
    finish(old,IDLE_LIMIT);
    assert(b.full && value.syncing && circle.enabled && strstr(value.markup,"#22c55e"));
    unsigned int timer=b.sync_timer;
    tick(timer,200); Process *poll=last_process;
    int spawned=spawns;
    for (int i=0;i<5;i++) tick(timer,200);
    assert(spawns == spawned); /* One poll in flight, no queue. */
    finish(poll,CHARGING_FULL);
    assert(b.sync == CHARGE_SETTLED && !b.sync_timer && !value.syncing && !timers[timer].active);
    assert(strstr(value.markup,"#22c55e"));
    puts("PASS confirmed mode bridges stale Charging; stale jobs ignored; one poll in flight; settles on actual status");

    run(&b,"toggle-charge",1); finish(last_process,IDLE_FULL);
    timer=b.sync_timer; tick(timer,5000);
    assert(b.sync == CHARGE_SETTLED && !b.sync_timer && !value.syncing);
    assert(strstr(value.markup,"#f4f4f5"));
    puts("PASS bounded timeout restores the real reported color");

    run(&b,"toggle-charge",1); finish(last_process,IDLE_FULL);
    tick(b.sync_timer,200); finish(last_process,UNPLUGGED);
    assert(b.sync == CHARGE_SETTLED && !charge.visible && !divider.visible && !value.syncing);
    assert(strstr(value.markup,"#f4f4f5"));
    run(&b,"toggle-charge",1); last_process->success=0; finish(last_process,"error\n");
    assert(!b.sync_timer && !value.syncing && !circle.enabled == !b.full);
    run(&b,"toggle-charge",1); finish(last_process,FULL_BATTERY);
    assert(!b.sync_timer && !value.syncing && strstr(value.markup,"#f4f4f5"));
    puts("PASS unplug, failed action and battery already full do not claim charging");

    run(&b,"toggle-charge",1); finish(last_process,IDLE_FULL);
    timer=b.sync_timer; tick(timer,200); old=last_process;
    run(&b,"toggle-charge",1);
    assert(!timers[timer].active && b.sync == CHARGE_SETTLED);
    finish(last_process,STALE_CHARGING_LIMIT);
    assert(b.sync == CHARGE_STOPPING && !circle.enabled && value.syncing);
    finish(old,CHARGING_FULL);
    assert(b.sync == CHARGE_STOPPING && !circle.enabled && !b.full);
    tick(b.sync_timer,200); finish(last_process,IDLE_LIMIT);
    assert(!b.sync_timer && b.sync == CHARGE_SETTLED && !value.syncing);
    assert(b.references == 1 && !b.polling && !b.acting);
    puts("PASS fast reversals cancel older transitions and cannot restore an outdated mode");

    run(&b,"toggle-charge",1); finish(last_process,IDLE_FULL);
    tick(b.sync_timer,200); old=last_process;
    Battery *heap=malloc(sizeof(*heap)); *heap=b;
    ((Job *)old->job)->battery=heap;
    timers[b.sync_timer].data=heap;
    int writes=value.writes;
    wbcffi_deinit(heap); finish(old,CHARGING_FULL);
    assert(value.writes == writes);
    puts("PASS teardown cancels transition timers and async completions do not touch disposed widgets");
}
