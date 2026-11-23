#!/bin/sh

# hwmon numbers can change between boots; identify the CPU driver by name.
for sensor in /sys/class/hwmon/hwmon*; do
    IFS= read -r driver < "$sensor/name" || continue
    case "$driver" in
        k10temp)
            # Prefer AMD's die temperature when the driver exposes it.
            if [ -r "$sensor/temp2_input" ]; then
                printf '%s/temp2_input\n' "$sensor"
                exit 0
            fi
            ;;
        coretemp) ;;
        *) continue ;;
    esac

    if [ -r "$sensor/temp1_input" ]; then
        printf '%s/temp1_input\n' "$sensor"
        exit 0
    fi
done
