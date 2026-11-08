#ifndef WAYBAR_AUDIO_CLIENT_H
#define WAYBAR_AUDIO_CLIENT_H

typedef struct AudioClient AudioClient;
typedef struct { double percent; int muted; int available; } AudioState;
typedef void (*AudioChanged)(void *, AudioState);

AudioClient *audio_client_new(const char *sink_name, AudioChanged, void *);
void audio_client_retry(AudioClient *);
AudioState audio_client_state(AudioClient *);
double audio_client_raw_volume(AudioClient *);
int audio_client_step(AudioClient *, double percent);
int audio_client_toggle_mute(AudioClient *);
void audio_client_free(AudioClient *);

#endif
