CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror
RUNTIME_LIBS = -l:libgtk-3.so.0 -l:libgdk-3.so.0 -l:libglib-2.0.so.0 -l:libgobject-2.0.so.0 -l:libgio-2.0.so.0 -l:libpango-1.0.so.0

all: optical-spacing.so hit-areas.so battery-control.so clock-control.so item-divider.so volume-control.so metric-control.so

hit-areas.so: hit-areas.c
	$(CC) $(CFLAGS) -std=c11 -shared -fPIC -Wl,-z,defs $< -o $@ $(RUNTIME_LIBS) -l:libcairo.so.2

volume-control.so: volume-control.c audio-client.c audio-client.h pulse-abi.h volume-curve.h
	$(CC) $(CFLAGS) -std=c11 -shared -fPIC -Wl,-z,defs volume-control.c audio-client.c -o $@ $(RUNTIME_LIBS) -l:libpulse.so.0 -lm

%.so: %.c
	$(CC) $(CFLAGS) -std=c11 -shared -fPIC -Wl,-z,defs $< -o $@ $(RUNTIME_LIBS)

metric-control.so: metric-control.c
	$(CC) $(CFLAGS) -std=c11 -shared -fPIC -Wl,-z,defs $< -o $@ $(RUNTIME_LIBS) -l:libnm.so.0

clock-control.so: clock-control.c
	$(CC) $(CFLAGS) -std=c11 -shared -fPIC -Wl,-z,defs $< -o $@ $(RUNTIME_LIBS) -l:libjson-glib-1.0.so.0
